"""Audit hash chain (FD-AUD-004, FD-AUD-005).

A single global chain links every audit record by its committed sequence number:

    hash = SHA-256(prevHash ‖ canonical(record content))

Content is the immutable column set (everything except ``seq``/``prevHash``/
``hash``).  Linking runs *after* commit in a short transaction so business
transactions never serialise on the chain head; :func:`verify_chain` recomputes
the chain and reports the first break.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factory_foundation.common.hashing import canonical_json, sha256_hex
from factory_foundation.models.audit import AuditChainStateRow, AuditEventRow

GENESIS = ""  # prevHash for the first linked record


def canonical_record_content(row: AuditEventRow) -> bytes:
    payload: dict[str, Any] = {
        "id": row.id,
        "occurredAt": row.occurred_at.isoformat(),
        "operatorType": row.operator_type,
        "operatorName": row.operator_name,
        "sourceSubsystem": row.source_subsystem,
        "eventType": row.event_type,
        "objectType": row.object_type,
        "objectId": row.object_id,
        "projectId": row.project_id,
        "before": row.before_summary,
        "after": row.after_summary,
        "correlationId": row.correlation_id,
        "redacted": row.redacted,
    }
    return canonical_json(payload)


def compute_hash(prev_hash: str | None, content: bytes) -> str:
    base = (prev_hash or GENESIS).encode("utf-8")
    return f"sha256:{sha256_hex(base + content)}"


def hash_record(row: AuditEventRow, prev_hash: str | None) -> str:
    return compute_hash(prev_hash, canonical_record_content(row))


@dataclass(frozen=True)
class ChainState:
    last_linked_seq: int
    last_hash: str | None


def load_or_init_chain_state(session: Session) -> ChainState:
    state = session.get(AuditChainStateRow, "global")
    if state is None:
        state = AuditChainStateRow(key="global", last_linked_seq=0, last_hash=None)
        session.add(state)
        session.flush()
    return ChainState(state.last_linked_seq, state.last_hash)


def _iter_pending(session: Session) -> Iterator[AuditEventRow]:
    """Unlinked records in link order: (occurred_at, id), locked to one linker."""
    stmt = (
        select(AuditEventRow)
        .where(AuditEventRow.seq.is_(None))
        .order_by(AuditEventRow.occurred_at.asc(), AuditEventRow.id.asc())
        .with_for_update()
    )
    yield from session.execute(stmt).scalars()


def link_batch(session: Session, *, limit: int | None = None) -> int:
    """Link pending records into the chain.  Returns how many were linked."""
    state_row = session.get(AuditChainStateRow, "global")
    if state_row is None:
        state_row = AuditChainStateRow(key="global", last_linked_seq=0, last_hash=None)
        session.add(state_row)
        session.flush()

    last_seq = state_row.last_linked_seq
    last_hash = state_row.last_hash
    count = 0
    for row in _iter_pending(session):
        if limit is not None and count >= limit:
            break
        last_seq += 1
        record_hash = hash_record(row, last_hash)
        row.seq = last_seq
        row.prev_hash = last_hash if last_hash is not None else GENESIS
        row.hash = record_hash
        last_hash = record_hash
        count += 1
    state_row.last_linked_seq = last_seq
    state_row.last_hash = last_hash
    session.flush()
    return count


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_break_seq: int | None = None
    reason: str | None = None  # "content_changed" | "record_missing" | "link_missing"
    detail: str | None = None


def _iter_linked(session: Session, start: int, end: int | None) -> Iterator[AuditEventRow]:
    stmt = select(AuditEventRow).where(AuditEventRow.seq >= start).order_by(AuditEventRow.seq.asc())
    if end is not None:
        stmt = stmt.where(AuditEventRow.seq <= end)
    yield from session.execute(stmt).yield_per(500).scalars()


def verify_chain(
    session: Session, *, start_seq: int = 1, end_seq: int | None = None
) -> VerifyResult:
    """Recompute and validate the chain over ``[start_seq, end_seq]`` (FD-AUD-005).

    ``end_seq`` defaults to the current chain head (full-chain verification).  A
    missing sequence number *within* the range is reported as ``record_missing``;
    reaching the head cleanly returns ``ok``.
    """
    if end_seq is None:
        max_seq = session.execute(
            select(func.max(AuditEventRow.seq)).where(AuditEventRow.seq.is_not(None))
        ).scalar_one_or_none()
        if max_seq is None:
            return VerifyResult(ok=True, checked=0)
        end_seq = int(max_seq)
    if start_seq > end_seq:
        return VerifyResult(ok=True, checked=0)

    # the prev hash that the record at start_seq should carry
    expected_prev = session.execute(
        select(AuditEventRow.hash).where(AuditEventRow.seq == start_seq - 1)
    ).scalar_one_or_none()
    if start_seq == 1:
        expected_prev = GENESIS
    elif expected_prev is None:
        return VerifyResult(
            ok=False,
            checked=0,
            first_break_seq=start_seq,
            reason="record_missing",
            detail=f"no predecessor record at seq {start_seq - 1}",
        )

    rows = {r.seq: r for r in _iter_linked(session, start_seq, end_seq)}
    checked = 0
    for current_seq in range(start_seq, end_seq + 1):
        present = rows.get(current_seq)
        if present is None:
            return VerifyResult(
                ok=False,
                checked=checked,
                first_break_seq=current_seq,
                reason="record_missing",
                detail=f"no audit record with seq {current_seq}",
            )
        if present.hash is None or present.prev_hash is None:
            return VerifyResult(
                ok=False,
                checked=checked,
                first_break_seq=current_seq,
                reason="link_missing",
                detail=f"record {present.id} has no link fields",
            )
        if present.prev_hash != expected_prev:
            return VerifyResult(
                ok=False,
                checked=checked,
                first_break_seq=current_seq,
                reason="content_changed",
                detail="prevHash does not match the previous record's hash",
            )
        recomputed = hash_record(present, present.prev_hash)
        if recomputed != present.hash:
            return VerifyResult(
                ok=False,
                checked=checked,
                first_break_seq=current_seq,
                reason="content_changed",
                detail=f"stored hash differs from recomputed for record {present.id}",
            )
        # the next record must chain from this one's hash
        expected_prev = present.hash
        checked += 1
    return VerifyResult(ok=True, checked=checked)
