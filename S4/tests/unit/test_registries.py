"""登记表与代码必须同步（SRS 附录 A、5 章、DX-ENV/DX-EXE 的错误码）。

一张表悄悄漏登，失败点会在**调用方的事务里**（S0 拒绝未登记的产物类型、审计事件类型与
ID 前缀）。这些测试在本子系统内就把这种漂移抓住。
"""

from __future__ import annotations

import inspect
import re

import pytest

from factory_dhis2 import errors, registries

#: S0 的内置目录已经把 owner 写成 S4 的代码（不重复登记，见 registries 的说明）。
S0_BUILT_IN_S4_CODES = frozenset(
    {
        "DHIS2_AUTH_ERROR",
        "DHIS2_PERMISSION_ERROR",
        "DHIS2_NOT_FOUND",
        "DHIS2_IMPORT_ERROR",
        "DHIS2_TIMEOUT",
        "DRIFT_DETECTED",
    }
)

#: S0 自己拥有、但任何子系统都可以抛出的通用代码（S1 按同一代码做 REST 映射）。
S0_SHARED_CODES = frozenset({"CONCURRENCY_ERROR", "INTERNAL_ERROR"})

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+\.[a-z0-9_]+$")


def _raised_codes() -> set[str]:
    """errors.py 里每个异常类的 canonical code。"""
    found: set[str] = set()
    for _name, obj in inspect.getmembers(errors, inspect.isclass):
        if issubclass(obj, errors.Dhis2Error) and obj is not errors.Dhis2Error:
            found.add(obj.code)
    return found


def test_every_audit_event_follows_the_foundation_naming_rule() -> None:
    for event_type in registries.DX_AUDIT_EVENT_TYPES:
        assert _EVENT_TYPE_RE.match(event_type), event_type


def test_audit_events_match_srs_appendix_a_exactly() -> None:
    """附录 A 列出的 21 个事件，一个不多一个不少。"""
    expected = {
        "dx.environment.created",
        "dx.environment.updated",
        "dx.environment.disabled",
        "dx.environment.lock_changed",
        "dx.credential_ref.changed",
        "dx.connection.tested",
        "dx.capability.probed",
        "dx.capability.stale",
        "dx.discovery.completed",
        "dx.snapshot.created",
        "dx.validate.started",
        "dx.validate.completed",
        "dx.commit.started",
        "dx.commit.completed",
        "dx.commit.failed",
        "dx.commit.needs_manual_check",
        "dx.manual_check.resolved",
        "dx.maintenance.executed",
        "dx.post_verify.completed",
        "dx.fixture.imported",
        "dx.delete.executed",
        "dx.env_lock.acquired",
        "dx.env_lock.released",
    }
    assert set(registries.DX_AUDIT_EVENT_TYPES) == expected


def test_every_audit_event_is_owned_by_s4() -> None:
    assert set(registries.DX_AUDIT_EVENT_TYPES.values()) == {"S4"}


def test_registered_error_codes_have_an_exception_class() -> None:
    """登记了却没人抛出的错误码，等于一条没人执行的规则。"""
    raised = _raised_codes()
    for code in registries.DX_ERROR_CODES:
        assert code in raised | S0_BUILT_IN_S4_CODES, code


def test_every_raised_code_is_registered_or_s0_built_in() -> None:
    known = set(registries.DX_ERROR_CODES) | S0_BUILT_IN_S4_CODES | S0_SHARED_CODES
    for code in _raised_codes():
        assert code in known, code


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        # DX-CLI-003 / 总体 SRS 19.2.2：传输与竞争类可重试
        ("DHIS2_UNREACHABLE", True),
        ("DHIS2_RESPONSE_INVALID", True),
        ("DX_OUTSIDE_COMMIT_WINDOW", True),
        ("DHIS2_PROFILE_STALE", True),
        # 判定、锁、哈希不一致类不得自动重试
        ("ENVIRONMENT_LOCKED", False),
        ("DX_PAYLOAD_HASH_MISMATCH", False),
        ("DX_PRODUCTION_PROTECTED", False),
        ("DX_DELETION_FORBIDDEN", False),
        ("DX_FEATURE_NOT_ENABLED", False),
    ],
)
def test_retryability_matches_the_requirement(code: str, retryable: bool) -> None:
    assert registries.DX_ERROR_CODES[code] == ("S4", retryable)


def test_artifact_types_are_all_owned_by_s4() -> None:
    for owners in registries.DX_ARTIFACT_TYPES.values():
        assert owners == ["S4"]


def test_discovery_and_snapshot_are_bounded_but_generous() -> None:
    """DX-NFR-03：5,000 对象规模必须装得下，同时不能是“无上限”。"""
    assert registries.DX_ARTIFACT_MAX_SIZES["DISCOVERY_RESULT"] >= 64 * 1024 * 1024
    assert registries.DX_ARTIFACT_MAX_SIZES["SNAPSHOT"] >= 64 * 1024 * 1024


def test_id_prefixes_are_prefixed_and_unique() -> None:
    prefixes = list(registries.DX_ID_PREFIXES)
    assert len(prefixes) == len(set(prefixes))
    for prefix in prefixes:
        assert prefix.isalnum() and prefix.islower(), prefix


def test_execution_idempotency_scope_is_the_non_expiring_one() -> None:
    """SRS 8 章：作用域 ``dx.execution`` 不过期（FD-IDEM-003）。"""
    from factory_foundation.api import DHIS2_EXECUTION_SCOPE

    assert registries.IdemScope.EXECUTION == DHIS2_EXECUTION_SCOPE == "dx.execution"


#: 登记了但**当前里程碑还不该写出**的审计事件（附录 A 与 SRS 12 章的差集）。
#: 每一项都必须是有意为之，且要在这里写清它等哪个里程碑，否则"登记了却从不写出"的漂移
#: 就只存在于运行期——S0 只拒绝未登记的事件名，不会提醒某个登记名从来没人用过。
_NOT_YET_EMITTED: dict[str, str] = {
    "dx.fixture.imported": "M3（DX-TRK-001…003：Tracker Data Executor 与演示用户）",
}


def test_every_registered_audit_event_is_actually_emitted() -> None:
    """附录 A：登记的事件名必须真有写出点（除明确记档的里程碑延后者）。

    校验方式是扫源码里的 ``audit.record(audit.<CONST>)``：常量名与事件字符串在
    ``audit.py`` 里一一对应，所以"登记了但没有任何调用点"这种静默漂移能被测试抓到。
    """
    import ast
    from pathlib import Path

    import factory_dhis2

    source_root = Path(factory_dhis2.__file__).parent
    constants = {
        name: value
        for name, value in vars(__import__("factory_dhis2.audit", fromlist=["x"])).items()
        if name.isupper() and isinstance(value, str) and value.startswith("dx.")
    }
    emitted: set[str] = set()
    for path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in str(path) or path.name == "audit.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record"
                and node.args
            ):
                # 事件名也可能来自条件表达式（``COMPLETED if applied else FAILED``），
                # 所以按可能的参数逐个收集
                candidates: list[ast.expr] = [node.args[0]]
                target = node.args[0]
                while isinstance(target, ast.IfExp):
                    candidates.extend([target.body, target.orelse])
                    target = target.orelse
                for arg in candidates:
                    if isinstance(arg, ast.Attribute) and arg.value.id == "audit":
                        emitted.add(constants.get(arg.attr, f"<unknown:{arg.attr}>"))

    registered = set(registries.DX_AUDIT_EVENT_TYPES)
    never_written = sorted(registered - emitted)
    unexpected = [name for name in never_written if name not in _NOT_YET_EMITTED]
    assert not unexpected, f"这些审计事件登记了却没有任何写出点：{unexpected}"
    # 反过来：记档为"等里程碑"的事件一旦真的开始写出，就要从例外表里删掉
    stale_exemptions = [name for name in _NOT_YET_EMITTED if name in emitted]
    assert not stale_exemptions, f"例外表已过时，请删除：{stale_exemptions}"
