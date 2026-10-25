"""Audit helper (AI-POL-008, 00 doc 6.6 / D-11).

S3 writes audit through S0 so the record joins the caller's transaction and the hash
chain: an audit row that commits separately from the state change it describes is the
failure mode D-11 exists to prevent.  This module exists so the ``source_subsystem`` and
operator wiring is stated once instead of at each of the sixteen call sites.
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import AuditEventInput, OperatorType, audit
from sqlalchemy.orm import Session

_EVENT_PREFIX = "ai."


def record(
    event_type: str,
    *,
    session: Session,
    object_type: str | None = None,
    object_id: str | None = None,
    project_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    operator: str | None = None,
    operator_is_user: bool = False,
) -> None:
    """Append one ``ai.*`` event in the caller's transaction.

    ``operator_is_user`` distinguishes a human action (a relaxation, a prompt activation)
    from the runtime's own bookkeeping, which is what D-16 records while the system has
    no authentication: the *name* is the identity, and its kind must not be lost.
    """
    if not event_type.startswith(_EVENT_PREFIX):
        raise ValueError(f"S3 may only write ai-namespaced events, got {event_type!r}")
    audit(
        AuditEventInput(
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            project_id=project_id,
            before=before,
            after=after,
            source_subsystem="S3",
            operator_type=OperatorType.USER if operator_is_user else OperatorType.SYSTEM,
            operator_name=operator,
        ),
        session=session,
    )


__all__ = ["record"]
