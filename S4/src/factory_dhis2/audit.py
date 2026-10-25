"""S4 的审计写入（附录 A、总体 SRS 21.4）。

审计与业务状态在**同一事务**里写（D-11、NFR-REL-005）：这里的 ``session`` 一律由调用方
传入其正在使用的工作单元。凭证特征由 S0 在写入前脱敏（FD-AUD-007），但 S4 仍不把
Secret 值放进摘要——只放引用（DX-BR-03）。
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import AuditEventInput, OperatorType, audit
from sqlalchemy.orm import Session

from factory_dhis2.registries import DX_AUDIT_EVENT_TYPES

#: 附录 A 的事件名，代码里用常量而不是字面量，避免拼错后在运行时才失败。
ENVIRONMENT_CREATED = "dx.environment.created"
ENVIRONMENT_UPDATED = "dx.environment.updated"
ENVIRONMENT_DISABLED = "dx.environment.disabled"
ENVIRONMENT_LOCK_CHANGED = "dx.environment.lock_changed"
CREDENTIAL_REF_CHANGED = "dx.credential_ref.changed"
CONNECTION_TESTED = "dx.connection.tested"
CAPABILITY_PROBED = "dx.capability.probed"
CAPABILITY_STALE = "dx.capability.stale"
DISCOVERY_COMPLETED = "dx.discovery.completed"
SNAPSHOT_CREATED = "dx.snapshot.created"
VALIDATE_STARTED = "dx.validate.started"
VALIDATE_COMPLETED = "dx.validate.completed"
COMMIT_STARTED = "dx.commit.started"
COMMIT_COMPLETED = "dx.commit.completed"
COMMIT_FAILED = "dx.commit.failed"
COMMIT_NEEDS_MANUAL_CHECK = "dx.commit.needs_manual_check"
MANUAL_CHECK_RESOLVED = "dx.manual_check.resolved"
MAINTENANCE_EXECUTED = "dx.maintenance.executed"
POST_VERIFY_COMPLETED = "dx.post_verify.completed"
FIXTURE_IMPORTED = "dx.fixture.imported"
DELETE_EXECUTED = "dx.delete.executed"
ENV_LOCK_ACQUIRED = "dx.env_lock.acquired"
ENV_LOCK_RELEASED = "dx.env_lock.released"


def record(
    event_type: str,
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    project_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    operator: str | None = None,
    operator_is_system: bool = False,
    session: Session | None = None,
) -> None:
    """写一条审计记录。

    未登记的事件名会在这里立刻失败——这是有意为之：附录 A 是需求的一部分，新增事件必须
    先登记（``registries.DX_AUDIT_EVENT_TYPES``），否则审计覆盖无从核对。
    """
    if event_type not in DX_AUDIT_EVENT_TYPES:
        raise KeyError(f"unregistered S4 audit event type: {event_type}")
    audit(
        AuditEventInput(
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            project_id=project_id,
            before=before,
            after=after,
            source_subsystem="S4",
            operator_type=OperatorType.SYSTEM if operator_is_system else OperatorType.USER,
            operator_name=operator,
        ),
        session=session,
    )


__all__ = [
    "CAPABILITY_PROBED",
    "CAPABILITY_STALE",
    "COMMIT_COMPLETED",
    "COMMIT_FAILED",
    "COMMIT_NEEDS_MANUAL_CHECK",
    "COMMIT_STARTED",
    "CONNECTION_TESTED",
    "CREDENTIAL_REF_CHANGED",
    "DELETE_EXECUTED",
    "DISCOVERY_COMPLETED",
    "ENV_LOCK_ACQUIRED",
    "ENV_LOCK_RELEASED",
    "ENVIRONMENT_CREATED",
    "ENVIRONMENT_DISABLED",
    "ENVIRONMENT_LOCK_CHANGED",
    "ENVIRONMENT_UPDATED",
    "FIXTURE_IMPORTED",
    "MAINTENANCE_EXECUTED",
    "MANUAL_CHECK_RESOLVED",
    "POST_VERIFY_COMPLETED",
    "SNAPSHOT_CREATED",
    "VALIDATE_COMPLETED",
    "VALIDATE_STARTED",
    "record",
]
