"""S4 的架构守卫（DX-BR-01…12、00 文档 4.2/6.2/6.9）。

这些检查不是风格问题，每一条对应一个**会在生产里出事**的失效模式：

* 只有 :mod:`factory_dhis2.client.session` 可以直连 DHIS2：任何模块自己 ``import httpx``
  就绕过了认证、超时、重试、脱敏与 correlationId（DX-CLI-001）。
* 凭证引用之外不允许出现“看起来像令牌”的常量（DX-ENV-003、FR-SEC-006）。
* 外部 I/O 函数必须真的带标记，否则 S0 无法拒绝“事务中调用 DHIS2”（00 文档 6.2）。
* ``dx`` 不允许跨 schema 外键（00 文档 4.2、D-08）。
* 禁止导入 S1/S2/S3（SRS 1.3：S4 不调用它们）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from factory_foundation.api import DxBase, find_cross_schema_fk, is_external_io

import factory_dhis2.api as api

SRC = Path(api.__file__).resolve().parent
#: 允许直接使用 httpx 的模块（Client 与其测试替身）
HTTPX_ALLOWED = {
    "client/session.py",
    "client/tls.py",
    "testing/transport.py",
}
FORBIDDEN_IMPORTS = ("factory_control", "factory_blueprint", "factory_intelligence")
#: 标记为外部 I/O 的公共函数（S1 的 EXTERNAL_IO_METHODS 必须落在其中）
EXTERNAL_IO_API = {
    "test_connection",
    "check_capability_freshness",
    "probe_capabilities",
    "discover",
    "generate_uids",
    "uid_exists",
    "code_exists",
    "has_associated_data",
    "prepare_and_validate",
    "check_drift",
    "commit",
    "reconcile",
    "post_verify",
    "import_fixtures",
    "run_maintenance",
    "delete_objects",
    "diff_snapshot_vs_current",
    "diff_environments",
}


def _modules() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in str(path))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_names(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", _modules(), ids=lambda path: str(path.relative_to(SRC)))
def test_only_the_client_talks_http_directly(path: Path) -> None:
    """DX-CLI-001 / DX-BR-01：所有 DHIS2 请求必须经 Client 发出。"""
    relative = str(path.relative_to(SRC))
    imported = _imported_names(_tree(path))
    uses_httpx = any(name == "httpx" or name.startswith("httpx.") for name in imported)
    if relative not in HTTPX_ALLOWED:
        assert not uses_httpx, f"{relative} 直接用 httpx：绕过 Client 就绕过了脱敏与重试"


@pytest.mark.parametrize("path", _modules(), ids=lambda path: str(path.relative_to(SRC)))
def test_s4_never_calls_the_other_subsystems(path: Path) -> None:
    """SRS 1.3：S4 不调用 S1、S2、S3（依赖方向是 S4 → S0）。"""
    imported = _imported_names(_tree(path))
    offenders = {
        name for name in imported if name.split(".")[0] in FORBIDDEN_IMPORTS
    }
    assert not offenders, f"{path.name} 导入了 {offenders}"


def test_no_cross_schema_foreign_keys_in_dx() -> None:
    import factory_dhis2.models  # noqa: F401 - 装载 dx 元数据

    violations = find_cross_schema_fk([DxBase.metadata])
    assert not violations, [str(item) for item in violations]


def test_external_io_functions_are_marked() -> None:
    """标记缺失的后果不是“慢”，是 S1 的事务里挂着一个 60 秒的网络等待。"""
    unmarked = sorted(
        name for name in EXTERNAL_IO_API if not is_external_io(getattr(api, name, None))  # type: ignore[arg-type]
    )
    assert not unmarked, unmarked


def test_transactional_functions_are_not_marked() -> None:
    """反过来也成立：环境 CRUD 与执行查询必须能被并入调用方事务（SRS 7.1/7.4）。"""
    wrongly_marked = sorted(
        name
        for name in (
            "create_environment",
            "update_environment",
            "disable_environment",
            "get_environment",
            "list_environments",
            "get_protection",
            "set_emergency_lock",
            "list_expiring_credentials",
            "get_execution",
            "get_execution_by_key",
            "resolve_manual_check",
            "delete_project_data",
        )
        if is_external_io(getattr(api, name, None))  # type: ignore[arg-type]
    )
    assert not wrongly_marked, wrongly_marked


def test_dx_schema_is_the_only_one_s4_writes() -> None:
    """每张表都在 ``dx``：出现别的 schema 就是越界写别人的数据（D-08、D-14）。"""
    import factory_dhis2.models  # noqa: F401

    schemas = {table.schema for table in DxBase.metadata.sorted_tables}
    assert schemas == {"dx"}, schemas


def test_long_running_functions_persist_resumable_state() -> None:
    """00 文档 6.3 / DX-EXE-004：可恢复的中间状态存在 ``dx``，不靠内存。"""
    import factory_dhis2.models as models

    assert hasattr(models, "ExecutionStepRow")
    assert hasattr(models, "EnvironmentLockRow")


def test_source_contains_no_look_alike_credentials() -> None:
    """FR-SEC-006：源码里不允许出现可使用的凭证常量（测试令牌形态除外）。"""
    offenders: list[str] = []
    for path in _modules():
        text = path.read_text(encoding="utf-8")
        for marker in ("district", "admin:", "Authorization: Bearer dhp_"):
            if "tests/" in str(path):
                continue
            if marker in text:
                offenders.append(f"{path.relative_to(SRC)}: {marker}")
    assert not offenders, offenders


def test_api_exports_every_requirement_function() -> None:
    """SRS 7 章表格里的函数一个都不能少。"""
    required = [
        "MIGRATION_CONFIG",
        "initialize",
        "create_environment",
        "update_environment",
        "disable_environment",
        "get_environment",
        "list_environments",
        "get_protection",
        "set_emergency_lock",
        "list_expiring_credentials",
        "test_connection",
        # DX-CON-005：最近一次连接测试的只读查询（S1 CP-UI-012、DX-AC-11）
        "get_last_connection_test",
        # DX-CAP-009：最新能力索引的只读查询
        "get_capability_profile_index",
        "check_capability_freshness",
        "probe_capabilities",
        "discover",
        "list_discovery_results",
        # DX-DSC-011：发现对象检索（S1 CP-UI-005、DX-AC-12）
        "search_discovery_objects",
        "generate_uids",
        "uid_exists",
        "code_exists",
        "has_associated_data",
        "prepare_and_validate",
        "check_drift",
        "commit",
        "get_execution_by_key",
        "get_execution",
        "reconcile",
        "resolve_manual_check",
        "post_verify",
        "import_fixtures",
        "run_maintenance",
        "delete_objects",
        "diff_snapshot_vs_current",
        "diff_environments",
        "delete_project_data",
    ]
    missing = [name for name in required if not hasattr(api, name)]
    assert not missing, missing


def test_no_default_environment_in_signatures() -> None:
    """DX-ENV-004 / DX-BR-02：每个涉及 DHIS2 的公共函数都显式接收 environmentKey。

    以**执行**为主语的函数（reconcile、diff_snapshot_vs_current…）按执行 ID 定位，执行行
    里已经记着环境键，因此不在此列。
    """
    import inspect

    execution_keyed = {
        "reconcile",
        "diff_snapshot_vs_current",
        "get_execution",
        "get_execution_by_key",
        "get_execution_status",
        "resolve_manual_check",
        "run_maintenance",
        "delete_project_data",
        # SRS 7.4 就把它写成 (project_id, key_a, key_b)：两个环境键都在参数里
        "diff_environments",
    }
    dhis2_facing = (EXTERNAL_IO_API | {"get_protection", "get_environment", "set_emergency_lock"}) - execution_keyed
    for name in sorted(dhis2_facing):
        function = getattr(api, name, None)
        if function is None:
            continue
        parameters = list(inspect.signature(function).parameters)
        assert parameters, name
        assert parameters[0] == "environment_key", f"{name} 的第一个参数应是 environment_key：{parameters}"


__all__ = ["api", "pytest"]
