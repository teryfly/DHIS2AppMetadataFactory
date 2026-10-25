"""Audit tables in schema ``core``: ``audit_event``, ``audit_chain_state``
(SRS section 5; FD-AUD-002, FD-AUD-004)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_foundation.models.base import CoreBase


class AuditEventRow(CoreBase):
    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # global sequence, assigned at link time (FD-AUD-004); null until linked
    seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operator_type: Mapped[str] = mapped_column(String(16), nullable=False)  # USER | SYSTEM
    operator_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_subsystem: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)

    object_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    before_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    prev_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    hash: Mapped[str | None] = mapped_column(String(71), nullable=True)

    __table_args__ = (
        Index("ix_audit_project", "project_id"),
        Index("ix_audit_object", "object_type", "object_id"),
        Index("ix_audit_event_type", "event_type"),
        Index("ix_audit_correlation", "correlation_id"),
        # ordering used when allocating sequence: (occurred_at, id)
        Index("ix_audit_link_order", "occurred_at", "id"),
    )


class AuditChainStateRow(CoreBase):
    """Singleton row tracking the head of the global hash chain (FD-AUD-004)."""

    __tablename__ = "audit_chain_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    last_linked_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
