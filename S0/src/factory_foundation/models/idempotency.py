"""Idempotency table in schema ``core``: ``idempotency_record``
(SRS section 5; FD-IDEM-001)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_foundation.enums import IdempotencyStatus
from factory_foundation.models.base import CoreBase


class IdempotencyRecordRow(CoreBase):
    __tablename__ = "idempotency_record"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)

    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=IdempotencyStatus.IN_PROGRESS.value
    )
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def status_enum(self) -> IdempotencyStatus:
        return IdempotencyStatus(self.status)
