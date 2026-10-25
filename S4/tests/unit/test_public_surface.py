"""公共接口与调用方期望的一致性（DX-API-001、00 文档 6.1、CP-ASM-04）。

S1 用 ``getattr(factory_dhis2.api, name)`` 按**名字**解析调用，S2 按它自己的 Protocol
调用四个只读函数。名字、位置顺序、关键字参数名与返回字段名都是跨仓库契约，改一个就是在
破坏别人——而这种破坏在本子系统自己的单测里通常看不出来。

本文件不导入 S1/S2（依赖方向不允许），而是**读取它们的源码**并逐条比对。文件不在时跳过：
从私有 PyPI 装包的 CI 环境里只有本仓库。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from factory_foundation.api import is_external_io

import factory_dhis2.api as api

#: 仓库根：本文件在 ``S4/tests/unit/`` 下，向上三层
_ROOT = Path(__file__).resolve().parents[3]
REPO = _ROOT / "S1/src/factory_control"
S1_REGISTRY = REPO / "adapters/registry.py"
S1_PROTOCOLS = REPO / "ports/protocols.py"
S1_DTO = REPO / "ports/dto.py"
S1_FAKE = REPO / "testing/fakes.py"
S2_PROTOCOL = _ROOT / "S2/src/factory_blueprint/s4/protocol.py"

#: S1 的 ExecutionPort 声明的关键字参数（长任务与幂等约定，00 文档 6.3/6.4）
REQUIRED_KEYWORD_ONLY = {"idempotency_key", "progress", "cancel"}


def _source(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"调用方源码不在本环境：{path}")
    return path.read_text(encoding="utf-8")


def _method_map(path: Path) -> dict[str, str]:
    """S1 ``ExecutionAdapter.METHOD_MAP``：端口方法名 → api 函数名。

    只在 ``ExecutionAdapter`` 类体内取，否则会顺带抓到其他 Port 的映射表。
    """
    text = _source(path)
    match = re.search(r"class ExecutionAdapter\b(.*?)(?=\nclass |\Z)", text, re.S)
    if match is None:
        return {}
    pairs = re.findall(r'"([a-z_]+)"\s*:\s*\(\s*"([a-z_]+)"', match.group(1))
    return {port: function for port, function in pairs}


def _protocol_signature(path: Path, class_name: str) -> dict[str, ast.arguments]:
    tree = ast.parse(_source(path))
    out: dict[str, ast.arguments] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    out[item.name] = item.args
    return out


def _external_io_methods(path: Path) -> set[str]:
    """``ExecutionAdapter.EXTERNAL_IO_METHODS``（同样只在类体内取）。"""
    text = _source(path)
    block = re.search(r"class ExecutionAdapter\b(.*?)(?=\nclass |\Z)", text, re.S)
    if block is None:
        pytest.skip("调用方没有 ExecutionAdapter 类")
    match = re.search(r"EXTERNAL_IO_METHODS\s*[:=].*?\((.*?)\)", block.group(1), re.S)
    if match is None:
        pytest.skip("调用方没有 EXTERNAL_IO_METHODS 清单")
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_every_method_map_entry_resolves_to_a_callable() -> None:
    """S1 解析不到的名字会在运行期抛 ProviderMethodUnavailableError——那正是它现在会做的。"""
    mapping = _method_map(S1_REGISTRY)
    assert mapping, "METHOD_MAP 解析失败：S1 改了结构，这个测试需要跟着改"
    missing = [
        (port, function)
        for port, function in mapping.items()
        if not callable(getattr(api, function, None))
    ]
    assert not missing, f"factory_dhis2.api 缺少：{missing}"


def test_external_io_marker_matches_the_callers_expectation() -> None:
    """S1 在事务内调用外部 I/O 方法时会直接报错，标记集合必须覆盖它的全部声明。"""
    expected = _external_io_methods(S1_REGISTRY)
    actual = {
        name
        for name in api.__all__
        if is_external_io(getattr(api, name, None))  # type: ignore[arg-type]
    }
    assert expected <= actual, f"S1 认为是外部 I/O 但我们没有标记：{sorted(expected - actual)}"


def test_positional_parameter_names_follow_the_protocol() -> None:
    """S1 按位置传参，参数名与顺序变了就会在它的活动里错位。"""
    signatures = _protocol_signature(S1_PROTOCOLS, "ExecutionPort")
    assert signatures, "ExecutionPort 解析失败"
    for port_name, arguments in signatures.items():
        function = getattr(api, port_name, None)
        if function is None or not callable(function):
            continue
        their = [item.arg for item in arguments.args if item.arg != "self"]
        ours = _positional_names(function)
        # 环境键与项目键的位置必须一致；其余参数允许 S4 多出可选参数
        for index, name in enumerate(their):
            if index >= len(ours):
                break
            assert _compatible(name, ours[index]), f"{port_name}: 第 {index} 个参数 {name} vs {ours[index]}"


def test_long_running_functions_accept_progress_and_cancel() -> None:
    """00 文档 6.3：长任务必须接受 ProgressReporter 与 CancellationToken。"""
    import inspect

    for name in (
        "discover",
        "prepare_and_validate",
        "commit",
        "post_verify",
        "import_fixtures",
        "delete_objects",
    ):
        function = getattr(api, name)
        parameters = inspect.signature(function).parameters
        for keyword in REQUIRED_KEYWORD_ONLY & set(parameters):
            assert parameters[keyword].kind is inspect.Parameter.KEYWORD_ONLY, f"{name}.{keyword}"
        assert "progress" in parameters and "cancel" in parameters, name


@pytest.mark.parametrize("name", sorted(api.__all__))
def test_everything_exported_actually_exists(name: str) -> None:
    assert hasattr(api, name), name


def test_s2_read_only_surface_is_compatible() -> None:
    """S2 的 ``ReadonlyDhis2`` 四个函数：名字与参数顺序逐字一致。"""
    signatures = _protocol_signature(S2_PROTOCOL, "ReadonlyDhis2")
    if not signatures:
        pytest.skip("S2 协议文件不在此环境")
    for name, arguments in signatures.items():
        their = [item.arg for item in arguments.args if item.arg != "self"]
        function = getattr(api, name)
        ours = _positional_names(function)
        assert their == ours[: len(their)], f"{name}: {their} vs {ours}"


def test_return_fields_cover_what_s1_reads() -> None:
    """S1 的镜像 ``extra="ignore"``：我们少给字段它就用默认值，那是最坏的一种沉默。

    ``content`` 一项例外：它是可选的大字段，S1 只在需要时才去读产物，S4 不把它塞进
    每次新鲜度检查的返回值里。
"""
    skips = {"CapabilityFreshnessInfo": {"content"}}
    mirrors = _mirror_fields(S1_DTO)
    from pydantic import BaseModel

    for model_name, fields in mirrors.items():
        our_model = getattr(api, model_name, None)
        if our_model is None or not (isinstance(our_model, type) and issubclass(our_model, BaseModel)):
            continue
        ours = set(our_model.model_fields)
        missing = {field for field in fields if field not in ours} - skips.get(model_name, set())
        assert not missing, f"{model_name} 缺字段：{sorted(missing)}"


def _positional_names(function: object) -> list[str]:
    import inspect

    signature = inspect.signature(function)  # type: ignore[arg-type]
    return [
        parameter.name
        for parameter in signature.parameters.values()
        if parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]


def _compatible(their: str, ours: str) -> bool:
    """协议里叫 ``environment_key``，实现可以同名；别名只接受已知的同义写法。"""
    aliases = {
        "environment_key": {"environment_key"},
        "project_id": {"project_id"},
        "package_artifact_id": {"package_artifact_id"},
        "expected_hash": {"expected_hash"},
        "fixture_artifact_id": {"fixture_artifact_id"},
        "deletion_artifact_id": {"deletion_artifact_id"},
        "execution_id": {"execution_id"},
        "idempotency_key": {"idempotency_key"},
        "outcome": {"outcome"},
        "note": {"note"},
        "operator": {"operator"},
    }
    return ours in aliases.get(their, {their})


def _mirror_fields(path: Path) -> dict[str, set[str]]:
    """S1 ``ports/dto.py`` 中 S4 段的镜像类字段名（snake_case）。"""
    tree = ast.parse(_source(path))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in {
            "EnvironmentInfo",
            "EnvironmentProtection",
            "CapabilityFreshness",
            "DiscoveryOutcome",
            "Dhis2ValidationOutcome",
            "DriftResult",
            "ExecutionOutcome",
            "ReconcileResult",
            "PostVerifyOutcome",
            "ExpiringCredential",
            "ConnectionCheck",
        }:
            fields: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.add(item.target.id)
            out[node.name] = fields
    # S1 的镜像名与我们的返回名不完全同名，映射过去
    renaming = {"CapabilityFreshness": "CapabilityFreshnessInfo"}
    return {renaming.get(name, name): fields for name, fields in out.items()}


__all__ = ["api"]


def test_s4_publishes_the_content_formats_through_the_public_api() -> None:
    """§1.2-11 / DX-CAP-008 / DX-DSC-009：内容格式必须能从唯一公共入口拿到。

    00 文档 6.1 规定其他子系统只能导入 ``factory_dhis2.api``。格式模型如果只在
    ``factory_dhis2.contract`` 里，S2 与 S3 就面临两个坏选择：违规深导入，或者照注释手抄
    字段名——而产物文档是 ``extra="allow"``，字段抄错不会报错，只会在编译期读出空值。
    """
    capability = api.content_format_schema("CAPABILITY_PROFILE")
    discovery = api.content_format_schema("DISCOVERY_RESULT")

    for schema, expected in ((capability, "environmentKey"), (discovery, "discoveredAt")):
        assert schema["type"] == "object"
        assert expected in schema["properties"], sorted(schema["properties"])[:8]

    # 导出的模型也在 api 上：调用方可以直接用真类型校验读到的产物
    for name in (
        "CapabilityProfileDocument",
        "DiscoveryResultDocument",
        "DiscoveredObjectDocument",
        "OrgUnitDocument",
    ):
        assert name in api.__all__ and hasattr(api, name), name

    assert api.content_format_schema("capability_profile") is not None
    from factory_dhis2.errors import FeatureNotEnabledError

    with pytest.raises(FeatureNotEnabledError):
        api.content_format_schema("SOMETHING_UNPUBLISHED")
