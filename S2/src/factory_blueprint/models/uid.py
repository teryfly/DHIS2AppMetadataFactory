"""UID allocation ledger and the per-project UID pool (BP-REG-006/008a).

``uid_allocation.uid`` is the primary key *across all projects*: a UID is never
handed to two business concepts in the whole Factory (FR-REG-004/005 checks).
Retired UIDs stay in the ledger forever so no allocation can ever reuse them.

The ledger is append-only per business concept: re-planning something a reset
discarded allocates a fresh UID and leaves the retired row alone, so at most one
allocation per ``(project_id, business_key)`` may be ACTIVE at a time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.enums import UidAllocationStatus
from factory_blueprint.models.base import BpTable


class UidAllocationRow(BpTable):
    __tablename__ = "uid_allocation"

    uid: Mapped[str] = mapped_column(String(11), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_key: Mapped[str] = mapped_column(String(255), nullable=False)
    dhis2_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_environment: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UidAllocationStatus.ACTIVE.value
    )
    allocated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: patch that caused the allocation / retirement, for audit tracing
    patch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # a concept may be planned, discarded and planned again; only one of those
        # allocations is live at a time (BP-AC-07 keeps the discarded ones forever)
        Index(
            "uq_bp_uid_alloc_active_per_key",
            "project_id",
            "business_key",
            unique=True,
            postgresql_where=text(f"status = '{UidAllocationStatus.ACTIVE.value}'"),
        ),
        Index("ix_bp_uid_alloc_project_status", "project_id", "status"),
    )


class UidPoolRow(BpTable):
    __tablename__ = "uid_pool"

    uid: Mapped[str] = mapped_column(String(11), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_environment: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_bp_uid_pool_project_fetched", "project_id", "fetched_at"),)
