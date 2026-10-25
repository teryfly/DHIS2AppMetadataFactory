"""Audit service: write, query, link and verify (FD-AUD).

* :func:`audit` writes a record inside the *caller's* transaction; a failure
  rolls the whole transaction back (FD-AUD-001, NFR-REL-005).  Before writing,
  credential features are detected and masked (FD-AUD-007).
* :func:`link_pending` performs post-commit hash chaining (FD-AUD-004).
* :func:`verify_chain` / :func:`run_full_chain_verify` provide integrity checks
  (FD-AUD-005, FD-AUD-006).
* There is deliberately no delete/update API — records are append-only
  (FD-AUD-003, FD-AUD-009).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factory_foundation.artifacts.dto import Page
from factory_foundation.audit import chain
from factory_foundation.audit.chain import VerifyResult
from factory_foundation.audit.dto import AuditEventInput, AuditQuery, AuditRecord
from factory_foundation.audit.registry import require_registered
from factory_foundation.common.clock import Clock, SystemClock
from factory_foundation.common.context import current_context_or_default
from factory_foundation.common.errors import NotFoundError
from factory_foundation.common.logging import get_logger, log_with
from factory_foundation.common.redaction import redact_structure
from factory_foundation.db.uow import current_session, resolve_session, unit_of_work
from factory_foundation.enums import OperatorType
from factory_foundation.models.audit import AuditEventRow

_logger = get_logger("factory_foundation.audit")


def _record_from_row(row: AuditEventRow) -> AuditRecord:
    return AuditRecord(
        id=row.id,
        seq=row.seq,
        occurred_at=row.occurred_at,
        operator_type=row.operator_type,
        operator_name=row.operator_name,
        source_subsystem=row.source_subsystem,
        event_type=row.event_type,
        object_type=row.object_type,
        object_id=row.object_id,
        project_id=row.project_id,
        before=row.before_summary,
        after=row.after_summary,
        correlation_id=row.correlation_id,
        redacted=row.redacted,
        prev_hash=row.prev_hash,
        hash=row.hash,
    )


def audit(
    event: AuditEventInput,
    *,
    session: Session | None = None,
    clock: Clock | None = None,
) -> AuditRecord:
    """Append an audit record in the caller's unit of work (FD-AUD-001/002/007)."""
    sess = session if session is not None else current_session()
    obj_clock = clock or SystemClock()
    ctx = current_context_or_default()

    require_registered(event.event_type, sess)

    before_out, before_red = _redact(event.before)
    after_out, after_red = _redact(event.after)
    redacted = before_red or after_red
    if redacted:
        log_with(
            _logger,
            30,  # WARNING
            "audit event contained credential features; masked before write (FD-AUD-007)",
            eventType=event.event_type,
            objectId=event.object_id,
        )

    operator_type = event.operator_type or OperatorType(ctx.operator_type)
    row = AuditEventRow(
        id=_new_audit_id(),
        occurred_at=event.occurred_at or obj_clock.now(),
        operator_type=operator_type.value,
        operator_name=event.operator_name or ctx.operator_name,
        source_subsystem=event.source_subsystem or ctx.source_subsystem,
        event_type=event.event_type,
        object_type=event.object_type,
        object_id=event.object_id,
        project_id=event.project_id or ctx.project_id,
        before_summary=before_out,
        after_summary=after_out,
        correlation_id=event.correlation_id or ctx.correlation_id,
        redacted=redacted,
        seq=None,
        prev_hash=None,
        hash=None,
    )
    sess.add(row)
    sess.flush()
    return _record_from_row(row)


def _new_audit_id() -> str:
    from factory_foundation.common.ids import new_id

    return new_id("aud")


def _redact(summary: dict[str, Any] | None) -> tuple[dict[str, Any] | None, bool]:
    if summary is None:
        return None, False
    result = redact_structure(summary)
    return (result.value if isinstance(result.value, dict) else summary), result.redacted


def get_audit(event_id: str, session: Session | None = None) -> AuditRecord:
    with resolve_session(session) as sess:
        row = sess.get(AuditEventRow, event_id)
        if row is None:
            raise NotFoundError(f"audit record not found: {event_id}")
        return _record_from_row(row)


def query_audit(query: AuditQuery, session: Session | None = None) -> Page[AuditRecord]:
    """Filtered, paginated audit query (FD-AUD-008)."""
    with resolve_session(session) as sess:
        stmt = select(AuditEventRow)
        count_stmt = select(func.count()).select_from(AuditEventRow)
        conds = []
        if query.project_id:
            conds.append(AuditEventRow.project_id == query.project_id)
        if query.object_type:
            conds.append(AuditEventRow.object_type == query.object_type)
        if query.object_id:
            conds.append(AuditEventRow.object_id == query.object_id)
        if query.event_type:
            conds.append(AuditEventRow.event_type == query.event_type)
        if query.operator_name:
            conds.append(AuditEventRow.operator_name == query.operator_name)
        if query.correlation_id:
            conds.append(AuditEventRow.correlation_id == query.correlation_id)
        if query.occurred_after:
            conds.append(AuditEventRow.occurred_at >= query.occurred_after)
        if query.occurred_before:
            conds.append(AuditEventRow.occurred_at <= query.occurred_before)
        for c in conds:
            stmt = stmt.where(c)
            count_stmt = count_stmt.where(c)
        total = sess.execute(count_stmt).scalar_one()
        stmt = (
            stmt.order_by(AuditEventRow.occurred_at.desc()).limit(query.limit).offset(query.offset)
        )
        rows = sess.execute(stmt).scalars().all()
        items = [_record_from_row(r) for r in rows]
        return Page[AuditRecord](items=items, total=total, limit=query.limit, offset=query.offset)


# --- Chaining & integrity --------------------------------------------------


def link_pending(*, limit: int | None = None) -> int:
    """Link committed-but-unlinked records into the chain (FD-AUD-004).

    Runs in its own short transaction; scheduled by S1 so the delay from commit
    to link stays within 10 seconds.
    """
    with unit_of_work() as sess:
        return chain.link_batch(sess, limit=limit)


def verify_chain(
    *, start_seq: int = 1, end_seq: int | None = None, session: Session | None = None
) -> VerifyResult:
    """Validate the hash chain over a range or the full chain (FD-AUD-005)."""
    with resolve_session(session) as sess:
        return chain.verify_chain(sess, start_seq=start_seq, end_seq=end_seq)


def run_full_chain_verify(
    *, now: datetime | None = None, session: Session | None = None
) -> VerifyResult:
    """Full-chain verification that writes its outcome to the audit log
    (FD-AUD-006).  Intended to be scheduled daily by S1."""
    with resolve_session(session) as sess:
        result = chain.verify_chain(sess)
    if session is None:
        with unit_of_work() as sess2:
            audit(
                AuditEventInput(
                    event_type="core.audit.chain_verified",
                    object_type="audit_chain",
                    object_id="global",
                    after={
                        "ok": result.ok,
                        "checked": result.checked,
                        "firstBreakSeq": result.first_break_seq,
                        "reason": result.reason,
                    },
                    source_subsystem="S0",
                    occurred_at=now,
                ),
                session=sess2,
            )
    else:
        audit(
            AuditEventInput(
                event_type="core.audit.chain_verified",
                object_type="audit_chain",
                object_id="global",
                after={
                    "ok": result.ok,
                    "checked": result.checked,
                    "firstBreakSeq": result.first_break_seq,
                    "reason": result.reason,
                },
                source_subsystem="S0",
                occurred_at=now,
            ),
            session=session,
        )
    return result
