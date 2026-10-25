"""Registry tables in schema ``core``: artifact types, audit event types, error
codes (SRS section 5, FD-ART-002, FD-AUD-010, FD-COM-001)."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from factory_foundation.models.base import CoreBase


class ArtifactTypeRow(CoreBase):
    __tablename__ = "artifact_type"

    type: Mapped[str] = mapped_column(Text, primary_key=True)
    # allowed owner subsystem codes; several subsystems may register the same
    # type and their owner lists merge (FD-ART-002)
    owner_subsystems: Mapped[list[str]] = mapped_column(JSON, default=list)
    # optional per-type content size cap; null means unlimited (FD-ART-006)
    max_size: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AuditEventTypeRow(CoreBase):
    __tablename__ = "audit_event_type"

    event_type: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_subsystem: Mapped[str] = mapped_column(Text, nullable=False)


class ErrorCodeRow(CoreBase):
    __tablename__ = "error_code"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_subsystem: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class IdPrefixRow(CoreBase):
    """Persisted ID-prefix ownership (companion to the in-process registry)."""

    __tablename__ = "id_prefix"

    prefix: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_subsystem: Mapped[str] = mapped_column(Text, nullable=False)
