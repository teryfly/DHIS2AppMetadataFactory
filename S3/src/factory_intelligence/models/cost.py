"""``ai.budget`` — AI-COST-002 (limit enforcement is M4; the row exists now).

The table ships with the MVP because AI-RT-017 already includes ``BUDGET_EXCEEDED``
and AI-COST-003 defines when the judgement is made, so the runtime path is written
once and tested with an injected limit.  What M4 adds is the governance action that
lets an operator set a limit at all — see :func:`factory_intelligence.api.set_budget`.
``limit`` staying ``None`` means unlimited, which is the MVP behaviour.
"""

from __future__ import annotations

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class BudgetRow(AiTable):
    __tablename__ = "budget"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: ``None`` = no limit.  Costs are compared in this currency, and the unit prices
    #: in ``ai.model`` must be stated in it too or the comparison is meaningless.
    limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: [0.5, 0.8] — fractions of ``limit`` at which a run result starts reporting a
    #: warning (AI-COST-002)
    thresholds: Mapped[list[float]] = mapped_column(JSONB, nullable=False, default=list)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")

    __table_args__ = (Index("ix_ai_budget_limit", "limit"),)


__all__ = ["BudgetRow"]
