"""配置、依赖注入槽与错误模型的基础覆盖（DX-CLI-001/003、DX-NFR-01、00 文档 6.5）。

这些模块本身很简单，但它们是**每个**外部 I/O 函数都要经过的地方：默认值错了（比如超时
变成 0、退避不增长）会放大成整条链路的故障，而调用方只看到“DHIS2 很慢”。
"""

from __future__ import annotations

import inspect

import pytest

from factory_dhis2 import deps, errors, registries
from factory_dhis2.config import (
    ENV_PREFIX,
    RETRYABLE_STATUS_CODES,
    Dhis2Settings,
    configure,
    get_settings,
    load_settings,
    reset,
)
from factory_dhis2.enums import (
    COMMITTING_MODES,
    DEFAULT_OBJECT_TYPES,
    DRIFT_WATCHED_DISPOSITIONS,
    EXECUTOR_ORDER,
    PRODUCTION_CLASSES,
    CapabilityFreshness,
    DeletionPolicy,
    EnvironmentClass,
    ExecutionMode,
    ExecutionStatus,
    IssueLevel,
    LlmDataPolicy,
    MaintenanceAction,
)


def test_defaults_come_from_the_requirements() -> None:
    """DX-NFR-01 与 DX-EXE-033 的数字都写在 SRS 里，改它们必须是有意为之。"""
    settings = Dhis2Settings()
    assert settings.default_timeout_seconds == 60
    assert settings.async_import_threshold == 500
    assert settings.uid_batch_limit == 100
    assert settings.credential_expiry_warning_days == 14


def test_retry_status_codes_are_the_transport_class_only() -> None:
    """DX-CLI-003：只有超时、429、502、503、504 与网络中断会自动重试。"""
    assert frozenset({429, 502, 503, 504}) == RETRYABLE_STATUS_CODES


def test_environment_overrides_are_read_from_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"{ENV_PREFIX}DEFAULT_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv(f"{ENV_PREFIX}RETRY_MAX_ATTEMPTS", "5")
    monkeypatch.setenv(f"{ENV_PREFIX}LIVE_ALLOW_COMMITS", "true")
    loaded = load_settings()
    assert loaded.default_timeout_seconds == 15
    assert loaded.retry_max_attempts == 5
    assert loaded.live_allow_commits is True
    # 没设的键保持默认
    assert loaded.async_import_threshold == 500


def test_configure_and_reset_round_trip() -> None:
    configure(Dhis2Settings(async_import_threshold=1234))
    assert get_settings().async_import_threshold == 1234
    reset()
    assert get_settings().async_import_threshold == 500


def test_with_overrides_does_not_mutate_the_original() -> None:
    original = Dhis2Settings()
    changed = original.with_overrides(discovery_page_size=50)
    assert changed.discovery_page_size == 50
    assert original.discovery_page_size != 50


def test_llm_policy_ordering_is_strictest_first() -> None:
    """D-07：调用方取“最严格者”，因此顺序必须与总体 SRS 20.2 一致。"""
    assert LlmDataPolicy.LOCAL_ONLY.rank < LlmDataPolicy.PRIVATE_NETWORK.rank
    assert LlmDataPolicy.PRIVATE_NETWORK.rank < LlmDataPolicy.APPROVED_EXTERNAL.rank
    assert LlmDataPolicy.APPROVED_EXTERNAL.rank < LlmDataPolicy.UNRESTRICTED.rank


def test_committing_modes_are_exactly_the_locking_ones() -> None:
    """DX-EXE-030：需要环境锁的模式。"""
    assert frozenset(
        {
            ExecutionMode.COMMIT,
            ExecutionMode.FIXTURE,
            ExecutionMode.DELETE,
            ExecutionMode.MAINTENANCE,
        }
    ) == COMMITTING_MODES
    assert ExecutionMode.VALIDATE not in COMMITTING_MODES


def test_executor_order_is_fixed() -> None:
    """DX-EXE-037 / DX-BR-12。"""
    assert EXECUTOR_ORDER == ("METADATA", "DATASTORE", "TRACKER_DATA", "APP_UPLOAD")


def test_execution_status_has_exactly_the_three_outcomes() -> None:
    """S1 的镜像只有这三个成员，多一个就是它校验不了的字符串。"""
    assert {item.value for item in ExecutionStatus} == {
        "COMPLETED",
        "FAILED",
        "NEEDS_MANUAL_CHECK",
    }


def test_freshness_values_are_case_exact_for_the_caller() -> None:
    """S1 用 ``== CapabilityFreshness.EXPIRED.value`` 直接比较字符串。"""
    assert {item.value for item in CapabilityFreshness} == {
        "VALID",
        "EXPIRED",
        "VERSION_CHANGED",
    }


def test_drift_watched_dispositions_are_reuse_reference_update() -> None:
    """DX-EXE-020 的定义域，CREATE 不在其中（自己写的对象不算被人改）。"""
    assert frozenset({"REUSE", "REFERENCE", "UPDATE"}) == DRIFT_WATCHED_DISPOSITIONS


def test_production_class_set_is_used_by_every_guard() -> None:
    assert frozenset({EnvironmentClass.PRODUCTION.value}) == PRODUCTION_CLASSES
    assert EnvironmentClass.PRODUCTION in PRODUCTION_CLASSES


def test_default_object_types_match_the_greenfield_rule() -> None:
    """DX-DSC-003 的四件套。"""
    assert DEFAULT_OBJECT_TYPES == (
        "categoryCombo",
        "category",
        "categoryOption",
        "categoryOptionCombo",
    )


def test_maintenance_actions_in_mvp_are_one() -> None:
    """DX-EXE-013：MVP 只有清理应用缓存。"""
    assert {item.value for item in MaintenanceAction} == {"CLEAR_APP_CACHE"}


def test_deletion_policy_values_are_documented() -> None:
    assert {item.value for item in DeletionPolicy} == {"FORBID", "SAFE_ONLY"}


def test_dependency_slots_are_single_valued_and_resettable() -> None:
    """transport / clock / sleeper 是唯一槽位：泄漏会污染下一个测试。"""
    sentinel = object()
    deps.set_transport(sentinel)
    assert deps.get_transport() is sentinel
    sleeps: list[float] = []
    deps.set_sleeper(sleeps.append)
    deps.get_sleeper()(0.25)
    assert sleeps == [0.25]
    deps.reset_providers()
    assert deps.get_transport() is None
    assert sleeps == [0.25]


def test_default_sleeper_is_time_sleep() -> None:
    import time

    deps.reset_providers()
    assert deps.get_sleeper() is time.sleep


def test_clock_defaults_to_the_system_clock() -> None:
    from factory_foundation.api import SystemClock

    deps.reset_providers()
    assert isinstance(deps.get_clock(), SystemClock)


def test_tls_adapter_factory_is_optional() -> None:
    deps.set_tls_adapter_factory(lambda policy: "custom")
    assert deps.get_tls_adapter_factory() is not None
    deps.reset_providers()
    assert deps.get_tls_adapter_factory() is None


def test_error_helpers_have_a_useful_blocking_set() -> None:
    blocking = errors.blocking_issue_codes()
    assert errors.EnvironmentLockedError.code in blocking
    assert errors.ConcurrencyError.code in blocking
    assert errors.Dhis2UnreachableError.code not in blocking


def test_issue_from_error_keeps_the_unified_shape() -> None:
    """DX-EXE-027 的统一问题格式：调用方按固定键读，级别键名一律 ``level``（00 文档 6.10）。"""

    problem = errors.DriftDetectedError("objects changed", details={"uid": "aaaa"})
    row = errors.issue_from_error(problem, object_uid="aaaa")
    assert set(row) == {
        "issueId",
        "ruleId",
        "level",
        "objectKey",
        "objectUid",
        "description",
        "sourceStage",
        "artifactId",
        "suggestedAction",
        "details",
    }
    assert row["ruleId"] == "DRIFT_DETECTED"
    assert row["sourceStage"] in {"MAPPING", "PAGE_DESIGN"}
    # 级别键名是 level（00 文档 6.10）：读的一侧按 Issue.level 取值时不能落到默认 WARNING
    assert row["level"] == IssueLevel.BLOCKING.value
    assert "severity" not in row


def test_error_classes_are_factory_errors_with_registered_codes() -> None:
    known = set(registries.DX_ERROR_CODES)
    for _name, obj in inspect.getmembers(errors, inspect.isclass):
        if not (issubclass(obj, errors.Dhis2Error) and obj is not errors.Dhis2Error):
            continue
        assert obj.code != "INTERNAL_ERROR", obj
        assert obj.code in known or obj.code.startswith("DHIS2_") or obj.code in {
            "DRIFT_DETECTED",
            "CONCURRENCY_ERROR",
        }, obj.code
