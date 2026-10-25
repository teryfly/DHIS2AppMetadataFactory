"""``bp.blueprint_version`` — immutable version index (BP-VER-001/002).

Version *content* lives only in the S0 ``BLUEPRINT`` artifact referenced by
``artifact_id``; this row is the queryable index, so index rows can be rebuilt
from the artifact content at any time (BP-VER-002).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.enums import VersionStatus
from factory_blueprint.models.base import BpTable


class BlueprintVersionRow(BpTable):
    __tablename__ = "blueprint_version"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_level: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VersionStatus.CURRENT.value
    )
    source_artifact_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    decision_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: project semantics mirrored into the index so read-only queries and stage
    #: scope decisions never need the artifact content (BP-VER-002)
    delivery_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="B")
    project_type: Mapped[str] = mapped_column(String(16), nullable=False, default="BROWNFIELD")
    default_language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    translation_languages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: mirrored so a semantics read never has to touch an artifact (BP-VER-002)
    target_dhis2_version_constraint: Mapped[str] = mapped_column(
        String(128), nullable=False, default=""
    )
    has_fixture_specifications: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: stage whose Gate promoted this draft — drives "is mapping frozen" (BP-DRF-005.5)
    promoted_from_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    promoted_from_draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_bp_version_project_version"),
        # BP-DRF-001 builds every draft on "the current version", so a project
        # has exactly one: the draft's base could not be named otherwise
        Index(
            "uq_bp_version_current_per_project",
            "project_id",
            unique=True,
            postgresql_where=text(f"status = '{VersionStatus.CURRENT.value}'"),
        ),
        Index("ix_bp_version_project_created", "project_id", "created_at"),
    )
