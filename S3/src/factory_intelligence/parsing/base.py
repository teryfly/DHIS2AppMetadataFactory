"""Parser contract and NRR construction (AI-INP-002, AI-INP-004, AI-INP-005).

Two shapes are defined here and every parser in this package produces them.

**A ``Location`` is mandatory, not optional.**  AI-INP-005 requires every NRR record to
carry "来源引用（输入产物 ID + 位置）", and AI-INP-009 requires a conflict to list "各来源的
位置" — a parser that cannot say where a fact came from satisfies neither, and a conflict
would be unrepresentable.  That is why ``Location`` is part of the protocol's output type
rather than a field each parser invents, and why one canonical string form
(``sheet:字段!B3``, ``line:44``, ``xref:/dataElements/0``) is defined here: it is the value
that lands in S2's ``source.location``, so it must read the same however it was produced.

**An NRR entry is a superset of S2's ``requirements.items``.**  AI-INP-005 says the NRR
schema *is* that section's schema, and for the fields a Blueprint stores it is; AI-INP-005
additionally demands thirteen categories, which the section does not model (its
``additionalProperties`` is false).  So :class:`NrrEntry` carries the section's exact field
names plus ``category``, and :meth:`NrrEntry.to_requirements_item` is the deliberate
projection that drops the extra when a Requirements Agent patches one into a draft.  Both
halves are tested against S2's real schema, so neither claim can rot quietly.
"""

from __future__ import annotations

import enum
import hashlib
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from factory_foundation.api import DataClassification

from factory_intelligence.enums import (
    DETERMINISTIC_PARSERS,
    NrrStatus,
    ParserType,
)


#: Thirteen categories, so a parser that cannot place a fact has no valid answer.  A
#: catch-all would let the categories silently stop meaning anything.
@enum.unique
class NrrCategory(str, enum.Enum):
    """The thirteen categories AI-INP-005 requires an NRR to cover."""

    BUSINESS_GOAL = "businessGoal"
    PARTICIPANT = "participant"
    BUSINESS_ENTITY = "businessEntity"
    USE_CASE = "useCase"
    BUSINESS_PROCESS = "businessProcess"
    BUSINESS_RULE = "businessRule"
    DATA_REQUIREMENT = "dataRequirement"
    ORGANISATION = "organisationRequirement"
    ACCESS = "accessRequirement"
    REPORT = "reportRequirement"
    NOTIFICATION = "notificationRequirement"
    I18N = "i18nRequirement"
    OPEN_QUESTION = "openQuestion"


@enum.unique
class LocationKind(str, enum.Enum):
    """The coordinate vocabularies the parsers share (see the module docstring)."""

    LINE = "line"
    SHEET = "sheet"
    ROW = "row"
    PAGE = "page"
    XREF = "xref"
    REGION = "region"
    SECTION = "section"
    DIAGRAM = "diagram"


@dataclass(frozen=True)
class Location:
    """Where in an input a fact was read (AI-INP-005, AI-INP-009)."""

    kind: LocationKind
    value: str

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.value}"

    @classmethod
    def line(cls, number: int) -> Location:
        return cls(LocationKind.LINE, str(number))

    @classmethod
    def cell(cls, sheet: str, cell: str) -> Location:
        """A spreadsheet cell, e.g. ``sheet:字段!B3``."""
        return cls(LocationKind.SHEET, f"{sheet}!{cell}")

    @classmethod
    def row(cls, number: int, *, sheet: str | None = None) -> Location:
        return cls(LocationKind.ROW, f"{sheet}#{number}" if sheet else str(number))

    @classmethod
    def page(cls, number: int) -> Location:
        return cls(LocationKind.PAGE, str(number))

    @classmethod
    def pointer(cls, pointer: str) -> Location:
        """A JSON pointer into an existing DHIS2 export or discovery result."""
        return cls(
            LocationKind.XREF, pointer if pointer.startswith("/") else f"/{pointer.lstrip('/')}"
        )

    @classmethod
    def region(cls, shape: str, name: str) -> Location:
        return cls(LocationKind.REGION, f"{shape}({name})")

    @classmethod
    def section(cls, heading: str) -> Location:
        """The heading a piece of document text sat under (AI-INP-005).

        A Word document has no lines and no cells: what a reader navigates by is the
        heading, so that is the coordinate the ``docx`` extractor reports.
        """
        return cls(LocationKind.SECTION, heading)

    @classmethod
    def diagram_element(cls, name: str) -> Location:
        return cls(LocationKind.DIAGRAM, name)


@dataclass(frozen=True)
class NrrEntry:
    """One normalised requirement: S2's field names, plus the category it belongs to."""

    category: NrrCategory
    business_key: str
    statement: str
    location: Location | None = None
    #: 1.0 for a deterministic parse — a measured fact, not an estimate (AI-INP-010)
    confidence: float = 1.0
    #: CANDIDATE until a human or a deterministic parse confirms it (AI-INP-008)
    status: NrrStatus = NrrStatus.CANDIDATE
    source_artifact_id: str = ""
    rationale: str | None = None
    priority: str | None = None
    actors: tuple[str, ...] = ()
    open_questions: tuple[dict[str, Any], ...] = ()
    #: the category's own material (attributes, steps, fields); kept out of the S2
    #: projection so a Blueprint never receives a key its schema forbids
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_requirements_item(self) -> dict[str, Any]:
        """Project onto ``requirements.items`` (``additionalProperties: false``)."""
        item: dict[str, Any] = {
            "businessKey": self.business_key,
            "statement": self.statement,
            "status": self.status.value,
            "confidence": self.confidence,
            "source": self.source_payload(),
        }
        if self.rationale:
            item["rationale"] = self.rationale
        if self.priority:
            item["priority"] = self.priority
        if self.actors:
            item["actors"] = list(self.actors)
        if self.open_questions:
            item["openQuestions"] = [dict(question) for question in self.open_questions]
        return item

    def to_nrr_record(self) -> dict[str, Any]:
        """The NRR artifact's own record, with the category the section cannot hold."""
        record = self.to_requirements_item()
        record["category"] = self.category.value
        if self.payload:
            record["payload"] = dict(self.payload)
        return record

    def source_payload(self) -> dict[str, Any]:
        source: dict[str, Any] = {}
        if self.source_artifact_id:
            source["artifactId"] = self.source_artifact_id
        if self.location is not None:
            source["location"] = str(self.location)
        return source

    def with_status(self, status: NrrStatus, *, confidence: float | None = None) -> NrrEntry:
        changes: dict[str, Any] = {"status": status}
        if confidence is not None:
            changes["confidence"] = confidence
        return replace(self, **changes)

    def fact_key(self) -> tuple[str, str, str]:
        """Identity for conflict detection: two entries asserting about the same thing.

        The field is part of the key so a conflict is between *the same claim* about an
        entity rather than between any two statements that name it (AI-INP-009).
        """
        return (self.category.value, self.business_key, "statement")


@dataclass
class Fragment:
    """What one parser produced from one input artifact (AI-INP-004)."""

    parser_type: ParserType
    parser_version: str
    source_artifact_id: str
    entries: list[NrrEntry] = field(default_factory=list)
    #: questions the parser could not resolve itself, e.g. an unconfirmed column mapping
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    #: a deterministic parser whose *structure* needs a human (AI-INP-002, AI-INP-006)
    needs_review: bool = False
    review_reason: str | None = None
    #: anything the parse record should carry alongside parserVersion
    notes: dict[str, Any] = field(default_factory=dict)
    #: taken from the input artifact; the NRR may not be classified below it (FD-ART-008)
    classification: DataClassification = DataClassification.INTERNAL

    @property
    def used_model(self) -> bool:
        """Whether a model was involved, per the parser table rather than by request."""
        return self.parser_type not in DETERMINISTIC_PARSERS

    @property
    def is_empty(self) -> bool:
        return not self.entries and not self.open_questions

    def extend(self, entries: Iterable[NrrEntry]) -> None:
        self.entries.extend(entries)


class ParseError(Exception):
    """A parser's own failure, carrying the error code the SRS names (AI-INP-003a).

    An exception rather than a status because the caller must not be able to forget it:
    AI-INP-003a exists to stop a parse producing a silently empty NRR.
    """

    def __init__(self, code: str, message: str, *, location: Location | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.location = location


@runtime_checkable
class Parser(Protocol):
    """One deterministic or model-assisted parser (AI-INP-002)."""

    parser_type: ParserType
    parser_version: str

    def parse(
        self,
        content: bytes,
        *,
        source_artifact_id: str,
        classification: DataClassification,
        options: Mapping[str, Any],
    ) -> Fragment:
        """Turn bytes into NRR entries.

        Pure over ``content``: AI-INP-010 and AC-REQ-01 require the same input to parse to
        the same NRR twice, so a parser may not read a clock, a random number or a
        database.
        """
        ...


def business_key(category: NrrCategory | str, label: str) -> str:
    """A deterministic businessKey from a human label (AI-INP-010).

    S2 requires a businessKey to be unique per container and to look like an identifier,
    and the same input must give the same key twice — so this is a slug, with a digest
    appended only when the label has no usable characters at all.
    """
    slug = _slug(label)
    prefix = _slug(str(category.value if isinstance(category, NrrCategory) else category))
    if not slug:
        slug = "x" + _digest(label)[:8]
    return f"{prefix}_{slug}" if prefix else slug


def disambiguate(existing: Sequence[str], candidate: str, *, seed: str) -> str:
    """Make ``candidate`` unique within ``existing``, deterministically.

    The seed is the entry's location, so the result depends on the input and never on
    insertion order or a counter — which would make two parses of one file disagree.
    """
    if candidate not in existing:
        return candidate
    for take in (4, 6, 8, 12):
        tagged = f"{candidate}_{_digest(seed)[:take]}"
        if tagged not in existing:
            return tagged
    return f"{candidate}_{_digest(seed + candidate)[:12]}"


def _slug(value: str) -> str:
    normalised = (
        unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    )
    return "_".join(_split_tokens(normalised)[:8]).lower()[:80]


def _split_tokens(value: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for char in value:
        if char.isalnum():
            current.append(char)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "NrrCategory",
    "LocationKind",
    "Location",
    "NrrEntry",
    "Fragment",
    "ParseError",
    "Parser",
    "business_key",
    "disambiguate",
]
