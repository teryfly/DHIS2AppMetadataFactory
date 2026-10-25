"""Deterministic conflict detection over parsed fragments (AI-INP-009, AC-REQ-01).

This runs **before** any model is consulted and before anything is written: AI-INP-009
requires a conflict between several inputs to become a Question listing each source's
location, and a Question a human answers is only worth having if the machine was certain
it was not a duplicate.  Both halves are therefore here in one module: the normalisation
that decides "same fact, same value", and the grouping that reports the disagreement.

**Normalisation may never invent a conflict.**  A false conflict costs a human a HIGH,
blocking question (FR-REQ-007 stops the Gate while it is unanswered); a missed one is
caught by the Gate reader anyway.  The asymmetry decides the rules, all of them in
:func:`normalise_value` so the question "what did the parser consider equal?" has exactly
one answer in the codebase:

* NFKC, so a full-width ``Ｐ`` and a half-width ``P`` are the same character;
* whitespace collapsed and stripped, so ``"Pregnancy "`` equals ``"Pregnancy"``;
* case-folded, so ``"PREGNANCY"`` equals ``"pregnancy"`` — the SRS names names as
  case-insensitive and an entity label is a name;
* numbers compared as decimals, so ``1.10``, ``1.1`` and ``01.10`` are one value and
  ``"12"`` is not ``"12.0 units"``;
* a *fixed* boolean vocabulary, so ``yes``/``Y``/``true``/``是`` are one value.  Fixed and
  closed on purpose: an open-ended "looks affirmative" test would merge ``"allowed"`` and
  ``"denied"`` the moment a model phrased one of them oddly.

Anything the rules above do not cover is compared verbatim (after NFKC and whitespace),
which is the conservative remainder: two statements that merely read similarly are *not*
forced together, because the parser cannot tell whether they mean the same fact.

Equal values reinforce into one entry (``confidence = max``, union of sources, and
``CONFIRMED`` only when *every* contributor was ``CONFIRMED`` — a single unconfirmed
source keeps the whole fact unconfirmed, which is AI-INP-008's rule applied to corroboration
rather than to extraction).  Different values on one fact all stay ``CANDIDATE``:
**there is no automatic winner**, and the SRS is right that there cannot be one — the two
sources are equally authoritative inputs and choosing between them is a business decision.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from factory_intelligence.enums import NrrStatus, QuestionSeverity
from factory_intelligence.parsing.base import Fragment, NrrCategory, NrrEntry

#: ``payload`` key holding every source a reinforced entry drew from, when more than one
#: contributed.  Deliberately outside ``to_requirements_item()``'s projection: S2's
#: ``requirements.items`` schema has ``additionalProperties: false`` and carries one
#: ``source``, while the NRR must be able to say that two inputs said the same thing.
SOURCE_UNION_KEY = "sourceRefs"

#: The closed boolean vocabulary.  Keys are already normalised (NFKC, casefolded, one
#: space run); a word absent from both sets is a word, not a boolean.
TRUE_WORDS: frozenset[str] = frozenset(
    {"true", "yes", "y", "t", "1", "on", "是", "对", "有", "允许", "正确"}
)
FALSE_WORDS: frozenset[str] = frozenset(
    {"false", "no", "n", "f", "0", "off", "否", "不对", "无", "没有", "禁止", "错误"}
)

#: Fields of an entry that hold a set of values rather than one assertion, so two sources
#: naming different members are agreeing about the same fact, not contradicting it.
MULTI_VALUED_KEYS: frozenset[str] = frozenset({"options", "attributes", "values"})


def normalise_value(value: object) -> str:
    """The canonical form two assertions are compared in (see the module docstring).

    Pure, total and order-independent, so a regression test can pin every rule above
    individually — which is how "normalisation does not fabricate conflicts" is proven
    rather than asserted.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        #: ``inf`` and ``nan`` are numbers Python prints that no fact may assert, and
        #: ``Decimal`` rates two spellings of them equal; their verbatim text is returned so
        #: the comparison stays total (the conservative remainder rule above).
        number = _decimal_text(str(value))
        return number if number is not None else str(value)
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    text = " ".join(text.split()).casefold()
    if not text:
        return ""
    as_number = _decimal_text(text)
    if as_number is not None:
        return as_number
    if text in TRUE_WORDS:
        return "true"
    if text in FALSE_WORDS:
        return "false"
    return text


def _decimal_text(text: str) -> str | None:
    """The same number in one spelling, or ``None`` when the text is not a number."""
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        #: ``nan``/``inf`` are not values a requirement may assert, and ``Decimal``'s own
        #: ``normalize`` would compare two differently-spelled infinities as equal.
        return None
    if number == 0:
        #: ``Decimal("0.0").normalize()`` reads as ``0E-1``; a plain ``0`` keeps ``0.00``,
        #: ``0`` and ``0.0`` one value, which is what a reader would say they are.
        return "0"
    normalised = number.normalize()
    exponent = normalised.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:
        #: ``1E+2`` and ``100`` are the same amount written two ways
        normalised = normalised.quantize(Decimal(1))
    return format(normalised, "f")


@dataclass(frozen=True)
class FactValue:
    """One competing value of one fact, with every entry that asserted it."""

    #: the normalised form two sources were judged equal in
    value: str
    entries: tuple[NrrEntry, ...]

    @property
    def statement(self) -> str:
        return self.entries[0].statement

    def sources(self) -> list[dict[str, Any]]:
        return [
            {
                "artifactId": entry.source_artifact_id,
                "location": str(entry.location) if entry.location is not None else "",
                "value": _asserted_text(entry),
            }
            for entry in self.entries
        ]


@dataclass(frozen=True)
class Conflict:
    """Two or more different normalised values asserted about one fact (AI-INP-009)."""

    category: NrrCategory
    business_key: str
    values: tuple[FactValue, ...]

    @property
    def fact_key(self) -> tuple[str, str, str]:
        return (self.category.value, self.business_key, "statement")

    def sources(self) -> list[dict[str, Any]]:
        """Every competing value with the location it was read from, in a stable order."""
        out: list[dict[str, Any]] = []
        for fact in self.values:
            out.extend(fact.sources())
        return out

    def as_payload(self) -> dict[str, Any]:
        """The ``conflicts[]`` record of the NRR document (AI-INP-004, AI-INP-009)."""
        return {
            "category": self.category.value,
            "businessKey": self.business_key,
            "values": [
                {
                    "value": fact.value,
                    "statement": fact.statement,
                    "sources": fact.sources(),
                }
                for fact in self.values
            ],
            "locations": sorted(
                str(entry.location)
                for fact in self.values
                for entry in fact.entries
                if entry.location is not None
            ),
            "resolution": "NONE — an automatic winner would decide a business fact (AI-INP-009)",
        }

    def question(self) -> dict[str, Any]:
        """The one HIGH, blocking Question this conflict produces.

        Each suggestion carries the source location in its ``rationale`` so the human can
        open the file at the line that disagrees rather than guess which input was meant.
        """
        listed = "; ".join(_position_of(fact.entries[0]) for fact in self.values)
        return {
            "text": (
                f"The inputs disagree about {self.category.value} '{self.business_key}' "
                f"({listed}). Which statement is correct? Neither source was preferred "
                "automatically (AI-INP-009)."
            ),
            "severity": QuestionSeverity.HIGH.value,
            "blocking": True,
            "affects": [f"requirements.items.{self.business_key}"],
            "suggestions": [
                {
                    "id": f"S{index}",
                    "text": fact.statement,
                    "rationale": "; ".join(
                        f"{_artifact_label(entry.source_artifact_id)} at {entry.location}"
                        for entry in sorted(fact.entries, key=_source_order)
                        if entry.location is not None
                    )
                    or f"asserted in {_artifact_label(fact.entries[0].source_artifact_id)}",
                    "changesUid": False,
                    "regenerates": False,
                }
                for index, fact in enumerate(self.values, start=1)
            ],
            "details": {"sources": self.sources(), "factKey": list(self.fact_key)},
        }


@dataclass(frozen=True)
class MergeResult:
    """What merging produced: one entry per fact, plus every conflict found."""

    entries: tuple[NrrEntry, ...]
    conflicts: tuple[Conflict, ...]

    def conflict_payloads(self) -> list[dict[str, Any]]:
        return [conflict.as_payload() for conflict in self.conflicts]

    def conflict_questions(self) -> list[dict[str, Any]]:
        return [conflict.question() for conflict in self.conflicts]

    def with_conflicts(self, extra: Sequence[Conflict]) -> MergeResult:
        """The same merge, plus conflicts found against material outside this parse.

        A new value rather than a mutation: the document, the gate and the questions all read
        ``conflicts``, and the one that disagrees with an earlier input has to reach all three
        or the fact would be confirmed from one of them (AI-INP-009).
        """
        if not extra:
            return self
        return MergeResult(
            entries=self.entries,
            conflicts=tuple(sorted((*self.conflicts, *extra), key=lambda item: item.fact_key)),
        )


def cross_source_conflicts(
    entries: Sequence[NrrEntry], history: Sequence[NrrEntry]
) -> list[Conflict]:
    """Where this parse disagrees with what an *earlier input* already said (AI-INP-009).

    ``parse_input`` takes one artifact, so "several inputs about one fact" can only be seen
    by comparing this parse against the entries the project already recorded — which is what
    ``ai.nrr_entry`` is there for.  Identity is the fact's own category and its base business
    key (the ``disambiguate`` suffix stripped), because two sources naming the same field say
    the same *about* the same thing even when a parser made their keys unique inside one file.

    Nothing is decided here: both statements are returned with their artifact and location,
    and the gate keeps both sides at CANDIDATE.
    """
    if not history:
        return []
    by_fact: dict[tuple[str, str], list[NrrEntry]] = {}
    for item in history:
        by_fact.setdefault((item.category.value, _base_key(item.business_key)), []).append(item)

    out: list[Conflict] = []
    for entry in sorted(entries, key=_source_order):
        peers = by_fact.get((entry.category.value, _base_key(entry.business_key)), [])
        mine = normalise_value(_asserted_text(entry))
        differing = sorted(
            (item for item in peers if normalise_value(_asserted_text(item)) != mine),
            key=_source_order,
        )
        if not differing:
            continue
        out.append(
            Conflict(
                category=entry.category,
                business_key=entry.business_key,
                values=(
                    FactValue(value=mine, entries=(entry,)),
                    *(
                        FactValue(value=normalise_value(_asserted_text(item)), entries=(item,))
                        for item in differing
                    ),
                ),
            )
        )
    return out


def _base_key(business_key: str) -> str:
    """The key without a ``disambiguate`` suffix: ``field-age-2`` and ``field-age`` are one fact."""
    head, _, tail = business_key.rpartition("-")
    return head if head and tail.isdigit() else business_key


def merge_fragments(fragments: Sequence[Fragment]) -> MergeResult:
    """Merge every entry of every fragment into one normalised set of facts."""
    return merge_entries([entry for fragment in fragments for entry in fragment.entries])


def merge_entries(entries: Sequence[NrrEntry]) -> MergeResult:
    """Group by :meth:`NrrEntry.fact_key`, reinforce equal values, report differing ones."""
    groups: dict[tuple[str, str, str], list[NrrEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.fact_key(), []).append(entry)

    merged: list[NrrEntry] = []
    conflicts: list[Conflict] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=_source_order)
        category = NrrCategory(key[0])
        buckets: dict[str, list[NrrEntry]] = {}
        for entry in members:
            buckets.setdefault(normalise_value(_asserted_text(entry)), []).append(entry)
        if len(buckets) > 1:
            #: AI-INP-009: no winner is picked.  Every competing entry stays CANDIDATE and
            #: every one of them is still reported, so nothing a source said is lost.
            conflicts.append(
                Conflict(
                    category=category,
                    business_key=key[1],
                    values=tuple(
                        FactValue(value=value, entries=tuple(buckets[value]))
                        for value in sorted(buckets)
                    ),
                )
            )
            merged.extend(entry.with_status(NrrStatus.CANDIDATE) for entry in members)
            continue
        merged.append(_reinforce(category, key[1], members))
    return MergeResult(entries=tuple(merged), conflicts=tuple(conflicts))


def _reinforce(category: NrrCategory, business_key: str, members: Sequence[NrrEntry]) -> NrrEntry:
    """Collapse entries that said the same thing about the same fact into one entry."""
    primary = members[0]
    if len(members) == 1:
        return primary
    payload = _union_payload(primary.payload, members)
    contributors = {entry.source_artifact_id for entry in members}
    if len(contributors) > 1 or len({str(entry.location) for entry in members}) > 1:
        payload[SOURCE_UNION_KEY] = [
            {
                "artifactId": entry.source_artifact_id,
                "location": str(entry.location) if entry.location is not None else "",
            }
            for entry in sorted(members, key=_source_order)
        ]
    #: an unconfirmed source may not confirm a fact it shares (AI-INP-008), so CONFIRMED
    #: only survives when every contributor was already CONFIRMED
    status = (
        NrrStatus.CONFIRMED
        if all(entry.status is NrrStatus.CONFIRMED for entry in members)
        else NrrStatus.CANDIDATE
    )
    return NrrEntry(
        category=category,
        business_key=business_key,
        statement=primary.statement,
        location=primary.location,
        confidence=max(entry.confidence for entry in members),
        status=status,
        source_artifact_id=primary.source_artifact_id,
        rationale=primary.rationale,
        priority=primary.priority,
        actors=_union_strings(entry.actors for entry in members),
        open_questions=_union_strings(entry.open_questions for entry in members),
        payload=payload,
    )


def _union_payload(primary: Any, members: Sequence[NrrEntry]) -> dict[str, Any]:
    """Union the multi-valued payload keys; scalar payload keys keep the primary's reading.

    A scalar payload disagreement is not a conflict of *fact* — the fact is the statement,
    and ``payload`` is the category's own material — so the first source in stable order
    wins and the rest contribute only their list members.
    """
    payload: dict[str, Any] = dict(primary)
    for entry in members[1:]:
        for key, value in entry.payload.items():
            if key in MULTI_VALUED_KEYS or isinstance(value, list):
                existing = payload.get(key)
                payload[key] = _union_lists(existing, value) if isinstance(existing, list) else value
            else:
                payload.setdefault(key, value)
    return payload


def _union_lists(existing: list[Any], added: list[Any]) -> list[Any]:
    out = list(existing)
    seen = {_identity(item) for item in out}
    for item in added:
        marker = _identity(item)
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


def _union_strings(sources: Iterable[Iterable[Any]]) -> tuple[Any, ...]:
    out: list[Any] = []
    seen: set[str] = set()
    for group in sources:
        for item in group:
            marker = _identity(item)
            if marker not in seen:
                seen.add(marker)
                out.append(item)
    return tuple(sorted(out, key=_identity))


def _identity(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return f"{type(value).__name__}:{value}"
    return f"json:{normalise_value(_canonical(value))}"


def _canonical(value: Any) -> str:
    """A stable text form for a container, so a union of options does not depend on order."""
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)


def _source_order(entry: NrrEntry) -> tuple[str, str]:
    """The stable "first source": artifact id, then the location it was read from.

    Never insertion order: two parses of two files handed over in the opposite order must
    merge to the same NRR, or AC-REQ-01's byte equality would depend on a caller's loop.
    """
    return (entry.source_artifact_id, str(entry.location or ""))


def _asserted_text(entry: NrrEntry) -> str:
    """What the entry asserts: its statement.

    A separate function because it is the seam a reviewer will ask about: the fact key
    names *which* fact, and this is *what* is claimed about it.  Keeping it a function
    means a later change (e.g. comparing a payload field instead) is one edit, not a hunt.
    """
    return entry.statement


def _artifact_label(artifact_id: str) -> str:
    return f"input {artifact_id}" if artifact_id else "an unnamed input"


def _position_of(entry: NrrEntry) -> str:
    """``<input> at <location>`` — the pair AI-INP-009 requires a conflict to list."""
    location = str(entry.location) if entry.location is not None else "an unstated position"
    return f"{_artifact_label(entry.source_artifact_id)} at {location}"


__all__ = [
    "Conflict",
    "FactValue",
    "MergeResult",
    "SOURCE_UNION_KEY",
    "MULTI_VALUED_KEYS",
    "TRUE_WORDS",
    "FALSE_WORDS",
    "merge_entries",
    "merge_fragments",
    "normalise_value",
]
