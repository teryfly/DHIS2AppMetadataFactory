"""``ai.input_parse`` — one deterministic or model-assisted parse (AI-INP-004).

Every row records *what* was parsed, *by which parser at which version*, whether a
model was involved and the outcome, because AI-INP-010's "fixed input gives fixed NRR"
and AI-INP-003's "no model where a parser suffices" are only auditable if the parser
identity is stored next to the result.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class InputParseRow(AiTable):
    __tablename__ = "input_parse"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: content hash of the input, so a re-parse after the artifact was replaced is
    # visible as a different request rather than a contradiction (AI-INP-004)
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    parser_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: column mapping and the other options the caller supplied (AI-INP-006)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    used_model: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: PARSED | NEEDS_REVIEW | FAILED | POLICY_DENIED
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    nrr_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: the ``ParsedInput`` this parse answered, kept so a later call for the *same input with
    #: the same options* can hand back the first result — questions and all — without running
    #: a second parser or writing a second NRR (AI-INP-012)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: AI-INP-014: what the parser proposed for each worksheet when no mapping was supplied
    # — headers, category, proposed mapping, the rule that recognised it, affected keys
    mapping_proposal: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: bytes read, so AI-NFR-06's 100 MB claim is measurable rather than asserted
    input_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    operator: Mapped[str] = mapped_column(String(128), nullable=False, default="system")

    __table_args__ = (
        Index("ix_ai_input_parse_project_created", "project_id", "created_at"),
        Index("ix_ai_input_parse_artifact", "input_artifact_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InputParse {self.id} {self.parser_type} {self.status}>"


__all__ = ["InputParseRow"]
