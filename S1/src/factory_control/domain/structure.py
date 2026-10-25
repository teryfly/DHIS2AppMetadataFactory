"""Workflow stage structure (CP-WF-001 / CP-WF-002).

S1 creates the stage skeleton once per project and per Run, applying the SKIPPED
rules.  Delivery mode and whether the Blueprint carries a Fixture spec are asked
from S2 at that moment — S1 keeps no copy of them (D-14, CP-WF-002).
"""

from __future__ import annotations

from dataclasses import dataclass

from factory_control.domain.enums import (
    DESIGN_STAGES,
    RUN_STAGES,
    StageStatus,
    StageType,
)

#: Stages that disappear in delivery mode A (CP-WF-002 rule 1).
MODE_A_SKIPPED: frozenset[StageType] = frozenset({StageType.PAGE_DESIGN, StageType.HANDOFF})

#: Run stages whose status depends on the environment class (CP-WF-002 rule 3).
PRODUCTION_SKIPPED: frozenset[StageType] = frozenset({StageType.FIXTURE_IMPORT})

#: First stage of the design line, in order.
FIRST_DESIGN_STAGE = StageType.REQUIREMENTS


@dataclass(frozen=True)
class StagePlan:
    """What to create: a stage type with its initial status."""

    stage_type: StageType
    status: StageStatus
    reason: str | None = None


#: The design line plus the delivery step, in the order a token would walk it.
MAIN_LINE: tuple[StageType, ...] = DESIGN_STAGES + (StageType.HANDOFF,)


def design_line_plan(
    *,
    delivery_mode: str,
    has_fixture_spec: bool,
    design_reference_is_production: bool = False,
) -> list[StagePlan]:
    """The six design stages plus HANDOFF, with mode-A skips already applied.

    ``has_fixture_spec`` and ``design_reference_is_production`` are accepted for
    symmetry with the Run plan; the design line itself never skips FIXTURE_IMPORT.
    """
    _ = (has_fixture_spec, design_reference_is_production)
    plans: list[StagePlan] = []
    for index, stage_type in enumerate(DESIGN_STAGES):
        if delivery_mode == "A" and stage_type in MODE_A_SKIPPED:
            plans.append(
                StagePlan(
                    stage_type=stage_type,
                    status=StageStatus.SKIPPED,
                    reason="DELIVERY_MODE_A",
                )
            )
            continue
        status = StageStatus.READY if index == 0 else StageStatus.LOCKED
        plans.append(StagePlan(stage_type=stage_type, status=status))

    # [7] HANDOFF: SKIPPED in mode A (CP-WF-002), LOCKED in mode B until the
    # design line has passed and a non-production Run is VERIFIED (D-01).
    if delivery_mode == "A":
        plans.append(StagePlan(StageType.HANDOFF, StageStatus.SKIPPED, reason="DELIVERY_MODE_A"))
    else:
        plans.append(StagePlan(StageType.HANDOFF, StageStatus.LOCKED, reason=None))
    return plans


def run_line_plan(
    *,
    delivery_mode: str,
    has_fixture_spec: bool,
    environment_class: str,
) -> list[StagePlan]:
    """R1…R6 for one Deployment Run (CP-WF-002 rules 2 and 3)."""
    plans: list[StagePlan] = []
    for index, stage_type in enumerate(RUN_STAGES):
        if stage_type is StageType.FIXTURE_IMPORT:
            if not has_fixture_spec:
                plans.append(
                    StagePlan(stage_type=stage_type, status=StageStatus.SKIPPED, reason="NO_FIXTURE_SPEC")
                )
                continue
            if environment_class.upper() == "PRODUCTION":
                plans.append(
                    StagePlan(
                        stage_type=stage_type,
                        status=StageStatus.SKIPPED,
                        reason="PRODUCTION_FORBIDS_FIXTURE",
                    )
                )
                continue
        plans.append(
            StagePlan(
                stage_type=stage_type,
                status=StageStatus.READY if index == 0 else StageStatus.LOCKED,
            )
        )
    return plans


def next_design_stage(
    statuses: dict[StageType, StageStatus], current: StageType
) -> StageType | None:
    """The stage to unlock after ``current`` passed, skipping SKIPPED ones."""
    order = list(MAIN_LINE)
    try:
        index = order.index(current)
    except ValueError:
        return None
    for candidate in order[index + 1 :]:
        status = statuses.get(candidate)
        if status in (StageStatus.SKIPPED,):
            continue
        return candidate
    return None


def unlock_targets_for(
    statuses: dict[StageType, StageStatus], current: StageType
) -> list[StageType]:
    """All stages that may leave LOCKED once ``current`` is PASSED.

    Normally one, but when the remaining stages are all SKIPPED the caller needs
    the empty list to know the design line is finished.
    """
    nxt = next_design_stage(statuses, current)
    return [nxt] if nxt is not None else []


__all__ = [
    "FIRST_DESIGN_STAGE",
    "MODE_A_SKIPPED",
    "PRODUCTION_SKIPPED",
    "StagePlan",
    "design_line_plan",
    "next_design_stage",
    "run_line_plan",
    "unlock_targets_for",
]
