"""``ai.agent_run``, ``ai.agent_run_context``, ``ai.confirmation_record``.

The run row is the durable authority for one Agent execution: S1's ``apply_agent_result``
branches on its status, and a repeated idempotency key is answered by replaying it
(AI-AC-05).  ``status`` therefore holds a :class:`~factory_intelligence.enums.RunState`,
which has one value — ``RUNNING`` — that can never appear in a result: while a run is
executing there is nothing to return, and the runtime raises a retryable error instead.

``cancel_requested`` exists because S1 always passes ``cancel=None``: the only
cancellation channel that works across the two Temporal workers is a flag in the
database, polled at safe points (AI-RT-020, AI-AC-08).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class AgentRunRow(AiTable):
    __tablename__ = "agent_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    #: S1's stage row id, kept so an audit answer can name the exact stage instance
    stage_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- what this run was configured with (AI-RT-001) -------------------------
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    input_hashes: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    blueprint_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: the design-basis environment, kept so a *resumed* turn can still replenish the UID
    #: pool (AI-RT-013) — S1 supplies it once, on the start request (AI-RT-002)
    design_reference_environment_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    #: the discovery result behind AI-AG-011's similar objects and the capability profile
    #: the model had to fit.  Both are part of *what the run was given*, so a resumed turn
    #: that rebuilt its request without them would judge a different context than the first
    #: turn did — the similar-object requirement would silently disappear from turn two.
    discovery_result_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capability_profile_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- what it produced ------------------------------------------------------
    output_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    patch_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    question_local_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RUNNING")
    #: usage and cost are copied onto the row so ``get_agent_run`` needs no join
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: AI-RT-019: the query must answer 最近进度, and a run that is still executing has
    #: nothing else to answer with.  Written by the progress reporter, not by the caller.
    last_progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancel_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    operator: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_ai_agent_run_project_stage", "project_id", "stage"),
        Index("ix_ai_agent_run_workflow", "workflow_id"),
        # AI-AC-05: the lookup that answers a repeated call
        Index("ix_ai_agent_run_idempotency", "idempotency_key", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AgentRun {self.id} {self.agent_type} {self.status}>"


class AgentRunContextRow(AiTable):
    """The state needed to continue a run that is waiting for input (AI-RT-015).

    ``pending_question_local_ids`` is a column rather than something read out of the
    context artifact because the runtime must decide "still waiting?" inside a
    transaction, where touching object storage is forbidden (00 doc 6.2).
    """

    __tablename__ = "agent_run_context"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: versioned artifact holding the message sequence, classified at the maximum of
    #: its sources; a new version per turn (AI-RT-015)
    context_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    pending_question_local_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    #: model-emitted question id -> S3 canonical local id, so a resume can address the
    #: question S1 stored (S1 dedupes on ``(agent_run_id, source_local_id)``)
    question_id_map: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    submitted_patch_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    raised_question_local_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    base_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (Index("ix_ai_run_context_project", "project_id"),)


class ConfirmationRecordRow(AiTable):
    """AI-RT-016: a user answered a question that gates a decision.

    S2 decides whether a low-confidence decision carries a confirmation record
    (BP-DEC-004); this table is the S3-side evidence that the runtime wrote into the
    patch, deduplicated per question so a resume that re-sends the whole accumulated
    answer list does not emit the same operation twice.
    """

    __tablename__ = "confirmation_record"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_local_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: where the confirmation operation was addressed: /mapping/entries/<bk>/confirmation
    target_path: Mapped[str] = mapped_column(String(512), nullable=False)
    answerer: Mapped[str] = mapped_column(String(128), nullable=False)
    suggestion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    patch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index(
            "uq_ai_confirmation_run_question",
            "run_id",
            "question_local_id",
            unique=True,
        ),
        Index("ix_ai_confirmation_project", "project_id"),
    )


__all__ = ["AgentRunRow", "AgentRunContextRow", "ConfirmationRecordRow"]
