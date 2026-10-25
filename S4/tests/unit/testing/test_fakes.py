"""FakeDhis2Api 与真实公共 API 的一致性（DX-API-001、00 文档 6.1）。

``FakeDhis2Api`` 是 S1/S2/S3 在**没有 DHIS2、没有 PostgreSQL** 时用的开发替身。它的价值
全部建立在“换成真实实现后调用方一行不改”上：名字、参数、返回类型与拒绝行为必须一致。
一个少给一个函数、把“环境不存在”报成另一种错误、或者回放时给出另一份结论的 Fake，会让
调用方的测试在本地全绿、接上真实 S4 后立刻崩——那正是本文件要拦住的事。

因此断言分四层：

1. **清单与签名**：逐个遍历 ``factory_dhis2.api.__all__``，Fake 必须提供同名可调用对象，
   位置参数名与顺序逐字相同，必填参数一个不少（``inspect.signature``）。
2. **返回形状**：返回同一批 DTO 类型，且 DX-QRY-002 的真值性约定（“非空不等于存在”）一致。
3. **拒绝**：幂等回放、环境锁定、PRODUCTION 保护、未启用里程碑——同一个错误码、同一处。
4. **可断言的副作用**：每一次调用都进 :attr:`FakeDhis2Api.calls`，且不碰网络与数据库。
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from factory_foundation.api import (
    ManualCancellationToken,
    has_active_uow,
)
from pydantic import BaseModel, ValidationError

import factory_dhis2.api as api
from factory_dhis2.dto.connection import CapabilityFreshnessInfo, ConnectionCheck
from factory_dhis2.dto.discovery import DiscoveryOutcome, DiscoveryResultInfo
from factory_dhis2.dto.environment import (
    EnvironmentInfo,
    EnvironmentProtection,
    ExpiringCredential,
)
from factory_dhis2.dto.execution import (
    Dhis2ValidationOutcome,
    DiffReportOutcome,
    DriftResult,
    ExecutionOutcome,
    ExecutionStatusInfo,
    PostVerifyOutcome,
)
from factory_dhis2.dto.issue import Issue
from factory_dhis2.enums import (
    CapabilityFreshness,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    ExecutionStatus,
    IssueLevel,
    ReconcileOutcome,
    SourceStage,
    StepName,
    StepStatus,
)
from factory_dhis2.errors import (
    EnvironmentInvalidError,
    EnvironmentLockedError,
    FeatureNotEnabledError,
)
from factory_dhis2.testing.fakes import DEFAULT_VERSION, FakeDhis2Api, fake_issue

UID_SHAPE = re.compile(r"[A-Za-z][A-Za-z0-9]{10}")

#: Fake **故意**不提供的公共名字与理由。新增一项必须写清理由：调用方是按名字解析的
#: （``getattr(factory_dhis2.api, name)``），缺席就是它跑不通的那一格。
LEGITIMATELY_ABSENT: dict[str, str] = {
    "initialize": "Fake 不开数据库，迁移与目录登记属真实实现",
    "external_io": "S0 的外部 I/O 标记，不是 S4 的能力",
    "is_external_io": "S0 的外部 I/O 判定，不是 S4 的能力",
}

#: Fake 可以合理省略的**可选**关键字参数：它们要么是数据库会话，要么是只有真实实现才
#: 需要的观测/取消钩子。这个集合是封闭的——冒出新的成员就说明 Fake 少接了一个契约参数。
LEGITIMATELY_DROPPED_PARAMS: frozenset[str] = frozenset(
    {
        "session",  # Fake 无库
        "operator",  # 只进审计，Fake 不写审计
        "now",  # 时间窗判定：Fake 用 add_environment(within_window=...) 直接给定
        "schema_types",  # 探测细节：Fake 的 Capability Profile 是固定的
        "cancel",  # check_drift 在 Fake 里是瞬时答案，没有可取消的过程
    }
)


class RecordingProgress:
    """S0 的 ``ProgressReporter`` 是协议：这里只记录被报了什么。"""

    def __init__(self) -> None:
        self.reports: list[dict[str, Any]] = []

    def report(
        self,
        *,
        step: str,
        completed_fraction: float | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.reports.append({"step": step, "fraction": completed_fraction, "message": message})


def _public_functions() -> list[str]:
    return [
        name
        for name in api.__all__
        if callable(getattr(api, name, None)) and not isinstance(getattr(api, name), type)
    ]


@pytest.fixture
def fake() -> FakeDhis2Api:
    """一个确定时钟的 Fake：时间戳来自注入，不来自墙上时钟。"""
    return FakeDhis2Api(clock=datetime(2026, 3, 4, 5, 6, tzinfo=UTC))


# --- 1. 清单与签名 ---------------------------------------------------------


def test_fake_covers_every_public_function_of_the_api() -> None:
    """DX-API-001：调用方按名字解析，Fake 的缺口就是它跑不通的那一格。"""
    missing = [
        name
        for name in _public_functions()
        if name not in LEGITIMATELY_ABSENT and not callable(getattr(FakeDhis2Api, name, None))
    ]
    assert not missing, f"FakeDhis2Api 缺少公共函数：{missing}"


@pytest.mark.parametrize("name", sorted(_public_functions()))
def test_fake_signature_follows_the_public_protocol(name: str) -> None:
    """00 文档 6.1：参数名与位置顺序是冻结契约，Fake 必须逐字一致。"""
    if name in LEGITIMATELY_ABSENT:
        assert not hasattr(FakeDhis2Api, name), f"{name} 登记为缺席却仍然存在：清单要更新"
        return
    theirs = inspect.signature(getattr(api, name)).parameters
    ours = {
        key: value
        for key, value in inspect.signature(getattr(FakeDhis2Api, name)).parameters.items()
        if key != "self"
    }

    def positional(parameters: dict[str, inspect.Parameter]) -> list[str]:
        return [
            item.name
            for item in parameters.values()
            if item.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        ]

    assert positional(ours) == positional(theirs), (
        f"{name}: 位置参数 {positional(ours)} != {positional(theirs)}"
    )
    #: 必填参数一个都不能少——调用方一定会传，少了就是 TypeError
    required = [key for key, value in theirs.items() if value.default is inspect.Parameter.empty]
    assert set(required) <= set(ours), f"{name}: Fake 少了必填参数 {sorted(set(required) - set(ours))}"
    assert (set(theirs) - set(ours)) <= LEGITIMATELY_DROPPED_PARAMS, (
        f"{name}: Fake 不接受 {sorted(set(theirs) - set(ours))}"
    )
    assert not set(ours) - set(theirs), f"{name}: Fake 发明了 api 没有的参数"


def test_environment_read_functions_are_faked_with_the_same_transaction_contract() -> None:
    """00 文档 6.2：Fake 里这些函数同样自管状态，不需要调用方给会话。"""
    for name in ("create_environment", "update_environment", "disable_environment", "set_emergency_lock"):
        assert name in api.__all__ and callable(getattr(FakeDhis2Api, name))


# --- 2. 环境档案 -----------------------------------------------------------


def test_create_environment_accepts_the_same_mapping_as_the_real_api(fake: FakeDhis2Api) -> None:
    """DX-ENV-001：Fake 接同一个 spec 形状，返回同一个 EnvironmentInfo。"""
    info = fake.create_environment(
        {
            "environmentKey": "dev",
            "name": "开发实例",
            "baseUrl": "https://dev.example.org",
            "environmentClass": "DEV",
            "credentials": [
                {"scope": "discovery", "secretRef": "memory://dx/discovery"},
                {"scope": "import", "secretRef": "memory://dx/import"},
            ],
        }
    )
    assert isinstance(info, EnvironmentInfo)
    assert info.environment_key == "dev"
    assert info.version == DEFAULT_VERSION
    assert info.profile_version == "Dhis241Profile"
    assert info.enabled is True
    assert info.environment_class is EnvironmentClass.DEV
    assert [item.scope.value for item in info.credentials] == ["discovery", "import"]
    assert fake.calls[0][0] == "create_environment"


def test_create_environment_rejects_an_invalid_spec_the_same_way(fake: FakeDhis2Api) -> None:
    """DX-ENV-003：Secret 引用必须带 scheme，Fake 不放宽真实实现的输入校验。"""
    with pytest.raises(ValidationError):
        fake.create_environment(
            {
                "environmentKey": "dev",
                "name": "开发实例",
                "baseUrl": "https://dev.example.org",
                "credentials": [{"scope": "discovery", "secretRef": "not-a-ref"}],
            }
        )
    assert fake.environments == {}


def test_create_environment_refuses_a_second_registration(fake: FakeDhis2Api) -> None:
    """DX-ENV-004：同名环境是重复登记而不是覆盖。"""
    fake.add_environment("dev")
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        fake.create_environment(
            {
                "environmentKey": "dev",
                "name": "又一台",
                "baseUrl": "https://other.example.org",
                "credentials": [{"scope": "discovery", "secretRef": "memory://dx/discovery"}],
            }
        )
    assert "already exists" in str(excinfo.value)
    assert fake.get_environment("dev").base_url == "https://dev.example.org"


def test_unknown_environment_is_reported_as_an_invalid_environment(fake: FakeDhis2Api) -> None:
    """DX-ENV-004：S4 没有默认环境，“环境不存在”必须报成环境问题。

    错误码不同就不是同一个契约：S1 按 ``DX_ENVIRONMENT_INVALID`` 决定给运维看什么文案。
    """
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        fake.get_protection("nope")
    assert excinfo.value.code == "DX_ENVIRONMENT_INVALID"
    assert excinfo.value.details["environmentKey"] == "nope"
    with pytest.raises(EnvironmentInvalidError):
        fake.has_associated_data("nope", "dataElement", "uid")
    with pytest.raises(EnvironmentInvalidError):
        fake.test_connection("nope", idempotency_key="t")


def test_update_environment_merges_only_the_patched_fields(fake: FakeDhis2Api) -> None:
    """DX-ENV-001：patch 是增量，未给出的字段保持不变。"""
    fake.add_environment("dev")
    updated = fake.update_environment("dev", {"name": "改名了", "timeoutSeconds": 120})
    assert updated.name == "改名了"
    assert updated.timeout_seconds == 120
    assert updated.base_url == "https://dev.example.org", "未改动的 baseUrl 必须留着"
    assert fake.calls[-1] == ("update_environment", ("dev", {"name": "改名了", "timeoutSeconds": 120}))


def test_disable_environment_turns_the_profile_off(fake: FakeDhis2Api) -> None:
    """DX-ENV-008：禁用是状态变化，不是删除——历史执行还要能读。"""
    fake.add_environment("dev")
    fake.disable_environment("dev")
    info = fake.get_environment("dev")
    assert info is not None
    assert info.enabled is False
    assert info.status is EnvironmentStatus.DISABLED
    assert fake.calls[-1] == ("disable_environment", ("dev",))


def test_list_environments_is_sorted_and_get_reports_unknown(fake: FakeDhis2Api) -> None:
    """DX-ENV-001：调用方按列表展示，顺序必须稳定可预测。"""
    fake.add_environment("uat")
    fake.add_environment("dev")
    assert [item.environment_key for item in fake.list_environments()] == ["dev", "uat"]
    assert fake.get_environment("missing") is None


def test_get_protection_reports_the_window_and_lock_it_was_given(fake: FakeDhis2Api) -> None:
    """DX-ENV-002/007：S1 审批页读的就是这几个字段，一个都不能少。"""
    fake.add_environment("prod", environment_class=EnvironmentClass.PRODUCTION.value)
    protection = fake.get_protection("prod")
    assert isinstance(protection, EnvironmentProtection)
    assert protection.environment_class == EnvironmentClass.PRODUCTION.value
    assert protection.within_commit_window is True
    assert protection.emergency_locked is False
    assert protection.allows_fixture_import is False, "PRODUCTION 不允许导 Fixture"
    # DX-ENV-002：PRODUCTION 的删除策略默认是 FORBID（非生产才是 SAFE_ONLY）
    assert protection.deletion_policy == "FORBID"
    assert protection.timezone == "UTC"
    assert protection.status == EnvironmentStatus.ACTIVE.value


def test_get_protection_expands_the_commit_windows_of_a_protected_env(fake: FakeDhis2Api) -> None:
    """DX-ENV-002：有保护设置时窗口要展开成文档，S1 直接渲染它。"""
    from factory_dhis2.dto.environment import CommitWindow, EnvironmentSpec, ProtectionSpec
    from factory_dhis2.enums import LlmDataPolicy

    fake.create_environment(
        EnvironmentSpec(
            environment_key="prod",
            name="生产",
            base_url="https://prod.example.org",
            environment_class=EnvironmentClass.PRODUCTION,
            llm_data_policy=LlmDataPolicy.LOCAL_ONLY,
            credentials=[
                {"scope": "discovery", "secretRef": "memory://dx/discovery"},
                {"scope": "import", "secretRef": "memory://dx/import"},
            ],
            protection=ProtectionSpec(
                timezone="Asia/Shanghai",
                commit_windows=[CommitWindow(days=[2], start="20:00", end="23:00")],
            ),
        )
    )
    protection = fake.get_protection("prod")
    assert protection.timezone == "Asia/Shanghai"
    assert [item["days"] for item in protection.commit_windows] == [[2]]


def test_data_export_policy_shape_is_stable_for_the_gate_fingerprint(fake: FakeDhis2Api) -> None:
    """00 文档：S1 把这个 dict 直接进 Gate 指纹，键与取值必须可预测。"""
    fake.add_environment("dev")
    policy = fake.get_protection("dev").data_export_policy
    assert sorted(policy) == ["environmentClass", "llmDataPolicy", "rank"]
    assert isinstance(policy["rank"], int)


def test_set_emergency_lock_records_reason_and_operator(fake: FakeDhis2Api) -> None:
    """DX-ENV-007 / DX-AC-05：锁定留下原因与时间，解锁时原因一并消失。"""
    fake.add_environment("dev")
    result = fake.set_emergency_lock("dev", True, "割接演练", "sre-a")
    assert result["locked"] is True
    assert result["reason"] == "割接演练"
    assert result["changedAt"] == "2026-03-04T05:06:00+00:00", "时间来自注入时钟"
    assert fake.get_protection("dev").emergency_locked is True
    assert fake.get_protection("dev").lock_reason == "割接演练"

    fake.set_emergency_lock("dev", False, None, "sre-a")
    assert fake.get_protection("dev").emergency_locked is False
    assert fake.get_protection("dev").lock_reason is None
    assert fake.calls[-1] == ("set_emergency_lock", ("dev", False, None, "sre-a"))


def test_list_expiring_credentials_uses_the_injected_clock(fake: FakeDhis2Api) -> None:
    """DX-ENV-006：只报窗口内到期的，且永远只给引用不给值。"""
    fake.add_environment("dev")
    fake.environments["dev"].spec.credentials[0].expires_at = fake.now + timedelta(days=3)
    fake.environments["dev"].spec.credentials[1].expires_at = fake.now + timedelta(days=200)
    fake.expiring.append(
        ExpiringCredential(
            environment_key="other", secret_ref="memory://dx/other", expires_at=fake.now, scope="import"
        )
    )
    rows = fake.list_expiring_credentials(within_days=14)
    assert [item.environment_key for item in rows] == ["other", "dev"]
    assert rows[1].secret_ref == "memory://dx/discovery"
    assert rows[1].scope == CredentialScope.DISCOVERY.value
    assert fake.list_expiring_credentials(within_days=0)[0].environment_key == "other"


# --- 3. 能力与发现 ---------------------------------------------------------


def test_probe_then_freshness_then_mark_stale(fake: FakeDhis2Api) -> None:
    """DX-CAP-006：探测产出 CAPABILITY_PROFILE 产物，标记过期后新鲜度必须变。"""
    fake.add_environment("dev")
    assert fake.check_capability_freshness("dev").state is CapabilityFreshness.VALID
    artifact_id = fake.probe_capabilities("dev", idempotency_key="p1")
    assert artifact_id
    info = fake.check_capability_freshness("dev")
    assert isinstance(info, CapabilityFreshnessInfo)
    assert info.capability_profile_artifact_id == artifact_id
    assert info.profile_version == "Dhis241Profile"
    fake.mark_stale("dev")
    assert fake.check_capability_freshness("dev").state is CapabilityFreshness.EXPIRED
    assert fake.probe_capabilities("dev", idempotency_key="p2")
    assert fake.check_capability_freshness("dev").state is CapabilityFreshness.VALID
    assert fake.calls[-1] == ("probe_capabilities", ("dev", "p2"))


def test_discover_returns_the_real_dto_and_records_history(fake: FakeDhis2Api) -> None:
    """DX-DSC-009 / S5IF-ENV11：发现以产物 ID 交付，历史列表读的就是它。"""
    fake.add_environment("dev")
    fake.add_object("dev", "dataElement", "de1", code="W", name="体重")
    progress = RecordingProgress()
    outcome = fake.discover(
        "dev",
        "prj-1",
        "RUN_TARGET",
        [{"dhis2Type": "dataElement", "uid": "de1"}],
        idempotency_key="d1",
        progress=progress,
    )
    assert isinstance(outcome, DiscoveryOutcome)
    assert outcome.object_count == 1
    assert outcome.version == DEFAULT_VERSION
    assert outcome.content_hash and re.fullmatch(r"sha256:[0-9a-f]{64}", outcome.content_hash)
    assert outcome.default_object_ids == {"categoryCombo": "defaultCategoryCombo"}
    assert [item["step"] for item in progress.reports] == ["DISCOVERY"]
    assert progress.reports[0]["fraction"] == 1.0

    rows = fake.list_discovery_results("dev")
    assert isinstance(rows[0], DiscoveryResultInfo)
    assert rows[0].artifact_id == outcome.discovery_result_artifact_id
    assert rows[0].object_count == 1
    assert rows[0].scope == "RUN_TARGET"
    assert rows[0].project_id == "prj-1"


def test_latest_discovery_result_is_part_of_the_faked_surface(fake: FakeDhis2Api) -> None:
    """S2/S1 直接读“最近一次发现结果产物 ID”，Fake 也必须答得出（S5IF-ENV11）。"""
    fake.add_environment("dev")
    fake.add_environment("uat")
    assert fake.latest_discovery_result("dev") is None
    first = fake.discover("dev", "prj-1", idempotency_key="d1")
    second = fake.discover("dev", "prj-1", idempotency_key="d2")
    assert fake.latest_discovery_result("dev") == second.discovery_result_artifact_id
    assert second.discovery_result_artifact_id != first.discovery_result_artifact_id
    assert fake.latest_discovery_result("dev", "other-project") is None
    assert fake.latest_discovery_result("uat") is None


def test_list_discovery_results_filters_and_pages(fake: FakeDhis2Api) -> None:
    """S5IF-ENV11：审阅页要按环境与项目过滤并分页。"""
    fake.add_environment("dev")
    fake.add_discovery_result("dev", artifact_id="art-a", scope="RUN_TARGET", object_count=2)
    fake.add_discovery_result("dev", artifact_id="art-b", scope="FULL")
    assert [item.artifact_id for item in fake.list_discovery_results("dev", limit=1)] == ["art-a"]
    assert [item.artifact_id for item in fake.list_discovery_results("dev", limit=1, offset=1)] == ["art-b"]
    assert fake.list_discovery_results("dev", scope="FULL")[0].artifact_id == "art-b"
    assert fake.list_discovery_results("dev", project_id="absent") == []
    assert fake.list_discovery_results("nobody") == []


def test_test_connection_reports_missing_authorities(fake: FakeDhis2Api) -> None:
    """DX-CON-004 / DX-AC-01：权限不足时给出缺失清单，结论是失败。"""
    fake.add_environment("dev", missing_authorities={"import": ["F_METADATA_IMPORT"]})
    check = fake.test_connection("dev", ["discovery", "import"], idempotency_key="t1")
    assert isinstance(check, ConnectionCheck)
    assert check.ok is False
    assert check.result == "FAILED"
    assert check.message
    assert check.missing_authorities == {"import": ["F_METADATA_IMPORT"]}
    assert check.required_scopes == ["discovery", "import"]
    assert check.capability_profile_artifact_id
    failed = [item for item in check.checks if item["state"] == "FAILED"]
    assert [item["name"] for item in failed] == ["scope.import"]
    assert fake.calls[-1] == ("test_connection", ("dev", ["discovery", "import"], "t1"))


def test_test_connection_passes_and_skips_tls_when_not_required(fake: FakeDhis2Api) -> None:
    """DX-CON-001：全通过时也逐项列出检查名，S1 按名字展示。

    检查名与真实实现 ``connection/tester.py`` **逐字一致**（``scope.<范围>``）：按 Fake 开发
    的调用方换成真实实现后必须还能按同一名字取到同一项，否则"本地绿、接真就红"。
    """
    fake.add_environment("dev")
    check = fake.test_connection("dev", idempotency_key="t1")
    assert check.ok is True and check.result == "PASSED"
    assert [item["name"] for item in check.checks] == [
        "address",
        "tls",
        "auth",
        "version",
        "scope.discovery",
        "scope.import",
    ]
    assert check.version == DEFAULT_VERSION
    assert check.dhis2_version == DEFAULT_VERSION
    assert check.environment_key == "dev"


# --- 3b. 只读视图：连接测试、能力索引、发现检索（DX-AC-11 的替身面）---------


def test_fake_last_connection_test_reads_the_stored_result(fake: FakeDhis2Api) -> None:
    """DX-CON-005：Fake 与真实实现一样，读的是「上次测过的那一份」。"""
    fake.add_environment("dev")
    assert fake.get_last_connection_test("dev") is None
    fake.test_connection("dev", ["discovery", "import"], idempotency_key="f1", operator="ann")
    stored = fake.get_last_connection_test("dev")
    assert isinstance(stored, ConnectionCheck)
    assert stored.operator == "ann" and stored.connection_test_id
    # 再测一次覆盖成最新事实，而不是累积成列表
    fake.test_connection("dev", ["discovery"], idempotency_key="f2")
    assert fake.get_last_connection_test("dev").required_scopes == ["discovery"]
    assert len(fake.calls) >= 2, "两次 test_connection 都进了调用记录"


def test_fake_capability_profile_index_gives_staleness(fake: FakeDhis2Api) -> None:
    """DX-CAP-009：没探测过时 ``None``，探测后给出过期与原因，全程不打 DHIS2。"""
    fake.add_environment("dev")
    assert fake.get_capability_profile_index("dev") is None
    fake.probe_capabilities("dev", idempotency_key="p1")
    index = fake.get_capability_profile_index("dev")
    assert index is not None and index.stale is False and index.artifact_id
    fake.mark_stale("dev")
    stale = fake.get_capability_profile_index("dev")
    assert stale is not None and stale.stale is True
    assert stale.stale_reason == "version-changed"


def test_fake_search_discovery_objects_matches_type_and_keyword(
    fake: FakeDhis2Api,
) -> None:
    """DX-DSC-011：Fake 的检索给出与真实实现同一批字段与同一个分页语义。"""
    fake.add_environment("dev")
    fake.add_object("dev", "dataElement", "de1", code="WEIGHT_KG", name="体重")
    fake.add_object("dev", "dataElement", "de2", code="HEIGHT_CM", name="身高")
    fake.add_object("dev", "optionSet", "os1", code="YES_NO", name="是否")
    outcome = fake.discover("dev", None, "RUN_TARGET", idempotency_key="d1")
    artifact_id = str(outcome.discovery_result_artifact_id)

    by_type = fake.search_discovery_objects(artifact_id, "dataElement")
    assert [item.uid for item in by_type.items] == ["de1", "de2"] and by_type.total == 2
    assert by_type.items[0].code == "WEIGHT_KG" and by_type.items[0].name == "体重"

    by_keyword = fake.search_discovery_objects(artifact_id, None, "yes", limit=1)
    assert by_keyword.total == 1 and [item.uid for item in by_keyword.items] == ["os1"]
    assert (by_keyword.limit, by_keyword.offset) == (1, 0)

    paged = fake.search_discovery_objects(artifact_id, "dataElement", limit=1, offset=1)
    assert [item.uid for item in paged.items] == ["de2"] and paged.total == 2


def test_search_of_an_unknown_discovery_artifact_returns_nothing(fake: FakeDhis2Api) -> None:
    """检索一个 Fake 没见过的产物 ID 时给空结果，而不是替调用方猜一个环境。"""
    found = fake.search_discovery_objects("art-never-discovered", "dataElement")
    assert found.items == [] and found.total == 0


# --- 4. 只读查询（S2 唯一能用的四件事）------------------------------------


def test_generate_uids_produces_valid_distinct_uids(fake: FakeDhis2Api) -> None:
    """DX-QRY-001 / DX-AC-08：11 位、首位字母、互不相同，且正好 N 个。"""
    uids = fake.generate_uids("dev", 25)
    assert len(uids) == 25 and len(set(uids)) == 25
    assert all(UID_SHAPE.fullmatch(item) for item in uids)
    assert fake.generate_uids("dev", 0) == []
    assert fake.calls[-1] == ("generate_uids", ("dev", 0))


def test_generate_uids_still_works_with_a_ten_char_prefix(fake: FakeDhis2Api) -> None:
    """DX-AC-08：前缀再长也必须留出数字位，否则宁可明确报错也不空转。"""
    fake.uid_prefix = "factorydx1"
    uids = fake.generate_uids("dev", 2)
    assert all(UID_SHAPE.fullmatch(item) and len(item) == 11 for item in uids)
    fake.uid_prefix = "way_too_long_prefix"
    with pytest.raises(ValueError, match="11-character"):
        fake.generate_uids("dev", 1)


def test_uid_exists_truthiness_is_the_answer_not_dict_emptiness(fake: FakeDhis2Api) -> None:
    """DX-QRY-002：S2 的用法是 ``dict(...)`` 后按真值判断，非空不等于存在。"""
    fake.add_environment("dev")
    fake.add_object("dev", "dataElement", "takenUid001", code="W", name="体重")
    result = fake.uid_exists("dev", ["takenUid001", "freeUid0000"])
    taken = dict(result)["takenUid001"]
    free = dict(result)["freeUid0000"]
    assert bool(taken) is True and taken["exists"] is True
    assert bool(free) is False, "非空 dict 恒为真就等于把占用信息丢掉"
    assert taken["dhis2Type"] == "dataElement" and taken["code"] == "W"
    assert taken["basis"] == "fake:in-memory-objects"
    assert free["dhis2Type"] is None and free["basis"]
    assert fake.calls[-1] == ("uid_exists", ("dev", ("takenUid001", "freeUid0000")))


def test_code_exists_reports_the_owning_uid(fake: FakeDhis2Api) -> None:
    """DX-QRY-003：占用要给出 UID，S2 才能判断是不是同一个对象。"""
    fake.add_environment("dev")
    fake.add_object("dev", "dataElement", "de1", code="WEIGHT", name="体重")
    result = fake.code_exists("dev", "dataElement", ["WEIGHT", "HEIGHT"])
    assert dict(result)["WEIGHT"] and result["WEIGHT"]["uid"] == "de1"
    assert not dict(result)["HEIGHT"]
    assert result["HEIGHT"]["uid"] is None
    assert result["HEIGHT"]["basis"] == "fake:in-memory-codes"
    assert fake.calls[-1] == ("code_exists", ("dev", "dataElement", ("WEIGHT", "HEIGHT")))


def test_has_associated_data_answers_from_the_preset(fake: FakeDhis2Api) -> None:
    """DX-QRY-004：真值性就是“还有关联数据”。"""
    fake.add_environment("dev")
    fake.add_object("dev", "dataElement", "busy", code="A", name="有数据", hasAssociatedData=True)
    fake.add_object("dev", "dataElement", "idle", code="B", name="空")
    busy = fake.has_associated_data("dev", "dataElement", "busy")
    idle = fake.has_associated_data("dev", "dataElement", "idle")
    assert busy.has_data is True and bool(busy) is True
    assert bool(idle) is False and idle.has_data is False
    assert idle["hasData"] is False and idle["evidence"] == {}
    assert idle["basis"]
    assert fake.has_associated_data("dev", "dataElement", "neverHeard")["hasData"] is False


# --- 5. 执行：幂等、锁定、里程碑 -------------------------------------------


def test_commit_replays_the_same_idempotency_key_with_the_same_conclusion(fake: FakeDhis2Api) -> None:
    """DX-EXE-036 / AC-EXE-04：同幂等键只回放，且回放的是**同一个结论**。"""
    fake.add_environment("dev")
    first = fake.commit(
        "dev", "prj-1", "art-pkg", "sha256:" + "a" * 64, operator="s1", idempotency_key="k1"
    )
    second = fake.commit(
        "dev", "prj-1", "art-pkg", "sha256:" + "a" * 64, operator="s1", idempotency_key="k1"
    )
    assert isinstance(first, ExecutionOutcome)
    assert first.execution_id == second.execution_id
    assert first == second, "回放给出另一份结论，重试中的调用方就会丢产物"
    assert len(fake.executions) == 1, "回放不得新增执行"
    assert fake.get_execution_by_key("k1").execution_id == first.execution_id
    assert fake.get_execution_by_key("other") is None
    assert fake.get_execution(first.execution_id or "") == first
    assert fake.get_execution("exe-none") is None


def test_commit_against_an_emergency_locked_environment_is_refused(fake: FakeDhis2Api) -> None:
    """DX-AC-05 / DX-BR-11：紧急锁定期间不接受提交，且拒绝发生在产生执行之前。"""
    fake.add_environment("prod", environment_class=EnvironmentClass.PRODUCTION.value, emergency_lock=True)
    with pytest.raises(EnvironmentLockedError) as excinfo:
        fake.commit("prod", "prj-1", "art-pkg", "sha256:" + "b" * 64, operator="s1", idempotency_key="k9")
    assert excinfo.value.details["environmentKey"] == "prod"
    assert fake.executions == {}
    assert fake.by_key == {}


def test_queued_commit_result_is_remembered_for_the_replay(fake: FakeDhis2Api) -> None:
    """DX-EXE-036：测试预置的失败结论同样要能被 ``get_execution_by_key`` 查到。"""
    fake.add_environment("dev")
    fake.queue_commit(ExecutionOutcome(execution_id="exe-mine", status=ExecutionStatus.FAILED, applied=False))
    committed = fake.commit("dev", "prj-1", "art-pkg", "sha256:" + "e" * 64, operator="s1", idempotency_key="k10")
    assert committed.execution_id == "exe-mine"
    assert fake.get_execution_by_key("k10") == committed


def test_import_fixtures_is_refused_on_production_but_rehearsable_on_dev(fake: FakeDhis2Api) -> None:
    """DX-AC-09：M3 的演练路径也不许把 Fixture 导向 PRODUCTION 类环境。"""
    fake.add_environment("dev")
    fake.add_environment("prod", environment_class=EnvironmentClass.PRODUCTION.value)
    outcome = fake.import_fixtures("dev", "prj-1", "art-fixture", operator="s1", idempotency_key="f1")
    assert outcome.status is ExecutionStatus.COMPLETED
    assert outcome.demo_user_secret_refs[0]["secretRef"].startswith("memory://")
    with pytest.raises(FeatureNotEnabledError) as excinfo:
        fake.import_fixtures("prod", "prj-1", "art-fixture", operator="s1", idempotency_key="f2")
    assert excinfo.value.code == "DX_FEATURE_NOT_ENABLED"
    assert "PRODUCTION" in str(excinfo.value)


def test_the_real_import_fixtures_entry_point_is_still_disabled(fake: FakeDhis2Api) -> None:
    """SRS 12 章：Fake 允许演练不等于能力已交付，公共入口照旧拒绝。"""
    with pytest.raises(FeatureNotEnabledError) as excinfo:
        api.import_fixtures("dev", "prj-1", "art", operator="s1", idempotency_key="k")
    assert excinfo.value.code == "DX_FEATURE_NOT_ENABLED"
    assert "M3" in str(excinfo.value)


def test_run_maintenance_only_accepts_the_clear_app_cache_action(fake: FakeDhis2Api) -> None:
    """DX-EXE-013：MVP 只支持清理应用缓存，其它动作明确拒绝并记录调用。"""
    fake.add_environment("dev")
    outcome = fake.run_maintenance(
        "dev", "exe-1", [{"action": "CLEAR_APP_CACHE"}], operator="s1", idempotency_key="m1"
    )
    assert outcome.status is ExecutionStatus.COMPLETED and outcome.applied is True
    with pytest.raises(FeatureNotEnabledError) as excinfo:
        fake.run_maintenance(
            "dev", "exe-1", [{"action": "DISABLE_SEARCH_ENGINE"}], operator="s1", idempotency_key="m2"
        )
    assert excinfo.value.details["unsupported"] == ["DISABLE_SEARCH_ENGINE"]
    assert fake.maintenance_calls == [
        ("dev", [{"action": "CLEAR_APP_CACHE"}]),
        ("dev", [{"action": "DISABLE_SEARCH_ENGINE"}]),
    ]


def test_prepare_and_validate_walks_the_four_steps_and_reports_progress(fake: FakeDhis2Api) -> None:
    """SRS 6.3：R3 的产物与步骤清单在 Fake 里同样齐备，调用方才测得到自己的编排。"""
    fake.add_environment("dev")
    progress = RecordingProgress()
    outcome = fake.prepare_and_validate(
        "dev",
        "prj-1",
        "art-pkg",
        "sha256:" + "c" * 64,
        import_options={"atomicMode": "NONE"},
        maintenance_actions=[{"action": "CLEAR_APP_CACHE"}],
        operator="s1",
        idempotency_key="v1",
        progress=progress,
    )
    assert isinstance(outcome, Dhis2ValidationOutcome)
    assert outcome.passed is True
    assert outcome.execution_plan_artifact_id and outcome.snapshot_artifact_id
    assert outcome.import_report_artifact_id and outcome.payload_hash == "sha256:" + "c" * 64
    assert outcome.import_options == {"atomicMode": "NONE"}
    assert outcome.maintenance_actions == [{"action": "CLEAR_APP_CACHE"}]
    assert [item.step for item in outcome.steps] == [
        StepName.PLAN.value,
        StepName.DRIFT_CHECK.value,
        StepName.SNAPSHOT.value,
        StepName.VALIDATE.value,
    ]
    assert all(item.status == StepStatus.COMPLETED.value for item in outcome.steps)

    status = fake.get_execution_status(outcome.execution_id or "")
    assert isinstance(status, ExecutionStatusInfo)
    assert status.mode == "VALIDATE", "模式写死成 COMMIT 会让调用方以为已经提交过"
    assert status.plan_artifact_id == outcome.execution_plan_artifact_id
    assert status.snapshot_artifact_id == outcome.snapshot_artifact_id
    assert status.idempotency_key == "v1"
    assert [item.step for item in status.steps] == [item.step for item in outcome.steps]


def test_queued_validate_result_is_returned_verbatim(fake: FakeDhis2Api) -> None:
    """DX-API-001：Fake 的价值在于能造出真实实例未必给的答案（失败、被挡住）。"""
    fake.add_environment("dev")
    fake.queue_validate(Dhis2ValidationOutcome(passed=False, issues=[fake_issue("引用缺失", uid="de1")]))
    outcome = fake.prepare_and_validate("dev", "prj-1", "art-pkg", None, operator="s1", idempotency_key="v2")
    assert outcome.passed is False
    assert outcome.issues[0].level is IssueLevel.BLOCKING
    assert outcome.issues[0].is_blocking is True
    assert outcome.execution_id, "未给执行 ID 时 Fake 补一个可追踪的 ID"
    assert fake.get_execution_status(outcome.execution_id).status is ExecutionStatus.COMPLETED


def test_check_drift_answers_the_preset_per_environment(fake: FakeDhis2Api) -> None:
    """DX-EXE-021：漂移是环境级事实，Fake 按环境给定。"""
    fake.add_environment("dev")
    fake.add_environment("uat")
    assert fake.check_drift("dev", "art-pkg").drifted is False
    fake.set_drift("uat", DriftResult(drifted=True, objects=[{"uid": "de1"}], environment_key="uat"))
    result = fake.check_drift("uat", "art-pkg", project_id="prj-1")
    assert isinstance(result, DriftResult) and result.drifted is True
    assert result.objects == [{"uid": "de1"}]
    assert fake.calls[-1] == ("check_drift", ("uat", "art-pkg"))


def test_reconcile_and_manual_check_keep_the_execution_consistent(fake: FakeDhis2Api) -> None:
    """DX-EXE-035/031：对账按“是否已生效”回答，人工核实结论回写到同一条执行。"""
    fake.add_environment("dev")
    committed = fake.commit(
        "dev", "prj-1", "art-pkg", "sha256:" + "f" * 64, operator="s1", idempotency_key="k11"
    )
    execution_id = committed.execution_id or ""
    assert fake.reconcile(execution_id).outcome is ReconcileOutcome.APPLIED

    fake.executions[execution_id].applied = False
    assert fake.reconcile(execution_id).outcome is ReconcileOutcome.NOT_APPLIED
    fake.executions[execution_id].applied = None
    assert fake.reconcile(execution_id).outcome is ReconcileOutcome.UNKNOWN

    unknown = fake.reconcile("exe-unknown")
    assert unknown.outcome is ReconcileOutcome.UNKNOWN
    assert unknown.evidence == {"executionId": "exe-unknown"}

    fake.executions[execution_id].status = ExecutionStatus.NEEDS_MANUAL_CHECK
    resolved = fake.resolve_manual_check(execution_id, "PASSED", "已人工确认", "sre")
    assert resolved == {
        "executionId": execution_id,
        "resolved": True,
        "releasesLock": True,
        "note": "已人工确认",
    }
    after = fake.get_execution(execution_id)
    assert after is not None and after.status is ExecutionStatus.COMPLETED and after.applied is True
    fake.resolve_manual_check(execution_id, "FAILED", "确认未生效", "sre")
    assert fake.get_execution(execution_id).status is ExecutionStatus.FAILED


def test_post_verify_delete_and_diff_shapes(fake: FakeDhis2Api) -> None:
    """SRS 6.5 / DX-EXE-038 / DX-DIFF-001：这三件事 Fake 也给得出可断言的形状。"""
    fake.add_environment("dev")
    fake.add_object("dev", "dataElement", "de1", code="A", name="甲")
    verified = fake.post_verify(
        "dev", "prj-1", "art-pkg", "exe-1", operator="s1", idempotency_key="pv1"
    )
    assert isinstance(verified, PostVerifyOutcome)
    assert verified.consistent is True and verified.compared_objects == 1
    assert verified.report_artifact_id == "art-fake-verify-exe-1"

    deleted = fake.delete_objects(
        "dev", "prj-1", "art-del", "sha256:" + "1" * 64, operator="s1", idempotency_key="dl1"
    )
    assert deleted.status is ExecutionStatus.COMPLETED and deleted.applied is True
    assert deleted.payload_hash == "sha256:" + "1" * 64

    snapshot_diff = fake.diff_snapshot_vs_current("exe-1")
    assert isinstance(snapshot_diff, DiffReportOutcome)
    assert snapshot_diff.diff_report_artifact_id == "art-fake-diff-exe-1"
    assert snapshot_diff.summary == {"created": 0, "updated": 0, "unchanged": 0, "referenceOnly": 0}
    env_diff = fake.diff_environments("prj-1", "dev", "dev", blueprint_version="0.1.0")
    assert env_diff.summary["blueprintVersion"] == "0.1.0"
    assert env_diff.diff_report_artifact_id == "art-fake-diff-dev-dev"


def test_delete_project_data_keeps_the_execution_trail(fake: FakeDhis2Api) -> None:
    """DX-DATA-02：只删发现历史；执行记录必须留下支撑追溯链。

    真实实现连删执行记录都不尝试（数据库触发器挡住），Fake 如果顺手删了，S1 的“项目删除
    之后仍可追溯”这条测试就成了空话。
    """
    fake.add_environment("dev")
    committed = fake.commit(
        "dev", "prj-1", "art-pkg", "sha256:" + "2" * 64, operator="s1", idempotency_key="k13"
    )
    fake.discover("dev", "prj-1", idempotency_key="d1")
    fake.discover("dev", "prj-2", idempotency_key="d2")
    fake.delete_project_data("prj-1")
    assert fake.calls[-1] == ("delete_project_data", ("prj-1",))
    assert (committed.execution_id or "") in fake.executions, "执行记录被删了"
    assert [item["project_id"] for item in fake.discovery_history] == ["prj-2"]


def test_calls_are_recorded_for_assertions_about_side_effects(fake: FakeDhis2Api) -> None:
    """DX-API-001：测试要能证明“只调了该调的那一次”，尤其是写操作。"""
    fake.add_environment("dev")
    fake.generate_uids("dev", 2)
    fake.uid_exists("dev", ["a"])
    fake.code_exists("dev", "dataElement", ["A"])
    fake.has_associated_data("dev", "dataElement", "a")
    assert [name for name, _ in fake.calls] == [
        "generate_uids",
        "uid_exists",
        "code_exists",
        "has_associated_data",
    ]
    assert all(isinstance(args, tuple) for _, args in fake.calls)


def test_fake_touches_no_database_and_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """DX-API-001：一个会偷偷联网的 Fake 会让 S1 的测试依赖 S4 是否健康。"""

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Fake 发起了真实网络请求")

    monkeypatch.setattr(httpx.Client, "request", explode)
    subject = FakeDhis2Api()
    subject.add_environment("dev")
    subject.probe_capabilities("dev", idempotency_key="p")
    subject.discover("dev", "prj-1", idempotency_key="d")
    subject.commit("dev", "prj-1", "art", "sha256:" + "4" * 64, operator="s1", idempotency_key="k")
    assert has_active_uow() is False


def test_cancellation_token_is_accepted_by_the_long_running_entry_points(fake: FakeDhis2Api) -> None:
    """00 文档 6.3：Fake 同样接 ProgressReporter / CancellationToken。"""
    fake.add_environment("dev")
    token = ManualCancellationToken()
    outcome = fake.delete_objects(
        "dev", "prj-1", "art-del", "sha256:" + "5" * 64, operator="s1", idempotency_key="k15", cancel=token
    )
    assert isinstance(outcome, ExecutionOutcome)
    assert fake.post_verify(
        "dev", "prj-1", "art", None, operator="s1", idempotency_key="pv2", cancel=token
    ).execution_id is None
    token.cancel("用户取消")
    assert fake.commit(
        "dev", "prj-1", "art", "sha256:" + "6" * 64, operator="s1", idempotency_key="k16", cancel=token
    ).status is ExecutionStatus.COMPLETED


def test_every_return_value_is_a_public_dto_type(fake: FakeDhis2Api) -> None:
    """00 文档 6.1：Fake 与真实实现返回同一批 DTO，调用方的镜像才不会变形状。"""
    fake.add_environment("dev")
    samples: list[Any] = [
        fake.get_environment("dev"),
        fake.list_environments()[0],
        fake.get_protection("dev"),
        fake.test_connection("dev", idempotency_key="t"),
        fake.check_capability_freshness("dev"),
        fake.discover("dev", "prj-1", idempotency_key="d"),
        fake.list_discovery_results("dev")[0],
        fake.prepare_and_validate("dev", "prj-1", "art", None, operator="s1", idempotency_key="v"),
        fake.check_drift("dev", "art"),
        fake.commit("dev", "prj-1", "art", "sha256:" + "6" * 64, operator="s1", idempotency_key="k17"),
        fake.reconcile("exe-1"),
        fake.post_verify("dev", "prj-1", "art", "exe-1", operator="s1", idempotency_key="pv3"),
        fake.diff_snapshot_vs_current("exe-1"),
        fake.diff_environments("prj-1", "dev", "dev"),
        fake_issue("断链", uid="de1", key="a.b"),
    ]
    for sample in samples:
        assert isinstance(sample, BaseModel), type(sample)
    assert isinstance(samples[-1], Issue)
    assert samples[-1].object_uid == "de1"


# --- 6. 预置辅助与问题形状 -------------------------------------------------


def test_default_clock_is_deterministic_without_injection() -> None:
    """DX-NFR：没注入时钟时 Fake 也从一个固定时刻出发，测试不随墙上时钟漂移。"""
    fresh = FakeDhis2Api()
    assert fresh.now == datetime(2026, 1, 1, tzinfo=UTC)
    assert fresh.uid_prefix == "fake"
    assert DEFAULT_VERSION.startswith("2.41")


def test_scripting_helpers_seed_objects_and_locks(fake: FakeDhis2Api) -> None:
    """DX-API-001：预置辅助就是 Fake 的“环境状态”，不给它就得改真实实现。"""
    info = fake.add_environment(
        "dev", environment_class=EnvironmentClass.UAT.value, version="2.41.3", within_window=False
    )
    assert info.version == "2.41.3"
    assert fake.get_protection("dev").within_commit_window is False
    fake.add_object("dev", "program", "p1", code="P", name="项目")
    assert fake.environments["dev"].objects["p1"]["dhis2Type"] == "program"
    assert fake.uid_exists("dev", ["p1"])["p1"]["dhis2Type"] == "program"


def test_fake_issue_carries_the_object_binding() -> None:
    """DX-EXE-027：问题要能映射回 businessKey，Fake 造的问题同样带绑定。"""
    issue = fake_issue("缺少引用", uid="de1", key="demo.element")
    assert issue.rule_id == "DXIS-REFERENCE_ERROR"
    assert issue.level is IssueLevel.BLOCKING
    assert issue.source_stage is SourceStage.MAPPING
    assert issue.object_uid == "de1" and issue.object_key == "demo.element"
    assert issue.issue_id == fake_issue("缺少引用", uid="de1", key="demo.element").issue_id, (
        "同一问题在同一载荷上永远是同一个 ID"
    )
