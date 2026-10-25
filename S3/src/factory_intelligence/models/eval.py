"""``ai.eval_run`` and ``ai.eval_case_result`` — AI-EVAL-001/005.

An evaluation run is written whether it passed or not: the *absence* of a passing
evaluation is the fact AI-REG-003 gates on, and a deleted failure would look like a
missing record rather than a refused change.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class EvalRunRow(AiTable):
    __tablename__ = "eval_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("ai.prompt_version.id"), nullable=True
    )
    #: the prompt identity as declared, kept even if the version row is later replaced
    prompt_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    eval_set_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: {"cases": 12, "matched": 11, "injectionPassed": true, "modelCalls": 12,
    #: "inputTokens": 48000, "outputTokens": 9000, "estimatedCost": 0.12}
    #: The last four are the run's own reading of the calls it made; the per-call figures are
    #: in ``ai.model_call`` under ``workflow_id = "eval:<this id>"`` with no project, because
    #: an evaluation spends no project's budget (AI-NFR-03 still books each call).
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    run_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")

    __table_args__ = (
        Index("ix_ai_eval_run_agent_prompt", "agent_type", "prompt_name", "prompt_version"),
    )


class EvalCaseResultRow(AiTable):
    """The per-item difference AI-EVAL-001 asks an evaluation to produce."""

    __tablename__ = "eval_case_result"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    eval_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("ai.eval_run.id"), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: AI-EVAL-003: an injection case passes by *not* containing what the injection asked for
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="golden")
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_ai_eval_case_run", "eval_run_id"),)


__all__ = ["EvalRunRow", "EvalCaseResultRow"]
