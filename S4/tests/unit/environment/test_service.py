"""环境档案的读写往返（DX-ENV-001/003/004/008/009、DX-BR-03、FR-SEC-006）。

这些测试走 `environment.service` 的真实入口，断言的是**留下来的事实**：档案读回来是
什么、每次变更写了哪条审计、进行中的执行怎样拦住改目标。凭证值只存在于进程内存，因此
这里先用 S0 的内存后端真的存一个令牌，再证明它没有出现在任何一行里。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from factory_foundation.api import (
    AuditQuery,
    install_development_backends,
    query_audit,
    store_secret,
    unit_of_work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2.dto.environment import (
    CommitWindow,
    CredentialRef,
    EnvironmentPatch,
    EnvironmentSpec,
    ProtectionSpec,
)
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    DeletionPolicy,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.environment import loader
from factory_dhis2.environment.service import (
    create_environment,
    disable_environment,
    get_environment,
    list_environments,
    update_environment,
)
from factory_dhis2.errors import (
    EnvironmentInUseError,
    EnvironmentInvalidError,
)
from factory_dhis2.models.environment import (
    EnvironmentCredentialRow,
    EnvironmentRow,
)
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.models.profile import CapabilityProfileIndexRow

pytestmark = pytest.mark.usefixtures("harness")

IMPORT_TOKEN = "dhp_pat_import_token_value_1"


def spec(key: str = "staging", **changes: object) -> EnvironmentSpec:
    document: dict[str, object] = {
        "environment_key": key,
        "name": "Staging 环境",
        "base_url": "https://staging.example.org/instance",
        "environment_class": EnvironmentClass.TEST,
        "version_constraint": "2.41.x",
        "credentials": [
            {
                "scope": CredentialScope.DISCOVERY,
                "secret_ref": "memory://dx/discovery",
                "label": "discovery 账户",
            },
            {
                "scope": CredentialScope.IMPORT,
                "secret_ref": "memory://dx/import",
                "label": "import 账户",
            },
        ],
        "llm_data_policy": LlmDataPolicy.PRIVATE_NETWORK,
    }
    document.update(changes)
    return EnvironmentSpec.model_validate(document)


def running_execution(session: Session, key: str, execution_id: str = "dxexe0000000000000000000001") -> None:
    session.add(
        ExecutionRow(
            id=execution_id,
            environment_key=key,
            mode="COMMIT",
            status="RUNNING",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    session.flush()


def strings_of(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from strings_of(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from strings_of(item)
    elif isinstance(value, str):
        yield value


def row_strings(row: object) -> list[str]:
    return list(strings_of({k: v for k, v in vars(row).items() if not k.startswith("_")}))


def environment_id(session: Session, key: str) -> str:
    return session.execute(
        select(EnvironmentRow.id).where(EnvironmentRow.key == key)
    ).scalar_one()


# --- DX-ENV-001/004：登记与读回 ----------------------------------------------


def test_registering_an_environment_then_reading_it_back_returns_the_declared_fields() -> None:
    """DX-ENV-001：档案的每个字段都要原样读得回来。"""
    with unit_of_work() as session:
        created = create_environment(spec(), session=session, operator="alice")
    assert created.environment_key == "staging"
    assert created.name == "Staging 环境"
    assert created.base_url == "https://staging.example.org/instance"
    assert created.environment_class is EnvironmentClass.TEST
    assert created.version_constraint == "2.41.x"
    assert created.timeout_seconds == 60
    assert created.auth_mode is AuthMode.PAT
    assert created.enabled is True
    assert created.status is EnvironmentStatus.ACTIVE


def test_reading_an_unknown_environment_returns_none_not_an_error() -> None:
    """DX-ENV-004：S1 用 ``get_environment`` 判断“环境是否存在”。"""
    with unit_of_work() as session:
        assert get_environment("never-registered", session=session) is None


def test_assembling_a_target_for_an_unknown_environment_raises() -> None:
    """DX-ENV-004：执行侧没有默认环境，未知键必须是错误而不是猜测。"""
    with unit_of_work() as session, pytest.raises(EnvironmentInvalidError) as excinfo:
        loader.require_environment(session, "never-registered")
    assert excinfo.value.details["environmentKey"] == "never-registered"


def test_registering_the_same_key_twice_is_refused() -> None:
    """DX-ENV-001：环境键是唯一的，重复登记不是覆盖。"""
    with unit_of_work() as session:
        create_environment(spec(), session=session, operator="alice")
    with unit_of_work() as session, pytest.raises(EnvironmentInvalidError):
        create_environment(spec(name="第二个"), session=session, operator="alice")


def test_the_version_reported_comes_from_the_latest_capability_profile() -> None:
    """DX-ENV-001：``version`` 来自最近一次探测，而不是档案里的静态约束。"""
    with unit_of_work() as session:
        create_environment(spec(), session=session, operator="alice")
        session.add_all(
            [
                CapabilityProfileIndexRow(
                    id="dxcap0000000000000000000001",
                    environment_key="staging",
                    profile_version="Dhis241Profile",
                    dhis2_version="2.41.7",
                    probed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    artifact_id="art0000000000000000000000001",
                    content_hash="sha256:" + "a" * 64,
                ),
                CapabilityProfileIndexRow(
                    id="dxcap0000000000000000000002",
                    environment_key="staging",
                    profile_version="Dhis241Profile",
                    dhis2_version="2.41.10",
                    probed_at=datetime(2026, 2, 1, tzinfo=UTC),
                    artifact_id="art0000000000000000000000002",
                    content_hash="sha256:" + "b" * 64,
                ),
            ]
        )
        session.flush()
    with unit_of_work() as session:
        read_back = get_environment("staging", session=session)
    assert read_back is not None
    assert read_back.version == "2.41.10"
    assert read_back.profile_version == "Dhis241Profile"


def test_list_environments_reports_every_registered_environment() -> None:
    """DX-ENV-009：环境是全局对象，清单不属于任何项目。"""
    with unit_of_work() as session:
        create_environment(spec("dev"), session=session, operator="alice")
        create_environment(spec("prod"), session=session, operator="alice")
        keys = [item.environment_key for item in list_environments(session=session)]
    assert keys == ["dev", "prod"]


# --- DX-ENV-008：变更与审计 ---------------------------------------------------


def test_updating_the_name_then_reading_it_back_reports_the_new_value() -> None:
    """DX-ENV-001/008：改档案读回来就是新值。"""
    with unit_of_work() as session:
        create_environment(spec(), session=session, operator="alice")
    with unit_of_work() as session:
        updated = update_environment("staging", EnvironmentPatch(name="预发环境"), session=session, operator="alice")
        read_back = get_environment("staging", session=session)
    assert updated.name == "预发环境"
    assert read_back is not None and read_back.name == "预发环境"


def test_an_update_writes_the_environment_updated_audit_with_both_sides() -> None:
    """DX-ENV-008 / 附录 A：每次变更写审计，改前改后都要看得见。"""
    with unit_of_work() as session:
        create_environment(spec(), session=session, operator="alice")
        row_id = environment_id(session, "staging")
    with unit_of_work() as session:
        update_environment(
            "staging",
            EnvironmentPatch(base_url="https://new.example.org/instance", timeout_seconds=30),
            session=session,
            operator="alice",
        )
        events = query_audit(
            AuditQuery(event_type="dx.environment.updated", object_id=row_id), session=session
        ).items
    assert len(events) == 1
    assert events[0].before["baseUrl"] == "https://staging.example.org/instance"
    assert events[0].after["baseUrl"] == "https://new.example.org/instance"
    assert events[0].after["timeoutSeconds"] == 30


def test_a_protection_only_change_is_still_visible_in_the_audit() -> None:
    """DX-ENV-008：只改时间窗/删除策略时，审计的 before 与 after 必须不同。

    审计摘要此前只含档案列，保护设置的变更会记成 before==after —— 等于"记录了但没记下
    任何事"，而 PRODUCTION 的时间窗与删除策略正是审批人要回看的内容。
    """
    with unit_of_work() as session:
        create_environment(
            spec(
                environment_class=EnvironmentClass.PRODUCTION,
                protection=ProtectionSpec(
                    commit_windows=[CommitWindow(days=[2], start="20:00", end="23:00")],
                    deletion_policy=DeletionPolicy.FORBID,
                ),
            ),
            session=session,
            operator="alice",
        )
        row_id = environment_id(session, "staging")
    with unit_of_work() as session:
        update_environment(
            "staging",
            EnvironmentPatch(
                protection=ProtectionSpec(
                    commit_windows=[CommitWindow(days=[4], start="20:00", end="23:00")],
                    deletion_policy=DeletionPolicy.SAFE_ONLY,
                )
            ),
            session=session,
            operator="bob",
        )
        events = query_audit(
            AuditQuery(event_type="dx.environment.updated", object_id=row_id), session=session
        ).items
    assert len(events) == 1
    before = events[0].before["protection"]
    after = events[0].after["protection"]
    assert before != after, "只改保护设置时审计必须能看出改了什么"
    assert before["deletionPolicy"] == "FORBID" and after["deletionPolicy"] == "SAFE_ONLY"
    assert before["commitWindows"][0]["days"] == [2]
    assert after["commitWindows"][0]["days"] == [4]


def test_notes_and_the_async_threshold_round_trip_through_the_profile() -> None:
    """DX-ENV-001 的"备注"与 DX-EXE-001/033 的按环境阈值都必须写得进、读得出。"""
    with unit_of_work() as session:
        created = create_environment(
            spec(notes="只用于 UAT 回归", async_import_threshold=120), session=session
        )
    assert created.notes == "只用于 UAT 回归"
    assert created.async_import_threshold == 120
    with unit_of_work() as session:
        read_back = get_environment("staging", session=session)
    assert read_back is not None
    assert read_back.notes == "只用于 UAT 回归"
    assert read_back.async_import_threshold == 120, (
        "没有这一列时 plan 永远读不到按环境的阈值（DX-EXE-033）"
    )

    with unit_of_work() as session:
        patched = update_environment(
            "staging",
            EnvironmentPatch(notes="改过负责人", async_import_threshold=80),
            session=session,
        )
    assert patched.notes == "改过负责人" and patched.async_import_threshold == 80
    with unit_of_work() as session:
        untouched = update_environment(
            "staging", EnvironmentPatch(name="只改名"), session=session
        )
    assert untouched.notes == "改过负责人" and untouched.async_import_threshold == 80, (
        "补丁没提到的字段必须保持原样（DX-ENV-008）"
    )


def test_the_async_threshold_reaches_the_environment_target() -> None:
    """DX-EXE-033：列要一路装配进 ``EnvironmentTarget``，否则阈值判定读不到它。"""
    from factory_dhis2.environment.loader import build_target

    with unit_of_work() as session:
        create_environment(spec(async_import_threshold=42), session=session)
    with unit_of_work() as session:
        target = build_target(session, "staging", allow_unprobed=True)
    assert target.async_import_threshold == 42


def test_an_unconfigured_async_threshold_stays_unset_and_uses_the_global_value() -> None:
    from factory_dhis2.environment.loader import build_target

    with unit_of_work() as session:
        create_environment(spec(), session=session)
    with unit_of_work() as session:
        target = build_target(session, "staging", allow_unprobed=True)
    assert target.async_import_threshold is None, (
        "空值才代表「没配」，落成 0 会让每个包都走异步导入"
    )


def test_changing_a_secret_reference_is_audited_by_reference_only() -> None:
    """DX-ENV-003 / 21.4：凭证引用变更写 ``dx.credential_ref.changed``，只写引用。"""
    with unit_of_work() as session:
        create_environment(spec(), session=session, operator="alice")
    with unit_of_work() as session:
        update_environment(
            "staging",
            EnvironmentPatch(
                credentials=[
                    CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref="memory://dx/discovery"),
                    CredentialRef(scope=CredentialScope.IMPORT, secret_ref="memory://dx/import-rotated"),
                ]
            ),
            session=session,
            operator="alice",
        )
        events = query_audit(
            AuditQuery(event_type="dx.credential_ref.changed"), session=session
        ).items
        row = session.get(EnvironmentCredentialRow, ("staging", CredentialScope.IMPORT.value))
    changed = [event for event in events if event.before == {"secretRef": "memory://dx/import"}]
    assert len(changed) == 1
    assert changed[0].after == {"secretRef": "memory://dx/import-rotated"}
    assert changed[0].object_id == "staging/import"
    assert row is not None and row.secret_ref == "memory://dx/import-rotated"


def test_dropping_a_credential_scope_removes_the_row_and_audits_it() -> None:
    """DX-ENV-008：补丁是一次整体替换，删掉的引用不能留在表里。"""
    keep_two = [
        CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref="memory://dx/discovery"),
        CredentialRef(scope=CredentialScope.IMPORT, secret_ref="memory://dx/import"),
    ]
    with unit_of_work() as session:
        create_environment(spec(), session=session, operator="alice")
    with unit_of_work() as session:
        update_environment(
            "staging",
            EnvironmentPatch(
                credentials=[
                    *keep_two,
                    CredentialRef(scope=CredentialScope.TRACKER, secret_ref="memory://dx/tracker"),
                ]
            ),
            session=session,
            operator="alice",
        )
    with unit_of_work() as session:
        update_environment(
            "staging", EnvironmentPatch(credentials=keep_two), session=session, operator="alice"
        )
    with unit_of_work() as session:
        scopes = sorted(
            row.scope
            for row in session.execute(
                select(EnvironmentCredentialRow).where(
                    EnvironmentCredentialRow.environment_key == "staging"
                )
            ).scalars()
        )
    assert scopes == [CredentialScope.DISCOVERY.value, CredentialScope.IMPORT.value]


def test_an_update_that_breaks_the_rules_leaves_the_archive_untouched() -> None:
    """DX-ENV-008：规则不过就整笔回滚，不存在改一半的档案。"""
    with unit_of_work() as session:
        create_environment(spec(), session=session, operator="alice")
    with pytest.raises(EnvironmentInvalidError), unit_of_work() as session:
        update_environment(
            "staging", EnvironmentPatch(timeout_seconds=0), session=session, operator="alice"
        )
    with unit_of_work() as session:
        read_back = get_environment("staging", session=session)
    assert read_back is not None and read_back.timeout_seconds == 60


# --- DX-ENV-003：只存引用 ------------------------------------------------------


def test_a_stored_credential_reference_never_carries_the_secret_value() -> None:
    """DX-ENV-003 / DX-BR-03 / FR-SEC-006：行里与审计里都没有凭证值。"""
    install_development_backends()
    ref = store_secret("dx/import-live", IMPORT_TOKEN, scheme="memory")

    with unit_of_work() as session:
        create_environment(
            spec(
                credentials=[
                    CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref="memory://dx/discovery"),
                    CredentialRef(scope=CredentialScope.IMPORT, secret_ref=ref),
                ]
            ),
            session=session,
            operator="alice",
        )
    with unit_of_work() as session:
        rows = list(session.execute(select(EnvironmentRow)).scalars())
        rows += list(session.execute(select(EnvironmentCredentialRow)).scalars())
        stored_values = [value for row in rows for value in row_strings(row)]
        audit_payloads = [
            value
            for event in query_audit(AuditQuery(limit=200), session=session).items
            for value in list(strings_of(event.before or {})) + list(strings_of(event.after or {}))
        ]
        credential_row = session.get(
            EnvironmentCredentialRow, ("staging", CredentialScope.IMPORT.value)
        )

    assert IMPORT_TOKEN not in stored_values, "凭证值出现在 dx 行里"
    assert IMPORT_TOKEN not in audit_payloads, "凭证值出现在审计摘要里"
    assert ref in stored_values, "断言要有意义：引用本身必须存下来了"
    assert credential_row is not None and credential_row.secret_ref == ref


def test_the_credential_table_has_no_column_that_could_hold_a_value() -> None:
    """DX-ENV-003：结构上就装不下明文，比“代码里小心”更可靠。"""
    columns = {column.name for column in EnvironmentCredentialRow.__table__.columns}
    assert columns == {
        "environment_key",
        "scope",
        "secret_ref",
        "expires_at",
        "label",
        "created_at",
        "updated_at",
    }


def test_environment_rows_carry_no_project_reference() -> None:
    """DX-ENV-009 / D-03：环境是全局对象，不承载项目信息。"""
    columns = {column.name for column in EnvironmentRow.__table__.columns}
    assert not any("project" in column for column in columns), columns


# --- DX-ENV-008/009：进行中的执行拦住的改动 -----------------------------------


def test_changing_the_base_url_while_an_execution_is_running_is_refused() -> None:
    """DX-ENV-008：执行中途换目标等于把一次提交劈成两个实例。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        running_execution(session, "prod")
    with unit_of_work() as session, pytest.raises(EnvironmentInUseError) as excinfo:
        update_environment(
            "prod",
            EnvironmentPatch(base_url="https://other.example.org/instance"),
            session=session,
            operator="alice",
        )
    assert excinfo.value.details["environmentKey"] == "prod"


def test_changing_secret_references_while_an_execution_is_running_is_refused() -> None:
    """DX-ENV-008：Secret 引用同样不能在执行中途更换。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        running_execution(session, "prod")
    with unit_of_work() as session, pytest.raises(EnvironmentInUseError):
        update_environment(
            "prod",
            EnvironmentPatch(
                credentials=[
                    CredentialRef(
                        scope=CredentialScope.DISCOVERY, secret_ref="memory://dx/new-discovery"
                    ),
                    CredentialRef(scope=CredentialScope.IMPORT, secret_ref="memory://dx/new-import"),
                ]
            ),
            session=session,
            operator="alice",
        )


def test_a_pending_manual_check_execution_also_blocks_a_target_change() -> None:
    """DX-EXE-031 的一环：结果未确认时同样不许换目标。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        session.add(
            ExecutionRow(
                id="dxexe0000000000000000000002",
                environment_key="prod",
                mode="COMMIT",
                status="NEEDS_MANUAL_CHECK",
            )
        )
    with unit_of_work() as session, pytest.raises(EnvironmentInUseError):
        update_environment(
            "prod",
            EnvironmentPatch(base_url="https://other.example.org"),
            session=session,
            operator="alice",
        )


def test_a_finished_execution_does_not_block_the_environment() -> None:
    """只有进行中的执行才拦住改动，否则一次部署后就再也改不动环境了。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        session.add(
            ExecutionRow(
                id="dxexe0000000000000000000003",
                environment_key="prod",
                mode="COMMIT",
                status="COMPLETED",
            )
        )
    with unit_of_work() as session:
        updated = update_environment(
            "prod",
            EnvironmentPatch(base_url="https://other.example.org"),
            session=session,
            operator="alice",
        )
    assert updated.base_url == "https://other.example.org"


def test_renaming_an_environment_while_an_execution_is_running_is_allowed() -> None:
    """DX-ENV-008 拦的是“换目标”，改名与调超时不影响那次执行。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        running_execution(session, "prod")
    with unit_of_work() as session:
        updated = update_environment(
            "prod", EnvironmentPatch(name="生产（改名）", timeout_seconds=45), session=session, operator="alice"
        )
    assert updated.name == "生产（改名）"
    assert updated.timeout_seconds == 45


def test_disabling_an_environment_with_an_active_execution_is_refused() -> None:
    """DX-ENV-009：禁用前必须没有进行中的执行。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        running_execution(session, "prod")
    with unit_of_work() as session:
        with pytest.raises(EnvironmentInUseError):
            disable_environment("prod", session=session, operator="alice")
        assert get_environment("prod", session=session) is not None


def test_disabling_writes_the_disabled_audit_and_flips_enabled() -> None:
    """DX-ENV-001/009：禁用是一次可审计的状态变更。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        row_id = environment_id(session, "prod")
    with unit_of_work() as session:
        disable_environment("prod", session=session, operator="alice")
        read_back = get_environment("prod", session=session)
        events = query_audit(
            AuditQuery(event_type="dx.environment.disabled", object_id=row_id), session=session
        ).items
    assert read_back is not None
    assert read_back.enabled is False
    assert read_back.status is EnvironmentStatus.DISABLED
    assert [event.after["environmentKey"] for event in events] == ["prod"]


def test_a_disabled_environment_cannot_be_used_for_a_dhis2_call() -> None:
    """DX-ENV-001 的 status 语义：禁用中的环境装配不出目标。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        disable_environment("prod", session=session, operator="alice")
    with unit_of_work() as session, pytest.raises(EnvironmentInvalidError):
        loader.build_target(session, "prod")


def test_an_environment_without_a_probe_falls_back_to_its_version_constraint() -> None:
    """DX-CAP-003：没有探测结果时只有版本 Profile 可用，且来源是约束而不是猜测。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
        target = loader.build_target(session, "prod")
    assert target.key == "prod"
    assert target.profile.profile_version == "Dhis241Profile"
    assert target.dhis2_version == "2.41.x"


def test_a_missing_credential_scope_is_refused_when_the_caller_needs_it() -> None:
    """DX-ENV-003 / DX-CON-002：范围缺失在装配目标时就暴露。"""
    with unit_of_work() as session:
        create_environment(spec("prod"), session=session, operator="alice")
    with unit_of_work() as session, pytest.raises(EnvironmentInvalidError) as excinfo:
        loader.build_target(session, "prod", scope_hint=CredentialScope.MAINTENANCE)
    assert excinfo.value.details["scope"] == CredentialScope.MAINTENANCE.value


def test_a_round_trip_from_create_through_update_to_disable_keeps_the_key_stable() -> None:
    """DX-ENV-001/004/008/009：环境键是全流程唯一的寻址方式。"""
    with unit_of_work() as session:
        created = create_environment(spec("uat"), session=session, operator="alice")
    with unit_of_work() as session:
        updated = update_environment("uat", EnvironmentPatch(name="UAT 环境"), session=session, operator="alice")
        assert get_environment("uat", session=session) is not None
    with unit_of_work() as session:
        disable_environment("uat", session=session, operator="alice")
        read_back = get_environment("uat", session=session)
    assert created.environment_key == updated.environment_key == "uat"
    assert read_back is not None and read_back.enabled is False
