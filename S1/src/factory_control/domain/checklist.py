"""What a stage needs before it can run (S5IF-FLD01 【裁决】).

No SRS ever defined "input completeness", so the ruling gives S1 the definition instead
of letting it be guessed: one precondition checklist per stage type, completeness =
satisfied ÷ total, and the unsatisfied items named so the screen can say *why*.

Each key is stable on the wire and localised by :mod:`factory_control.i18n`, because S5
shows the label as it is (UI-WB-002) and cannot invent wording for a state it does not
own.  The checklist never calls a provider: every item is read from S1's own rows, since
this runs on the stage list that the navigation polls (CP-NFR-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory_control.domain.enums import DESIGN_STAGES, RUN_STAGES, StageStatus, StageType

#: The four checks the ruling names.
PRIOR_STAGE_COMPLETE = "PRIOR_STAGE_COMPLETE"
UPSTREAM_ARTIFACTS = "UPSTREAM_ARTIFACTS"
PARSED_INPUT = "PARSED_INPUT"
CAPABILITY_PROFILE_VALID = "CAPABILITY_PROFILE_VALID"

#: Run steps from R1 onward compile against a capability profile, so R2…R6 need one to
#: have been recorded by R1 (CP-WF-022).  Checked as "R1 recorded a profile", never by
#: asking S4 — ``check_capability_freshness`` reaches DHIS2.
_RUN_NEEDS_PROFILE = RUN_STAGES

_DONE = (StageStatus.PASSED.value, StageStatus.SKIPPED.value)


@dataclass(frozen=True)
class StageFacts:
    """What S1 knows about one stage, with no provider call behind any field."""

    stage_type: StageType
    status: str
    in_run: bool
    output_artifact_ids: list[str]
    prior_stage_done: bool | None = None
    prior_stage_produced: bool | None = None
    project_has_parsed_input: bool = False
    run_recorded_capability_profile: bool = False


def checks_for(stage_type: StageType, *, in_run: bool) -> tuple[str, ...]:
    """The checklist for one stage type.

    Design-line and Run-line stages of the same type would differ only in the profile
    item, and no stage type is shared between the two lines, so ``in_run`` alone decides.
    """
    checks: list[str] = [PRIOR_STAGE_COMPLETE, UPSTREAM_ARTIFACTS]
    if not in_run and stage_type is StageType.REQUIREMENTS:
        checks.append(PARSED_INPUT)
    if in_run and stage_type in _RUN_NEEDS_PROFILE:
        checks.append(CAPABILITY_PROFILE_VALID)
    return tuple(checks)


def order_for(stage_type: StageType, *, in_run: bool) -> tuple[StageType, ...]:
    return RUN_STAGES if in_run else DESIGN_STAGES + (StageType.HANDOFF,)


def evaluate(facts: StageFacts) -> list[str]:
    """The checks *not* satisfied, in checklist order (empty means ready)."""
    answers = {
        PRIOR_STAGE_COMPLETE: _prior_done(facts),
        UPSTREAM_ARTIFACTS: _upstream_present(facts),
        PARSED_INPUT: facts.project_has_parsed_input,
        CAPABILITY_PROFILE_VALID: facts.run_recorded_capability_profile,
    }
    return [
        key
        for key in checks_for(facts.stage_type, in_run=facts.in_run)
        if not answers[key]
    ]


def completeness(facts: StageFacts, language: str = "en") -> dict[str, Any]:
    """The ``inputCompleteness`` object, with the labels S5 renders."""
    from factory_control.i18n import checklist_label

    checks = checks_for(facts.stage_type, in_run=facts.in_run)
    missing = evaluate(facts)
    total = len(checks)
    satisfied = total - len(missing)
    return {
        "satisfied": satisfied,
        "total": total,
        "ratio": (satisfied / total) if total else None,
        "missing": [{"key": key, "label": checklist_label(key, language)} for key in missing],
    }


def _prior_done(facts: StageFacts) -> bool:
    if facts.prior_stage_done is None:
        # The first stage of its line has no predecessor to wait for.
        return True
    return facts.prior_stage_done


def _upstream_present(facts: StageFacts) -> bool:
    if facts.prior_stage_produced is None:
        return True
    return facts.prior_stage_produced


__all__ = [
    "CAPABILITY_PROFILE_VALID",
    "PARSED_INPUT",
    "PRIOR_STAGE_COMPLETE",
    "StageFacts",
    "UPSTREAM_ARTIFACTS",
    "checks_for",
    "completeness",
    "evaluate",
    "order_for",
]
