"""Workflow status and project milestone derivation (CP-WF-023 / CP-WF-024).

Both are *derived* values: S1 stores the last derivation for listing, but the
answer always comes from the current stage, Gate and Run rows — never from what
the browser last showed (CP-WF-007: 禁止从前端状态推断系统状态).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from factory_control.domain.enums import (
    MilestoneStatus,
    RunStatus,
    StageStatus,
    StageType,
    WorkflowStatus,
)

#: CP-WF-023 priority order.  Lower index wins.
_STATUS_PRIORITY: tuple[WorkflowStatus, ...] = (
    WorkflowStatus.CANCELLED,
    WorkflowStatus.PAUSED,
    WorkflowStatus.COMPLETED,
    WorkflowStatus.FAILED,
    WorkflowStatus.BLOCKED,
    WorkflowStatus.WAITING_USER,
    WorkflowStatus.ACTIVE,
)


@dataclass(frozen=True)
class StageFact:
    stage_type: StageType
    status: StageStatus
    in_run: bool = False


@dataclass(frozen=True)
class RunFact:
    status: RunStatus
    outdated: bool = False


@dataclass(frozen=True)
class WorkflowFacts:
    """Everything the derivation needs, gathered by the service layer."""

    cancelled: bool = False
    paused: bool = False
    delivery_mode: str = "A"
    stages: list[StageFact] = field(default_factory=list)
    runs: list[RunFact] = field(default_factory=list)
    architecture_gate_approved: bool = False
    design_gate_approved: bool = False
    package_gate_approved: bool = False
    dhis2_validated: bool = False

    def stage(self, stage_type: StageType) -> StageStatus | None:
        for fact in self.stages:
            if fact.stage_type is stage_type and not fact.in_run:
                return fact.status
        return None


def derive_workflow_status(facts: WorkflowFacts) -> WorkflowStatus:
    """CP-WF-023."""
    statuses = {s.status for s in facts.stages}
    open_runs = any(
        r.status
        in (RunStatus.CREATED, RunStatus.IN_PROGRESS, RunStatus.WAITING_APPROVAL, RunStatus.MANUAL_CHECK)
        for r in facts.runs
    )
    verified_runs = [r for r in facts.runs if r.status is RunStatus.VERIFIED]

    design_complete = _design_line_complete(facts)
    completed = bool(verified_runs) and not open_runs and design_complete
    if facts.delivery_mode == "B":
        completed = completed and facts.stage(StageType.HANDOFF) is StageStatus.PASSED

    conditions: dict[WorkflowStatus, bool] = {
        WorkflowStatus.CANCELLED: facts.cancelled,
        WorkflowStatus.PAUSED: facts.paused,
        WorkflowStatus.COMPLETED: completed,
        WorkflowStatus.FAILED: any(
            s in (StageStatus.FAILED, StageStatus.MANUAL_CHECK) for s in statuses
        ),
        WorkflowStatus.BLOCKED: StageStatus.BLOCKED in statuses,
        WorkflowStatus.WAITING_USER: any(
            s in (StageStatus.WAITING_INPUT, StageStatus.WAITING_APPROVAL) for s in statuses
        ),
        WorkflowStatus.ACTIVE: True,
    }
    for status in _STATUS_PRIORITY:
        if conditions[status]:
            return status
    return WorkflowStatus.ACTIVE  # pragma: no cover - ACTIVE is always reachable


def _design_line_complete(facts: WorkflowFacts) -> bool:
    """Every design stage PASSED or SKIPPED (CP-WF-018 precondition, reused here)."""
    for fact in facts.stages:
        if fact.in_run:
            continue
        if fact.stage_type is StageType.HANDOFF:
            continue
        if fact.status not in (StageStatus.PASSED, StageStatus.SKIPPED):
            return False
    return True


def derive_milestone_status(facts: WorkflowFacts) -> MilestoneStatus:
    """CP-WF-024, evaluated from the highest watermark down."""
    if facts.delivery_mode == "B" and facts.stage(StageType.HANDOFF) is StageStatus.PASSED:
        return MilestoneStatus.HANDED_OFF
    if any(r.status is RunStatus.VERIFIED for r in facts.runs):
        return MilestoneStatus.VERIFIED
    if any(r.status is RunStatus.COMMITTED for r in facts.runs):
        return MilestoneStatus.DEPLOYED
    if facts.dhis2_validated:
        return MilestoneStatus.DHIS2_VALIDATED
    if facts.package_gate_approved:
        return MilestoneStatus.PACKAGE_APPROVED
    design_approved = facts.architecture_gate_approved and (
        facts.delivery_mode != "B" or facts.design_gate_approved
    )
    if design_approved:
        return MilestoneStatus.DESIGN_APPROVED
    return MilestoneStatus.DRAFT


#: Order used when a caller wants "the furthest reached milestone".
MILESTONE_ORDER: tuple[MilestoneStatus, ...] = (
    MilestoneStatus.DRAFT,
    MilestoneStatus.DESIGN_APPROVED,
    MilestoneStatus.PACKAGE_APPROVED,
    MilestoneStatus.DHIS2_VALIDATED,
    MilestoneStatus.DEPLOYED,
    MilestoneStatus.VERIFIED,
    MilestoneStatus.HANDED_OFF,
)


def current_stage_id(facts: WorkflowFacts, stage_ids: dict[StageType, str]) -> str | None:
    """The stage a human should look at first: RUNNING, then waiting, then ready."""
    ordered: list[StageStatus] = [
        StageStatus.RUNNING,
        StageStatus.WAITING_INPUT,
        StageStatus.WAITING_APPROVAL,
        StageStatus.MANUAL_CHECK,
        StageStatus.BLOCKED,
        StageStatus.FAILED,
        StageStatus.READY,
    ]
    for status in ordered:
        for fact in facts.stages:
            if fact.status is status and fact.stage_type in stage_ids:
                return stage_ids[fact.stage_type]
    return None


__all__ = [
    "MILESTONE_ORDER",
    "RunFact",
    "StageFact",
    "WorkflowFacts",
    "current_stage_id",
    "derive_milestone_status",
    "derive_workflow_status",
]
