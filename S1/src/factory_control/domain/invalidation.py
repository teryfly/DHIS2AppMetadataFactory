"""Invalidation propagation (CP-WF-014 / CP-WF-015, D-04).

S2 owns the decision about *which* stages a new Blueprint version affects; S1
turns that answer into the five propagation rules and applies them inside the
same unit of work that produced the new version.  The rules are ordered, so the
plan is computed as data first and applied by the service afterwards — which also
makes it directly testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from factory_control.domain.enums import (
    ChangeLevel,
    GateStatus,
    RunStatus,
    StageStatus,
    StageType,
)

#: Design stages in main-line order; used for "MAPPING and everything after it".
DESIGN_ORDER: tuple[StageType, ...] = (
    StageType.REQUIREMENTS,
    StageType.DOMAIN,
    StageType.DISCOVERY,
    StageType.MAPPING,
    StageType.PAGE_DESIGN,
    StageType.BASELINE_COMPILE,
    StageType.HANDOFF,
)


def stage_index(stage_type: StageType) -> int:
    return DESIGN_ORDER.index(stage_type)


def stages_from(stage_type: StageType) -> list[StageType]:
    """The stage itself plus everything after it in the design line (rule 5)."""
    return list(DESIGN_ORDER[stage_index(stage_type) :])


@dataclass
class StageState:
    stage_id: str
    stage_type: StageType
    status: StageStatus
    is_current: bool = False
    deployment_run_id: str | None = None


@dataclass
class RunState:
    run_id: str
    status: RunStatus
    in_commit: bool = False


@dataclass
class GateState:
    gate_id: str
    status: GateStatus
    stage_type: StageType | None = None
    deployment_run_id: str | None = None


@dataclass(frozen=True)
class InvalidationInput:
    change_level: ChangeLevel
    affected_stages: list[StageType]
    current_stage_id: str | None = None
    #: Stages that must not be invalidated by this promotion, whatever `affected_stages` says.
    #:
    #: CP-WF-015 rule 1 protects "非当前阶段", and the id it protects is
    #: ``workflow.current_stage_id``. That is not enough for a design Gate: by the time the
    #: promotion propagates, the stage whose approval caused it has already been completed and
    #: the workflow has advanced to its successor, so the approved stage is *not* the current one
    #: any more — and BP-IMP-003 legitimately lists it among the affected stages, because a
    #: section's own stage is part of "that stage plus every later stage". Without naming it
    #: here, a Gate approval invalidates the very stage it just passed, and that stage is then
    #: unrecoverable: re-running it fails (its section is already promoted, so there is nothing
    #: left to add) while its draft stays OPEN, and BP-DRF-001 allows only one active draft per
    #: project, so the next stage can no longer open one either.
    protect_stage_ids: list[str] = field(default_factory=list)
    stages: list[StageState] = field(default_factory=list)
    runs: list[RunState] = field(default_factory=list)
    gates: list[GateState] = field(default_factory=list)


@dataclass
class InvalidationPlan:
    """What to change.  The service performs it, in this order, in one UoW."""

    invalidate_stage_ids: list[str] = field(default_factory=list)
    invalidate_gate_ids: list[str] = field(default_factory=list)
    cancel_run_ids: list[str] = field(default_factory=list)
    mark_outdated_run_ids: list[str] = field(default_factory=list)
    reason: str = "BLUEPRINT_SUPERSEDED"

    def as_dict(self) -> dict[str, object]:
        return {
            "invalidateStages": list(self.invalidate_stage_ids),
            "invalidateGates": list(self.invalidate_gate_ids),
            "cancelRuns": list(self.cancel_run_ids),
            "outdatedRuns": list(self.mark_outdated_run_ids),
            "reason": self.reason,
        }


_OPEN_RUN = {RunStatus.CREATED, RunStatus.IN_PROGRESS, RunStatus.WAITING_APPROVAL, RunStatus.MANUAL_CHECK}
_FINISHED_RUN = {RunStatus.COMMITTED, RunStatus.VERIFIED}


def plan(input: InvalidationInput) -> InvalidationPlan:  # noqa: A002 - SRS vocabulary
    """Apply rules 1–5 of CP-WF-015 and return the resulting changes."""
    result = InvalidationPlan()

    affected = set(input.affected_stages)
    if input.change_level.is_major:
        # Rule 5: Major invalidates MAPPING and everything after it.
        affected |= set(stages_from(StageType.MAPPING))

    protected = set(input.protect_stage_ids) | ({input.current_stage_id} if input.current_stage_id else set())

    # Rule 1: affected design stages that are not the current one go INVALIDATED,
    # and their Gates follow.
    for stage in input.stages:
        if stage.deployment_run_id is not None:
            continue
        if stage.stage_id in protected:
            continue
        if stage.stage_type in affected and stage.status in (
            StageStatus.PASSED,
            StageStatus.WAITING_APPROVAL,
            StageStatus.WAITING_INPUT,
            StageStatus.READY,
            StageStatus.BLOCKED,
            StageStatus.FAILED,
        ):
            result.invalidate_stage_ids.append(stage.stage_id)

    # Rule 4: an already PASSED HANDOFF loses its basis.
    for stage in input.stages:
        if (
            stage.stage_type is StageType.HANDOFF
            and stage.status is StageStatus.PASSED
            and stage.stage_id not in protected
            and stage.stage_id not in result.invalidate_stage_ids
        ):
            result.invalidate_stage_ids.append(stage.stage_id)

    invalidated_types = {
        s.stage_type
        for s in input.stages
        if s.stage_id in result.invalidate_stage_ids and s.deployment_run_id is None
    }

    # Rules 2 and 3 on Runs.
    for run in input.runs:
        if run.status in _OPEN_RUN:
            if run.in_commit:
                # Rule 2 second half: a Run already committing finishes, then marks.
                result.mark_outdated_run_ids.append(run.run_id)
            else:
                result.cancel_run_ids.append(run.run_id)
        elif run.status in _FINISHED_RUN:
            # Rule 3: history is never rewritten, only flagged.
            result.mark_outdated_run_ids.append(run.run_id)

    # Gates of invalidated stages and cancelled Runs lose their meaning
    # (CP-WF-012 uses the same INVALIDATED target for waiting Gates).
    for gate in input.gates:
        if gate.status is not GateStatus.WAITING:
            continue
        if gate.stage_type in invalidated_types or gate.deployment_run_id in set(
            result.cancel_run_ids
        ):
            result.invalidate_gate_ids.append(gate.gate_id)

    for stage in input.stages:
        if (
            stage.stage_type in invalidated_types
            and stage.stage_id not in result.invalidate_stage_ids
            and stage.deployment_run_id is None
            and stage.status not in (StageStatus.LOCKED, StageStatus.SKIPPED)
        ):
            result.invalidate_stage_ids.append(stage.stage_id)

    return result


__all__ = [
    "DESIGN_ORDER",
    "GateState",
    "InvalidationInput",
    "InvalidationPlan",
    "RunState",
    "StageState",
    "plan",
    "stage_index",
    "stages_from",
]
