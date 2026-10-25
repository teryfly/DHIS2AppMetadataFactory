"""The AI-INP-008 confidence gate, and the invariant it enforces.

AI-INP-008 has two halves and they are easy to conflate.  An entry a model produced whose
confidence is below the project's extraction threshold may not sit in the NRR's confirmed
part, and it must *also* be put in front of a human as a Question.  The first half alone is
a status field nobody reads; the second half alone leaves a CONFIRMED item in the document
that a later Requirements Agent patch would carry into a draft.  Both are done here, in one
place, so no parser has to remember them.

Where an entry came from is decided by *two* independent signals, deliberately:

* the artifact it was read from belongs to a fragment whose parser type is model-assisted
  (:attr:`Fragment.used_model`, which is derived from
  :data:`factory_intelligence.enums.DETERMINISTIC_PARSERS`, not from a flag a caller set);
  **or**
* its confidence is below ``1.0``, because a deterministic parse reports a measured fact at
  exactly 1.0 (AI-INP-010) and only a model reports anything else.

The second signal is what stops a new model-assisted parser type from quietly slipping
through the gate before the enum table is updated — the failure mode that would let a guess
be confirmed.

:meth:`parity_violations` is the invariant :func:`check_confidence_parity` states, and it is
the same rule the Requirements Agent's constraint check
:func:`factory_intelligence.agents.checks.rules.nrr_confidence_parity` applies to a patch:
no CONFIRMED item below the threshold, and ``REJECTED`` is a human verdict an agent or a
parser may not produce.  Keeping both readings in one function is what stops the parser's
gate and the patch-side check from drifting apart — a drift that would show up as an NRR
S3 accepted and S2 refused at freeze.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory_intelligence.enums import NrrStatus, QuestionSeverity
from factory_intelligence.parsing.base import Fragment, NrrEntry, ParseError

#: The path prefix a question about an NRR item names in ``affects`` (AI-RT-010): the
#: answer must address the item S1 will later find in the ``requirements`` section.
AFFECTS_PREFIX = "requirements.items."

#: A deterministic parse is a measured fact, so anything below this came from a model
#: (AI-INP-010) — the structural half of the origin test described above.
DETERMINISTIC_CONFIDENCE = 1.0


@dataclass(frozen=True)
class GateResult:
    """The entries after the gate, and the questions the gate raised."""

    entries: tuple[NrrEntry, ...]
    questions: tuple[dict[str, Any], ...]

    @property
    def held_back(self) -> int:
        """How many model entries the gate refused to confirm."""
        return len(self.questions)


def apply_gate(
    entries: Sequence[NrrEntry],
    fragments: Sequence[Fragment],
    *,
    threshold: float,
    conflicted: Collection[tuple[str, str]] = (),
) -> GateResult:
    """Confirm what is certain, hold back what is not, and ask about the rest (AI-INP-008).

    ``threshold`` is the project's *extraction* threshold: Blueprint ``generationPolicy``
    when it states one, else ``settings().default_extraction_confidence``.

    ``conflicted`` is the set of ``(category, businessKey)`` pairs :func:`merge` found two or
    more sources disagreeing about.  They are held back whatever else is true of them: a
    deterministic source is authoritative about what it says, and two authoritative sources
    saying different things is exactly the case AI-INP-009 says no machine may settle.
    """
    origins = _origins(fragments)
    contested = set(conflicted)
    held: list[NrrEntry] = []
    questions: list[dict[str, Any]] = []
    for entry in entries:
        used_model, needs_review = origins.get(entry.source_artifact_id, (False, False))
        model_extracted = used_model or entry.confidence < DETERMINISTIC_CONFIDENCE
        if entry.fact_key()[0] == entry.category.value and (
            entry.category.value,
            entry.business_key,
        ) in contested:
            held.append(entry.with_status(NrrStatus.CANDIDATE))
            continue
        if model_extracted and entry.confidence < threshold:
            questions.append(low_confidence_question(entry, threshold=threshold))
            held.append(entry.with_status(NrrStatus.CANDIDATE))
            continue
        if model_extracted:
            #: at or above the threshold a model's answer is still a proposal — AI-INP-002
            #: marks documents and images as 必须复核, so nothing a model said is confirmed
            #: by the extraction alone
            held.append(entry.with_status(NrrStatus.CANDIDATE))
            continue
        if needs_review:
            #: the structure is known but a human has to approve it (an unconfirmed column
            #: mapping, AI-INP-006); the parser already raised the question for it
            held.append(entry.with_status(NrrStatus.CANDIDATE))
            continue
        #: a deterministic parse with nothing outstanding is CONFIRMED at exactly 1.0
        held.append(entry.with_status(NrrStatus.CONFIRMED, confidence=DETERMINISTIC_CONFIDENCE))
    return GateResult(entries=tuple(held), questions=tuple(questions))


def low_confidence_question(entry: NrrEntry, *, threshold: float) -> dict[str, Any]:
    """The Question naming the ``affects`` path of the item it puts in doubt."""
    location = str(entry.location) if entry.location is not None else "an unstated position"
    return {
        "text": (
            f"The extraction of {entry.category.value} '{entry.business_key}' (at {location} in "
            f"input {entry.source_artifact_id}) is {entry.confidence:g} confident, below the "
            f"project threshold of {threshold:g}. Is this statement correct as written?"
        ),
        "severity": QuestionSeverity.HIGH.value,
        "blocking": True,
        "affects": [f"{AFFECTS_PREFIX}{entry.business_key}"],
        "suggestions": [
            {
                "id": "S1",
                "text": entry.statement,
                "rationale": f"accept the extraction as it was read at {location}",
                "changesUid": False,
                "regenerates": False,
            },
            {
                "id": "S2",
                "text": "Reject it — the field was read wrongly and must be re-supplied",
                "rationale": (
                    "a rejected extraction stays CANDIDATE; the Gate reader records the "
                    "verdict (AI-INP-008)"
                ),
                "changesUid": False,
                "regenerates": False,
            },
        ],
        "details": {
            "confidence": entry.confidence,
            "threshold": threshold,
            "sources": [
                {
                    "artifactId": entry.source_artifact_id,
                    "location": location,
                    "value": entry.statement,
                }
            ],
        },
    }


def parity_violations(entries: Sequence[NrrEntry], threshold: float) -> list[str]:
    """Every way these entries break the AI-INP-008 invariant, as readable messages."""
    return check_parity_items([entry.to_requirements_item() for entry in entries], threshold)


def check_parity_items(items: Sequence[Mapping[str, Any]], threshold: float) -> list[str]:
    """The invariant over S2's ``requirements.items`` shape — the patch-side twin reads this.

    Kept as a function over the *item* form rather than over :class:`NrrEntry` so the same
    words describe both paths: the parser's gate and the Requirements Agent's constraint
    check.  It returns messages instead of raising because a constraint check has to report
    every violation in one round trip (AI-RT-011's corrective retry needs the full list).
    """
    findings: list[str] = []
    for item in items:
        key = str(item.get("businessKey") or "?")
        status = item.get("status")
        confidence = item.get("confidence")
        if (
            status == NrrStatus.CONFIRMED.value
            and isinstance(confidence, (int, float))
            and float(confidence) < threshold
        ):
            findings.append(
                f"requirement {key!r} is CONFIRMED at confidence {confidence}, below the "
                f"{threshold:g} threshold; low-confidence items stay CANDIDATE and become "
                "questions (AI-INP-008)"
            )
        if status == NrrStatus.REJECTED.value:
            findings.append(
                f"requirement {key!r} is REJECTED; that verdict is a human's, recorded at "
                "the Gate — a parse or an agent may produce CANDIDATE or CONFIRMED only"
            )
    return findings


def check_confidence_parity(entries: Sequence[NrrEntry], threshold: float) -> None:
    """Raise if an NRR would break AI-INP-008 — used before an artifact is written.

    Writing an NRR whose confirmed part contains a below-threshold item would hide the very
    thing the gate exists to surface, and S2 would refuse it at freeze anyway.
    """
    violations = parity_violations(entries, threshold)
    if not violations:
        return
    raise ParseError(
        "AI_OUTPUT_INVALID",
        "the merged NRR violates the confidence invariant (AI-INP-008): " + "; ".join(violations),
    )


def _origins(fragments: Sequence[Fragment]) -> dict[str, tuple[bool, bool]]:
    """``artifact id -> (used a model, needs a human review)`` for the gate to read.

    ``Fragment.used_model`` is derived from the parser table rather than from a flag, so a
    parser cannot claim to be deterministic by forgetting to say it was not (AI-INP-003).
    """
    out: dict[str, tuple[bool, bool]] = {}
    for fragment in fragments:
        out[fragment.source_artifact_id] = (fragment.used_model, fragment.needs_review)
    return out


__all__ = [
    "AFFECTS_PREFIX",
    "DETERMINISTIC_CONFIDENCE",
    "GateResult",
    "apply_gate",
    "check_confidence_parity",
    "check_parity_items",
    "low_confidence_question",
    "parity_violations",
]
