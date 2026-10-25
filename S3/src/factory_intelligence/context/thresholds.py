"""Reading the project's thresholds and entries from S2 (AI-RT-004, AI-INP-008).

The Blueprint — not S3 — owns the extraction and decision confidence thresholds (they are
``generationPolicy`` fields, and ``generationPolicy`` is mirrored nowhere queryable:
S2's ``ProjectSemantics`` answers from version columns so that reading it never touches an
artifact).  So the threshold arrives as a *side effect of a read the runtime already does*:
the sections selector gains ``generationPolicy``, and because that section is a whole object
``get_entries`` returns it verbatim.  Zero extra I/O, and it sees a patched value rather than
the value the project was created with.

The fallback constants are pinned to S2's own default in
``tests/unit/test_thresholds.py``.  S3 disagreeing with S2 here is not a style problem: a
lower number asks users to confirm decisions S2 never required, and a higher one lets
through a decision S2 will block at freeze with ``BPV-DEC-*``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory_blueprint.api import SelectedEntries

from factory_intelligence.config import (
    DEFAULT_DECISION_CONFIDENCE,
    DEFAULT_EXTRACTION_CONFIDENCE,
    settings,
)
from factory_intelligence.deps import get_blueprint
from factory_intelligence.dto.request import AgentRunRequest

GENERATION_POLICY_SECTION = "generationPolicy"


@dataclass(frozen=True)
class Thresholds:
    """The two confidence levels a run is judged by."""

    decision: float
    extraction: float
    #: where the numbers came from, so a run record can say whether the Blueprint spoke
    source: str = "generationPolicy"

    @property
    def lowest(self) -> float:
        return min(self.decision, self.extraction)


def from_entries(selected: SelectedEntries) -> Thresholds:
    """Pull the thresholds out of an entry selection that included ``generationPolicy``."""
    policy: dict[str, Any] = {}
    for entry in selected.entries:
        if entry.get("section") == GENERATION_POLICY_SECTION:
            value = entry.get("value")
            if isinstance(value, dict):
                policy = value
            break
    decision = _number(policy.get("decisionConfidenceThreshold"))
    extraction = _number(policy.get("extractionConfidenceThreshold"))
    if decision is None and extraction is None:
        return Thresholds(
            decision=settings().default_decision_confidence,
            extraction=settings().default_extraction_confidence,
            source="configuration",
        )
    return Thresholds(
        decision=decision
        if decision is not None
        else settings().default_decision_confidence,
        extraction=extraction
        if extraction is not None
        else settings().default_extraction_confidence,
        source="generationPolicy" if policy else "configuration",
    )


def selector_for(
    request: AgentRunRequest, *, sections: tuple[str, ...], read_only_sections: tuple[str, ...] = (),
    source: str = "APPROVED",
) -> dict[str, Any]:
    """Build the minimal retrieval selector AI-RT-004 demands.

    ``scope`` carries page keys for the per-page agents and businessKeys elsewhere, and is
    split by shape rather than guessed: a scope entry that looks like a page key goes to
    ``pageKeys``, everything else to ``businessKeys``.  Sending a page key as a businessKey
    would retrieve nothing and the agent would then design a page from an empty context —
    the quietest possible failure.
    """
    page_keys = [item for item in request.scope if item.startswith("page:") or _looks_like_page_key(item)]
    #: a decision id is addressed by S2's ``decision_ids``, not also as a business key: S2
    #: ANDs the three lists, so listing one item twice retrieves nothing (AI-RT-004)
    decision_ids = [
        item
        for item in request.scope
        if item not in page_keys and (item.startswith("dec-") or item.startswith("d-"))
    ]
    #: a registry entry is addressed by its business key, which is what S2's ``get_entries``
    #: filters on (AI-RT-004's fourth selector kind has no list of its own)
    business_keys = [
        item for item in request.scope if item not in page_keys and item not in decision_ids
    ]
    # S2's EntrySelector declares snake_case fields with no aliases and ``extra="ignore"``,
    # so the camelCase spelling this function used to emit was *dropped*: every run asked
    # for whole sections and the selector never narrowed anything (AI-RT-004, D-14).
    return {
        "sections": [*sections, GENERATION_POLICY_SECTION, *read_only_sections],
        "business_keys": business_keys,
        "page_keys": [item.removeprefix("page:") for item in page_keys],
        "decision_ids": decision_ids,
        "source": source,
    }


def retrieve(
    request: AgentRunRequest,
    *,
    sections: tuple[str, ...],
    read_only_sections: tuple[str, ...] = (),
    draft_materialised: bool,
) -> SelectedEntries:
    """One S2 read, with the generation policy folded in (see the module docstring)."""
    source = "DRAFT" if draft_materialised and request.draft_id else "APPROVED"
    selector = selector_for(
        request,
        sections=sections,
        read_only_sections=read_only_sections,
        source=source,
    )
    return get_blueprint().entries(request.project_id, selector)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
        return float(value)
    return None


def _looks_like_page_key(item: str) -> bool:
    return item.endswith("Page") or item.startswith("page_") or item.startswith("page-")


__all__ = [
    "Thresholds",
    "from_entries",
    "selector_for",
    "retrieve",
    "GENERATION_POLICY_SECTION",
    "DEFAULT_DECISION_CONFIDENCE",
    "DEFAULT_EXTRACTION_CONFIDENCE",
]
