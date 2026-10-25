"""UID pool: replenish outside the transaction, take numbers inside it (BP-REG-008a).

Calling DHIS2 to mint UIDs is external I/O and cannot happen in S1's transaction,
while UID assignment must be atomic with the patch that needs it.  The pool
reconciles the two: batches are fetched ahead of time from the design reference
environment, and allocation only takes rows out of ``bp.uid_pool``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from factory_foundation.api import external_io, unit_of_work
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from factory_blueprint.deps import get_dhis2
from factory_blueprint.dto.registry import UidPoolStatus
from factory_blueprint.enums import UidAllocationStatus
from factory_blueprint.errors import UidPoolExhausted
from factory_blueprint.models.uid import UidAllocationRow, UidPoolRow

UID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{10}$")
DEFAULT_BATCH_SIZE = 200
DEFAULT_LOW_WATER_MARK = 50


def available_count(session: Session, project_id: str) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(UidPoolRow).where(UidPoolRow.project_id == project_id)
        ).scalar_one()
    )


def pool_status(
    session: Session,
    project_id: str,
    *,
    low_water_mark: int = DEFAULT_LOW_WATER_MARK,
    source_environment: str | None = None,
) -> UidPoolStatus:
    available = available_count(session, project_id)
    return UidPoolStatus(
        project_id=project_id,
        available=available,
        low_water_mark=low_water_mark,
        source_environment=source_environment,
        needs_replenishment=available <= low_water_mark,
    )


def _already_known(session: Session, uids: list[str]) -> set[str]:
    """Every UID this Factory has already issued or holds in any project pool."""
    allocated = set(
        session.execute(
            select(UidAllocationRow.uid).where(UidAllocationRow.uid.in_(uids))
        ).scalars()
    )
    pooled = set(
        session.execute(select(UidPoolRow.uid).where(UidPoolRow.uid.in_(uids))).scalars()
    )
    return allocated | pooled


@external_io
def replenish(
    project_id: str,
    environment_key: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    low_water_mark: int = DEFAULT_LOW_WATER_MARK,
    min_available: int | None = None,
    fetched_at: datetime | None = None,
) -> int:
    """Fetch a batch of UIDs from DHIS2 through S4 and add them to the pool.

    Returns the pool size afterwards.  UIDs that fail the shape rule, or that the
    Factory has already issued, are discarded rather than pooled.
    """
    with unit_of_work() as session:
        current = available_count(session, project_id)
    target = min_available if min_available is not None else low_water_mark
    needed = max(batch_size, target - current, 0)
    if needed <= 0:
        return current

    issued = get_dhis2().generate_uids(environment_key, needed)
    moment = fetched_at or datetime.now(tz=UTC)
    with unit_of_work() as session:
        known = _already_known(session, issued)
        fresh: list[str] = []
        seen: set[str] = set()
        for uid in issued:
            if not UID_PATTERN.match(uid) or uid in known or uid in seen:
                continue
            seen.add(uid)
            fresh.append(uid)
        for uid in fresh:
            session.add(
                UidPoolRow(
                    uid=uid,
                    project_id=project_id,
                    source_environment=environment_key,
                    fetched_at=moment,
                )
            )
        session.flush()
        return available_count(session, project_id)


def ensure_available(session: Session, project_id: str, count: int) -> None:
    """BP-REG-008a.3: refuse the patch with a retryable error when the pool is dry."""
    available = available_count(session, project_id)
    if available < count:
        raise UidPoolExhausted(
            f"UID pool holds {available} of {count} needed UIDs; replenish the pool and retry",
            details={"projectId": project_id, "needed": count, "available": available},
        )


def take(session: Session, project_id: str, count: int) -> list[tuple[str, str]]:
    """Remove and return ``(uid, sourceEnvironment)`` pairs in fetch order (FIFO).

    The source environment travels with the UID so the allocation ledger records
    which instance minted it (BP-REG-008a), which drift checks later rely on.
    """
    ensure_available(session, project_id, count)
    rows = session.execute(
        select(UidPoolRow)
        .where(UidPoolRow.project_id == project_id)
        .order_by(UidPoolRow.fetched_at, UidPoolRow.uid)
        .limit(count)
        .with_for_update(skip_locked=True)
    ).scalars().all()
    if len(rows) < count:
        raise UidPoolExhausted(
            f"UID pool ran out while allocating for {project_id}",
            details={"projectId": project_id, "needed": count, "taken": len(rows)},
        )
    taken = [(row.uid, row.source_environment) for row in rows]
    session.execute(delete(UidPoolRow).where(UidPoolRow.uid.in_([uid for uid, _ in taken])))
    session.flush()
    return taken


def record_allocation(
    session: Session,
    *,
    uid: str,
    project_id: str,
    business_key: str,
    dhis2_type: str,
    source_environment: str,
    allocated_at: datetime,
    patch_id: str | None = None,
) -> UidAllocationRow:
    row = UidAllocationRow(
        uid=uid,
        project_id=project_id,
        business_key=business_key,
        dhis2_type=dhis2_type,
        source_environment=source_environment,
        status=UidAllocationStatus.ACTIVE.value,
        allocated_at=allocated_at,
        patch_id=patch_id,
    )
    session.add(row)
    session.flush()
    return row


def retire(session: Session, uids: list[str], *, retired_at: datetime | None = None) -> int:
    """BP-REG-006: a rejected or discarded patch's UIDs are retired forever."""
    if not uids:
        return 0
    active = list(
        session.execute(
            select(UidAllocationRow.uid).where(
                UidAllocationRow.uid.in_(uids),
                UidAllocationRow.status == UidAllocationStatus.ACTIVE.value,
            )
        ).scalars()
    )
    if active:
        session.execute(
            update(UidAllocationRow)
            .where(UidAllocationRow.uid.in_(active))
            .values(status=UidAllocationStatus.RETIRED.value, retired_at=retired_at)
        )
        session.flush()
    return len(active)


def allocation_for(session: Session, project_id: str, business_key: str) -> UidAllocationRow | None:
    """The live allocation of one concept; retired history stays in the ledger."""
    return session.execute(
        select(UidAllocationRow)
        .where(
            UidAllocationRow.project_id == project_id,
            UidAllocationRow.business_key == business_key,
            UidAllocationRow.status == UidAllocationStatus.ACTIVE.value,
        )
        .order_by(UidAllocationRow.allocated_at.desc())
    ).scalars().first()


def is_reusable(session: Session, uid: str) -> bool:
    """A UID ever issued by this Factory may never be issued again (FR-REG-004)."""
    return session.execute(
        select(UidAllocationRow.uid).where(UidAllocationRow.uid == uid)
    ).scalar_one_or_none() is None
