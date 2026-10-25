"""``bp.decision`` — mapping and architecture decision index (BP-DEC-*).

Full decision content (alternatives, rationale, risks, confirmation) lives in
the version content; this index keeps the fields Gate summaries and freeze
validation need to find quickly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.models.base import BpTable


class DecisionRow(BpTable):
    __tablename__ = "decision"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    blueprint_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: decision ID as written in the Blueprint (e.g. ``MAP-017``)
    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_business_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    alternatives: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    risks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    impact_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: {questionId, answerer} or {operator} for a human patch (BP-DEC-004)
    confirmation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    affected_artifacts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("project_id", "blueprint_version", "decision_id"),
        Index("ix_bp_decision_impact", "project_id", "blueprint_version", "impact_level"),
    )
