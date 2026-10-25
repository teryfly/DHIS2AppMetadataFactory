"""``ai`` schema table base (mirrors ``factory_blueprint.models.base``).

SRS 5 says every entity carries creation and update timestamps and omits them from
the field lists; this is where they come from.  The schema itself is bound once, on
:class:`~factory_foundation.api.AiBase`, so no table declares ``schema=`` and the
cross-schema foreign-key checker sees exactly the ``ai`` tables.
"""

from __future__ import annotations

from datetime import datetime

from factory_foundation.api import AiBase
from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AiTable(TimestampMixin, AiBase):
    """Abstract base for every S3-owned table."""

    __abstract__ = True


__all__ = ["TimestampMixin", "AiTable"]
