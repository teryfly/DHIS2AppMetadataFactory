"""Idempotency store (FD-IDEM).

Records a side-effecting call keyed by ``(scope, key)`` with a request digest.
Re-invoking with the same key and digest returns the first result (or an
in-progress marker); the same key with a *different* digest is a conflict
(FD-IDEM-002, FD-AC-06).  ``dx.execution`` records never expire (FD-IDEM-003,
FD-BR-08) so a DHIS2 COMMIT can never be double-submitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_foundation.common.clock import SystemClock
from factory_foundation.common.errors import IdempotencyConflictError, NotFoundError
from factory_foundation.common.hashing import normalize_hash
from factory_foundation.db.uow import has_active_uow, resolve_session, unit_of_work
from factory_foundation.enums import DHIS2_EXECUTION_SCOPE, IdempotencyStatus
from factory_foundation.models.idempotency import IdempotencyRecordRow

# Scopes whose records must never be deleted by expiry cleanup.
NON_EXPIRING_SCOPES = {DHIS2_EXECUTION_SCOPE}


@dataclass(frozen=True)
class IdemRecord:
    scope: str
    key: str
    status: IdempotencyStatus
    request_digest: str
    result_ref: dict[str, Any] | None
    error_code: str | None
    expires_at: datetime | None


@dataclass(frozen=True)
class BeginResult:
    created: bool
    status: IdempotencyStatus
    record: IdemRecord
    already_completed: bool

    @property
    def first_result(self) -> dict[str, Any] | None:
        return self.record.result_ref


def _to_record(row: IdempotencyRecordRow) -> IdemRecord:
    return IdemRecord(
        scope=row.scope,
        key=row.key,
        status=IdempotencyStatus(row.status),
        request_digest=row.request_digest,
        result_ref=row.result_ref,
        error_code=row.error_code,
        expires_at=row.expires_at,
    )


class IdempotencyStore:
    def begin(
        self,
        scope: str,
        key: str,
        request_digest: str,
        *,
        expires_at: datetime | None = None,
        created_at: datetime | None = None,
        session: Session | None = None,
    ) -> BeginResult:
        digest = normalize_hash(request_digest)
        now = created_at or SystemClock().now()
        if session is not None or has_active_uow():
            with resolve_session(session) as sess:
                return self._begin_in(sess, scope, key, digest, expires_at, now)
        with unit_of_work() as sess:
            return self._begin_in(sess, scope, key, digest, expires_at, now)

    def _begin_in(
        self,
        sess: Session,
        scope: str,
        key: str,
        digest: str,
        expires_at: datetime | None,
        now: datetime,
    ) -> BeginResult:
        row = (
            sess.execute(
                select(IdempotencyRecordRow)
                .where(IdempotencyRecordRow.scope == scope, IdempotencyRecordRow.key == key)
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if row is not None:
            if row.request_digest != digest:
                raise IdempotencyConflictError(
                    f"idempotency key {scope}/{key} already used with a different request digest",
                    details={"scope": scope, "key": key},
                )
            status = IdempotencyStatus(row.status)
            return BeginResult(
                created=False,
                status=status,
                record=_to_record(row),
                already_completed=status is IdempotencyStatus.COMPLETED,
            )
        new = IdempotencyRecordRow(
            scope=scope,
            key=key,
            request_digest=digest,
            status=IdempotencyStatus.IN_PROGRESS.value,
            result_ref=None,
            created_at=now,
            expires_at=None if scope in NON_EXPIRING_SCOPES else expires_at,
        )
        sess.add(new)
        sess.flush()
        return BeginResult(
            created=True,
            status=IdempotencyStatus.IN_PROGRESS,
            record=_to_record(new),
            already_completed=False,
        )

    def complete(
        self,
        scope: str,
        key: str,
        result_ref: dict[str, Any] | None = None,
        *,
        session: Session | None = None,
    ) -> IdemRecord:
        def _do(sess: Session) -> IdemRecord:
            row = self._get(sess, scope, key)
            row.status = IdempotencyStatus.COMPLETED.value
            row.result_ref = result_ref
            row.error_code = None
            sess.flush()
            return _to_record(row)

        if session is not None or has_active_uow():
            with resolve_session(session) as sess:
                return _do(sess)
        with unit_of_work() as sess:
            return _do(sess)

    def fail(
        self,
        scope: str,
        key: str,
        *,
        error_code: str | None = None,
        session: Session | None = None,
    ) -> IdemRecord:
        def _do(sess: Session) -> IdemRecord:
            row = self._get(sess, scope, key)
            row.status = IdempotencyStatus.FAILED.value
            row.error_code = error_code
            sess.flush()
            return _to_record(row)

        if session is not None or has_active_uow():
            with resolve_session(session) as sess:
                return _do(sess)
        with unit_of_work() as sess:
            return _do(sess)

    def get(self, scope: str, key: str, session: Session | None = None) -> IdemRecord:
        with resolve_session(session) as sess:
            return _to_record(self._get(sess, scope, key))

    def _get(self, sess: Session, scope: str, key: str) -> IdempotencyRecordRow:
        row = (
            sess.execute(
                select(IdempotencyRecordRow).where(
                    IdempotencyRecordRow.scope == scope, IdempotencyRecordRow.key == key
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise NotFoundError(f"no idempotency record for {scope}/{key}")
        return row

    def cleanup_expired(
        self, now: datetime, *, session: Session | None = None
    ) -> list[tuple[str, str]]:
        """Delete expired records except non-expiring scopes (FD-IDEM-003)."""

        def _do(sess: Session) -> list[tuple[str, str]]:
            rows = (
                sess.execute(
                    select(IdempotencyRecordRow).where(
                        IdempotencyRecordRow.expires_at.is_not(None),
                        IdempotencyRecordRow.expires_at < now,
                    )
                )
                .scalars()
                .all()
            )
            removed: list[tuple[str, str]] = []
            for row in rows:
                if row.scope in NON_EXPIRING_SCOPES:
                    continue
                removed.append((row.scope, row.key))
                sess.delete(row)
            sess.flush()
            return removed

        if session is not None or has_active_uow():
            with resolve_session(session) as sess:
                return _do(sess)
        with unit_of_work() as sess:
            return _do(sess)


idempotency = IdempotencyStore()


def cleanup_expired_idempotency(
    now: datetime, *, session: Session | None = None
) -> list[tuple[str, str]]:
    """Top-level alias of :meth:`IdempotencyStore.cleanup_expired` (SRS §6)."""
    return idempotency.cleanup_expired(now, session=session)
