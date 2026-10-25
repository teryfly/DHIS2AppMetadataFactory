"""``ai.nrr_entry`` — one searchable row per NRR entry (AI-INP-015).

AI-INP-015 puts an NRR's *category and parsing detail* in "S3's NRR artifact and the ``ai``
schema", and explicitly not in S2's closed ``requirements`` section.  The artifact holds the
whole document; this table holds the same entries projected one row each, because a query
that filters by category and confidence interval and paginates cannot be served by reading
every NRR artifact of a project — and reading artifact content is external I/O, which
SRS 7 does not give a 只读 function (00 doc 6.2).

The rows are written by the parse that produced them and deleted with them
(:data:`factory_intelligence.models.DELETABLE_TABLES`): they are parse output, not a second
copy of the requirements, and the artifact stays the authoritative document.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class NrrEntryRow(AiTable):
    __tablename__ = "nrr_entry"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: the parse that produced this entry, so a query can name its record (AI-INP-013)
    parse_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nrr_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_key: Mapped[str] = mapped_column(String(256), nullable=False)
    #: one of AI-INP-005's thirteen categories
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: where in the input it was read from (AI-INP-005), in ``kind:value`` form
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: CANDIDATE | CONFIRMED | REJECTED — ``CONFIRMED`` is "已确认" (AI-INP-008)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        #: two parses of one input with one option set give one row per entry; a re-parse
        #: with a different mapping is a different parse and therefore different rows
        UniqueConstraint(
            "parse_id", "input_artifact_id", "business_key", name="uq_ai_nrr_entry_per_parse"
        ),
        Index("ix_ai_nrr_entry_project_category", "project_id", "category"),
        Index("ix_ai_nrr_entry_project_confidence", "project_id", "confidence"),
        Index("ix_ai_nrr_entry_project_artifact", "project_id", "input_artifact_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<NrrEntry {self.id} {self.category} {self.business_key}>"


__all__ = ["NrrEntryRow"]
