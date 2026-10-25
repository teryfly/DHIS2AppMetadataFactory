"""Event stream records (CP-EVT-001 / 004 / 006).

One row per state change, numbered monotonically *per workflow*.  The sequence is
allocated by locking the workflow row, so concurrent transactions on the same
workflow serialise on it and the numbers stay commit-ordered (总体 SRS FR-IF-005).

``append`` writes inside the caller's unit of work and, in that same transaction,
issues ``pg_notify``.  PostgreSQL defers a transactional NOTIFY until commit and
drops it if the transaction rolls back, which is exactly the "no phantom events"
property CP-EVT-005 needs — no post-commit callback and no second connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factory_control.config import get_settings
from factory_control.foundation import (
    ArtifactInfo,
    DataClassification,
    FactoryError,
    NotFoundError,
    new_id,
    utcnow,
)
from factory_control.models import EventStreamRecord, Workflow

#: The NOTIFY/LISTEN channel is configuration, not a constant: a deployment that renames it has
#: to have both sides move together (CP-EVT-005).  ``emit_notify`` reads it through
#: :func:`notify_channel` on every call, and the LISTEN side reads the same setting in
#: :func:`factory_control.api._attach_listener`.

_EVENT_TYPES = frozenset(
    {
        "workflow.started",
        "workflow.paused",
        "workflow.resumed",
        "workflow.completed",
        "workflow.failed",
        "workflow.updated",
        "stage.started",
        "stage.waiting_input",
        "stage.waiting_approval",
        "stage.passed",
        "stage.failed",
        "stage.blocked",
        "stage.invalidated",
        "stage.manual_check",
        "stage.rerun",
        "agent.started",
        "agent.completed",
        "agent.progress",
        "question.created",
        "question.answered",
        "gate.created",
        "gate.approved",
        "gate.rejected",
        "gate.invalidated",
        "artifact.created",
        "artifact.superseded",
        # CP-API-012: the parse is claimed in the request and finished on a background
        # thread, so its start and its outcome are both pushed rather than discovered by
        # refreshing the inputs list.
        "input.parse",
        "validation.completed",
        "execution.started",
        "execution.progress",
        "execution.completed",
        "execution.failed",
        "deployment_run.created",
        "deployment_run.completed",
        "deployment_run.failed",
        "deployment_run.cancelled",
        "deployment_run.outdated",
        # CP-UI-013: a drift re-check is an action a user took, and its verdict is a fact
        # the console shows — so it is pushed, not left for the next poll to discover.
        "deployment_run.drift_checked",
        "change_request.updated",
        "notification.created",
        "stream.reset",
    }
)


def _utc_iso(moment: datetime) -> str:
    """总体 SRS 19.2.3 shows ``occurredAt`` as UTC with a ``Z`` suffix, always."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    """An event as delivered to clients: the SSE frame body (总体 SRS 19.2.3)."""
    event_id: str
    workflow_id: str
    sequence: int
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any]

    def to_sse(self) -> str:
        import json

        body = {
            "eventType": self.event_type,
            "workflowId": self.workflow_id,
            "sequence": self.sequence,
            "occurredAt": _utc_iso(self.occurred_at),
            "payload": self.payload,
        }
        return f"id: {self.sequence}\nevent: {self.event_type}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"

    def as_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "workflowId": self.workflow_id,
            "sequence": self.sequence,
            "eventType": self.event_type,
            "occurredAt": _utc_iso(self.occurred_at),
            "payload": self.payload,
        }


class UnknownEventTypeError(FactoryError):
    code = "VALIDATION_ERROR"


class PayloadTooSensitiveError(FactoryError):
    """CP-EVT-006: RESTRICTED and PHI_PII content must never reach an event."""

    code = "POLICY_DENIED"


def _ensure_known(event_type: str) -> None:
    if event_type not in _EVENT_TYPES:
        raise UnknownEventTypeError(
            f"unknown event type: {event_type}", details={"eventType": event_type}
        )


def append(
    session: Session,
    *,
    workflow_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    retention_days: int = 7,
    occurred_at: datetime | None = None,
) -> Event:
    """Allocate the next sequence, insert the record and notify, in this transaction."""
    _ensure_known(event_type)
    when = occurred_at or utcnow()

    workflow = session.execute(
        select(Workflow).where(Workflow.id == workflow_id).with_for_update()
    ).scalars().first()
    if workflow is None:
        raise NotFoundError(
            f"cannot append event: unknown workflow {workflow_id}",
            details={"workflowId": workflow_id},
        )
    sequence = int(workflow.last_event_seq or 0) + 1
    workflow.last_event_seq = sequence

    record = EventStreamRecord(
        id=new_id("evt"),
        workflow_id=workflow_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=when,
        payload=dict(payload or {}),
        expires_at=when + timedelta(days=max(retention_days, 1)),
    )
    session.add(record)
    session.flush()
    emit_notify(session, workflow_id, sequence)
    return Event(
        event_id=record.id,
        workflow_id=workflow_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=when,
        payload=record.payload or {},
    )


def notify_channel() -> str:
    """The channel this process notifies on (CP-EVT-005).

    Read from :mod:`factory_control.config` on every call rather than frozen into a constant:
    a pinned one was how the NOTIFY side kept shouting into ``factory_cp_events`` while
    ``FACTORY_SSE_CHANNEL`` sent the LISTEN side somewhere else — pushes simply stopped, with no
    error anywhere, because the poll fallback kept the stream alive at ``sse_poll_seconds``.
    """
    return get_settings().sse_channel


def emit_notify(session: Session, workflow_id: str, sequence: int) -> None:
    """Transactional NOTIFY carrying only a pointer; listeners re-read the row.

    The payload stays far below PostgreSQL's 8 KiB limit — it names the workflow and the
    sequence, and the row is read back from ``cp.event_stream_records``.
    """
    from sqlalchemy import text

    session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": notify_channel(), "payload": f"{workflow_id}:{sequence}"},
    )


def read_after(
    session: Session, workflow_id: str, after_sequence: int, *, limit: int = 500
) -> list[Event]:
    """Rows with ``sequence > after_sequence`` in order — the SSE replay source."""
    rows = (
        session.execute(
            select(EventStreamRecord)
            .where(
                EventStreamRecord.workflow_id == workflow_id,
                EventStreamRecord.sequence > after_sequence,
            )
            .order_by(EventStreamRecord.sequence)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_to_event(r) for r in rows]


def oldest_retained_sequence(session: Session, workflow_id: str) -> int | None:
    """Lowest sequence still retained; ``None`` when the workflow has no events yet."""
    value = session.execute(
        select(func.min(EventStreamRecord.sequence)).where(
            EventStreamRecord.workflow_id == workflow_id
        )
    ).scalar()
    return None if value is None else int(value)


def purge_expired(session: Session, *, now: datetime, batch_limit: int = 5000) -> int:
    """CP-EVT-004 retention sweep; returns the number of rows removed."""
    from sqlalchemy import delete

    ids = (
        session.execute(
            select(EventStreamRecord.id)
            .where(EventStreamRecord.expires_at.is_not(None), EventStreamRecord.expires_at < now)
            .limit(batch_limit)
        )
        .scalars()
        .all()
    )
    if not ids:
        return 0
    session.execute(delete(EventStreamRecord).where(EventStreamRecord.id.in_(ids)))
    session.flush()
    return len(ids)


def payload_for_artifact(artifact: ArtifactInfo) -> dict[str, Any]:
    """Allow-listed projection of an artifact (CP-EVT-006)."""
    if artifact.data_classification.rank >= DataClassification.RESTRICTED.rank:
        raise PayloadTooSensitiveError(
            f"artifact {artifact.id} is {artifact.data_classification.value} and cannot be described in an event",
            artifact_id=artifact.id,
            details={"classification": artifact.data_classification.value},
        )
    return {
        "artifactId": artifact.id,
        "artifactType": artifact.type,
        "version": artifact.version,
        "status": artifact.status.value,
        "contentHash": artifact.content_hash,
    }


def _to_event(row: EventStreamRecord) -> Event:
    return Event(
        event_id=row.id,
        workflow_id=row.workflow_id,
        sequence=int(row.sequence),
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        payload=dict(row.payload or {}),
    )


__all__ = [
    "Event",
    "PayloadTooSensitiveError",
    "UnknownEventTypeError",
    "append",
    "emit_notify",
    "notify_channel",
    "oldest_retained_sequence",
    "payload_for_artifact",
    "purge_expired",
    "read_after",
]
