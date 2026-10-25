"""``bp.prepared_content`` — S2's staging mirror for two-phase operations.

Content is written to object storage by an external-I/O call and registered later
inside the caller's transaction (FD-ART-005).  Between those two calls S2 needs the
document it just built — to validate it and to fill the version index — and reading
it back is impossible before the artifact exists.  This table carries it across the
boundary and is deleted by the commit step.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.models.base import BpTable


class PreparedContentRow(BpTable):
    __tablename__ = "prepared_content"

    #: the S0 staging handle this content was written under
    handle: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: the document the staged one will be compared against, kept for the same reason
    #: ``blueprint_draft.base_content`` is: change level and Diff must not read an
    #: artifact inside the caller's transaction (BP-IMP-001, 00 doc 6.2)
    base_content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    #: provenance carried across the staging boundary so the version index can
    #: record source artifacts without re-reading them (BP-SCH-005)
    source_artifact_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_bp_prepared_project_kind", "project_id", "kind"),)
