"""Artifact storage service (FD-ART).

Two-phase creation (FD-ART-005): :func:`stage_content` streams bytes to object
storage and verifies the SHA-256 (an *external I/O* function, run outside any
transaction), returning a staging handle; :func:`register_artifact` then records
the immutable metadata inside the caller's unit of work so "artifact + state
change + audit" share one transaction.

Content is immutable; a "modification" is a new artifact chained via
``prev_version_id`` and the previous version is marked SUPERSEDED (FD-ART-003,
FD-ART-007).  Classification may not be lower than the highest source unless a
redaction reference is supplied (FD-ART-008).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factory_foundation.artifacts.catalog import assert_type_owner
from factory_foundation.artifacts.dto import (
    ArtifactCreate,
    ArtifactInfo,
    ArtifactQuery,
    LineageResult,
    Page,
    StagingHandle,
)
from factory_foundation.common.clock import Clock, SystemClock
from factory_foundation.common.context import current_context_or_default
from factory_foundation.common.errors import (
    ArtifactError,
    ArtifactPurgedError,
    NotFoundError,
)
from factory_foundation.common.hashing import normalize_hash
from factory_foundation.common.ids import new_id
from factory_foundation.db.external_io import external_io
from factory_foundation.db.uow import (
    current_session,
    has_active_uow,
    resolve_session,
    unit_of_work,
    writable_session,
)
from factory_foundation.enums import ArtifactStatus, DataClassification
from factory_foundation.models.artifact import (
    ArtifactRow,
    ArtifactSourceRow,
    ArtifactStagingRow,
)
from factory_foundation.objectstore.base import build_object_path, key_from_location
from factory_foundation.objectstore.provider import get_object_store

#: staged content older than this is eligible for cleanup (FD-ART-005)
STAGING_TTL = timedelta(hours=24)


def _to_info(row: ArtifactRow, source_ids: list[str]) -> ArtifactInfo:
    return ArtifactInfo(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        type=row.type,
        version=row.version,
        status=ArtifactStatus(row.status),
        content_location=row.content_location,
        content_hash=row.content_hash,
        size=row.size,
        media_type=row.media_type,
        data_classification=DataClassification(row.data_classification),
        build_record=row.build_record,
        source_artifact_ids=source_ids,
        prev_version_id=row.prev_version_id,
        creator_subsystem=row.creator_subsystem,
        creator_operator=row.creator_operator,
        created_at=row.created_at,
        content_purged=row.content_purged,
        redaction_ref=row.redaction_ref,
    )


def _source_ids(artifact_id: str, session: Session) -> list[str]:
    rows = (
        session.execute(
            select(ArtifactSourceRow.source_artifact_id).where(
                ArtifactSourceRow.artifact_id == artifact_id
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def get_artifact(artifact_id: str, session: Session | None = None) -> ArtifactInfo:
    """Return artifact metadata (raising ``NOT_FOUND`` when absent)."""
    with resolve_session(session) as sess:
        row = sess.get(ArtifactRow, artifact_id)
        if row is None:
            raise NotFoundError(
                f"artifact not found: {artifact_id}", details={"artifactId": artifact_id}
            )
        return _to_info(row, _source_ids(artifact_id, sess))


def _get_row(artifact_id: str, session: Session) -> ArtifactRow:
    row = session.get(ArtifactRow, artifact_id)
    if row is None:
        raise NotFoundError(
            f"artifact not found: {artifact_id}", details={"artifactId": artifact_id}
        )
    return row


# --- Two-phase creation ----------------------------------------------------


@external_io
def stage_content(
    stream: Any,
    *,
    project_id: str,
    artifact_type: str,
    expected_hash: str | None = None,
    media_type: str = "application/octet-stream",
    provisional_id: str | None = None,
    clock: Clock | None = None,
) -> StagingHandle:
    """Write content to object storage and verify its hash (FD-ART-004, FD-ART-005).

    Must be called outside any unit of work.  Reads the content back from the
    store and compares the recomputed SHA-256 to the hash produced during the
    write and, when provided, to ``expected_hash``; any mismatch aborts without
    registering anything (FD-AC-01).  The handle is recorded in a short internal
    transaction and expires after :data:`STAGING_TTL` if never registered.
    """
    store = get_object_store()
    obj_clock = clock or SystemClock()
    artifact_id = provisional_id or new_id("art")
    key = build_object_path(project_id, artifact_type, artifact_id)

    put = store.put(key, stream, media_type=media_type)
    write_digest = f"sha256:{put.sha256_hex}"

    # FD-ART-004: read back and verify.
    read_digest = "sha256:" + store.open_read(key).sha256_hex()
    if read_digest != write_digest:
        store.delete(key)
        raise ArtifactError(
            "content readback hash does not match write hash; aborting",
            artifact_id=artifact_id,
            details={"write": write_digest, "readback": read_digest},
        )
    if expected_hash is not None and read_digest != normalize_hash(expected_hash):
        store.delete(key)
        raise ArtifactError(
            "content hash does not match expected hash",
            artifact_id=artifact_id,
            details={"expected": normalize_hash(expected_hash), "actual": read_digest},
        )

    handle = new_id("stg")
    with unit_of_work() as sess:
        sess.add(
            ArtifactStagingRow(
                handle=handle,
                content_location=put.location,
                content_hash=write_digest,
                size=put.size,
                media_type=media_type,
                created_at=obj_clock.now(),
            )
        )
    return StagingHandle(
        handle=handle,
        content_location=put.location,
        content_hash=write_digest,
        size=put.size,
        media_type=media_type,
    )


def register_artifact(
    staging_handle: StagingHandle | str,
    create: ArtifactCreate,
    *,
    session: Session | None = None,
    clock: Clock | None = None,
    content_id: str | None = None,
) -> ArtifactInfo:
    """Register artifact metadata in the caller's transaction (FD-ART-005).

    Enforces type ownership, classification rules, size caps and version
    superseding; writes ``core.artifact.created`` / ``core.artifact.superseded``
    audit events in the same transaction (FD-AUD-001, 21.4).
    """
    sess = session if session is not None else current_session()
    obj_clock = clock or SystemClock()
    ctx = current_context_or_default()

    handle = staging_handle.handle if isinstance(staging_handle, StagingHandle) else staging_handle
    staging = sess.get(ArtifactStagingRow, handle)
    if staging is None:
        raise ArtifactError(
            f"unknown or expired staging handle: {handle}", details={"handle": handle}
        )

    # FD-ART-002 / FD-AC-05: only owners may create a type.
    type_row = assert_type_owner(create.type, create.creator_subsystem, sess)

    # FD-ART-006: optional per-type size cap.
    if type_row.max_size is not None and staging.size > type_row.max_size:
        raise ArtifactError(
            f"content size {staging.size} exceeds type limit {type_row.max_size} for {create.type}",
            details={"type": create.type, "size": staging.size, "max": type_row.max_size},
        )

    # FD-ART-008 / FD-AC-04: classification may not drop below the highest source.
    if create.source_artifact_ids and create.redaction_ref is None:
        src_rows = (
            sess.execute(select(ArtifactRow).where(ArtifactRow.id.in_(create.source_artifact_ids)))
            .scalars()
            .all()
        )
        found = {r.id for r in src_rows}
        missing = set(create.source_artifact_ids) - found
        if missing:
            raise ArtifactError(
                f"unknown source artifact(s): {sorted(missing)}",
                details={"missing": sorted(missing)},
            )
        highest = DataClassification.max_of(
            [DataClassification(r.data_classification) for r in src_rows]
        )
        if not create.data_classification.at_least(highest):
            raise ArtifactError(
                f"declared classification {create.data_classification} is lower than the highest "
                f"source classification {highest}; a redaction_ref is required",
                details={"declared": create.data_classification.value, "source_max": highest.value},
            )

    # FD-ART-003 / FD-ART-007: chain from a previous version, superseding it.
    version = 1
    if create.prev_version_id is not None:
        prev = _get_row(create.prev_version_id, sess)
        version = prev.version + 1
        prev.status = ArtifactStatus.SUPERSEDED.value
        _audit_superseded(sess, prev)

    artifact_id = content_id or _derive_artifact_id(staging.content_location)
    row = ArtifactRow(
        id=artifact_id,
        project_id=create.project_id,
        # A caller that names no workflow is not saying "this artifact belongs to no
        # workflow" — it is usually a library (S2, S3, S4) that has no workflow concept at
        # all.  The operation context carries it (FD-COM-005), so the association survives
        # the call instead of landing as a null and dropping the artifact out of
        # GET /workflows/{id}/artifacts, which is what 23.1 and 19.2 expect to list it in.
        workflow_id=create.workflow_id or current_context_or_default().workflow_id,
        type=create.type,
        version=version,
        status=create.status.value,
        content_location=staging.content_location,
        content_hash=staging.content_hash,
        size=staging.size,
        media_type=create.media_type,
        data_classification=create.data_classification.value,
        build_record=create.build_record,
        prev_version_id=create.prev_version_id,
        creator_subsystem=create.creator_subsystem,
        creator_operator=ctx.operator_name,
        created_at=obj_clock.now(),
        content_purged=False,
        redaction_ref=create.redaction_ref,
    )
    sess.add(row)
    for src in create.source_artifact_ids:
        sess.add(ArtifactSourceRow(artifact_id=artifact_id, source_artifact_id=src))

    # consume the staging handle
    sess.delete(staging)
    sess.flush()

    _audit_created(sess, row)
    return _to_info(row, list(create.source_artifact_ids))


def _derive_artifact_id(location: str) -> str:
    # staging path already embeds a generated art- id; recover it.
    tail = location.rsplit("/", 1)[-1]
    if tail.startswith("art-"):
        return tail
    return new_id("art")


# --- Content access --------------------------------------------------------


@external_io
def open_content(artifact_id: str) -> tuple[Iterator[bytes], DataClassification, str]:
    """Stream artifact content with its classification (FD-ART-009).

    External I/O: must run outside a unit of work.  A purged artifact raises
    ``ARTIFACT_PURGED`` (FD-ART-010).
    """
    with resolve_session(None) as sess:
        row = _get_row(artifact_id, sess)
        if row.content_purged:
            raise ArtifactPurgedError(
                f"artifact content has been purged: {artifact_id}",
                artifact_id=artifact_id,
            )
        classification = DataClassification(row.data_classification)
        media_type = row.media_type
        location = row.content_location
    key = _key_from_location(location)
    handle = get_object_store().open_read(key)
    return handle.iter_bytes(), classification, media_type


def read_content_bytes(artifact_id: str) -> tuple[bytes, DataClassification, str]:
    """Convenience wrapper returning the whole content (small artifacts/tests)."""
    chunks, classification, media_type = open_content(artifact_id)
    return b"".join(chunks), classification, media_type


def _key_from_location(location: str) -> str:
    return key_from_location(location, get_object_store())


# --- Status, lineage, versions --------------------------------------------


def set_status(
    artifact_id: str,
    new_status: ArtifactStatus,
    *,
    session: Session | None = None,
) -> ArtifactInfo:
    """Change an artifact's status within the caller's transaction (FD-ART-007)."""
    sess = session if session is not None else current_session()
    row = _get_row(artifact_id, sess)
    row.status = ArtifactStatus(new_status).value
    sess.flush()
    return _to_info(row, _source_ids(artifact_id, sess))


def lineage(artifact_id: str, session: Session | None = None) -> LineageResult:
    """Transitive source chain (up) and derived chain (down) (FD-ART-013)."""
    with resolve_session(session) as sess:
        _get_row(artifact_id, sess)
        sources = _walk(sess, artifact_id, up=True)
        derived = _walk(sess, artifact_id, up=False)
        return LineageResult(
            artifact_id=artifact_id,
            sources=[s for s in sources],
            derived=[d for d in derived],
        )


def _walk(session: Session, start: str, *, up: bool) -> list[str]:
    seen: set[str] = set()
    frontier = [start]
    order: list[str] = []
    while frontier:
        nxt: list[str] = []
        for current in frontier:
            if up:
                rows = (
                    session.execute(
                        select(ArtifactSourceRow.source_artifact_id).where(
                            ArtifactSourceRow.artifact_id == current
                        )
                    )
                    .scalars()
                    .all()
                )
            else:
                rows = (
                    session.execute(
                        select(ArtifactSourceRow.artifact_id).where(
                            ArtifactSourceRow.source_artifact_id == current
                        )
                    )
                    .scalars()
                    .all()
                )
            for rid in rows:
                if rid not in seen:
                    seen.add(rid)
                    order.append(rid)
                    nxt.append(rid)
        frontier = nxt
    return order


def version_history(artifact_id: str, session: Session | None = None) -> list[ArtifactInfo]:
    """All artifacts in the same version chain, ordered by version (FD-ART-003)."""
    with resolve_session(session) as sess:
        head = _get_row(artifact_id, sess)
        # walk up to the earliest version
        chain_prev = {head.id: head.prev_version_id}
        earliest = head
        while earliest.prev_version_id:
            earliest = _get_row(earliest.prev_version_id, sess)
            chain_prev[earliest.id] = earliest.prev_version_id
        # walk forward collecting children by prev pointer
        children: dict[str, list[str]] = {}
        rows = sess.execute(select(ArtifactRow)).scalars().all()
        for r in rows:
            if r.prev_version_id:
                children.setdefault(r.prev_version_id, []).append(r.id)
        result: list[ArtifactInfo] = []
        node: ArtifactRow | None = earliest
        visited: set[str] = set()
        while node is not None and node.id not in visited:
            visited.add(node.id)
            result.append(_to_info(node, _source_ids(node.id, sess)))
            kids = children.get(node.id, [])
            node = sess.get(ArtifactRow, kids[0]) if kids else None
        return result


# --- Queries ---------------------------------------------------------------


def query_artifacts(query: ArtifactQuery, session: Session | None = None) -> Page[ArtifactInfo]:
    """Filtered, paginated artifact metadata query (FD-ART-014)."""
    with resolve_session(session) as sess:
        stmt = select(ArtifactRow)
        count_stmt = select(func.count()).select_from(ArtifactRow)
        conditions = _query_conditions(query)
        for cond in conditions:
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        total = sess.execute(count_stmt).scalar_one()

        sort_col = getattr(ArtifactRow, query.order_by, ArtifactRow.created_at)
        stmt = stmt.order_by(sort_col.desc() if query.descending else sort_col.asc())
        stmt = stmt.limit(query.limit).offset(query.offset)
        rows = sess.execute(stmt).scalars().all()
        items = [_to_info(r, _source_ids(r.id, sess)) for r in rows]
        return Page[ArtifactInfo](items=items, total=total, limit=query.limit, offset=query.offset)


def _query_conditions(query: ArtifactQuery) -> list[Any]:
    conds: list[Any] = []
    if query.project_id:
        conds.append(ArtifactRow.project_id == query.project_id)
    if query.workflow_id:
        conds.append(ArtifactRow.workflow_id == query.workflow_id)
    if query.type:
        conds.append(ArtifactRow.type == query.type)
    if query.status:
        conds.append(ArtifactRow.status == query.status.value)
    if query.creator_subsystem:
        conds.append(ArtifactRow.creator_subsystem == query.creator_subsystem)
    if query.created_after:
        conds.append(ArtifactRow.created_at >= query.created_after)
    if query.created_before:
        conds.append(ArtifactRow.created_at <= query.created_before)
    return conds


# --- Purge & retention -----------------------------------------------------


@external_io
def purge_content(artifact_id: str, session: Session | None = None) -> ArtifactInfo:
    """Delete content, keep metadata & hash, mark purged, write audit (FD-ART-010)."""
    # Content deletion is external I/O, so this runs outside a caller transaction;
    # the metadata update + audit happen in a short internal transaction.
    with resolve_session(None) as read_sess:
        row = _get_row(artifact_id, read_sess)
        location = row.content_location
    get_object_store().delete(_key_from_location(location))

    with unit_of_work() as sess:
        orow = _get_row(artifact_id, sess)
        orow.content_purged = True
        sess.flush()
        _audit_purged(sess, orow)
        return _to_info(orow, _source_ids(artifact_id, sess))


def apply_retention(
    project_id: str,
    *,
    before: datetime,
    session: Session | None = None,
) -> list[str]:
    """Purge content of a project's artifacts older than ``before`` (FD-ART-011).

    Rules (the cut-off time) come from the caller (S1).  Only content is removed;
    metadata and audit are preserved.  Runs outside a caller transaction because
    it deletes object-store content; returns the purged artifact ids.
    """
    if has_active_uow():
        raise ArtifactError(
            "apply_retention must run outside a unit of work (it deletes object-store content)"
        )
    store = get_object_store()
    purged: list[str] = []
    with writable_session(session) as sess:
        rows = (
            sess.execute(
                select(ArtifactRow).where(
                    ArtifactRow.project_id == project_id,
                    ArtifactRow.created_at < before,
                    ArtifactRow.content_purged == False,  # noqa: E712
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            store.delete(_key_from_location(row.content_location))
            row.content_purged = True
            _audit_retention(sess, row)
            purged.append(row.id)
        sess.flush()
    return purged


# --- Cleanup (invoked by housekeeping) ------------------------------------


def cleanup_expired_staging(now: datetime, session: Session | None = None) -> list[str]:
    """Delete staging rows (and their content) older than STAGING_TTL (FD-ART-005)."""
    store = get_object_store()
    cutoff = now - STAGING_TTL
    removed: list[str] = []
    with unit_of_work() as sess:
        rows = (
            sess.execute(select(ArtifactStagingRow).where(ArtifactStagingRow.created_at < cutoff))
            .scalars()
            .all()
        )
        for row in rows:
            store.delete(_key_from_location(row.content_location))
            removed.append(row.handle)
            sess.delete(row)
        sess.flush()
    return removed


# --- Audit helpers (core-owned events, same transaction) ------------------


def _audit_created(session: Session, row: ArtifactRow) -> None:
    from factory_foundation.audit.dto import AuditEventInput
    from factory_foundation.audit.service import audit

    audit(
        AuditEventInput(
            event_type="core.artifact.created",
            object_type="artifact",
            object_id=row.id,
            project_id=row.project_id,
            after={"type": row.type, "version": row.version, "hash": row.content_hash},
            source_subsystem=row.creator_subsystem,
        ),
        session=session,
    )


def _audit_superseded(session: Session, row: ArtifactRow) -> None:
    from factory_foundation.audit.dto import AuditEventInput
    from factory_foundation.audit.service import audit

    audit(
        AuditEventInput(
            event_type="core.artifact.superseded",
            object_type="artifact",
            object_id=row.id,
            project_id=row.project_id,
            after={"status": ArtifactStatus.SUPERSEDED.value},
            source_subsystem="S0",
        ),
        session=session,
    )


def _audit_purged(session: Session, row: ArtifactRow) -> None:
    from factory_foundation.audit.dto import AuditEventInput
    from factory_foundation.audit.service import audit

    audit(
        AuditEventInput(
            event_type="core.artifact.purged",
            object_type="artifact",
            object_id=row.id,
            project_id=row.project_id,
            before={"content_purged": False},
            after={"content_purged": True},
            source_subsystem="S0",
        ),
        session=session,
    )


def _audit_retention(session: Session, row: ArtifactRow) -> None:
    """FD-ART-011 / 21.4: a retention sweep removes content, so it has to be visible.

    ``core.artifact.retention`` is registered for exactly this and was never written, which
    left a retention purge looking identical to an unexplained gap in the record: 21.4 lists
    artifact changes among the events that must be audited, and FD-AUD-003's append-only
    chain is what makes "who cleared this, when" answerable afterwards.
    """
    from factory_foundation.audit.dto import AuditEventInput
    from factory_foundation.audit.service import audit

    audit(
        AuditEventInput(
            event_type="core.artifact.retention",
            object_type="artifact",
            object_id=row.id,
            project_id=row.project_id,
            before={"content_purged": False},
            after={"content_purged": True},
            source_subsystem="S0",
        ),
        session=session,
    )
