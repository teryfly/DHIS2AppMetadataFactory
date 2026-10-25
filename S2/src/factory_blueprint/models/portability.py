"""``bp.import_batch`` — the two-phase import staging table (BP-EXP-002).

``prepare_import`` is external I/O (it reads the exported artifact set and
rewrites artifact IDs through the caller's ``id_map``); ``commit_import`` runs in
the caller's unit of work and can therefore only touch ``bp`` rows.  The prepared
payload is persisted here so the commit step needs no content read.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.models.base import BpTable


class ImportBatchRow(BpTable):
    __tablename__ = "import_batch"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_deployment: Mapped[str | None] = mapped_column(String(128), nullable=True)
    export_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: id map reported by S0 unpack, kept for audit and for build-record rewriting
    id_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PREPARED")

    __table_args__ = (Index("ix_bp_import_batch_project_status", "project_id", "status"),)
