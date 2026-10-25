"""Audit DTOs (FD-AUD-002)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from factory_foundation.enums import OperatorType


class AuditEventInput(BaseModel):
    """Fields a caller supplies to :func:`audit` (FD-AUD-002)."""

    event_type: str
    object_type: str | None = None
    object_id: str | None = None
    project_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    source_subsystem: str | None = None
    operator_type: OperatorType | None = None
    operator_name: str | None = None
    correlation_id: str | None = None
    occurred_at: datetime | None = None


class AuditRecord(BaseModel):
    """A stored audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    seq: int | None
    occurred_at: datetime
    operator_type: str
    operator_name: str
    source_subsystem: str
    event_type: str
    object_type: str | None
    object_id: str | None
    project_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    correlation_id: str | None
    redacted: bool
    prev_hash: str | None
    hash: str | None


class AuditQuery(BaseModel):
    project_id: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    event_type: str | None = None
    operator_name: str | None = None
    correlation_id: str | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    limit: int = 100
    offset: int = 0
