"""Input parsing, column mapping and issue explanation contracts (S5IF-INP)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from factory_control.rest.schemas.common import CamelModel, IssueView, ListEnvelope
from factory_control.rest.schemas.gates import QuestionView

# An input that was never parsed says so by leaving this empty — CP-UI-007.2.
PARSE_STATUSES = ("PARSING", "PARSED", "NEEDS_REVIEW", "FAILED", "POLICY_DENIED")
AGENT_RUN_STATUSES = (
    # S3 answers WAITING_INPUT for a run that is still RUNNING; S1 corrects it
    # from its own stage state before responding (CP-UI-007.5).
    "RUNNING",
    "COMPLETED",
    "WAITING_INPUT",
    "FAILED",
    "POLICY_DENIED",
    "BUDGET_EXCEEDED",
    "CANCELLED",
)


class ParseOptions(CamelModel):
    """S5IF-INP04 — the same endpoint covers a first parse and a re-parse with a
    confirmed column mapping (AI-INP-006)."""

    column_mapping: dict[str, str] = Field(default_factory=dict)
    sheet: str | None = None
    delimiter: str | None = None
    has_header: bool | None = None
    parser: str | None = None
    exported_as: str | None = None

    def to_options(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in (
                ("columnMapping", dict(self.column_mapping)),
                ("sheet", self.sheet),
                ("delimiter", self.delimiter),
                ("hasHeader", self.has_header),
                ("parser", self.parser),
                ("exportedAs", self.exported_as),
            )
            if value not in (None, {}, "")
        }


class ParseInputRequest(CamelModel):
    stage_id: str | None = None
    options: ParseOptions | None = None


class InputView(CamelModel):
    """S5IF-INP02 — an uploaded input plus how far its parsing got."""

    artifact_id: str
    filename: str | None = None
    media_type: str | None = None
    size_bytes: int = 0
    content_hash: str | None = None
    data_classification: str | None = None
    created_at: datetime | None = None
    parse_status: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    parser_version: str | None = None
    parser_type: str | None = None
    # Null until a parse has run; a bool once it has (S5IF-INP02 asks 是否使用模型, and S3's
    # ``used_model`` is a bool — the mirror and this view both said string).
    used_model: bool | None = None
    parse_id: str | None = None
    #: the background run the list can link to the LLM log; null for pre-upgrade parses
    parse_run_id: str | None = None
    nrr_artifact_id: str | None = None


class InputPage(ListEnvelope):
    items: list[InputView] = Field(default_factory=list)


class ColumnMappingProposalView(CamelModel):
    """S5IF-INP03 — the transitional shape.

    S3 keeps the structured ``TablePlan`` and ``proposedColumnMapping`` inside the
    NRR document and exports neither (S5IF-UP07), so what S1 can honestly forward
    today is the mapping each review Question already carries.
    """

    question_id: str | None = None
    sheet: str | None = None
    question: str | None = None
    headers: list[str] = Field(default_factory=list)
    affected_keys: list[str] = Field(default_factory=list)
    column_mapping: dict[str, str] = Field(default_factory=dict)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)


class ColumnMappingView(CamelModel):
    artifact_id: str
    workflow_id: str | None = None
    proposals: list[ColumnMappingProposalView] = Field(default_factory=list)
    # Declared for the structured proposal S3 has not exported yet.
    table_plans: list[dict[str, Any]] | None = None


class ExplainRequest(CamelModel):
    project_id: str


class ExplainResultView(CamelModel):
    """The body is *not* here: it lives in the AGENT_OUTPUT artifact (S5IF-INP05)."""

    explanation_artifact_id: str | None = None


class AgentProgressView(CamelModel):
    step: str | None = None
    completed_fraction: float | None = None
    message: str | None = None


class AgentRunView(CamelModel):
    """S5IF-INP06 — S1's Question store and event stream complete what S3 reports."""

    run_id: str
    workflow_id: str | None = None
    stage_id: str | None = None
    stage: str | None = None
    status: str = "COMPLETED"
    summary: str | None = None
    confidence: float | None = None
    patch_ids: list[str] = Field(default_factory=list)
    agent_output_artifact_id: str | None = None
    # Questions are S1's own rows (CP-Q-001), and the projection is the same component
    # ``GET /workflows/{id}/questions`` answers with — one shape, two entry points.
    questions: list[QuestionView] = Field(default_factory=list)
    issues: list[IssueView] = Field(default_factory=list)
    budget_status: dict[str, Any] = Field(default_factory=dict)
    denial_reasons: list[str] = Field(default_factory=list)
    rejected_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    progress: AgentProgressView | None = None


class CancelRunView(CamelModel):
    run_id: str
    status: str = "CANCELLED"


class LlmExchangeImageView(CamelModel):
    """A picture the request carried, as metadata — the base64 never leaves S3's call."""

    media_type: str
    label: str
    content_hash: str
    size_bytes: int


class LlmExchangeMessageView(CamelModel):
    role: str
    content: str
    images: list[LlmExchangeImageView] = Field(default_factory=list)


class LlmExchangeRequestView(CamelModel):
    messages: list[LlmExchangeMessageView] = Field(default_factory=list)
    schema_name: str | None = None
    json_schema: dict[str, Any] | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    agent_hint: dict[str, Any] | None = None


class LlmExchangeResponseView(CamelModel):
    text: str | None = None
    payload: dict[str, Any] | None = None
    finish_reason: str | None = None
    truncated: bool = False
    model: str | None = None
    request_id: str | None = None
    usage: dict[str, int] | None = None
    storage_truncated: bool = False


class LlmExchangeSummaryView(CamelModel):
    exchange_id: str
    sequence: int
    status: str
    provider: str
    provider_class: str | None = None
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class LlmLogView(CamelModel):
    """S5IF-INP08 — one input's parse-time model calls, plus the status that explains them."""

    parse_status: str | None = None
    parse_run_id: str | None = None
    items: list[LlmExchangeSummaryView] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class LlmExchangeDetailView(LlmExchangeSummaryView):
    input_artifact_id: str
    request: LlmExchangeRequestView
    response: LlmExchangeResponseView | None = None
    expires_at: datetime | None = None


__all__ = [
    "AGENT_RUN_STATUSES",
    "AgentProgressView",
    "AgentRunView",
    "CancelRunView",
    "ColumnMappingProposalView",
    "ColumnMappingView",
    "ExplainRequest",
    "ExplainResultView",
    "InputPage",
    "InputView",
    "LlmExchangeDetailView",
    "LlmExchangeImageView",
    "LlmExchangeMessageView",
    "LlmExchangeRequestView",
    "LlmExchangeResponseView",
    "LlmExchangeSummaryView",
    "LlmLogView",
    "PARSE_STATUSES",
    "ParseInputRequest",
    "ParseOptions",
]
