"""保护设置：时间窗、紧急锁定、删除策略（DX-ENV-002/007）。

``get_protection`` 是 S1 在**审批时与提交前**都要实时读一次的值（CP-WF-027）：其中
``withinCommitWindow`` 取决于读取的那一刻，不能缓存，也不进任何产物。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2 import audit
from factory_dhis2.dto.environment import CommitWindow, EnvironmentProtection, ProtectionSpec
from factory_dhis2.enums import DeletionPolicy, EnvironmentClass, EnvironmentStatus, LlmDataPolicy
from factory_dhis2.environment.rules import resolve_deletion_policy
from factory_dhis2.environment.windows import (
    local_now,
    windows_as_documents,
    within_commit_window,
)
from factory_dhis2.errors import EnvironmentInvalidError
from factory_dhis2.models.environment import (
    EnvironmentProtectionRow,
    EnvironmentRow,
)


def _class_of(session: Session, environment_key: str) -> EnvironmentClass:
    """保护设置的默认值取决于环境类别（DX-ENV-002），所以这里必须按真实类别折算。

    S0 的会话是 ``autoflush=False``：新建环境时那一行还悬在 Session 里，不 flush 就查不到,
    于是"刚登记的环境"会被告知不存在。
    """
    session.flush()
    environment = session.execute(
        select(EnvironmentRow).where(EnvironmentRow.key == environment_key)
    ).scalars().first()
    if environment is None:
        raise EnvironmentInvalidError(
            f"unknown environment {environment_key!r}",
            details={"environmentKey": environment_key},
        )
    return EnvironmentClass(environment.environment_class)


def ensure_protection_row(
    session: Session,
    environment_key: str,
    protection: ProtectionSpec | None,
    *,
    allow_existing: bool = False,
) -> EnvironmentProtectionRow:
    """没有保护行时按 spec（或**该环境类别的默认值**）建一行。

    DX-ENV-002 的默认值是按类别给的：非生产 ``SAFE_ONLY``、PRODUCTION ``FORBID``。这里曾经
    反过来 —— 没配保护就一律 ``FORBID``，于是每个 DEV 环境都不许删除；而配了保护却没填策略
    的 PRODUCTION 又拿到了非生产的 ``SAFE_ONLY``。
    """
    environment_class = _class_of(session, environment_key)
    existing = session.get(EnvironmentProtectionRow, environment_key)
    if existing is not None:
        if protection is not None and allow_existing:
            _overwrite(existing, protection, environment_class=environment_class)
        return existing
    row = EnvironmentProtectionRow(
        environment_key=environment_key,
        timezone=(protection.timezone if protection else "UTC"),
        commit_windows=(
            windows_as_documents(protection.commit_windows, protection.timezone)
            if protection
            else []
        ),
        deletion_policy=resolve_deletion_policy(
            environment_class, protection.deletion_policy if protection else None
        ).value,
        # 紧急锁定不在这里写：只有 set_emergency_lock 能改它（DX-ENV-007）
        emergency_lock=False,
    )
    session.add(row)
    return row


def _overwrite(
    row: EnvironmentProtectionRow,
    protection: ProtectionSpec,
    *,
    environment_class: EnvironmentClass,
) -> None:
    row.timezone = protection.timezone
    row.commit_windows = windows_as_documents(protection.commit_windows, protection.timezone)
    row.deletion_policy = resolve_deletion_policy(
        environment_class, protection.deletion_policy
    ).value
    row.required_approvals = protection.required_approvals
    row.operator_role = protection.operator_role


def _documents_of(row: EnvironmentProtectionRow | None) -> list[dict[str, Any]]:
    return list(row.commit_windows) if row else []


def protection_spec_of(session: Session, environment_key: str) -> ProtectionSpec | None:
    row = session.get(EnvironmentProtectionRow, environment_key)
    if row is None:
        return None
    windows = [
        CommitWindow(
            days=[int(day) for day in item.get("days", [])],
            start=str(item.get("start", "00:00")),
            end=str(item.get("end", "23:59")),
            note=item.get("note") if isinstance(item.get("note"), str) else None,
        )
        for item in _documents_of(row)
        if isinstance(item, dict)
    ]
    return ProtectionSpec(
        timezone=row.timezone,
        commit_windows=windows,
        deletion_policy=DeletionPolicy(row.deletion_policy),
        emergency_lock=row.emergency_lock,
        required_approvals=row.required_approvals,
        operator_role=row.operator_role,
    )


def get_protection(
    environment_key: str,
    *,
    session: Session,
    now: datetime | None = None,
) -> EnvironmentProtection:
    """SRS 7.1：类别、时间窗、紧急锁定、删除策略、数据外发策略、当前是否处于窗口内。"""
    environment = session.get(EnvironmentRow, environment_key) or _by_key(session, environment_key)
    row = session.get(EnvironmentProtectionRow, environment_key)
    commit_windows = list(row.commit_windows) if row else []
    timezone_name = row.timezone if row else "UTC"
    required = environment.environment_class == EnvironmentClass.PRODUCTION.value
    inside, reason = within_commit_window(
        _windows_of(commit_windows),
        timezone_name=timezone_name,
        now=now,
        required=required,
    )
    policy = LlmDataPolicy(environment.llm_data_policy)
    return EnvironmentProtection(
        environment_key=environment_key,
        environment_class=environment.environment_class,
        commit_windows=commit_windows,
        within_commit_window=inside,
        emergency_locked=bool(row and row.emergency_lock),
        #: 锁定时给出原因与时刻，让审批人看得见是谁锁的（DX-ENV-007）
        lock_reason=environment.key and (row.lock_reason if row else None),
        data_export_policy={
            "llmDataPolicy": policy.value,
            "rank": policy.rank,
            "environmentClass": environment.environment_class,
            "refusedWhen": "外部 LLM 提供方类别严于该策略时不得外发（D-07 由调用方合成）",
            "outsideCommitWindowReason": reason,
        },
        allows_fixture_import=environment.environment_class != EnvironmentClass.PRODUCTION.value,
        # 没有保护行时也要给出**该类别**的默认策略，而不是永远 SAFE_ONLY（DX-ENV-002）
        deletion_policy=resolve_deletion_policy(
            EnvironmentClass(environment.environment_class),
            DeletionPolicy(row.deletion_policy) if row else None,
        ).value,
        timezone=timezone_name,
        lock_changed_at=(
            row.lock_changed_at.isoformat()
            if row is not None and row.lock_changed_at is not None
            else None
        ),
        lock_changed_by=(row.lock_changed_by if row else None),
        llm_data_policy=policy.value,
        required_approvals=row.required_approvals if row else None,
        operator_role=row.operator_role if row else None,
        status=environment.status,
    )


def emergency_lock_of(session: Session, environment_key: str) -> tuple[bool, str | None]:
    row = session.get(EnvironmentProtectionRow, environment_key)
    if row is None:
        return False, None
    return bool(row.emergency_lock), row.lock_reason


def set_emergency_lock(
    environment_key: str,
    locked: bool,
    reason: str | None,
    operator: str,
    *,
    session: Session,
) -> dict[str, object]:
    """紧急锁定开关（DX-ENV-007：开关变更写审计）。"""
    environment = _by_key(session, environment_key)
    row = ensure_protection_row(session, environment_key, None, allow_existing=False)
    # 审计的 before 必须是**改动前那一份事实**：写 `not locked` 会让连续两次上锁的第二条
    # 记录留下一个从未发生过的 before=true，追溯时看不出到底改了什么（DX-ENV-007/008）
    previously_locked = bool(row.emergency_lock)
    previous_reason = row.lock_reason
    row.emergency_lock = locked
    row.lock_reason = reason if locked else None
    row.lock_changed_at = datetime.now(UTC)
    row.lock_changed_by = operator
    audit.record(
        audit.ENVIRONMENT_LOCK_CHANGED,
        object_type="Environment",
        object_id=environment.id,
        before={"locked": previously_locked, "reason": previous_reason},
        after={"locked": locked, "reason": reason},
        operator=operator,
        session=session,
    )
    # S1 的镜像把返回当 dict 用，不校验：键名保持 camelCase
    return {
        "environmentKey": environment_key,
        "locked": locked,
        "reason": reason,
        "changedAt": row.lock_changed_at.isoformat(),
    }


def reject_if_locked(session: Session, environment_key: str, operation: str) -> None:
    """DX-ENV-007 / DX-BR-11：锁定开启时拒绝一切写类执行。"""
    from factory_dhis2.errors import EnvironmentLockedError

    locked, reason = emergency_lock_of(session, environment_key)
    if locked:
        raise EnvironmentLockedError(
            f"environment {environment_key!r} is emergency-locked; {operation} is refused",
            details={
                "environmentKey": environment_key,
                "operation": operation,
                "reason": reason,
            },
        )


def _windows_of(documents: list[dict[str, Any]]) -> list[CommitWindow]:
    return [
        CommitWindow(
            days=[int(day) for day in item.get("days", [])],
            start=str(item.get("start", "00:00")),
            end=str(item.get("end", "23:59")),
        )
        for item in documents
        if isinstance(item, dict)
    ]


def _by_key(session: Session, environment_key: str) -> EnvironmentRow:
    row = session.execute(
        select(EnvironmentRow).where(EnvironmentRow.key == environment_key)
    ).scalars().first()
    if row is None:
        raise EnvironmentInvalidError(
            f"unknown environment {environment_key!r}",
            details={"environmentKey": environment_key},
        )
    if row.status == EnvironmentStatus.DISABLED.value:
        # 禁用中的环境仍可读保护设置：S1 的项目登记需要看到类别
        pass
    return row


def local_time_of(environment_key: str, session: Session) -> str:
    """环境时区的当前本地时间（错误信息与报告用）。"""
    row = session.get(EnvironmentProtectionRow, environment_key)
    zone = row.timezone if row else "UTC"
    return local_now(zone).isoformat()


__all__ = [
    "emergency_lock_of",
    "ensure_protection_row",
    "get_protection",
    "local_time_of",
    "protection_spec_of",
    "reject_if_locked",
    "set_emergency_lock",
]
