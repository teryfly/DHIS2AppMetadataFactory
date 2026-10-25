"""Artifact DTOs (Pydantic v2, 00 doc 6.1).

These are the types the public API accepts and returns.  Persistence uses the
``ArtifactRow`` ORM model in :mod:`factory_foundation.models.artifact`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from factory_foundation.enums import ArtifactStatus, DataClassification

T = TypeVar("T")


class StagingHandle(BaseModel):
    """Returned by :func:`stage_content`; consumed by :func:`register_artifact`."""

    model_config = ConfigDict(frozen=True)

    handle: str
    content_location: str
    content_hash: str
    size: int
    media_type: str | None = None


class ArtifactCreate(BaseModel):
    """Caller-supplied metadata for artifact registration (FD-ART-001)."""

    project_id: str
    type: str
    media_type: str
    data_classification: DataClassification
    creator_subsystem: str
    workflow_id: str | None = None
    build_record: dict[str, Any] | None = None
    source_artifact_ids: list[str] = Field(default_factory=list)
    prev_version_id: str | None = None
    status: ArtifactStatus = ArtifactStatus.DRAFT
    # reference to an S3-produced redaction record (FD-ART-008); S0 stores, not validates
    redaction_ref: str | None = None


class ArtifactInfo(BaseModel):
    """Full artifact metadata record returned by reads (FD-ART-001)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    workflow_id: str | None
    type: str
    version: int
    status: ArtifactStatus
    content_location: str
    content_hash: str
    size: int
    media_type: str
    data_classification: DataClassification
    build_record: dict[str, Any] | None
    source_artifact_ids: list[str]
    prev_version_id: str | None
    creator_subsystem: str
    creator_operator: str
    created_at: datetime
    content_purged: bool
    redaction_ref: str | None


class ContentStream(BaseModel):
    """Metadata accompanying a streamed content read (FD-ART-009)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    artifact_id: str
    data_classification: DataClassification
    media_type: str
    size: int
    chunks: Any  # Iterator[bytes]; typed Any to keep the model JSON-friendly


class ArtifactQuery(BaseModel):
    """Filter + pagination for :func:`query_artifacts` (FD-ART-014)."""

    project_id: str | None = None
    workflow_id: str | None = None
    type: str | None = None
    status: ArtifactStatus | None = None
    creator_subsystem: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    order_by: str = "created_at"
    descending: bool = True
    limit: int = 100
    offset: int = 0


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class LineageResult(BaseModel):
    artifact_id: str
    sources: list[str]  # upstream source ids (FD-ART-013)
    derived: list[str]  # downstream derived ids


class DiffResult(BaseModel):
    kind: str  # "line" | "json"
    a_id: str
    b_id: str
    changed: bool
    lines: list[dict[str, Any]] = Field(default_factory=list)  # unified-ish diff rows


class PackManifest(BaseModel):
    """Archive manifest written by :func:`pack` (FD-ART-015)."""

    artifacts: list[dict[str, Any]]
    # id -> hash so unpack can verify each entry
