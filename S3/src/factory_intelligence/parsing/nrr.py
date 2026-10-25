"""The NRR document: one normalised requirements artifact per parse (AI-INP-004, AI-INP-005).

The NRR is S3's own record of what an extraction produced, kept apart from the Blueprint's
``requirements`` section on purpose: the section holds the *confirmed* content of the design
after a human Gate, while this document holds the whole result — every item with its
confidence, every conflict with both sides, every question that came out of the parse.  A
later Requirements Agent reads this, cites it, and patches the section (AI-AG-001).

**Byte equality is the point.**  AC-REQ-01 and AI-INP-010 say two parses of one input give
the same result, and the cheapest proof of that is comparing the two artifacts' hashes —
which only works if the document is a deterministic function of the input bytes.  So every
list here is sorted before it is written:

* ``items`` by ``(category, businessKey, location, statement)`` — never by the order the
  parsers happened to emit them, and never by the order a caller handed over the fragments;
* ``conflicts`` and ``questions`` likewise, by their own keys;
* ``parserVersions`` and ``inputHashes`` are keyed maps, and S0's ``file_json`` sorts keys.

Serialisation itself is S0's canonical file form (00 doc 6.7): this module never writes a
JSON string, only the structure, so "same content, same hash" is one rule for the whole
factory rather than one per subsystem.  ``allow_nan=False`` in that form is also why no
float here may be ``nan`` or ``inf`` — a confidence the model reported as a non-finite
number is clamped where it is read, not here.

:meth:`build_record` is the half the *artifact metadata* carries: AI-INP-004 requires the
parse record and the artifact's build record to hold ``parserVersion``, and a caller that
had to compose that dict itself would be free to forget a key.  It is built here, from the
same fragments, so the NRR document and the build record cannot disagree about which parser
ran.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from factory_foundation.api import structure_hash

from factory_intelligence.dto.common import Question
from factory_intelligence.enums import QUESTION_SEVERITY_RANK, NrrStatus, QuestionSeverity
from factory_intelligence.parsing.base import Fragment, NrrEntry
from factory_intelligence.parsing.columnmapping import mapping_review_questions
from factory_intelligence.parsing.confidence import GateResult
from factory_intelligence.parsing.merge import MergeResult

#: The shape of this document.  A reader that sees a version it does not know can refuse
#: rather than mis-read a field whose meaning has since moved.
NRR_VERSION = "1.0.0"

#: The artifact type S3 registered for it (``registries.AI_ARTIFACT_TYPES``).
NRR_ARTIFACT_TYPE = "NRR"

#: Local question ids, numbered the way ``runtime/questions.py`` numbers a run's: the most
#: urgent first, then by what they address, then by text.  A parse has no run to renumber
#: against, so the same order is what makes two parses give the same ids.
QUESTION_ID_PREFIX = "Q"

#: Keys of a raw question this module assembles (see ``merge``, ``confidence``,
#: ``columnmapping``): anything else is dropped rather than passed into the DTO, whose
#: ``extra="ignore"`` would otherwise hide a producer that changed its shape.
_QUESTION_FIELDS = ("text", "severity", "blocking", "affects", "suggestions", "details")


def parser_versions(fragments: Sequence[Fragment]) -> dict[str, str]:
    """``parserType -> parserVersion`` for every parser that contributed (AI-INP-004)."""
    return {
        str(fragment.parser_type.value): fragment.parser_version for fragment in fragments
    }


def parser_version_label(fragments: Sequence[Fragment]) -> str:
    """The single ``parserVersion`` string a build record carries.

    One value per parser that ran, joined in a sorted order: a parse of two inputs really
    did use two versions, and naming only one would make the artifact's own record wrong.
    """
    pairs = sorted(f"{key}@{value}" for key, value in parser_versions(fragments).items())
    return ",".join(pairs) if pairs else "none"


def input_artifact_ids(fragments: Sequence[Fragment]) -> list[str]:
    seen: dict[str, None] = {}
    for fragment in fragments:
        if fragment.source_artifact_id:
            seen.setdefault(fragment.source_artifact_id, None)
    return sorted(seen)


def input_hashes(fragments: Sequence[Fragment]) -> dict[str, str]:
    """``artifactId -> "sha256:<hex>"`` — the pair AI-INP-004 requires of every parse."""
    out: dict[str, str] = {}
    for fragment in fragments:
        recorded = fragment.notes.get("inputHash")
        if isinstance(recorded, str) and recorded:
            out[fragment.source_artifact_id] = recorded
    return dict(sorted(out.items()))


def used_model(fragments: Sequence[Fragment]) -> bool:
    """Whether any contributing parser is model-assisted (AI-INP-004)."""
    return any(fragment.used_model for fragment in fragments)


def proposals_of(fragments: Sequence[Fragment]) -> tuple[dict[str, Any], ...]:
    """AI-INP-014's structured proposal, one record per worksheet that needed a mapping.

    Read out of the same ``mappingReview`` records that produced the AI-INP-006 Questions, so
    the table the UI shows and the question a human answered cannot disagree about what was
    proposed.  Sorted by ``(artifact, sheet)`` because AC-REQ-01's "two parses, one result"
    applies to this projection too.
    """
    out: list[dict[str, Any]] = []
    for fragment in fragments:
        for record in fragment.notes.get("mappingReview") or []:
            if not isinstance(record, dict):
                continue
            proposal = record.get("proposal")
            out.append(
                {
                    "artifactId": fragment.source_artifact_id,
                    "sheet": str(record.get("sheet") or ""),
                    "headers": [str(header) for header in (record.get("headers") or [])],
                    "category": record.get("category"),
                    "mapping": {
                        str(key): str(value)
                        for key, value in (proposal or {}).items()
                        if isinstance(proposal, Mapping)
                    },
                    "recognisedBy": record.get("recognisedBy") or fragment.notes.get("recognisedBy"),
                    "affectedKeys": [str(key) for key in (record.get("affectedKeys") or [])],
                    "question": record.get("question"),
                }
            )
    return tuple(sorted(out, key=lambda item: (item["artifactId"], item["sheet"])))


def _counts_of(
    entries: Sequence[NrrEntry], merge: MergeResult, gate: GateResult | None
) -> dict[str, int]:
    """What this NRR holds, and what the gate refused to confirm (AI-INP-004, AI-INP-008).

    One function for both halves — the artifact's build record and the document itself — so
    the two can never report different counts for one parse.
    """
    confirmed = sum(1 for entry in entries if entry.status is NrrStatus.CONFIRMED)
    return {
        "items": len(entries),
        "confirmed": confirmed,
        "candidate": len(entries) - confirmed,
        "conflicts": len(merge.conflicts),
        "heldBack": len(gate.questions) if gate is not None else 0,
    }


def build_record(
    fragments: Sequence[Fragment],
    *,
    entries: Sequence[NrrEntry],
    merge: MergeResult,
    gate: GateResult | None = None,
    extraction_threshold: float,
    threshold_source: str = "configuration",
) -> dict[str, Any]:
    """The ``build_record`` of the NRR artifact (AI-INP-004).

    ``parserVersion`` is present because the SRS names it, and the rest is present because a
    reader of an artifact with no other context cannot otherwise tell whether a CONFIRMED
    item was measured or guessed: ``usedModel`` plus ``thresholdsApplied`` is exactly that
    difference, and it is the difference AI-INP-008 turns into a question.
    """
    return {
        "nrrVersion": NRR_VERSION,
        "parserType": sorted({str(fragment.parser_type.value) for fragment in fragments}),
        "parserVersion": parser_version_label(fragments),
        "parserVersions": parser_versions(fragments),
        "usedModel": used_model(fragments),
        "inputArtifactIds": input_artifact_ids(fragments),
        "inputHashes": input_hashes(fragments),
        "thresholdsApplied": {"extractionConfidence": extraction_threshold, "source": threshold_source},
        "counts": _counts_of(entries, merge, gate),
        # the hash of the document's own content list, so a reader can prove two records
        # describe the same extraction without re-reading the artifact
        "itemsHash": structure_hash([entry.to_nrr_record() for entry in entries]),
    }


def build_document(
    *,
    project_id: str,
    fragments: Sequence[Fragment],
    entries: Sequence[NrrEntry],
    merge: MergeResult,
    gate: GateResult,
    questions: Sequence[Question],
    extraction_threshold: float,
    threshold_source: str = "configuration",
) -> dict[str, Any]:
    """The NRR artifact content (AI-INP-005's thirteen categories, with provenance)."""
    return {
        "nrrVersion": NRR_VERSION,
        "parserVersion": parser_version_label(fragments),
        "parserVersions": parser_versions(fragments),
        "projectId": project_id,
        "inputArtifactIds": input_artifact_ids(fragments),
        "inputHashes": input_hashes(fragments),
        "usedModel": used_model(fragments),
        "thresholdsApplied": {
            "extractionConfidence": extraction_threshold,
            "source": threshold_source,
        },
        #: the Requirements Agent reads the document, not the metadata, so the document has to
        #: say how much of it the gate held back (AI-INP-008)
        "counts": _counts_of(entries, merge, gate),
        "items": [_sorted_record(entry) for entry in sorted(entries, key=_item_order)],
        "conflicts": [
            payload
            for payload in sorted(
                merge.conflict_payloads(), key=lambda item: (item["category"], item["businessKey"])
            )
        ],
        "questions": [_question_record(question) for question in questions],
        "parseNotes": {
            artifact: _plain(fragment.notes)
            for artifact, fragment in sorted(
                ((item.source_artifact_id, item) for item in fragments),
                key=lambda pair: pair[0],
            )
        },
    }


def assemble_questions(
    fragments: Sequence[Fragment],
    merge: MergeResult,
    gate: GateResult,
) -> list[Question]:
    """Every question the parse raised, with canonical local ids (AI-INP-006/008/009).

    Four producers, one numbering: a conflict (AI-INP-009), a below-threshold extraction
    (AI-INP-008), a proposed column mapping (AI-INP-006) and whatever a parser could not
    resolve for itself.  All are HIGH or MEDIUM as declared by their producer; the ids are
    assigned in one deterministic order so a repeated parse asks the same questions in the
    same order rather than reshuffling them.
    """
    raw: list[dict[str, Any]] = []
    raw.extend(merge.conflict_questions())
    raw.extend(dict(question) for question in gate.questions)
    for fragment in fragments:
        raw.extend(mapping_review_questions(fragment))
        raw.extend(_open_questions(fragment))
    return number_questions(raw)


def number_questions(raw: Sequence[Mapping[str, Any]]) -> list[Question]:
    """Sort by urgency and mint ``Q1``…; the same set always numbers the same way."""
    ordered = sorted(raw, key=_question_order)
    out: list[Question] = []
    for index, item in enumerate(ordered, start=1):
        payload: dict[str, Any] = {field: item.get(field) for field in _QUESTION_FIELDS}
        severity = str(payload.get("severity") or QuestionSeverity.MEDIUM.value)
        if severity not in {member.value for member in QuestionSeverity}:
            severity = QuestionSeverity.MEDIUM.value
        payload["severity"] = severity
        #: HIGH blocks whatever a producer said about the flag (AI-RT-015)
        payload["blocking"] = bool(payload.get("blocking")) or severity == QuestionSeverity.HIGH.value
        payload["local_id"] = f"{QUESTION_ID_PREFIX}{index}"
        out.append(Question.model_validate(payload))
    return out


def _question_order(item: Mapping[str, Any]) -> tuple[int, str, str]:
    severity = str(item.get("severity") or QuestionSeverity.MEDIUM.value)
    try:
        rank = QUESTION_SEVERITY_RANK[QuestionSeverity(severity)]
    except ValueError:
        rank = QUESTION_SEVERITY_RANK[QuestionSeverity.MEDIUM]
    affects = item.get("affects") or []
    first_target = str(affects[0]) if affects else ""
    return (-rank, first_target, str(item.get("text") or ""))


def _open_questions(fragment: Fragment) -> list[dict[str, Any]]:
    """A parser's own unresolved facts, as questions.

    A tabular fragment whose outstanding review *is* the column mapping is skipped: its
    question already came out of :func:`mapping_review_questions` with the two suggestions
    an answer needs, and asking the same thing twice in two shapes would give a human two
    blocking questions about one mapping.
    """
    if isinstance(fragment.notes.get("mappingReview"), list):
        return []
    out: list[dict[str, Any]] = []
    for item in fragment.open_questions:
        text = str(item.get("question") or "")
        if not text:
            continue
        out.append(
            {
                "text": text,
                "severity": str(item.get("severity") or QuestionSeverity.MEDIUM.value),
                "blocking": False,
                # an OPEN_QUESTION entry states the question as its statement, so the two
                # are tied by that text rather than by an id a parser never assigned
                "affects": [
                    f"requirements.items.{entry.business_key}"
                    for entry in fragment.entries
                    if entry.statement == text
                ],
                "suggestions": [],
                "details": {"artifactId": fragment.source_artifact_id, "origin": "parser"},
            }
        )
    return out


def _item_order(entry: NrrEntry) -> tuple[str, str, str, str]:
    return (entry.category.value, entry.business_key, str(entry.location or ""), entry.statement)


def _question_record(question: Question) -> dict[str, Any]:
    """The stored form of a question, in the artifact's camelCase vocabulary.

    Projected by hand rather than dumped from the DTO: ``ParsedInput``'s field names are
    S1's Python names and may be re-aliased there, while a NRR already written has to stay
    readable in the same spelling as the ``items`` records beside it.
    """
    return {
        "localId": question.local_id,
        "text": question.text,
        "severity": question.severity,
        "blocking": question.is_blocking,
        "affects": list(question.affects),
        "suggestions": [dict(item) for item in question.suggestions],
        "details": dict(question.details),
    }


def _sorted_record(entry: NrrEntry) -> dict[str, Any]:
    return entry.to_nrr_record()


def _plain(value: Any) -> Any:
    """A JSON-safe, key-sorted copy of a parser's notes.

    Notes come from parsers of different vintages and may hold anything a parser recorded;
    the document must stay serialisable, and a container that is not a mapping or a
    sequence is reduced to its text rather than dropped.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=str)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain(item) for item in value]
    return str(value)


__all__ = [
    "NRR_ARTIFACT_TYPE",
    "NRR_VERSION",
    "QUESTION_ID_PREFIX",
    "assemble_questions",
    "build_document",
    "build_record",
    "input_artifact_ids",
    "input_hashes",
    "number_questions",
    "parser_version_label",
    "parser_versions",
    "proposals_of",
    "used_model",
]
