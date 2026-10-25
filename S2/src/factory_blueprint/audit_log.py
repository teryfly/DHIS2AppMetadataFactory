"""Audit writes for S2 events (S2 SRS appendix C, D-11).

The record is written in the caller's transaction: if it fails, the business
change rolls back with it (FD-AUD-001).  Operator identity comes from the
operation context S0 carries, which is how ``X-Operator`` reaches the audit trail.
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import AuditEventInput, OperatorType, audit, current_context_or_default
from sqlalchemy.orm import Session

SOURCE_SUBSYSTEM = "S2"


def record(
    event_type: str,
    *,
    object_type: str,
    object_id: str,
    project_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    operator_type: OperatorType | None = None,
    operator_name: str | None = None,
    session: Session | None = None,
) -> None:
    context = current_context_or_default()
    audit(
        AuditEventInput(
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            project_id=project_id if project_id is not None else context.project_id,
            before=before,
            after=after,
            source_subsystem=SOURCE_SUBSYSTEM,
            operator_type=operator_type or OperatorType(context.operator_type),
            operator_name=operator_name or context.operator_name,
            correlation_id=context.correlation_id,
        ),
        session=session,
    )
