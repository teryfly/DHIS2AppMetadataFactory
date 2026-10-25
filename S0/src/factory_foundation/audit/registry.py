"""Audit event-type registry (FD-AUD-010).

Event types follow the ``<subsystem>.<object>.<action>`` convention (e.g.
``cp.gate.approved``).  Each subsystem registers its own types at start-up;
writing an unregistered type is rejected so the mandatory audit events of overall
SRS 21.4 can be checked for coverage.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_foundation.common.errors import FactoryError
from factory_foundation.db.uow import resolve_session, writable_session
from factory_foundation.models.registry import AuditEventTypeRow

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+\.[a-z0-9_]+$")

# S0-owned event types (registered by initialize_foundation).
BUILTIN_AUDIT_EVENT_TYPES: dict[str, str] = {
    "core.artifact.created": "S0",
    "core.artifact.superseded": "S0",
    "core.artifact.purged": "S0",
    "core.artifact.retention": "S0",
    "core.audit.chain_verified": "S0",
}


def register_audit_event_types(
    event_types: dict[str, str] | Sequence[str],
    *,
    owner_subsystem: str | None = None,
    session: Session | None = None,
) -> None:
    """Register event types (``{type: owner}`` or a list with a common owner)."""
    pairs: Iterable[tuple[str, str]]
    if isinstance(event_types, dict):
        pairs = list(event_types.items())
    else:
        if owner_subsystem is None:
            raise FactoryError(
                "owner_subsystem is required when registering a list of event types",
                code="VALIDATION_ERROR",
            )
        pairs = [(t, owner_subsystem) for t in event_types]

    with writable_session(session) as sess:
        for event_type, owner in pairs:
            if not _EVENT_TYPE_RE.match(event_type):
                raise FactoryError(
                    f"event type {event_type!r} does not follow subsystem.object.action",
                    code="VALIDATION_ERROR",
                )
            row = sess.get(AuditEventTypeRow, event_type)
            if row is None:
                sess.add(AuditEventTypeRow(event_type=event_type, owner_subsystem=owner))
        sess.flush()


def list_audit_event_types(session: Session | None = None) -> list[tuple[str, str]]:
    with resolve_session(session) as sess:
        rows = (
            sess.execute(select(AuditEventTypeRow).order_by(AuditEventTypeRow.event_type))
            .scalars()
            .all()
        )
        return [(r.event_type, r.owner_subsystem) for r in rows]


def is_registered_event_type(event_type: str, session: Session | None = None) -> bool:
    with resolve_session(session) as sess:
        return sess.get(AuditEventTypeRow, event_type) is not None


def require_registered(event_type: str, session: Session) -> None:
    if session.get(AuditEventTypeRow, event_type) is None:
        raise FactoryError(
            f"unregistered audit event type: {event_type}",
            code="VALIDATION_ERROR",
            details={"eventType": event_type},
        )
