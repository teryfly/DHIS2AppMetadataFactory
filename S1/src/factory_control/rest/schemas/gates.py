"""Gate and Question contracts (existing routes, S5IF-FLD07 / FLD08).

``warnings`` keeps S2's ``level`` spelling as the single name S5 sees: S3 calls the
same concept ``severity`` and S1 normalises at this boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from factory_control.errors import ValidationFailedError
from factory_control.rest.schemas.blueprint import DecisionView
from factory_control.rest.schemas.common import BoundObjectView, CamelModel, ListEnvelope, UpperSnakeValue

WARNING_KINDS = ("BACKUP", "IMPORT_PARAMETER", "MAINTENANCE_ACTION")


class GateWarningView(CamelModel):
    issue_id: str | None = None
    rule_id: str | None = None
    level: str = "WARNING"
    message: str = ""
    business_key: str | None = None
    source_stage: str | None = None
    remediation: str | None = None


class ConfirmationItemView(CamelModel):
    id: str
    label: str | None = None
    required: bool = True
    kind: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_kind(self) -> ConfirmationItemView:
        if self.kind is not None and self.kind not in WARNING_KINDS:
            raise ValidationFailedError(
                "confirmationItems[].kind is not a known kind",
                details={"kind": self.kind, "allowed": list(WARNING_KINDS)},
            )
        return self


class ApprovalView(CamelModel):
    id: str
    approver_name: str | None = None
    decision: str | None = None
    comment: str | None = None
    created_at: datetime | None = None


class GateView(CamelModel):
    id: str
    type: str
    workflow_id: str | None = None
    stage_id: str | None = None
    deployment_run_id: str | None = None
    environment_key: str | None = None
    blueprint_ref: dict[str, Any] | None = None
    bound_objects: list[BoundObjectView] = Field(default_factory=list)
    warnings: list[GateWarningView] = Field(default_factory=list)
    risk_summary_ref: dict[str, Any] | None = None
    confirmation_items: list[ConfirmationItemView] = Field(default_factory=list)
    required_approvals: int = 1
    status: str
    invalidated_reason: str | None = None
    approvals: list[ApprovalView] = Field(default_factory=list)
    created_at: datetime | None = None


class GateList(ListEnvelope):
    items: list[GateView] = Field(default_factory=list)


class GateSummaryView(CamelModel):
    """One row of the Dashboard's Gate list (S5IF-FLD03).

    The two warning numbers are separate because UI-AC-04 acts on different ones: the
    approver sees how many there are, the button is greyed out by how many are still
    unresolved.  ``highOpenQuestionCount`` is CP-Q-006's blocker, shown on the list so the
    approver knows before opening the Gate.
    """

    id: str
    workflow_id: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    stage_id: str | None = None
    deployment_run_id: str | None = None
    environment_key: str | None = None
    type: str
    status: str
    required_approvals: int = 1
    approval_count: int = 0
    warning_count: int = 0
    unresolved_warning_count: int = 0
    high_open_question_count: int = 0
    created_at: datetime | None = None


class GateSummaryList(ListEnvelope):
    items: list[GateSummaryView] = Field(default_factory=list)


class GateFoundView(CamelModel):
    bound_objects: list[BoundObjectView] = Field(default_factory=list)


class GateWillDoView(CamelModel):
    gate_type: str | None = None
    environment_key: str | None = None
    risk_summary_ref: dict[str, Any] | None = None
    confirmation_items: list[ConfirmationItemView] = Field(default_factory=list)


class GateUncertainView(CamelModel):
    warnings: list[GateWarningView] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)


class GateRejectionView(CamelModel):
    """What rejecting a Gate answers: the Gate moved, and the stage it gates came back."""

    gate_id: str
    status: str
    stage_id: str | None = None


class GateExplanationView(CamelModel):
    """The six sections UI-GATE-001/004 renders (already present, now typed)."""

    gate_id: str
    gate_type: str
    found: GateFoundView = Field(default_factory=GateFoundView)
    will_do: GateWillDoView = Field(default_factory=GateWillDoView)
    why: dict[str, list[DecisionView]] = Field(default_factory=dict)
    uncertain: GateUncertainView = Field(default_factory=GateUncertainView)
    can_change: list[dict[str, Any]] = Field(default_factory=list)
    after_approval: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(CamelModel):
    decision: str
    comment: str | None = None
    approver_name: str | None = None
    created_at: datetime | None = None


class InvalidationPlanView(CamelModel):
    """What a Major promotion just invalidated (CP-WF-015).

    The approval answer has to carry it: the user approved one Gate and the effect ran
    across the workflow, so the screen must be able to say which stages, Gates and Runs
    moved without a second request.
    """

    invalidate_stages: list[str] = Field(default_factory=list)
    invalidate_gates: list[str] = Field(default_factory=list)
    cancel_runs: list[str] = Field(default_factory=list)
    outdated_runs: list[str] = Field(default_factory=list)
    reason: str | None = None


class GateDecisionView(CamelModel):
    gate_id: str
    status: str
    approvals: list[ApprovalDecision] = Field(default_factory=list)
    required_approvals: int = 1
    invalidated_reason: str | None = None
    next_stage: str | None = None
    blueprint_version: str | None = None
    change_level: UpperSnakeValue | None = None
    affected_stages: list[str] = Field(default_factory=list)
    invalidation: InvalidationPlanView | None = None


class SuggestionView(CamelModel):
    """S5IF-FLD08 — the shape UI-Q-001 needs, as S3 already produces it.

    ``extra`` is deliberately open: a column-mapping suggestion carries keys the
    generic suggestion does not have, and dropping them would take the mapping away
    from the screen that exists to review it.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow", from_attributes=True
    )

    id: str
    text: str | None = None
    rationale: str | None = None
    recommended: bool | None = None
    changes_uid: bool | None = None
    regenerates: list[str] = Field(default_factory=list)
    column_mapping: dict[str, str] | None = None


class QuestionSourceView(CamelModel):
    kind: str | None = None
    agent_run_id: str | None = None
    local_id: str | None = None


class QuestionAnswerView(CamelModel):
    """The answer S1 recorded (CP-Q-005 / CP-Q-007).

    ``text`` is the sentence the agent was resumed with — custom text when the operator
    wrote some, the chosen suggestion otherwise — and ``suggestionText`` stays beside the
    id so a reviewer can see what was picked without a second request.
    """

    suggestion_id: str | None = None
    suggestion_ids: list[str] = Field(default_factory=list)
    suggestion_text: str | None = None
    suggestion_texts: list[str] = Field(default_factory=list)
    custom_text: str | None = None
    text: str | None = None
    answered_at: str | None = None


class QuestionView(CamelModel):
    id: str
    project_id: str | None = None
    workflow_id: str | None = None
    stage_id: str | None = None
    source: QuestionSourceView = Field(default_factory=QuestionSourceView)
    text: str = ""
    severity: str = "MEDIUM"
    #: SINGLE renders as a radio group, MULTIPLE as a checkbox group
    answer_mode: str = "SINGLE"
    affects: list[str] = Field(default_factory=list)
    suggestions: list[SuggestionView] = Field(default_factory=list)
    blocking: bool = False
    status: str = "OPEN"
    answer: QuestionAnswerView | None = None
    answered_by: str | None = None
    answered_at: datetime | None = None
    created_at: datetime | None = None


class QuestionList(ListEnvelope):
    items: list[QuestionView] = Field(default_factory=list)


class QuestionPage(ListEnvelope):
    """A page of questions from more than one project (`GET /questions`)."""

    items: list[QuestionView] = Field(default_factory=list)


class AnswerView(CamelModel):
    """The answer, and whether it was the answer that unblocked the run (CP-Q-005/007).

    ``resumed`` is false while another blocking question is still open; the run continues
    on the last answer, not on this one.
    """

    question_id: str
    status: str
    resumed: bool = False


class QuestionnaireAnswerResult(CamelModel):
    question_id: str
    status: str


class QuestionnaireAnswerView(CamelModel):
    """The outcome of one questionnaire submit.

    ``questions`` is every question still open on the stage *after* the run resumed, so the
    review screen can render the next round immediately instead of guessing whether new
    questions appeared (and without a second read that could race the run).
    """

    answers: list[QuestionnaireAnswerResult] = Field(default_factory=list)
    resumed: bool = False
    run_id: str | None = None
    status: str | None = None
    questions: list[QuestionView] = Field(default_factory=list)
    run_id: str | None = None


__all__ = [
    "AnswerView",
    "ApprovalView",
    "ConfirmationItemView",
    "GateDecisionView",
    "GateExplanationView",
    "InvalidationPlanView",
    "GateRejectionView",
    "GateList",
    "GateView",
    "QuestionAnswerView",
    "QuestionList",
    "QuestionView",
    "SuggestionView",
]
