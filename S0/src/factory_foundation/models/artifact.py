"""Artifact tables in schema ``core``: ``artifact``, ``artifact_source``,
``artifact_staging`` (SRS section 5; FD-ART-001, FD-ART-005, FD-ART-013)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_foundation.enums import ArtifactStatus, DataClassification
from factory_foundation.models.base import CoreBase


class ArtifactRow(CoreBase):
    __tablename__ = "artifact"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    workflow_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)

    type: Mapped[str] = mapped_column(
        Text, ForeignKey("core.artifact_type.type"), nullable=False, index=True
    )
    # per-chain version number, auto-incremented when created from a previous
    # version (FD-ART-003)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ArtifactStatus.DRAFT.value, index=True
    )

    content_location: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)

    data_classification: Mapped[str] = mapped_column(String(16), nullable=False)

    build_record: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    prev_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("core.artifact.id"), nullable=True, index=True
    )

    creator_subsystem: Mapped[str] = mapped_column(Text, nullable=False)
    creator_operator: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    content_purged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # reference to a redaction record produced by S3; S0 only stores it (FD-ART-008)
    redaction_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    def classification_enum(self) -> DataClassification:
        return DataClassification(self.data_classification)

    def status_enum(self) -> ArtifactStatus:
        return ArtifactStatus(self.status)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Artifact {self.id} {self.type} v{self.version} {self.status}>"


class ArtifactSourceRow(CoreBase):
    __tablename__ = "artifact_source"

    artifact_id: Mapped[str] = mapped_column(Text, ForeignKey("core.artifact.id"), primary_key=True)
    source_artifact_id: Mapped[str] = mapped_column(
        Text, ForeignKey("core.artifact.id"), primary_key=True
    )


class ArtifactStagingRow(CoreBase):
    """Staged-but-unregistered content handle (FD-ART-005)."""

    __tablename__ = "artifact_staging"

    handle: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_location: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
