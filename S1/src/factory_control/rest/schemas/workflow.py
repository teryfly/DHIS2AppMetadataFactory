"""Workflow and stage contracts (existing routes, S5IF-FLD01 / FLD02).

``feedback`` and ``inputBlueprintRef`` were left as open objects while they were C-class
items; the 2026-09-29 ruling decided both shapes, so they are declared here — with
``feedback.kind`` documented as extensible rather than closed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from factory_control.rest.schemas.common import CamelModel, IssueView, ListEnvelope
from factory_control.rest.schemas.gates import QuestionView
from factory_control.rest.schemas.projects import ProjectStageView
from factory_control.rest.schemas.runs import RunView


class StageRunView(CamelModel):
    id: str
    attempt: int = 1
    agent_run_ids: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    build_ids: list[str] = Field(default_factory=list)
    result: dict[str, Any] | None = None


class MissingCheckView(CamelModel):
    """One precondition the stage cannot start without (S5IF-FLD01)."""

    key: str
    label: str


class InputCompletenessView(CamelModel):
    """Satisfied ÷ total, from the checklist S1 maintains per stage type."""

    satisfied: int = 0
    total: int = 0
    ratio: float | None = None
    missing: list[MissingCheckView] = Field(default_factory=list)


class StageFeedbackView(CamelModel):
    """Why this stage was refused, as S1 recorded it (S5IF-FLD02 【裁决】).

    ``kind`` is **not** a closed set: the six values below are what S1 writes today, the
    list may grow, and S5 renders an unknown value generically — title, reason text,
    jumpable references.  A whitelist in the client would turn an S1 addition into a
    broken screen, which is exactly what the ruling rules out.
    """

    kind: str = "VALIDATION"
    reason: str = ""
    source_stage: str | None = None
    gate_id: str | None = None
    issues: list[IssueView] = Field(default_factory=list)
    created_at: str | None = None
    rejected_by: str | None = None
    denial_reasons: list[str] = Field(default_factory=list)
    rejected_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    budget_status: dict[str, Any] = Field(default_factory=dict)


class BlueprintRefView(CamelModel):
    """What the stage's artifacts were produced from: a live draft or a frozen version."""

    kind: str
    id: str
    hash: str | None = None


class ActiveDraftView(CamelModel):
    """S5IF-BP05 【裁决】: S1 resolves the active draft so S5 never guesses it."""

    draft_id: str
    stage: str | None = None
    stage_scope: list[str] = Field(default_factory=list)
    base_version: str | None = None
    content_hash: str | None = None


class StageView(CamelModel):
    id: str
    workflow_id: str | None = None
    deployment_run_id: str | None = None
    stage_type: str
    status: str
    attempt: int = 1
    input_blueprint_ref: BlueprintRefView | None = None
    output_artifact_ids: list[str] = Field(default_factory=list)
    failure_source_stage: str | None = None
    feedback: StageFeedbackView | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # S5IF-FLD01: the seven items UI-WB-002 shows, computed server-side in one pass.
    input_completeness: InputCompletenessView | None = None
    artifact_count: int = 0
    open_question_count: int = 0
    high_open_question_count: int = 0
    blocking_issue_count: int = 0
    last_run_at: datetime | None = None
    current_artifact_version: str | None = None
    # CP-WF-031: the project's live draft, named by S1 so the client never guesses an id.
    active_draft_id: str | None = None


class StageDetailView(StageView):
    stage_runs: list[StageRunView] = Field(default_factory=list)
    questions: list[QuestionView] = Field(default_factory=list)


class StageList(ListEnvelope):
    items: list[StageView] = Field(default_factory=list)


class WorkflowView(CamelModel):
    id: str
    project_id: str
    status: str
    paused: bool = False
    cancelled: bool = False
    current_stage_id: str | None = None
    last_event_seq: int = 0
    version: int = 0
    milestone_status: str | None = None
    stages: list[ProjectStageView] = Field(default_factory=list)
    deployment_runs: list[RunView] = Field(default_factory=list)
    active_draft: ActiveDraftView | None = None


class WorkflowFlagView(CamelModel):
    workflow_id: str
    paused: bool | None = None
    cancelled: bool | None = None
    status: str | None = None


class ActionEntry(CamelModel):
    action: str
    available: bool
    target_id: str | None = None
    reason_code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AllowedActionsView(CamelModel):
    """S5IF-G10: the single source of truth for whether a button is usable."""

    allowed: list[ActionEntry] = Field(default_factory=list)
    unavailable: list[ActionEntry] = Field(default_factory=list)
    workflow_id: str | None = None
    evaluated_at: str | None = None
    #: ``"workflow"`` for the per-workflow answer and ``"platform"`` for the Administration
    #: pages' view, which has no workflow to name; the entries are shaped identically either
    #: way so S5 keeps exactly one gating mechanism (FR-UI-009).
    scope: str = "workflow"


class SubmitApprovalView(CamelModel):
    stage_id: str | None = None
    status: str | None = None
    gate_id: str | None = None
    blocking: int | None = None
    issues: list[IssueView] = Field(default_factory=list)
    build_id: str | None = None
    artifacts: dict[str, str] | None = None
    draft_hash: str | None = None


class ManualCheckView(CamelModel):
    execution_id: str | None = None
    resolved: bool | None = None
    releases_lock: bool | None = None
    status: str | None = None
    note: str | None = None


class PatchSubmitView(CamelModel):
    """S5IF-FLD11 — ``assignedUids`` is the new part."""

    stage_id: str
    patch_status: str | None = None
    draft_hash: str | None = None
    issues: list[IssueView] = Field(default_factory=list)
    # The Questions this Patch raised or superseded, by id (CP-UI-018).  Their bodies
    # stay on ``GET /workflows/{id}/questions``, which UI-Q-001 reads anyway.
    questions: list[str] = Field(default_factory=list)
    gate_invalidated: bool | None = None
    assigned_uids: dict[str, str] = Field(default_factory=dict)


class QuestionCreatedView(CamelModel):
    question_id: str | None = None


__all__ = [
    "ActiveDraftView",
    "ActionEntry",
    "AllowedActionsView",
    "BlueprintRefView",
    "InputCompletenessView",
    "ManualCheckView",
    "MissingCheckView",
    "PatchSubmitView",
    "QuestionCreatedView",
    "StageDetailView",
    "StageFeedbackView",
    "StageList",
    "StageRunView",
    "StageView",
    "SubmitApprovalView",
    "WorkflowFlagView",
    "WorkflowView",
]
