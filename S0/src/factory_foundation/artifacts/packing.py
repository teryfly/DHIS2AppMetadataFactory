"""Artifact packing & unpacking (FD-ART-015).

:func:`pack` writes a single ZIP archive carrying every selected artifact's
metadata and content, with a manifest recording each artifact's *metadata* hash
and *content* hash separately.  PHI_PII content can be excluded (metadata and
hash are retained regardless).

:func:`unpack` verifies **all** hashes before registering anything; a single
mismatch aborts with nothing imported.  Import preserves creator, classification,
status, build record and version, restores source / previous-version links through
an old→new id mapping, and returns that mapping.  An abort after content was
written removes that content again: object storage has no transaction, so "nothing
imported" only holds if the bytes leave as well.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from factory_foundation.artifacts.service import _get_row, _source_ids, open_content
from factory_foundation.common.errors import ArtifactError
from factory_foundation.common.hashing import content_hash, file_json, normalize_hash
from factory_foundation.common.ids import new_id
from factory_foundation.db.external_io import external_io
from factory_foundation.db.uow import unit_of_work
from factory_foundation.enums import DataClassification
from factory_foundation.models.artifact import ArtifactRow, ArtifactSourceRow
from factory_foundation.objectstore.base import ObjectStore, build_object_path
from factory_foundation.objectstore.provider import get_object_store

MANIFEST_NAME = "manifest.json"


@dataclass
class PackResult:
    archive: bytes
    archive_hash: str
    artifact_ids: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)


def _metadata_payload(row: ArtifactRow, source_ids: list[str]) -> dict[str, Any]:
    return {
        "id": row.id,
        "projectId": row.project_id,
        "workflowId": row.workflow_id,
        "type": row.type,
        "version": row.version,
        "status": row.status,
        "contentHash": row.content_hash,
        "size": row.size,
        "mediaType": row.media_type,
        "dataClassification": row.data_classification,
        "buildRecord": row.build_record,
        "sourceArtifactIds": sorted(source_ids),
        "prevVersionId": row.prev_version_id,
        "creatorSubsystem": row.creator_subsystem,
        "creatorOperator": row.creator_operator,
        "createdAt": row.created_at.isoformat(),
        "contentPurged": row.content_purged,
        "redactionRef": row.redaction_ref,
    }


@external_io
def pack(artifact_ids: list[str], *, include_phi_content: bool = True) -> PackResult:
    """Pack artifacts into a single verified archive (FD-ART-015)."""
    entries: list[dict[str, Any]] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for artifact_id in artifact_ids:
            with _read_tx() as sess:
                row = _get_row(artifact_id, sess)
                source_ids = _source_ids(artifact_id, sess)
            metadata = _metadata_payload(row, source_ids)
            meta_bytes = file_json(metadata)
            meta_hash = content_hash(meta_bytes)
            meta_entry = f"meta/{artifact_id}.json"
            zf.writestr(meta_entry, meta_bytes)

            content_entry: str | None = None
            if not row.content_purged and (
                include_phi_content or row.data_classification != DataClassification.PHI_PII.value
            ):
                chunks, _classification, _media = open_content(artifact_id)
                content_entry = f"content/{artifact_id}.bin"
                # copied chunk by chunk: an artifact's size is deliberately unbounded
                # (FD-ART-006, NFR-CAP-003), and joining it here would put the whole largest
                # object in memory on top of the archive itself
                with zf.open(content_entry, "w") as sink:
                    for chunk in chunks:
                        sink.write(chunk)

            entries.append(
                {
                    "id": artifact_id,
                    "metadataEntry": meta_entry,
                    "metadataHash": meta_hash,
                    "contentEntry": content_entry,
                    "contentHash": row.content_hash,  # canonical content hash
                    "contentIncluded": content_entry is not None,
                }
            )
        manifest = {"version": 1, "artifacts": entries}
        zf.writestr(MANIFEST_NAME, file_json(manifest))

    archive = buf.getvalue()
    return PackResult(
        archive=archive,
        archive_hash=content_hash(archive),
        artifact_ids=list(artifact_ids),
        manifest=manifest,
    )


class _read_tx:
    """Small helper: a short read transaction outside any caller UoW."""

    def __enter__(self) -> Session:
        self._ctx = unit_of_work()
        return self._ctx.__enter__()

    def __exit__(self, *exc_info: Any) -> Any:
        return self._ctx.__exit__(*exc_info)


@dataclass
class UnpackResult:
    id_map: dict[str, str]
    imported: list[str]


@external_io
def unpack(
    archive: bytes,
    target_project_id: str,
    *,
    session: Session | None = None,
) -> UnpackResult:
    """Verify then import a packed archive into ``target_project_id`` (FD-ART-015)."""
    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except KeyError as exc:
            raise ArtifactError("archive has no manifest") from exc

        # 1. Verify every metadata + content hash before touching the store/DB.
        staged: list[dict[str, Any]] = []
        for entry in manifest["artifacts"]:
            meta_bytes = zf.read(entry["metadataEntry"])
            if content_hash(meta_bytes) != normalize_hash(entry["metadataHash"]):
                raise ArtifactError(
                    f"metadata hash mismatch for {entry['id']}; import aborted",
                    artifact_id=entry["id"],
                )
            content_bytes: bytes | None = None
            if entry["contentIncluded"]:
                content_bytes = zf.read(entry["contentEntry"])
                if content_hash(content_bytes) != normalize_hash(entry["contentHash"]):
                    raise ArtifactError(
                        f"content hash mismatch for {entry['id']}; import aborted",
                        artifact_id=entry["id"],
                    )
            metadata = json.loads(meta_bytes.decode("utf-8"))
            staged.append({"metadata": metadata, "entry": entry, "content": content_bytes})

        # 2. Old → new id map.
        id_map = {item["metadata"]["id"]: new_id("art") for item in staged}

        # 3. Write content to object store (external I/O, outside the DB tx).
        store = get_object_store()
        locations: dict[str, tuple[str, int]] = {}
        written: list[str] = []
        try:
            for item in staged:
                md = item["metadata"]
                new_id_val = id_map[md["id"]]
                if item["content"] is not None:
                    key = build_object_path(target_project_id, md["type"], new_id_val)
                    put = store.put(key, item["content"], media_type=md["mediaType"])
                    # recorded as soon as the store accepts it: from this point an object
                    # exists that no artifact row refers to, and only this function can undo it
                    written.append(key)
                    # FD-BR-02: content is registrable only after it has been read back and
                    # hashed; the archive's own hash is the expectation to match.
                    readback = normalize_hash(f"sha256:{store.open_read(key).sha256_hex()}")
                    if readback != normalize_hash(item["entry"]["contentHash"]):
                        raise ArtifactError(
                            f"readback hash mismatch for {md['id']}; import aborted",
                            artifact_id=md["id"],
                        )
                    locations[md["id"]] = (put.location, put.size)

            # 4. Register everything in ONE transaction (atomic).
            return _register_imported(staged, id_map, locations, target_project_id, session)
        except Exception as error:
            residual = _discard(store, written)
            if residual:
                raise ArtifactError(
                    f"import aborted and {len(residual)} object(s) were left in the store: {error}",
                    artifact_id=staged[0]["metadata"]["id"],
                    details={"objectKeys": residual, "cause": type(error).__name__},
                ) from error
            # nothing was imported: no row was committed and no object survives the attempt
            raise


def _discard(store: ObjectStore, keys: list[str]) -> list[str]:
    """Remove the content an aborted import wrote; return the keys that are still there.

    A delete that fails must not replace the failure that caused the abort — the caller is
    already losing the import, and the reason matters more than the cleanup.  What it must
    not do is lose the *location*: the residual keys are the operator's only lead, so they
    come back in the error's ``details`` rather than in a log line nobody reads.
    """
    residual: list[str] = []
    for key in keys:
        try:
            store.delete(key)
        except Exception:  # noqa: BLE001 - the aborting error is the one worth raising
            residual.append(key)
    return residual


def _register_imported(
    staged: list[dict[str, Any]],
    id_map: dict[str, str],
    locations: dict[str, tuple[str, int]],
    target_project_id: str,
    session: Session | None,
) -> UnpackResult:
    def _do(sess: Session) -> UnpackResult:
        for item in staged:
            md = item["metadata"]
            new_id_val = id_map[md["id"]]
            entry = item["entry"]
            prev_new = id_map.get(md["prevVersionId"]) if md.get("prevVersionId") else None
            sources_new = [id_map[s] for s in md["sourceArtifactIds"] if s in id_map]
            # content absent (excluded PHI or originally purged) => imported as purged
            content_purged = not entry["contentIncluded"]
            if entry["contentIncluded"]:
                loc, size = locations[md["id"]]
            else:
                loc, size = f"purged://{new_id_val}", md["size"]
            content_hash_value = normalize_hash(entry["contentHash"])
            row = ArtifactRow(
                id=new_id_val,
                project_id=target_project_id,
                workflow_id=None,  # workflow linkage does not cross project boundaries
                type=md["type"],
                version=md["version"],
                status=md["status"],
                content_location=loc,
                content_hash=content_hash_value,
                size=size,
                media_type=md["mediaType"],
                data_classification=md["dataClassification"],
                build_record=md["buildRecord"],
                prev_version_id=prev_new,
                creator_subsystem=md["creatorSubsystem"],
                creator_operator=md["creatorOperator"],
                created_at=_parse_dt(md["createdAt"]),
                content_purged=content_purged,
                redaction_ref=md.get("redactionRef"),
            )
            sess.add(row)
            for src_new in sources_new:
                sess.add(ArtifactSourceRow(artifact_id=new_id_val, source_artifact_id=src_new))
        sess.flush()
        return UnpackResult(id_map=id_map, imported=sorted(id_map.values()))

    if session is not None:
        return _do(session)
    with unit_of_work() as sess:
        return _do(sess)


def _parse_dt(value: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(value)
