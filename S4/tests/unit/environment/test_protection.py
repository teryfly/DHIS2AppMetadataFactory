"""保护设置的实时读取与紧急锁定（DX-ENV-002、DX-ENV-007、DX-BR-11、CP-WF-027）。

``get_protection`` 是 S1 在审批时与提交前都要实时读一次的值，其中 ``withinCommitWindow``
取决于读取的那一刻；紧急锁定的开关变更必须留下审计（附录 A 的
``dx.environment.lock_changed``），且锁定只拒绝写类执行——观察环境不是修改环境。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from factory_foundation.api import AuditQuery, query_audit, unit_of_work
from sqlalchemy.orm import Session

from factory_dhis2.dto.environment import (
    CommitWindow,
    CredentialRef,
    EnvironmentPatch,
    EnvironmentSpec,
    ProtectionSpec,
)
from factory_dhis2.enums import (
    CredentialScope,
    DeletionPolicy,
    EnvironmentClass,
    LlmDataPolicy,
)
from factory_dhis2.environment import loader, protection
from factory_dhis2.environment.service import create_environment, get_environment
from factory_dhis2.errors import EnvironmentInvalidError, EnvironmentLockedError
from factory_dhis2.models.environment import EnvironmentProtectionRow

#: 2026-01-06 是星期二
TUESDAY_2100_SHANGHAI = datetime(2026, 1, 6, 13, 0, tzinfo=UTC)

pytestmark = pytest.mark.usefixtures("harness")


def credentials() -> list[CredentialRef]:
    return [
        CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref="memory://dx/discovery"),
        CredentialRef(scope=CredentialScope.IMPORT, secret_ref="memory://dx/import"),
    ]


def register(
    session: Session,
    key: str,
    *,
    environment_class: EnvironmentClass = EnvironmentClass.TEST,
    protection_spec: ProtectionSpec | None = None,
) -> None:
    create_environment(
        EnvironmentSpec(
            environment_key=key,
            name=key,
            base_url=f"https://{key}.example.org",
            environment_class=environment_class,
            version_constraint="2.41.x",
            credentials=credentials(),
            llm_data_policy=LlmDataPolicy.LOCAL_ONLY,
            protection=protection_spec,
        ),
        session=session,
        operator="alice",
    )


def evening_windows() -> list[CommitWindow]:
    return [CommitWindow(days=[2, 4], start="20:00", end="23:00")]


def production_spec() -> ProtectionSpec:
    return ProtectionSpec(
        timezone="Asia/Shanghai",
        commit_windows=evening_windows(),
        deletion_policy=DeletionPolicy.FORBID,
    )


def environment_row_id(session: Session, key: str) -> str:
    from sqlalchemy import select

    from factory_dhis2.models.environment import EnvironmentRow

    return session.execute(
        select(EnvironmentRow.id).where(EnvironmentRow.key == key)
    ).scalar_one()


def lock_events(session: Session, environment_id: str) -> list:
    """本环境的 ``dx.environment.lock_changed`` 事件。"""
    page = query_audit(
        AuditQuery(
            event_type="dx.environment.lock_changed",
            object_type="Environment",
            object_id=environment_id,
        ),
        session=session,
    )
    return list(page.items)


# --- DX-ENV-007：紧急锁定 -----------------------------------------------------


def test_locking_an_environment_reports_it_as_locked_with_the_reason() -> None:
    """DX-ENV-007：开紧急锁后 ``get_protection`` 报告 locked 与理由。"""
    with unit_of_work() as session:
        register(session, "prod")
        protection.set_emergency_lock("prod", True, "实例正在迁移", "alice", session=session)
        view = protection.get_protection("prod", session=session)
    assert view.emergency_locked is True
    assert view.lock_reason == "实例正在迁移"


def test_unlocking_clears_the_reason_again() -> None:
    with unit_of_work() as session:
        register(session, "prod")
        protection.set_emergency_lock("prod", True, "实例正在迁移", "alice", session=session)
        protection.set_emergency_lock("prod", False, None, "alice", session=session)
        view = protection.get_protection("prod", session=session)
        row = protection.emergency_lock_of(session, "prod")
    assert view.emergency_locked is False
    assert view.lock_reason is None
    assert row == (False, None)


def test_lock_and_unlock_each_write_an_audit_event() -> None:
    """DX-ENV-007 / 附录 A：每一次开关变更都写一条 ``dx.environment.lock_changed``。"""
    with unit_of_work() as session:
        register(session, "prod")
        environment_id = environment_row_id(session, "prod")
        protection.set_emergency_lock("prod", True, "疑似误写", "bob", session=session)
        opening = lock_events(session, environment_id)
        protection.set_emergency_lock("prod", False, None, "bob", session=session)
        closing = lock_events(session, environment_id)
    assert [event.after for event in opening] == [{"locked": True, "reason": "疑似误写"}]
    assert [event.after for event in closing if event.after == {"locked": False, "reason": None}]
    assert len(closing) == 2
    assert {event.operator_name for event in closing} == {"bob"}
    assert {event.source_subsystem for event in closing} == {"S4"}


def test_written_lock_audit_points_at_the_environment_row() -> None:
    """审计的对象 id 就是环境行的 id：S1 用它把变更串成一条链。"""
    with unit_of_work() as session:
        register(session, "prod")
        environment_id = environment_row_id(session, "prod")
        protection.set_emergency_lock("prod", True, "x", "bob", session=session)
        events = lock_events(session, environment_id)
    assert len(events) == 1
    assert events[0].object_type == "Environment"
    assert events[0].before == {"locked": False, "reason": None}


def test_two_consecutive_locks_record_what_actually_changed() -> None:
    """DX-ENV-007/008：审计的 before 是改动前那份事实，不是 ``not locked`` 的推算。

    连续两次上锁时第二次什么都没变；如果 before 被写成"未锁定"，追溯时就会以为发生过一次
    解锁再锁定，而审计里根本找不到那次解锁。
    """
    with unit_of_work() as session:
        register(session, "prod")
        environment_id = environment_row_id(session, "prod")
        protection.set_emergency_lock("prod", True, "演练", "alice", session=session)
        protection.set_emergency_lock("prod", True, "演练改因", "bob", session=session)
        events = lock_events(session, environment_id)
    assert len(events) == 2
    # query_audit 按时间倒序回，这里按操作者取，避免依赖返回顺序
    by_operator = {event.operator_name: event for event in events}
    first, second = by_operator["alice"], by_operator["bob"]
    assert first.before == {"locked": False, "reason": None}
    assert first.after == {"locked": True, "reason": "演练"}
    assert second.before == {"locked": True, "reason": "演练"}
    assert second.after == {"locked": True, "reason": "演练改因"}


def test_reject_if_locked_refuses_a_committing_operation() -> None:
    """DX-ENV-007 / DX-AC-05 / DX-BR-11：锁定开启时写类执行被拒绝。"""
    with unit_of_work() as session:
        register(session, "prod")
        protection.set_emergency_lock("prod", True, "实例正在迁移", "alice", session=session)
        with pytest.raises(EnvironmentLockedError) as excinfo:
            protection.reject_if_locked(session, "prod", "COMMIT")
    assert excinfo.value.details["operation"] == "COMMIT"
    assert excinfo.value.details["reason"] == "实例正在迁移"
    assert excinfo.value.code == "ENVIRONMENT_LOCKED"


def test_reject_if_locked_passes_a_free_environment() -> None:
    with unit_of_work() as session:
        register(session, "dev")
        protection.reject_if_locked(session, "dev", "COMMIT")


def test_a_locked_environment_can_still_be_read_and_targeted() -> None:
    """DX-ENV-007：锁定只拦写。发现、漂移检查、部署后验证共用的读侧装配照常可用。"""
    with unit_of_work() as session:
        register(session, "prod")
        protection.set_emergency_lock("prod", True, "封锁中", "alice", session=session)
        assert get_environment("prod", session=session) is not None
        view = protection.get_protection("prod", session=session)
        target = loader.build_target(session, "prod", scope_hint=CredentialScope.DISCOVERY)
        with pytest.raises(EnvironmentLockedError):
            protection.reject_if_locked(session, "prod", "COMMIT")
    assert view.emergency_locked is True
    assert target.key == "prod"
    assert target.scope_available(CredentialScope.DISCOVERY) is True


def test_locking_an_unknown_environment_is_refused() -> None:
    with unit_of_work() as session, pytest.raises(EnvironmentInvalidError):
        protection.set_emergency_lock("ghost", True, "x", "alice", session=session)


def test_set_emergency_lock_answers_the_shape_s1_mirrors() -> None:
    """S1 的镜像把返回当 dict 用：键名保持 camelCase。"""
    with unit_of_work() as session:
        register(session, "prod")
        document = protection.set_emergency_lock("prod", True, "封锁", "alice", session=session)
    assert set(document) == {"environmentKey", "locked", "reason", "changedAt"}
    assert document["environmentKey"] == "prod"


# --- DX-ENV-002：当前是否落在窗口内 -------------------------------------------


def test_a_production_environment_inside_its_window_is_allowed() -> None:
    with unit_of_work() as session:
        register(session, "prod", environment_class=EnvironmentClass.PRODUCTION, protection_spec=production_spec())
        view = protection.get_protection(
            "prod", session=session, now=TUESDAY_2100_SHANGHAI
        )
    assert view.within_commit_window is True


def test_a_production_environment_outside_its_window_is_refused_with_a_reason() -> None:
    with unit_of_work() as session:
        register(session, "prod", environment_class=EnvironmentClass.PRODUCTION, protection_spec=production_spec())
        view = protection.get_protection(
            "prod", session=session, now=datetime(2026, 1, 7, 13, 0, tzinfo=UTC)
        )
    assert view.within_commit_window is False
    assert view.data_export_policy["outsideCommitWindowReason"]


def test_a_production_environment_with_no_window_configured_is_never_allowed() -> None:
    """DX-ENV-002：空清单不是一种许可——即使此刻本该开放。"""
    with unit_of_work() as session:
        register(
            session,
            "prod",
            environment_class=EnvironmentClass.PRODUCTION,
            protection_spec=production_spec(),
        )
        session.get(EnvironmentProtectionRow, "prod").commit_windows = []
        view = protection.get_protection("prod", session=session, now=TUESDAY_2100_SHANGHAI)
    assert view.within_commit_window is False


def test_a_non_production_environment_without_windows_is_allowed() -> None:
    """DX-ENV-002：时间窗只约束 PRODUCTION 类。"""
    with unit_of_work() as session:
        register(session, "dev")
        view = protection.get_protection("dev", session=session, now=datetime(2026, 1, 7, 3, 0, tzinfo=UTC))
    assert view.within_commit_window is True


# --- 报告给 S1 的其他事实 -----------------------------------------------------


def test_protection_reports_the_class_timezone_and_deletion_policy() -> None:
    with unit_of_work() as session:
        register(session, "prod", environment_class=EnvironmentClass.PRODUCTION, protection_spec=production_spec())
        view = protection.get_protection("prod", session=session)
    assert view.environment_class == EnvironmentClass.PRODUCTION.value
    assert view.timezone == "Asia/Shanghai"
    assert view.deletion_policy == DeletionPolicy.FORBID.value
    assert view.status == "ACTIVE"


def test_fixture_import_is_offered_only_for_non_production_environments() -> None:
    """DX-TRK-002 的判定输入：S1 据 ``allowsFixtureImport`` 决定按钮是否可点。"""
    with unit_of_work() as session:
        register(session, "prod", environment_class=EnvironmentClass.PRODUCTION, protection_spec=production_spec())
        register(session, "dev")
        production_view = protection.get_protection("prod", session=session)
        dev_view = protection.get_protection("dev", session=session)
    assert production_view.allows_fixture_import is False
    assert dev_view.allows_fixture_import is True


def test_data_export_policy_carries_the_rank_used_for_the_strictest_wins_rule() -> None:
    """D-07：S1 取全部目标环境的最严格者，S4 只如实回报。"""
    with unit_of_work() as session:
        register(session, "dev")
        view = protection.get_protection("dev", session=session)
    assert view.data_export_policy["llmDataPolicy"] == LlmDataPolicy.LOCAL_ONLY.value
    assert view.data_export_policy["rank"] == LlmDataPolicy.LOCAL_ONLY.rank


def test_a_disabled_environment_still_exposes_its_protection() -> None:
    """禁用中的环境仍要读得到类别：S1 的项目登记要看它（DX-ENV-009）。"""
    from factory_dhis2.environment.service import disable_environment

    with unit_of_work() as session:
        register(session, "prod", environment_class=EnvironmentClass.PRODUCTION, protection_spec=production_spec())
        disable_environment("prod", session=session, operator="alice")
        view = protection.get_protection("prod", session=session)
    assert view.status == "DISABLED"
    assert view.environment_class == EnvironmentClass.PRODUCTION.value


def test_windows_survive_the_round_trip_through_the_row() -> None:
    """保护设置从行里还原成 spec 时必须与登记时一致（DX-ENV-008 叠加补丁的前提）。"""
    with unit_of_work() as session:
        register(session, "prod", environment_class=EnvironmentClass.PRODUCTION, protection_spec=production_spec())
        restored = protection.protection_spec_of(session, "prod")
    assert restored is not None
    assert restored.timezone == "Asia/Shanghai"
    assert [window.days for window in restored.commit_windows] == [[2, 4]]
    assert restored.deletion_policy is DeletionPolicy.FORBID


def test_updating_protection_replaces_the_windows_rather_than_appending() -> None:
    """DX-ENV-008：改保护设置是一次整体替换，否则关掉的窗口会悄悄留着。"""
    with unit_of_work() as session:
        register(session, "prod", environment_class=EnvironmentClass.PRODUCTION, protection_spec=production_spec())
        from factory_dhis2.environment.service import update_environment

        update_environment(
            "prod",
            EnvironmentPatch(
                protection=ProtectionSpec(
                    timezone="UTC",
                    commit_windows=[CommitWindow(days=[6], start="01:00", end="02:00")],
                    deletion_policy=DeletionPolicy.SAFE_ONLY,
                )
            ),
            session=session,
            operator="alice",
        )
        restored = protection.protection_spec_of(session, "prod")
    assert restored is not None
    assert restored.timezone == "UTC"
    assert [window.days for window in restored.commit_windows] == [[6]]


def test_local_time_of_uses_the_environment_clock() -> None:
    """错误信息与报告里的“现在是几点”必须是环境的时间。"""
    with unit_of_work() as session:
        register(
            session,
            "prod",
            environment_class=EnvironmentClass.PRODUCTION,
            protection_spec=production_spec(),
        )
        moment = datetime.fromisoformat(protection.local_time_of("prod", session))
    assert moment.utcoffset() == timedelta(hours=8)
