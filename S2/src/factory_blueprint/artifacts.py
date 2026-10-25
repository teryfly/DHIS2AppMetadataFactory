"""S2's gateway to the foundation artifact service.

Everything S2 stores outside its own schema goes through here, which keeps the
external-I/O boundary explicit: staging and content reads are external I/O and
must run outside a unit of work, while registration is a transactional write that
S1 can enlist in its own transaction (00 doc 6.2, FD-ART-005).
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterable
from typing import Any

from factory_foundation.api import (
    ArtifactCreate,
    ArtifactInfo,
    DataClassification,
    NotFoundError,
    StagingHandle,
    content_hash,
    external_io,
    file_json,
    get_artifact,
    read_content_bytes,
    register_artifact,
    stage_content,
    structure_hash,
)
from sqlalchemy.orm import Session

JSON_MEDIA_TYPE = "application/json"
ZIP_MEDIA_TYPE = "application/zip"
MARKDOWN_MEDIA_TYPE = "text/markdown"
YAML_MEDIA_TYPE = "application/yaml"

#: ZIP entries must not carry the wall clock (FR-CMP-001, BP-CMP-002).
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


@external_io
def stage_bytes(
    data: bytes,
    project_id: str,
    artifact_type: str,
    media_type: str = JSON_MEDIA_TYPE,
    expected_hash: str | None = None,
) -> StagingHandle:
    """Write content to object storage and verify its hash (FD-ART-004)."""
    return stage_content(
        iter([data]),
        project_id=project_id,
        artifact_type=artifact_type,
        expected_hash=expected_hash,
        media_type=media_type,
    )


@external_io
def stage_json(document: dict[str, Any], project_id: str, artifact_type: str) -> StagingHandle:
    return stage_bytes(file_json(document), project_id, artifact_type, JSON_MEDIA_TYPE)


@external_io
def stage_files(files: dict[str, bytes], project_id: str, artifact_type: str) -> StagingHandle:
    """Pack a deterministic ZIP: sorted names, fixed timestamp, no extra attributes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.comment = b""
        for name in sorted(files):
            info = zipfile.ZipInfo(filename=name, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            archive.writestr(info, files[name])
    return stage_bytes(buffer.getvalue(), project_id, artifact_type, ZIP_MEDIA_TYPE)


def register(
    handle: StagingHandle | str,
    *,
    project_id: str,
    artifact_type: str,
    data_classification: DataClassification,
    build_record: dict[str, Any] | None = None,
    source_artifact_ids: list[str] | None = None,
    workflow_id: str | None = None,
    media_type: str | None = None,
    session: Session | None = None,
) -> ArtifactInfo:
    """Register staged metadata inside the caller's transaction (FD-ART-005).

    The media type comes from the staging handle when the caller does not say: a
    packed ZIP product must not be registered as JSON, or S1 and S4 would read
    `metadata.json` out of something that is an archive.
    """
    resolved = media_type or (handle.media_type if isinstance(handle, StagingHandle) else None)
    create = ArtifactCreate(
        project_id=project_id,
        type=artifact_type,
        media_type=resolved or JSON_MEDIA_TYPE,
        data_classification=data_classification,
        creator_subsystem="S2",
        workflow_id=workflow_id,
        build_record=build_record,
        source_artifact_ids=source_artifact_ids or [],
    )
    return register_artifact(handle, create, session=session)


def read_metadata(artifact_id: str, session: Session | None = None) -> ArtifactInfo:
    """Metadata only, so this is safe to call inside a transaction."""
    return get_artifact(artifact_id, session=session)


@external_io
def read_json(artifact_id: str) -> tuple[dict[str, Any], DataClassification]:
    data, classification, _media = read_content_bytes(artifact_id)
    document: dict[str, Any] = json.loads(data)
    return document, classification


@external_io
def read_bytes(artifact_id: str) -> tuple[bytes, DataClassification]:
    data, classification, _media = read_content_bytes(artifact_id)
    return data, classification


def highest_classification(classifications: list[DataClassification]) -> DataClassification:
    """BP-CMP-007: a derived artifact inherits the highest level of its sources."""
    return DataClassification.max_of(classifications)


def classification_of_inputs(artifact_ids: Iterable[str]) -> DataClassification:
    """The level a product must declare for this set of inputs (BP-CMP-007).

    S0 refuses a declaration below the highest source, so this is what callers of
    :func:`register` must pass; a source that has been purged does not lower it.
    """
    levels = [DataClassification.INTERNAL]
    for artifact_id in sorted(set(artifact_ids)):
        try:
            levels.append(read_metadata(artifact_id).data_classification)
        except NotFoundError:
            continue
    return highest_classification(levels)


def unpack_archive(data: bytes) -> dict[str, bytes]:
    """The entries of a ZIP this subsystem packed, keyed by name in sorted order."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in sorted(archive.namelist())}


def archive_hashes(data: bytes) -> dict[str, str]:
    """Per-entry content hashes of a packed product (BP-CMP-003, reproduction)."""
    return {name: content_hash(blob) for name, blob in sorted(unpack_archive(data).items())}


def blueprint_document_hash(document: dict[str, Any]) -> str:
    """The hash a Blueprint version is identified by (BP-VER-001)."""
    return structure_hash(document)
