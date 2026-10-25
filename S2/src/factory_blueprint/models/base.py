"""ORM base shared by every ``bp`` table.

Tables are declared on S0's :class:`factory_foundation.models.base.BpBase`, which
is bound to the ``bp`` schema (00 doc 4.1, D-08).  No foreign key may point
outside ``bp`` — cross-schema references are stored as plain IDs (D-14), and the
foundation's FK checker enforces that.
"""

from __future__ import annotations

from datetime import datetime

from factory_foundation.api import BpBase
from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """S2 SRS section 5: every entity carries created/updated time."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BpTable(TimestampMixin, BpBase):
    """Abstract base mixing the timestamp columns into the ``bp`` metadata."""

    __abstract__ = True
