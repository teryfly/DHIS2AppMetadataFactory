"""Tables for capabilities that are gated, not absent: in-stage chat (M3) and
desensitisation (M4).

Both are created by migration 0001 even though their code paths raise
:class:`~factory_intelligence.errors.FeatureNotEnabled`.  The reason is AI-DATA-01:
``ai`` schema changes go through migration scripts, and a table that arrives with M3
forces a schema change into a milestone whose SRS deliverable is agents and
compilers, not DDL.  The columns also pin the contract now — in particular that the
desensitisation mapping is stored *encrypted* and that only S3 can read it
(AI-POL-009, AI-DATA-02).
"""

from __future__ import annotations

from sqlalchemy import Boolean, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class ChatSessionRow(AiTable):
    """AI-CHAT-003: one session per project stage, history kept as an artifact."""

    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    context_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: clearing a session supersedes the artifact rather than deleting history
    cleared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("project_id", "stage", name="uq_ai_chat_session_project_stage"),
    )


class DesensitizationRecordRow(AiTable):
    """AI-POL-009 / AI-DATA-02: the substitution map never leaves S3, and never in clear.

    ``mapping_encrypted`` holds ciphertext keyed by an S0 Secret, because the mapping
    *is* the sensitive data: knowing that "Patient A" was replaced by "P-1" undoes the
    redaction.  S0 stores only the reference to the derived artifact, never the map.
    """

    __tablename__ = "desensitization_record"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    derived_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    mapping_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    #: S0 Secret reference for the key; the key itself is never in this row
    key_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)


__all__ = ["ChatSessionRow", "DesensitizationRecordRow"]
