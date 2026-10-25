"""Similar-object search (AI-AG-011, FR-DSC-003, AI-AG-003).

Before the Mapping Agent is asked to decide, S3 searches the design-basis environment's
discovery result for objects that already look like each object the agent wants to create:
``code`` exact, ``name`` after normalisation within an edit-distance similarity, and option
sets by Jaccard over option codes.  This is deterministic and runs *without* a model
deliberately: it is the evidence the agent is given, and letting the model guess which
existing objects might be reusable would produce a different answer on every run — which
AI-EVAL and the Golden Project regression could never pin down.

The agent's obligation is the other half of AI-AG-011: for every similar object found it
must decide REUSE or CREATE and give a reason.  :class:`SimilarHit` is what
``agents/checks/rules.py::reuse_decision_per_similar_object`` holds it to, via
``CheckContext.similar_object_keys``.

**An unreadable discovery result raises.**  Returning an empty list would tell the agent
"nothing similar exists in this environment", and in a brownfield project that is the most
expensive wrong answer available: it manufactures duplicate objects that then collide at
deploy time.  The shape of a DISCOVERY_RESULT belongs to S4, which does not exist yet, so
several plausible spellings are accepted and anything else is refused loudly.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from factory_intelligence.errors import AiError

#: The three bases AI-AG-011 names, in the order they are trusted.
BASIS_CODE = "code"
BASIS_NAME = "name"
BASIS_OPTIONS = "options"

_NAME_BASIS_WEIGHT = 1.0
_OPTION_BASIS_WEIGHT = 0.9


class DiscoveryShapeUnreadable(AiError):
    """The discovery artifact is not in a form S3 can search (see the module docstring)."""

    code = "AI_ERROR"


@dataclass(frozen=True)
class ObjectRecord:
    """One object, whether observed in an environment or proposed by the agent."""

    kind: str
    code: str | None = None
    name: str | None = None
    option_codes: frozenset[str] = field(default_factory=frozenset)
    #: an observed UID is data for comparison only; S3 never allocates or writes one
    observed_uid: str | None = None
    pointer: str | None = None
    #: extra fields worth showing the agent (categoryCombo, valueType, parent…)
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.name or self.code or self.kind

    @property
    def identity(self) -> str:
        """What the agent must name in its decision so the check can find it."""
        return self.code or self.name or self.pointer or self.kind


@dataclass(frozen=True)
class SimilarHit:
    proposed: ObjectRecord
    observed: ObjectRecord
    score: float
    basis: tuple[str, ...]

    @property
    def is_strong(self) -> bool:
        """A code match is not a judgement call, and an agent cannot claim otherwise.

        Recorded separately because AI-AG-011 asks the agent for a decision on every
        similar object: with an exact code match the only defensible answers are REUSE or
        an explicit reason it is a different object in a different managed scope.
        """
        return BASIS_CODE in self.basis


@dataclass(frozen=True)
class SearchThresholds:
    name: float = 0.85
    options: float = 0.60
    max_results: int = 8


def normalize_name(value: str | None) -> str:
    """Case, whitespace, punctuation and diacritic insensitive (AI-AG-011).

    NFKD then drop combining marks, so ``Emplacement`` and ``emplacement`` agree; ASCII
    fold, so a full-width form agrees too; keep digits, because ``Visit 2`` and ``Visit 3``
    must not.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    folded = stripped.casefold()
    kept = [char if char.isalnum() else " " for char in folded]
    return " ".join("".join(kept).split())


def edit_similarity(a: str, b: str) -> float:
    """``1 - levenshtein(a, b) / max(len)`` on normalised names, 1.0 when both empty.

    Levenshtein is written here rather than imported: the dependency would be used by one
    function, and this is the textbook two-row dynamic program — about thirty lines, no
    third-party supply chain, and easy to read in review.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    distance = _levenshtein(a, b)
    return max(0.0, 1.0 - distance / max(len(a), len(b)))


def _levenshtein(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            cost = 0 if char == other else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Overlap of two option-code sets; 0 unless both are empty, which is not similarity."""
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union)


def find_similar(
    proposed: Sequence[ObjectRecord],
    observed: Sequence[ObjectRecord],
    thresholds: SearchThresholds | None = None,
) -> dict[str, list[SimilarHit]]:
    """For each proposed object, the observed objects that resemble it, best first."""
    limits = thresholds or SearchThresholds()
    out: dict[str, list[SimilarHit]] = {}
    for candidate in proposed:
        hits: list[SimilarHit] = []
        for other in observed:
            hit = _compare(candidate, other, limits)
            if hit is not None:
                hits.append(hit)
        hits.sort(key=lambda item: (-item.score, item.observed.identity))
        out[candidate.identity] = hits[: limits.max_results]
    return out


def _compare(
    candidate: ObjectRecord, other: ObjectRecord, limits: SearchThresholds
) -> SimilarHit | None:
    basis: list[str] = []
    score = 0.0
    if candidate.code and other.code and candidate.code == other.code:
        basis.append(BASIS_CODE)
        score = 1.0
    if candidate.kind == other.kind:
        name_score = edit_similarity(normalize_name(candidate.name), normalize_name(other.name))
        if name_score >= limits.name and (candidate.name or other.name):
            basis.append(BASIS_NAME)
            score = max(score, name_score * _NAME_BASIS_WEIGHT)
        option_score = jaccard(candidate.option_codes, other.option_codes)
        if option_score >= limits.options and option_score > 0.0:
            basis.append(BASIS_OPTIONS)
            score = max(score, option_score * _OPTION_BASIS_WEIGHT)
    if not basis:
        return None
    return SimilarHit(
        proposed=candidate, observed=other, score=round(score, 4), basis=tuple(basis)
    )


def parse_discovery_result(document: Any) -> list[ObjectRecord]:
    """Read a DISCOVERY_RESULT artifact into records.

    Accepted spellings: a list of objects; ``{"objects": [...]}``; a mapping of kind to a
    list; and a mapping of kind to a mapping of uid to object.  Keys are read tolerantly
    because the artifact is S4's to shape (00 doc 4.1) and S3 must not need an S4 release to
    change.
    """
    records: list[ObjectRecord] = []
    for index, raw in enumerate(_iter_objects(document)):
        if not isinstance(raw, dict):
            continue
        records.append(_record_from(raw, pointer=_pointer_for(raw, index=index)))
    if not records:
        raise DiscoveryShapeUnreadable(
            "the discovery result contains no readable objects; refusing to tell the agent "
            "that nothing similar exists",
            details={"topLevelKeys": sorted(str(key) for key in document)[:12] if isinstance(document, Mapping) else str(type(document).__name__)},
        )
    return records


def _iter_objects(document: Any) -> list[Any]:
    if isinstance(document, list):
        return list(document)
    if not isinstance(document, Mapping):
        return []
    for key in ("objects", "items", "results", "discovered", "data"):
        value = document.get(key)
        if isinstance(value, list):
            return list(value)
    out: list[Any] = []
    for value in document.values():
        if isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, Mapping):
            out.extend(item for item in value.values() if isinstance(item, dict))
    return out


def _record_from(raw: Mapping[str, Any], *, pointer: str | None) -> ObjectRecord:
    options = _option_codes(raw.get("options") or raw.get("optionValues") or [])
    return ObjectRecord(
        kind=str(raw.get("kind") or raw.get("klass") or raw.get("objectType") or raw.get("type") or ""),
        code=_optional_str(raw.get("code")),
        name=_optional_str(raw.get("name") or raw.get("displayName") or raw.get("shortName")),
        option_codes=options,
        observed_uid=_optional_str(raw.get("uid") or raw.get("id")),
        pointer=pointer,
        detail={
            key: value
            for key, value in raw.items()
            if key not in {"kind", "klass", "objectType", "type", "code", "name", "displayName", "shortName", "uid", "id", "options", "optionValues"}
        },
    )


def _option_codes(value: Any) -> frozenset[str]:
    if isinstance(value, Sequence) and not isinstance(value, str):
        codes = [item.get("code") for item in value if isinstance(item, Mapping) and item.get("code")]
        if not codes:
            codes = [item for item in value if isinstance(item, str)]
        return frozenset(str(code) for code in codes)
    return frozenset()


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _pointer_for(raw: Mapping[str, Any], *, index: int) -> str | None:
    pointer = raw.get("$pointer") or raw.get("pointer")
    if isinstance(pointer, str):
        return pointer
    return f"/objects/{index}"


def hits_to_context_records(hits: Iterable[SimilarHit]) -> list[dict[str, Any]]:
    """The shape put in front of the model, and the source of the check's expected keys.

    ``identity`` is what ``reuse_decision_per_similar_object`` searches the answer for, so
    the two sides of AI-AG-011 — the search and the obligation — have to agree on one name;
    this function is the only place that name is produced.
    """
    return [
        {
            "proposed": hit.proposed.identity,
            "observed": hit.observed.identity,
            "kind": hit.observed.kind,
            "code": hit.observed.code,
            "name": hit.observed.name,
            "similarity": hit.score,
            "basis": list(hit.basis),
            "observedUid": hit.observed.observed_uid,
            "decisionRequired": ["REUSE", "CREATE"],
        }
        for hit in hits
    ]


__all__ = [
    "ObjectRecord",
    "SimilarHit",
    "SearchThresholds",
    "DiscoveryShapeUnreadable",
    "normalize_name",
    "edit_similarity",
    "jaccard",
    "find_similar",
    "parse_discovery_result",
    "hits_to_context_records",
    "BASIS_CODE",
    "BASIS_NAME",
    "BASIS_OPTIONS",
]
