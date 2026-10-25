"""``ai.model_call`` — the usage and cost ledger (AI-LLM-004, AI-COST-001).

One row per model call, written in its **own short transaction immediately after the
call**.  That placement is not an optimisation: if the ledger rode on the run's final
commit, a run that failed validation would erase its own evidence and AI-NFR-03's
"100% of calls have usage and audit records" would be false exactly when it matters.
``tests/integration/test_agent_runtime.py::test_the_run_writes_one_usage_row_per_call_even_for_a_run_that_fails``
pins it — a run that never reaches its commit still shows the two calls it paid for.

The row holds no prompt, no answer and no artifact content — only accounting.  That
is why :func:`factory_intelligence.cleanup.delete_project_data` keeps it while
deleting everything else: AI-DEL-001 asks for the usage records to survive as
"汇总值（不含内容）", and these columns *are* that summary.  The
``ai_model_call_guard`` trigger in migration 0001 blocks UPDATE and DELETE, so a
usage row cannot be edited into a smaller bill later.
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class ModelCallRow(AiTable):
    __tablename__ = "model_call"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Deliberately NOT a foreign key.  A cascade that rewrites or nulls this column
    # when a run is deleted would let the deletion of a run edit its own billing, and
    # the immutability trigger installed by migration 0001 exists to make that
    # impossible.  The value is still recorded, and ``ai.agent_run`` is still the
    # place to look while the run exists (AI-DEL-001).
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # --- denormalised rollup keys: they outlive the run row by design (AI-COST-001)
    # Nullable, and only for one kind of call: AI-NFR-03 demands a row for *every* call, and
    # an evaluation's calls belong to no project — they are booked with the project left out
    # rather than with a fabricated one.  Every project rollup and every budget sum filters
    # on this column, so an absent project drops out of a project's bill by construction.
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_class: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: OK | ERROR — a failed call is still a call, and still costs (AI-LLM-004)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 1-based index within the run; AI-AC-01 asserts an exact call count
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: which budget this call consumed: transport retry, corrective retry, or first
    call_role: Mapped[str] = mapped_column(String(24), nullable=False, default="initial")

    __table_args__ = (
        Index("ix_ai_model_call_project_created", "project_id", "created_at"),
        Index("ix_ai_model_call_run", "run_id"),
        Index("ix_ai_model_call_rollup", "project_id", "stage", "agent_type", "model"),
    )


__all__ = ["ModelCallRow"]
