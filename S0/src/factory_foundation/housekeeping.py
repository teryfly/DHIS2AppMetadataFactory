"""Housekeeping (API: ``run_housekeeping``; FD-ASM-02).

S1 schedules these; S0 only provides the functions.  It covers the three duties
named in the public API table:

1. expired staging content (FD-ART-005 — 24h unregistered handles);
2. pending object deletions — content of already-purged artifacts that a previous
   delete failed to remove (retry);
3. expired idempotency records (FD-IDEM-003, excluding ``dx.execution``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from factory_foundation.artifacts.service import _key_from_location, cleanup_expired_staging
from factory_foundation.db.uow import has_active_uow, unit_of_work
from factory_foundation.idempotency.service import idempotency
from factory_foundation.models.artifact import ArtifactRow
from factory_foundation.objectstore.provider import get_object_store


@dataclass
class HousekeepingResult:
    staging_removed: list[str] = field(default_factory=list)
    deletions_retried: list[str] = field(default_factory=list)
    idempotency_removed: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stagingRemoved": self.staging_removed,
            "deletionsRetried": self.deletions_retried,
            "idempotencyRemoved": [list(t) for t in self.idempotency_removed],
            "warnings": self.warnings,
        }


def retry_pending_deletions() -> list[str]:
    """Delete object content still present for artifacts marked purged."""
    store = get_object_store()
    retried: list[str] = []
    with unit_of_work() as sess:
        rows = (
            sess.execute(
                select(ArtifactRow).where(ArtifactRow.content_purged == True)  # noqa: E712
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.content_location.startswith("purged://"):
                continue
            key = _key_from_location(row.content_location)
            try:
                if store.exists(key):
                    store.delete(key)
                    retried.append(row.id)
            except Exception as exc:  # noqa: BLE001 - keep sweeping; S1 retries next run
                retried.append(f"{row.id}: {exc}")
    return retried


def run_housekeeping(*, now: datetime | None = None) -> HousekeepingResult:
    if has_active_uow():
        from factory_foundation.common.errors import TransactionMisuseError

        raise TransactionMisuseError("run_housekeeping must not be called inside a unit of work")
    from factory_foundation.common.clock import SystemClock

    moment = now or SystemClock().now()
    result = HousekeepingResult()
    result.staging_removed = cleanup_expired_staging(moment)
    result.deletions_retried = retry_pending_deletions()
    result.idempotency_removed = idempotency.cleanup_expired(moment)
    return result
