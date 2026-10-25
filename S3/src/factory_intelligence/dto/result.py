"""Results out of S3 (AI-RT-017, SRS 7).

:class:`AgentRunResult` and :class:`ParsedInput` are the two shapes S1 branches on.
Their field names are S1's, and ``tests/unit/test_contract.py`` compares them against
``factory_control.ports.dto`` so the coupling is a test rather than a memory.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from factory_intelligence.dto.common import ApiModel, Issue, Question, RejectedArtifact
from factory_intelligence.enums import (
    BudgetState,
    ParseStatus,
    PolicyResult,
    ProviderClass,
    RunState,
)


class AgentRunResult(ApiModel):
    """One run's outcome (AI-RT-017).

    ``status`` is AI-RT-017's six results plus ``RUNNING``, which only a *query* ever
    returns: starting or resuming a run answers with a finished state, and a caller that
    asks about a run in flight (``get_agent_run``) is told the truth — ``RUNNING`` with its
    last progress — instead of one of the six words that would send S1's workflow to the
    wrong place.  S1 reads this field as a string, and AI-AC-14 requires exactly this.

    Question *texts* are deliberately absent (AI-RT-019): a Question is S1's to store and
    display, so only the local ids are returned, which is what lets a caller join its own
    rows to this run.
    """

    run_id: str | None = None
    status: RunState = RunState.COMPLETED
    summary: str | None = None
    confidence: float | None = None
    patch_ids: list[str] = Field(default_factory=list)
    agent_output_artifact_id: str | None = None
    questions: list[Question] = Field(default_factory=list)
    rejected_artifacts: list[RejectedArtifact] = Field(default_factory=list)
    #: {"state": "OK", "spent": 0.42, "limit": 5.0} — AI-COST-002's alert rides here
    budget_status: dict[str, Any] = Field(default_factory=dict)
    #: AI-POL-007: one entry per model that was tried and refused, with its reason
    denial_reasons: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    #: AI-RT-019: the run's own question ids, so a caller can join them to the Questions it
    #: stores; the text is S1's, and reading the run-context artifact here would make a
    #: read-only function perform external I/O inside the caller's transaction (00 doc 6.2)
    question_local_ids: list[str] = Field(default_factory=list)
    #: AI-RT-019's 最近进度: ``{step, completedFraction, data}`` as last reported
    last_progress: dict[str, Any] = Field(default_factory=dict)

    @property
    def blocking_questions(self) -> list[Question]:
        return [question for question in self.questions if question.is_blocking]


class ParsedInput(ApiModel):
    """Outcome of :func:`factory_intelligence.api.parse_input` (AI-INP-004/006/007)."""

    status: ParseStatus = ParseStatus.PARSED
    nrr_artifact_id: str | None = None
    questions: list[Question] = Field(default_factory=list)
    rejected_artifacts: list[RejectedArtifact] = Field(default_factory=list)
    parser_version: str | None = None
    #: which parser actually ran, and whether a model was involved (AI-INP-004)
    parser_type: str | None = None
    used_model: bool = False
    #: AI-INP-003a: the human-readable next step for a failed parse
    error_code: str | None = None
    error_message: str | None = None
    parse_id: str | None = None
    #: AI-RT-018: a parse's findings travel in the same unified Issue structure a run's do,
    #: with ``level`` — the workbench shows one list of problems per input, not one list
    #: per subsystem that spells its severity differently
    issues: list[Issue] = Field(default_factory=list)


class DeniedItem(ApiModel):
    ref: str
    classification: str | None = None
    reason: str = ""
    allowed_classes: list[str] = Field(default_factory=list)


class PolicyOutcome(ApiModel):
    """The verdict of one policy evaluation (AI-POL-003, AI-POL-008)."""

    result: PolicyResult = PolicyResult.ALLOWED
    provider_class: ProviderClass | None = None
    model: str | None = None
    #: the effective matrix that was applied, with its sources named for the audit
    effective_policy: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)
    denied_items: list[DeniedItem] = Field(default_factory=list)
    model_denials: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    decision_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.result is PolicyResult.ALLOWED and not self.denied_items

    def rejected_artifacts(self) -> list[RejectedArtifact]:
        """Same shape the run result and the parse result report (AI-INP-007)."""
        return [
            RejectedArtifact(
                ref=item.ref,
                classification=item.classification,
                reason=item.reason,
                allowed_classes=item.allowed_classes,
            )
            for item in self.denied_items
        ]


class BudgetView(ApiModel):
    """AI-COST-002/003 as reported to a caller."""

    state: BudgetState = BudgetState.UNSET
    spent: float = 0.0
    limit: float | None = None
    currency: str = "USD"
    #: which threshold, if any, the spend has crossed
    threshold_reached: float | None = None

    def as_result_field(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "spent": self.spent,
            "limit": self.limit,
            "currency": self.currency,
            "thresholdReached": self.threshold_reached,
        }


class UsageRollup(ApiModel):
    """One row of the AI-COST-001 group-by."""

    #: absent only on a cross-project rollup that did not group by project, which is the
    #: case AI-GOV-005 allows by passing ``project_id=None``
    project_id: str | None = None
    workflow_id: str | None = None
    stage: str | None = None
    agent_type: str | None = None
    model: str | None = None
    provider: str | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    failed_calls: int = 0


class EvalCaseOutcome(ApiModel):
    case_id: str
    passed: bool
    score: float = 0.0
    kind: str = "golden"
    diff: dict[str, Any] = Field(default_factory=dict)


class EvalResult(ApiModel):
    """AI-EVAL-001: a score plus the per-item differences that explain it."""

    eval_run_id: str | None = None
    agent_type: str
    prompt_name: str
    prompt_version: int
    model: str
    eval_set_version: str
    score: float = 0.0
    passed: bool = False
    cases: list[EvalCaseOutcome] = Field(default_factory=list)
    #: AI-EVAL-003: reported apart, because an otherwise perfect run that obeyed an
    #: injection sample must not pass.
    injection_cases: int = 0
    injection_passed: int = 0

    @property
    def qualified_prompt(self) -> str:
        return f"{self.prompt_name}.v{self.prompt_version}"


class ChatReply(ApiModel):
    """M3 (AI-CHAT-001/002); the shape is declared now so enabling it adds no API."""

    answer: str = ""
    patch_ids: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)


class DesensitizationOutcome(ApiModel):
    """M4 (AI-POL-009)."""

    derived_artifact_id: str
    record_id: str
    replaced_count: int = 0


__all__ = [
    "AgentRunResult",
    "ParsedInput",
    "DeniedItem",
    "PolicyOutcome",
    "BudgetView",
    "UsageRollup",
    "EvalCaseOutcome",
    "EvalResult",
    "ChatReply",
    "DesensitizationOutcome",
]
