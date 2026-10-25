"""执行计划（DX-EXE-010…013、DX-EXE-012、DX-EXE-033/037）。

计划回答的四个问题都只能依据**已持久化的事实**：执行什么（执行器与固定顺序、载荷与哈希、
预期变更、维护动作、表达式安排）、用什么参数导入（默认值来自 Capability Profile，调用方
改动的每一项都标记为要在 DEPLOY_GATE 显式确认）、目标环境配不配（每个操作都要被 Profile
支持，对应凭证范围的账户要具备所需权限，不满足是 BLOCKING）、什么会变化（Snapshot 规模与
漂移关注清单）。S4 在这里不判断 Gate，只做事实核对。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from factory_foundation.api import new_id, unit_of_work

from factory_dhis2.artifacts import read_json, read_metadata
from factory_dhis2.config import configure, get_settings
from factory_dhis2.enums import (
    CredentialScope,
    EnvironmentClass,
    ExecutorType,
    IssueLevel,
)
from factory_dhis2.errors import EnvironmentInvalidError
from factory_dhis2.execution import plan as plan_module
from factory_dhis2.execution.plan import (
    EXECUTOR_REQUIREMENTS,
    SUPPORTED_MAINTENANCE,
    async_import_decision,
    build_and_save,
    build_plan,
    import_options_are_valid,
    normalize_maintenance,
    order_executors,
    resolve_import_options,
    save_plan,
)
from factory_dhis2.models.environment import ConnectionTestRow
from factory_dhis2.testing import record_connection_test
from tests.unit.execution import (
    ENVIRONMENT,
    PROJECT,
    build_manifest,
    make_run_context,
    make_target,
    package_from_artifact,
    package_of,
    register_package_artifact,
    seed_environment,
)

UID = "de000000001"
COMBO_UID = "qAZsZ5kBwLk"


def _package(expressions: list[dict[str, Any]] | None = None) -> Any:
    return package_of(
        build_manifest(
            objects=[{"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "CREATE"}],
            uid_map=[{"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "CREATE"}],
            dispositions={"bk.weight": "CREATE"},
            controlled_fields={},
            expressions=expressions
            or [
                {"businessKey": "bk.weight", "field": "formName", "validation": "DHIS2_VALIDATE", "compiled": "#{x}"},
                {"businessKey": "bk.weight", "field": "expression", "validation": "POST_VERIFY", "compiled": "#{y}"},
            ],
            baselines=[{"businessKey": "bk.weight", "uid": UID, "observedHash": "sha256:one"}],
        ),
        {"dataElements": [{"id": UID, "name": "Weight"}]},
    )


def _context(**changes: Any) -> Any:
    target = changes.pop("target", None) or make_target()
    return make_run_context(changes.pop("package", None) or _package(), target=target)


def _profile_without(profile: Any, *logical_names: str) -> Any:
    return replace(
        profile,
        resources=tuple(
            entry for entry in profile.resources if entry.logical_name not in set(logical_names)
        ),
    )


# --- 导入参数（DX-EXE-011）---------------------------------------------------


def test_import_options_default_to_the_capability_profile() -> None:
    """DX-EXE-011：默认参数就是 Profile 的 ``import_params.json``，这里不另写一套。"""
    profile = make_target().profile

    resolved, confirmation = resolve_import_options(profile, None)

    assert resolved == profile.import_defaults("metadata")
    assert resolved["identifier"] == "ASSIGNED"
    assert resolved["importStrategy"] == "CREATE_AND_UPDATE"
    assert resolved["atomicMode"] == "ALL"
    assert confirmation == []


def test_a_non_default_option_is_surfaced_as_requiring_confirmation() -> None:
    """DX-EXE-011：改动过的非默认项要在计划里显式列出，等 DEPLOY_GATE 确认。"""
    profile = make_target().profile

    resolved, confirmation = resolve_import_options(profile, {"identifier": "EXISTING"})

    assert resolved["identifier"] == "EXISTING"
    assert len(confirmation) == 1
    assert confirmation[0]["parameter"] == "identifier"
    assert confirmation[0]["defaultValue"] == "ASSIGNED"
    assert confirmation[0]["requestedValue"] == "EXISTING"
    assert "DEPLOY_GATE" in confirmation[0]["reason"]


def test_a_value_that_matches_the_default_does_not_ask_for_confirmation() -> None:
    """DX-EXE-011：只有**偏离默认**才是需要确认的事，重复默认值不是。"""
    profile = make_target().profile

    resolved, confirmation = resolve_import_options(profile, {"atomicMode": "ALL"})

    assert resolved["atomicMode"] == "ALL"
    assert confirmation == []


def test_import_mode_is_not_something_the_caller_may_choose() -> None:
    """DX-BR-05：importMode 由步骤决定——调用方递进来的这个键被忽略，而不是带进参数集。"""
    profile = make_target().profile

    resolved, confirmation = resolve_import_options(profile, {"importMode": "VALIDATE"})

    assert resolved["importMode"] == "COMMIT"
    assert confirmation == []


def test_a_parameter_this_version_does_not_have_is_refused() -> None:
    """DX-EXE-011/CLI-005：这个版本没有的参数不能靠猜带过去，直接拒绝并列出支持的集合。"""
    profile = make_target().profile

    with pytest.raises(EnvironmentInvalidError) as excinfo:
        resolve_import_options(profile, {"skipNotifications": True})

    assert excinfo.value.details["parameter"] == "skipNotifications"
    assert "identifier" in excinfo.value.details["supported"]


def test_a_value_outside_the_allowed_vocabulary_is_refused() -> None:
    """DX-EXE-011：参数在目录里但取值不在允许集合里，同样拒绝——不说「大概可以」。"""
    profile = make_target().profile

    with pytest.raises(EnvironmentInvalidError) as excinfo:
        resolve_import_options(profile, {"atomicMode": "SOMETIMES"})

    assert excinfo.value.details["allowed"] == ["ALL", "NONE"]
    assert "does not accept" in str(excinfo.value)


def test_a_boolean_option_is_accepted_in_its_own_vocabulary() -> None:
    """DX-EXE-033：``async`` 的真假值不是字符串词汇表能表达的，按布尔判定放行。"""
    profile = make_target().profile

    resolved, _confirmation = resolve_import_options(profile, {"async": True})

    assert resolved["async"] is True


def test_import_options_are_revalidated_against_the_profile() -> None:
    """DX-EXE-011：执行前还要能二次确认这份参数集仍被该版本支持（防止计划与执行之间漂移）。"""
    profile = make_target().profile

    assert import_options_are_valid(profile, dict(profile.import_defaults("metadata"))) is True
    assert import_options_are_valid(profile, {"importMode": "COMMIT", "atomicMode": "ALL"}) is True
    assert import_options_are_valid(profile, {"importStrategy": "DELETE", "unknown": 1}) is False


# --- 执行器顺序与准入（DX-EXE-037/012）--------------------------------------


def test_executors_run_in_the_order_the_requirements_fix() -> None:
    """DX-EXE-037 / DX-BR-12：固定顺序 Metadata → DataStore → Tracker → App Upload。"""
    planned = [
        ExecutorType.APP_UPLOAD.value,
        ExecutorType.TRACKER_DATA.value,
        ExecutorType.METADATA.value,
        ExecutorType.DATASTORE.value,
    ]

    assert order_executors(planned) == [
        "METADATA",
        "DATASTORE",
        "TRACKER_DATA",
        "APP_UPLOAD",
    ]
    assert order_executors(["METADATA"]) == ["METADATA"]
    assert order_executors([]) == []


def test_the_plan_document_records_what_will_be_done_and_with_what_facts(
    harness: Any,
) -> None:
    """DX-EXE-010：计划是产物，内容全是已持久化的事实：载荷、哈希、表达式安排、漂移清单。"""
    context = _context()
    # DX-EXE-012 的输入：权限依据是那行已持久化的连接测试结果
    seed_environment(harness)

    plan = build_plan(context, expected_hash=context.package.artifact_hash)
    document = plan.document

    assert document["kind"] == "EXECUTION_PLAN"
    assert document["environmentKey"] == ENVIRONMENT
    assert document["projectKey"] == PROJECT
    assert document["payloadHash"] == context.package.artifact_hash
    assert document["profileVersion"] == context.target.profile.profile_version
    assert document["executors"] == ["METADATA"]
    assert document["deletion"] is False
    assert document["maintenanceActions"] == []
    assert document["expressionValidation"]["atValidate"] == [{"businessKey": "bk.weight", "field": "formName"}]
    assert document["expressionValidation"]["atPostVerify"] == [{"businessKey": "bk.weight", "field": "expression"}]
    assert [item["uid"] for item in document["driftWatch"]] == []
    assert document["objectCount"] == 1
    assert plan.supported is True and plan.blockers == []
    assert document["asyncImport"]["async"] is False


def test_an_operation_the_profile_does_not_have_is_a_blocking_issue(harness: Any) -> None:
    """DX-EXE-012：目录里没有这个逻辑资源，就是阻塞问题而不是「试试看」。"""
    target = replace(make_target(), profile=_profile_without(make_target().profile, "system.tasks"))

    seed_environment(harness)
    plan = build_plan(_context(target=target), expected_hash=_package().artifact_hash)

    blocking = [issue for issue in plan.issues if issue.is_blocking]
    assert [issue.rule_id for issue in blocking] == ["DX-EXE-012"]
    assert "system.tasks" in blocking[0].description
    assert plan.supported is False


def test_an_executor_without_its_credential_scope_is_a_blocking_issue(harness: Any) -> None:
    """DX-EXE-012：对应凭证范围没配，执行器就跑不了——这里不猜某个账户也许能用。"""
    target = make_target(scopes=(CredentialScope.DISCOVERY,))
    # 权限证据齐备（覆盖六个范围），剩下的阻塞问题才只可能来自"环境没配那个范围的凭证"
    seed_environment(harness, connection_tested=False)
    record_connection_test(
        ENVIRONMENT,
        scopes=tuple(CredentialScope),
        tested_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    plan = build_plan(
        _context(target=target),
        executor_types=["METADATA", "APP_UPLOAD"],
        expected_hash=_package().artifact_hash,
    )

    messages = {issue.object_key for issue in plan.blockers}
    assert messages == {"METADATA", "APP_UPLOAD"}
    assert any("import" in issue.description for issue in plan.blockers)
    assert any("apps" in issue.description for issue in plan.blockers)


def test_missing_authorities_reported_by_the_connection_test_block_the_plan(
    harness: Any,
) -> None:
    """DX-EXE-012：权限的权威来源是已持久化的连接测试结果，计划生成不再打 DHIS2。"""
    seed_environment(harness, connection_tested=False)
    with unit_of_work() as session:
        session.add(
            ConnectionTestRow(
                id=new_id("dxcnt"),
                environment_key=ENVIRONMENT,
                tested_at=datetime(2026, 1, 2, tzinfo=UTC),
                checks=[],
                missing_authorities={"import": ["F_EXPORT_DATA"], "discovery": []},
                result="FAILED",
                # 真实那一行总会记下测过哪些范围：权限是按范围判定的
                required_scopes=["discovery", "import"],
            )
        )

    plan = build_plan(_context(), expected_hash=_package().artifact_hash)

    blocking = [issue for issue in plan.blockers if issue.rule_id == "DX-EXE-012"]
    assert [issue.object_key for issue in blocking] == ["import"]
    assert "F_EXPORT_DATA" in blocking[0].description
    assert blocking[0].suggested_action


def test_a_never_connection_tested_environment_cannot_be_planned(harness: Any) -> None:
    """DX-EXE-012：从没做过连接测试的环境，权限无法判定，计划必须阻断。

    "没有缺失记录"不等于"有权限"。把没验证过的环境当成可以提交，等于让一次从未测过的
    绑定直接拿到 COMMIT 资格——而这条防线正是 S4 在 Gate 之外唯一能自己把关的地方。
    """
    seed_environment(harness, connection_tested=False)

    plan = build_plan(_context(), expected_hash=_package().artifact_hash)

    blocking = [issue for issue in plan.blockers if issue.rule_id == "DX-EXE-012"]
    assert len(blocking) == 1, blocking
    assert "连接测试" in blocking[0].description
    assert blocking[0].suggested_action
    assert plan.supported is False


def test_a_connection_test_that_skipped_the_needed_scope_is_not_evidence(
    harness: Any,
) -> None:
    """DX-EXE-012：只测了 discovery 的那次连接测试，不能拿来证明 import 范围能用。"""
    seed_environment(harness, connection_tested=False)
    record_connection_test(ENVIRONMENT, scopes=(CredentialScope.DISCOVERY,))

    plan = build_plan(_context(), expected_hash=_package().artifact_hash)

    blocking = [issue for issue in plan.blockers if issue.object_key == "import"]
    assert blocking, plan.blockers
    assert "import" in blocking[0].description


def test_a_payload_without_a_change_summary_says_so_in_the_plan(harness: Any) -> None:
    """DX-EXE-010：缺 changeSummary 时计划要**明说**没有预期变更可核对，而不是留一个空对象。

    审批人看到空的 ``expectedChanges`` 会读成"这次什么都不改"；实际上只是 S2 没给摘要。
    不阻断执行（服务端 VALIDATE 才是权威判定），但这条警告必须进计划与执行报告。
    """
    seed_environment(harness)
    manifest = _package().manifest
    manifest.pop("changeSummary")
    package = package_of(manifest, {"dataElements": [{"id": UID}]})

    plan = build_plan(_context(package=package), expected_hash=package.artifact_hash)

    warning = [issue for issue in plan.issues if issue.rule_id == "DX-EXE-010"]
    assert len(warning) == 1, plan.issues
    assert warning[0].level is IssueLevel.WARNING
    assert not warning[0].is_blocking
    assert plan.document["expectedChanges"] == {}


def test_a_stale_connection_test_does_not_outrank_the_latest_one(harness: Any) -> None:
    """DX-EXE-012：读的是**最近一次**连接测试，旧记录里已经补齐的权限不再阻断。"""
    seed_environment(harness, connection_tested=False)
    with unit_of_work() as session:
        for tested_at, missing in (
            (datetime(2026, 1, 1, tzinfo=UTC), {"import": ["F_GONE"]}),
            (datetime(2026, 1, 5, tzinfo=UTC), {}),
        ):
            session.add(
                ConnectionTestRow(
                    id=new_id("dxcnt"),
                    environment_key=ENVIRONMENT,
                    tested_at=tested_at,
                    checks=[],
                    missing_authorities=missing,
                    result="FAILED" if missing else "PASSED",
                    required_scopes=["discovery", "import"],
                )
            )

    assert build_plan(_context(), expected_hash=_package().artifact_hash).blockers == []


# --- 维护与删除（DX-EXE-013/038）--------------------------------------------


def test_maintenance_is_off_until_the_caller_lists_it() -> None:
    """DX-EXE-013：没人列出动作就是空清单；列出来就带上 maintenance 凭证范围。"""
    assert normalize_maintenance(None) == []
    assert normalize_maintenance([{"action": "CLEAR_APP_CACHE"}]) == [
        {"action": "CLEAR_APP_CACHE", "credentialScope": "maintenance"}
    ]
    assert frozenset({"CLEAR_APP_CACHE"}) == SUPPORTED_MAINTENANCE


def test_a_deletion_payload_is_checked_against_the_environment_deletion_policy(
    harness: Any,
) -> None:
    """DX-EXE-038：FORBID 环境的删除载荷在计划里就是阻塞问题，不等到提交才发现。"""
    seed_environment(harness, deletion_policy="FORBID")

    plan = build_plan(_context(), deletion_payload=True, expected_hash=_package().artifact_hash)

    assert [issue.rule_id for issue in plan.blockers] == ["DX-EXE-038"]
    assert "FORBID" in plan.blockers[0].description
    assert plan.document["deletion"] is True


def test_a_safe_only_environment_allows_the_deletion_plan(harness: Any) -> None:
    """DX-EXE-038：SAFE_ONLY 不阻断计划——把关的是删除安全检查与批准，不是这里。"""
    seed_environment(harness, deletion_policy="SAFE_ONLY")

    plan = build_plan(_context(), deletion_payload=True, expected_hash=_package().artifact_hash)

    assert plan.blockers == []


def test_a_non_deletion_plan_ignores_the_deletion_policy(harness: Any) -> None:
    """DX-EXE-038：普通部署不看删除策略，FORBID 环境照样能装元数据。"""
    seed_environment(harness, deletion_policy="FORBID")

    assert build_plan(_context(), expected_hash=_package().artifact_hash).blockers == []


def test_a_missing_protection_row_is_treated_as_safe_only(harness: Any) -> None:
    """DX-ENV-002：环境没登记保护行时按 SAFE_ONLY 处理，而不是「没有策略 = 随便删」。"""
    from factory_dhis2.models.environment import EnvironmentProtectionRow

    seed_environment(harness)
    with unit_of_work() as session:
        row = session.get(EnvironmentProtectionRow, ENVIRONMENT)
        assert row is not None
        session.delete(row)
    plan = build_plan(_context(), deletion_payload=True, expected_hash=_package().artifact_hash)

    with unit_of_work() as session:
        assert session.get(EnvironmentProtectionRow, ENVIRONMENT) is None, "本测试的前提是没有保护行"
    assert plan.blockers == []


# --- 异步决定（DX-EXE-033）---------------------------------------------------


def test_the_caller_can_force_an_async_import_and_the_plan_says_who_decided(
    harness: Any,
) -> None:
    """DX-EXE-033：调用方显式要求的异步优先于阈值，并记下决定者是谁。"""
    del harness
    plan = build_plan(_context(), import_options={"async": True}, expected_hash=_package().artifact_hash)

    assert plan.document["asyncImport"] == {
        "async": True,
        "thresholdObjects": 500,
        "objectCount": 1,
        "decidedBy": "caller",
        "pollResource": "system.tasks",
    }


def _profile_without_async_default() -> Any:
    """一个没有把 ``async`` 写成默认值的 Profile：此时决定异步的才是阈值（DX-EXE-033）。"""
    profile = make_target().profile
    parameters = dict(profile.import_parameters)
    metadata = dict(parameters["metadata"])
    metadata["defaults"] = {
        key: value for key, value in metadata["defaults"].items() if key != "async"
    }
    parameters["metadata"] = metadata
    return replace(profile, import_parameters=parameters)


def test_the_threshold_decides_when_the_profile_does_not_state_async(harness: Any) -> None:
    """DX-EXE-033：载荷对象数超过配置阈值就改用异步导入，阈值来自配置。"""
    del harness
    configure(get_settings().with_overrides(async_import_threshold=0))
    target = replace(make_target(), profile=_profile_without_async_default())

    plan = build_plan(_context(target=target), expected_hash=_package().artifact_hash)

    assert plan.document["asyncImport"]["async"] is True
    assert plan.document["asyncImport"]["decidedBy"] == "threshold"
    assert plan.document["asyncImport"]["pollResource"] == "system.tasks"


def test_the_profiles_async_default_does_not_defeat_the_threshold(harness: Any) -> None:
    """DX-EXE-033：Profile 的默认参数里有 ``async=false``，它不等于"调用方要求同步"。

    合并后的参数永远带着这个默认值；拿合并结果判"调用方是否指定了 async"，阈值规则就彻底
    失效——5,000 对象的包也会走同步导入，一次请求把工作进程钉死（性能冒烟测试实测到的）。
    """
    seed_environment(harness)
    configure(get_settings().with_overrides(async_import_threshold=2))
    package = _three_object_package()

    plan = build_plan(_context(package=package), expected_hash=package.artifact_hash)

    assert plan.document["asyncImport"]["async"] is True, plan.document["asyncImport"]
    assert plan.document["asyncImport"]["decidedBy"] == "threshold"


def _three_object_package() -> Any:
    uids = [f"de00000000{index}" for index in range(1, 4)]
    return package_of(
        build_manifest(
            objects=[
                {
                    "businessKey": f"bk.{uid}",
                    "dhis2Type": "dataElement",
                    "uid": uid,
                    "disposition": "CREATE",
                }
                for uid in uids
            ],
            uid_map=[
                {"businessKey": f"bk.{uid}", "uid": uid, "dhis2Type": "dataElement"}
                for uid in uids
            ],
            dispositions={f"bk.{uid}": "CREATE" for uid in uids},
            controlled_fields={},
        ),
        {"dataElements": [{"id": uid} for uid in uids]},
    )


def test_the_environment_can_override_the_async_threshold(harness: Any) -> None:
    """DX-EXE-033：环境级阈值优先于全局配置——一台慢环境不该被大载荷拖死。"""
    configure(get_settings().with_overrides(async_import_threshold=500))
    uids = ["de000000001", "de000000002", "de000000003"]
    package = package_of(
        build_manifest(
            objects=[
                {"businessKey": f"bk.{uid}", "dhis2Type": "dataElement", "uid": uid, "disposition": "CREATE"}
                for uid in uids
            ],
            uid_map=[
                {"businessKey": f"bk.{uid}", "uid": uid, "dhis2Type": "dataElement", "disposition": "CREATE"}
                for uid in uids
            ],
            dispositions={},
            controlled_fields={},
        ),
        {"dataElements": [{"id": uid} for uid in uids]},
    )
    target = replace(make_target(), profile=_profile_without_async_default(), async_import_threshold=2)

    plan = build_plan(_context(target=target, package=package), expected_hash=package.artifact_hash)

    assert plan.document["asyncImport"]["thresholdObjects"] == 2
    assert plan.document["asyncImport"]["objectCount"] == 3
    assert plan.document["asyncImport"]["async"] is True


def test_a_small_payload_stays_synchronous(harness: Any) -> None:
    """DX-EXE-033：没到阈值就是同步导入——异步带来的轮询成本要用得其所。"""
    del harness
    configure(get_settings().with_overrides(async_import_threshold=500))
    target = replace(make_target(), profile=_profile_without_async_default())

    plan = build_plan(_context(target=target), expected_hash=_package().artifact_hash)

    assert plan.document["asyncImport"]["async"] is False
    assert plan.document["asyncImport"]["decidedBy"] == "threshold"


# --- 计划产物读不到时怎么办（DX-EXE-033 的另一半）--------------------------------


def test_a_missing_plan_document_recomputes_the_async_decision() -> None:
    """阈值规则存在的理由是"大载荷同步提交会顶穿 HTTP 超时"。

    计划产物读不到就默认同步，等于在最难复现的时刻（产物存储与数据库不同步）把那条保护
    悄悄关掉。这里按同一套规则用对象数与环境阈值重算，并把来源标成 ``recomputed``。
    """
    configure(get_settings().with_overrides(async_import_threshold=2))
    context = _context(target=replace(make_target(), profile=_profile_without_async_default()))

    decision = async_import_decision({}, context, object_count=3)

    assert decision["async"] is True
    assert decision["decidedBy"] == "recomputed:threshold"
    assert decision["thresholdObjects"] == 2
    assert decision["objectCount"] == 3


def test_a_recorded_decision_wins_even_when_the_count_says_otherwise() -> None:
    """计划里那份是权威：同一次部署的 VALIDATE 与 COMMIT 必须走同一条路。"""
    configure(get_settings().with_overrides(async_import_threshold=2))
    context = _context(target=replace(make_target(), profile=_profile_without_async_default()))
    recorded = {"asyncImport": {"async": False, "thresholdObjects": 2, "objectCount": 9}}

    assert async_import_decision(recorded, context, object_count=9)["async"] is False


def test_recomputing_ignores_the_profile_default_async_flag() -> None:
    """重算时只看调用方**显式**给的 async。

    合并过 Profile 默认值的那份 importOptions 恒带 ``async=false``，把它当"调用方指定"
    会让阈值规则彻底失效——这个坑已经在计划生成处踩过一次（``test_the_threshold_decides_
    when_the_profile_does_not_state_async``），重算路径上不能再来一次。
    """
    configure(get_settings().with_overrides(async_import_threshold=2))
    context = _context(target=replace(make_target(), profile=_profile_without_async_default()))

    merged_from_profile = async_import_decision(
        {}, context, object_count=3, caller_options={"async": False, "importMode": "COMMIT"}
    )
    assert merged_from_profile["async"] is False, "调用方显式要求同步，那就同步"
    assert merged_from_profile["decidedBy"] == "recomputed:caller"

    nobody_asked = async_import_decision({}, context, object_count=3, caller_options=None)
    assert nobody_asked["async"] is True, "没人要求时，阈值说了算"


def test_an_environment_override_still_applies_when_recomputing() -> None:
    """环境级阈值（DX-EXE-033 的"可按环境配置"）在重算路径上同样有效。"""
    configure(get_settings().with_overrides(async_import_threshold=1))
    target = replace(
        make_target(), profile=_profile_without_async_default(), async_import_threshold=10
    )

    decision = async_import_decision({}, _context(target=target), object_count=3)

    assert decision["async"] is False
    assert decision["thresholdObjects"] == 10


# --- 缺段与保存（DX-ASM-02、DX-EXE-010）-------------------------------------


def test_a_manifest_missing_a_required_section_is_a_blocking_issue(harness: Any) -> None:
    """DX-ASM-02：manifest 缺段是阻塞问题——漂移检查、Snapshot 与问题映射都依赖它。"""
    manifest = _package().manifest
    del manifest["observationBaselines"]
    package = package_of(manifest, {"dataElements": [{"id": UID}]})
    package.missing_sections = ["observationBaselines"]
    seed_environment(harness)

    plan = build_plan(_context(package=package), expected_hash=package.artifact_hash)

    assert [issue.rule_id for issue in plan.blockers] == ["DX-ASM-02"]
    assert "observationBaselines" in plan.blockers[0].description


def test_a_stated_hash_is_mandatory_for_a_plan_that_will_be_executed(harness: Any) -> None:
    """DX-EXE-005：计划不接受「大概就是这个载荷」——没给期望哈希就是拒绝。"""
    del harness
    from factory_dhis2.errors import PayloadHashMismatchError

    with pytest.raises(PayloadHashMismatchError):
        build_plan(_context())

    with pytest.raises(PayloadHashMismatchError):
        build_plan(_context(), expected_hash="sha256:" + "b" * 64)


# --- 保存（DX-EXE-010）-------------------------------------------------------


def _registered_context(harness: Any) -> Any:
    """保存路径的用例要一个**真**产物：计划的 source 引用载荷产物 ID。"""
    artifact_id = register_package_artifact({"dataElements": [{"id": UID, "name": "Weight"}]}, _package().manifest)
    package = package_from_artifact(artifact_id)
    return make_run_context(package, objects={("dataElement", UID): {"id": UID, "name": "Weight"}})


def test_the_plan_is_registered_as_an_EXECUTION_PLAN_artifact_with_the_package_as_source(
    harness: Any,
) -> None:
    """DX-EXE-010：计划以 EXECUTION_PLAN 产物保存，并声明它的来源是这份载荷。"""
    context = _registered_context(harness)
    plan = build_plan(context, expected_hash=context.package.artifact_hash)

    artifact_id = save_plan(context, plan)
    document = read_json(artifact_id)

    assert document["kind"] == "EXECUTION_PLAN"
    assert document["payloadHash"] == context.package.artifact_hash
    assert read_metadata(artifact_id).source_artifact_ids == [context.package.artifact_id]


def test_build_and_save_assembles_resolves_and_persists_in_one_go(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DX-EXE-010：装配 → 生成 → 保存是一条链，管线与测试都用它，避免两套口径。"""
    context = _registered_context(harness)
    monkeypatch.setattr(plan_module, "load_context", lambda *_args, **_kwargs: context)

    built, plan, artifact_id = build_and_save(
        ENVIRONMENT,
        context.package.artifact_id,
        project_id=PROJECT,
        maintenance_actions=[{"action": "CLEAR_APP_CACHE"}],
        expected_hash=context.package.artifact_hash,
    )

    assert built.package.artifact_id == context.package.artifact_id
    assert plan.document["maintenanceActions"] == [
        {"action": "CLEAR_APP_CACHE", "credentialScope": "maintenance"}
    ]
    assert read_json(artifact_id)["maintenanceActions"] == plan.document["maintenanceActions"]


def test_the_environment_class_and_version_travel_in_the_plan(harness: Any) -> None:
    """DX-EXE-010：计划里写清目标是哪一类环境、哪个版本，审批人看的一直是同一份事实。"""
    del harness
    target = replace(make_target(environment_class=EnvironmentClass.DEV), dhis2_version="2.41.2")

    document = build_plan(_context(target=target), expected_hash=_package().artifact_hash).document

    assert document["environmentClass"] == "DEV"
    assert document["dhis2Version"] == "2.41.2"
    assert document["capabilityProfileArtifactId"] == target.capability_profile_artifact_id


def test_every_executor_requirement_names_a_resource_the_profile_has(harness: Any) -> None:
    """DX-CLI-005：判定表里的每个逻辑资源都必须能在版本目录里解析出来。"""
    del harness
    profile = make_target().profile

    for executor, (_scope, resources) in EXECUTOR_REQUIREMENTS.items():
        missing = [name for name in resources if profile.resource(name) is None]
        assert missing == [], f"{executor} 需要的资源没登记：{missing}"
    assert all(scope in {item for item in CredentialScope} for scope, _r in EXECUTOR_REQUIREMENTS.values())


def test_issues_that_are_only_warnings_do_not_block_the_plan(harness: Any) -> None:
    """DX-EXE-012：只有 BLOCKING 才挡住计划——警告留给报告，不在这儿变成拒绝。"""
    context = _context()
    seed_environment(harness)
    plan = build_plan(context, expected_hash=context.package.artifact_hash)
    from factory_dhis2.dto.issue import make_issue

    plan.issues.append(make_issue("DX-EXE-011", IssueLevel.WARNING, "一个警告", object_key="bk.weight"))

    assert plan.blockers == []
    assert plan.supported is True
    assert len(plan.issues) == 1
