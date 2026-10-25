"""Read models over the parse records and the NRR index (AI-INP-013/014/015).

SRS V1.1.0 added these three reads because the workbench cannot show a per-input parse
status, a column-mapping table to review, or NRR entries by category out of S1's own rows:
S1 stores the step it ran, not what S3 concluded (CP-UI-007, CP-UI-003).  S3 owns the
facts, so S3 answers them.

All three are pure ``ai``-schema reads — no artifact content is opened.  That is a design
point, not an optimisation: SRS 7 classes these 只读, and reading artifact content is
external I/O, which 00 doc 6.2 forbids inside the caller's transaction that S1 holds on
every one of these endpoints.  The parse therefore projects what a query needs into
``ai.input_parse.mapping_proposal`` and ``ai.nrr_entry`` when it writes its record
(AI-INP-015 puts an NRR's category and parsing detail in the NRR artifact *and* the ``ai``
schema, and explicitly not in S2's closed ``requirements`` section).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from factory_foundation.api import (
    current_session,
    has_active_uow,
    unit_of_work,
)
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from factory_intelligence.dto.governance import (
    ColumnMappingProposal,
    NrrEntryView,
    NrrFilters,
    NrrQueryResult,
    Page,
    ParseRecord,
    SheetColumnMapping,
)
from factory_intelligence.enums import NrrStatus, ParseStatus
from factory_intelligence.models.nrr import NrrEntryRow
from factory_intelligence.models.parse import InputParseRow
from factory_intelligence.parsing.base import (
    Location,
    LocationKind,
    NrrCategory,
    NrrEntry,
)

__all__ = ["list_input_parses", "get_column_mapping_proposal", "query_nrr", "history_entries", "record_of"]


T = TypeVar("T")


def _read(session: Session | None, action: Callable[[Session], T]) -> T:
    if session is not None or has_active_uow():
        return action(session if session is not None else current_session())
    with unit_of_work() as sess:
        return action(sess)


def record_of(row: InputParseRow) -> ParseRecord:
    """One ``ai.input_parse`` row as AI-INP-013's record."""
    return ParseRecord(
        parse_id=row.id,
        project_id=row.project_id,
        input_artifact_id=row.input_artifact_id,
        input_hash=row.input_hash,
        parser_type=row.parser_type,
        parser_version=row.parser_version,
        used_model=row.used_model,
        status=ParseStatus(row.status),
        nrr_artifact_id=row.nrr_artifact_id,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
    )


def list_input_parses(
    project_id: str,
    input_artifact_ids: Sequence[str] | None = None,
    *,
    session: Session | None = None,
) -> list[ParseRecord]:
    """The most recent parse of each input, one record per artifact (AI-INP-013).

    "Most recent" is what the workbench shows: re-parsing after a confirmed column mapping
    (AI-INP-006) supersedes the earlier attempt rather than adding a row a reviewer has to
    order by hand.  An artifact that was never parsed is simply absent — an absent status
    is not a failed one, and S1 renders it as "未解析".
    """
    wanted = [str(item) for item in (input_artifact_ids or [])]

    def _query(sess: Session) -> list[ParseRecord]:
        statement: Select[Any] = (
            select(InputParseRow)
            .where(InputParseRow.project_id == project_id)
            .distinct(InputParseRow.input_artifact_id)
            .order_by(InputParseRow.input_artifact_id, InputParseRow.created_at.desc())
        )
        if wanted:
            statement = statement.where(InputParseRow.input_artifact_id.in_(wanted))
        rows: list[InputParseRow] = list(sess.execute(statement).scalars().all())
        return [record_of(row) for row in rows]

    return _read(session, _query)


def get_column_mapping_proposal(
    project_id: str,
    input_artifact_id: str,
    *,
    session: Session | None = None,
) -> ColumnMappingProposal:
    """AI-INP-014: each worksheet's headers, category, proposal, basis and affected keys.

    The UI renders this as a table of *source column → target field* for one reviewer to
    confirm, and the confirmed mapping goes back verbatim as ``options.columnMapping``
    (AI-INP-006).  An input that was never parsed, or one whose parser needed no mapping,
    answers with no sheets rather than an error: "nothing to review" and "we could not look"
    differ only in the parse record, which ``list_input_parses`` already returned.
    """

    def _query(sess: Session) -> ColumnMappingProposal:
        row = (
            sess.execute(
                select(InputParseRow)
                .where(
                    InputParseRow.project_id == project_id,
                    InputParseRow.input_artifact_id == input_artifact_id,
                )
                .order_by(InputParseRow.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is None:
            return ColumnMappingProposal(
                input_artifact_id=input_artifact_id, project_id=project_id, sheets=[]
            )
        sheets = [
            SheetColumnMapping(
                sheet=str(item.get("sheet") or ""),
                headers=[str(header) for header in (item.get("headers") or [])],
                category=item.get("category"),
                mapping={
                    str(key): str(value) for key, value in (item.get("mapping") or {}).items()
                },
                recognised_by=item.get("recognisedBy"),
                affected_keys=[str(key) for key in (item.get("affectedKeys") or [])],
                question=item.get("question"),
            )
            for item in (row.mapping_proposal or [])
            if isinstance(item, dict)
        ]
        return ColumnMappingProposal(
            input_artifact_id=input_artifact_id,
            project_id=project_id,
            parse_id=row.id,
            status=ParseStatus(row.status),
            sheets=sheets,
        )

    return _read(session, _query)


def history_entries(
    project_id: str,
    *,
    exclude_artifact_id: str | None = None,
    limit: int = 5000,
    session: Session | None = None,
) -> list[NrrEntry]:
    """The project's earlier NRR entries, as entries (AI-INP-009).

    Read from ``ai.nrr_entry`` rather than from the artifacts: a conflict is a comparison of
    what two inputs said, and the index already carries both sides with their location.  The
    *latest* parse of each earlier input is the one that still stands, so superseded attempts
    cannot raise a conflict against a statement the project already corrected.
    """

    def _query(sess: Session) -> list[NrrEntry]:
        latest = (
            select(InputParseRow.input_artifact_id.label("artifact"), func.max(InputParseRow.id).label("parse"))
            .where(InputParseRow.project_id == project_id)
            .group_by(InputParseRow.input_artifact_id)
            .subquery()
        )
        conditions = [
            NrrEntryRow.project_id == project_id,
            NrrEntryRow.input_artifact_id == latest.c.artifact,
            NrrEntryRow.parse_id == latest.c.parse,
        ]
        if exclude_artifact_id:
            conditions.append(NrrEntryRow.input_artifact_id != exclude_artifact_id)
        out: list[NrrEntry] = []
        for row in (
            sess.execute(select(NrrEntryRow).where(*conditions).limit(int(limit)))
            .scalars()
            .all()
        ):
            out.append(
                NrrEntry(
                    category=NrrCategory(row.category),
                    business_key=row.business_key,
                    statement=row.statement,
                    location=_location_of(row.location),
                    confidence=row.confidence,
                    status=NrrStatus(row.status),
                    source_artifact_id=row.input_artifact_id,
                )
            )
        return out

    return _read(session, _query)


def _location_of(text: str | None) -> Location | None:
    """``kind:value`` back into a :class:`Location`, or ``None`` when it says nothing."""
    if not text or ":" not in text:
        return None
    kind, _, value = text.partition(":")
    try:
        return Location(LocationKind(kind), value)
    except ValueError:
        return None


def _entry_view(row: NrrEntryRow) -> NrrEntryView:
    return NrrEntryView(
        business_key=row.business_key,
        # AI-INP-005's category vocabulary, in the spelling the NRR artifact uses
        category=row.category,
        statement=row.statement,
        artifact_id=row.input_artifact_id,
        location=row.location,
        confidence=row.confidence,
        confirmed=row.confirmed,
        parse_id=row.parse_id,
    )


def query_nrr(
    project_id: str,
    filters: NrrFilters | Mapping[str, Any] | None = None,
    page: Page | Mapping[str, Any] | None = None,
    *,
    session: Session | None = None,
) -> NrrQueryResult:
    """AI-INP-015: NRR entries by category, input artifact and confidence interval.

    Only the *latest* parse of an input is reported, because an earlier attempt's entries
    are superseded facts and a workbench that showed both would count one requirement
    twice.  Ordering is deterministic — category, then business key — so page two cannot
    repeat or drop an entry that page one already showed.
    """
    chosen = (
        filters
        if isinstance(filters, NrrFilters)
        else NrrFilters.model_validate(dict(filters or {}))
    )
    window = page if isinstance(page, Page) else Page.model_validate(dict(page or {}))
    categories = [str(item) for item in chosen.categories]
    # accept either the enum member name or its value, both of which a caller has seen
    categories = [
        NrrCategory[item].value if item in NrrCategory.__members__ else item for item in categories
    ]
    artifacts = [str(item) for item in chosen.input_artifact_ids]

    def _conditions() -> list[Any]:
        out: list[Any] = [NrrEntryRow.project_id == project_id]
        if categories:
            out.append(NrrEntryRow.category.in_(categories))
        if artifacts:
            out.append(NrrEntryRow.input_artifact_id.in_(artifacts))
        if chosen.min_confidence is not None:
            out.append(NrrEntryRow.confidence >= chosen.min_confidence)
        if chosen.max_confidence is not None:
            out.append(NrrEntryRow.confidence <= chosen.max_confidence)
        if chosen.confirmed_only is True:
            out.append(NrrEntryRow.status == NrrStatus.CONFIRMED.value)
        elif chosen.confirmed_only is False:
            out.append(NrrEntryRow.status != NrrStatus.CONFIRMED.value)
        return out

    def _query(sess: Session) -> NrrQueryResult:
        latest = (
            select(InputParseRow.input_artifact_id.label("artifact"), func.max(InputParseRow.id).label("parse"))
            .where(InputParseRow.project_id == project_id)
            .group_by(InputParseRow.input_artifact_id)
            .subquery()
        )
        conditions = [
            *_conditions(),
            NrrEntryRow.input_artifact_id == latest.c.artifact,
            NrrEntryRow.parse_id == latest.c.parse,
        ]
        total = int(
            sess.execute(select(func.count()).select_from(NrrEntryRow).where(*conditions)).scalar()
            or 0
        )
        rows = (
            sess.execute(
                select(NrrEntryRow)
                .where(*conditions)
                .order_by(NrrEntryRow.category, NrrEntryRow.business_key)
                .limit(window.limit)
                .offset(window.offset)
            )
            .scalars()
            .all()
        )
        return NrrQueryResult(
            items=[_entry_view(row) for row in rows],
            total=total,
            limit=window.limit,
            offset=window.offset,
        )

    return _read(session, _query)
