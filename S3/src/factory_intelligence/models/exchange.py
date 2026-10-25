"""``ai.llm_exchange`` — the prompt and answer of one parse-time model call.

This table exists because a parse failure is often a question about what the model was
actually asked; ``ai.model_call`` deliberately stores only accounting (AI-LLM-004 keeps it
free of content, and its trigger refuses edits).  The exchange is an *operational* log: it
is written only for business-input parsing, it carries the same classification as the input
it quotes, it expires (``expires_at``) and it is deleted with its project (AI-DEL-001).

The row is written in two steps so an operator can watch a slow answer arrive: ``IN_FLIGHT``
with the request before the provider is called, then ``OK``/``ERROR`` with the raw answer or
the failure.  A process that dies in between leaves ``IN_FLIGHT`` behind, which the read
model projects as ``INTERRUPTED`` once the row is older than any possible attempt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class LlmExchangeRow(AiTable):
    __tablename__ = "llm_exchange"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: the business input this exchange belongs to; only parse calls set it, which is what
    #: keeps agent-run and evaluation calls out of a log that exists for parse diagnosis
    input_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``parse:<artifact>`` — the same correlation the usage ledger carries for a parse
    workflow_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 1-based order of the calls one parse made, retries included
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    call_role: Mapped[str] = mapped_column(String(24), nullable=False, default="extraction")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    #: IN_FLIGHT | OK | ERROR (INTERRUPTED is a read-time projection, never stored)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="IN_FLIGHT")
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: messages (images as metadata only), schema and call parameters
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: the vendor's own words, bounded on write; the product still only uses the validated
    #: envelope, but a diagnostic log without the raw answer cannot explain a bad parse
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: usage, finish reason, truncation and the storage-truncation marker
    response_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: retention: after this the daily job deletes the row; never null
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_ai_llm_exchange_artifact", "project_id", "input_artifact_id", "created_at"),
        Index("ix_ai_llm_exchange_expires", "expires_at"),
        Index("ix_ai_llm_exchange_status", "status", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LlmExchange {self.id} {self.status} seq={self.sequence}>"


__all__ = ["LlmExchangeRow"]
