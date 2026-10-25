"""``bp.blueprint_draft`` and ``bp.blueprint_patch`` (SRS section 5, D-04).

The draft holds the *mutable* materialised Blueprint content (base version plus
every PROPOSED patch).  Mutable state cannot live in the immutable artifact
store, so this is the one place where a Blueprint document is kept in ``bp``;
promoting a draft writes the frozen content to a ``BLUEPRINT`` artifact and the
draft row keeps its history for audit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.enums import DraftStatus
from factory_blueprint.models.base import BpTable


class BlueprintDraftRow(BpTable):
    __tablename__ = "blueprint_draft"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    base_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: list of sections this stage may modify (appendix A)
    stage_scope: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    change_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DraftStatus.OPEN.value)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: the base version's content at materialization time, kept so change level and
    #: Diff can compare without reading the artifact inside a transaction (BP-IMP-001)
    base_content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    frozen_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    staging_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frozen_issues: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    promotable: Mapped[bool | None] = mapped_column(nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        # BP-DRF-001: at most one OPEN/FROZEN draft per project
        Index(
            "uq_bp_draft_active_per_project",
            "project_id",
            unique=True,
            postgresql_where=text(
                f"status IN ('{DraftStatus.OPEN.value}', '{DraftStatus.FROZEN.value}')"
            ),
        ),
        Index("ix_bp_draft_project_status", "project_id", "status"),
    )


class BlueprintPatchRow(BpTable):
    __tablename__ = "blueprint_patch"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_id: Mapped[str] = mapped_column(String(64), nullable=False)
    base_version: Mapped[str] = mapped_column(String(32), nullable=False)
    author_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: agentRunId for AGENT, operator name for USER/SYSTEM
    author_ref: Mapped[str] = mapped_column(Text, nullable=False)
    decision_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    operations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: advisory only (BP-DRF-002)
    declared_impact: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    change_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reject_issues: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    #: UIDs taken from the pool for this patch; retired if it is rejected/discarded
    assigned_uids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    result_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    applied_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_bp_patch_project_id"),
        Index("ix_bp_patch_draft_status", "draft_id", "status"),
        Index("ix_bp_patch_project_created", "project_id", "created_at"),
    )
