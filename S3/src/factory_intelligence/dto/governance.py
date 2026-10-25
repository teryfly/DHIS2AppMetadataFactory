"""Typed request and result models of the Governance capabilities (AI-GOV-001).

AI-GOV-001 makes every governance capability a *named function with typed parameters and
typed results*, and forbids "action name plus a free dictionary" from being the public API:
S1 must be able to declare a named REST request and response component for each one
(00 doc 6.11).  The models here are that surface; ``governance/capabilities.py`` produces
them and ``api.py`` exports them.

Field names serialise to the camelCase spelling S5 already reads (S1_add_api_for_S5.md
S5IF-GOV01…GOV19), which is what lets ``governance/dispatcher.py`` keep answering its
legacy action names byte-for-byte while delegating to the same implementation.  A result
model therefore declares ``alias_generator=to_camel`` with ``populate_by_name=True``: the
Python side stays snake_case like the rest of S3, and the wire side stays camelCase like the
rest of the factory.

Nothing here returns a secret value: AI-GOV-002 permits only the interface address and the
Key's *Secret reference* to leave the registry (AC-13).
"""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from factory_intelligence.dto.result import UsageRollup
from factory_intelligence.enums import (
    AgentType,
    Milestone,
    ParseStatus,
    ProviderClass,
    as_provider_class,
)

__all__ = [
    "GovernanceModel",
    "ProviderView",
    "ProviderSpec",
    "ProviderPatch",
    "CapabilitiesView",
    "ModelView",
    "ModelSpec",
    "ModelPatch",
    "EnableResult",
    "ProviderHealthView",
    "PromptView",
    "PromptDetailView",
    "ActivationView",
    "RegressionView",
    "PolicyMatrixView",
    "OrgPolicyView",
    "RelaxationView",
    "EffectivePolicyView",
    "EvaluationSummary",
    "EvaluationDetail",
    "AgentCatalogEntry",
    "AgentCatalog",
    "ParseRecord",
    "SheetColumnMapping",
    "ColumnMappingProposal",
    "NrrFilters",
    "Page",
    "NrrEntryView",
    "NrrQueryResult",
    "ProviderList",
    "ModelList",
    "PromptList",
    "EvaluationList",
    "ParseRecordList",
    "UsageRollupResult",
    "PromptReloadResult",
    "wire",
]


class GovernanceModel(BaseModel):
    """Base for the governance surface: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
        from_attributes=True,
    )


def wire(model: GovernanceModel) -> dict[str, Any]:
    """The camelCase JSON form the legacy ``governance`` façade and S1's REST layer emit."""
    return model.model_dump(mode="json", by_alias=True)


# --- providers and models (AI-REG-004, AI-GOV-002, AI-LLM-003) ------------------


class ProviderView(GovernanceModel):
    """One registered provider — address and Key *reference*, never a value (AC-13)."""

    id: str
    name: str
    provider_class: ProviderClass
    endpoint: str | None = None
    enabled: bool = True
    adapter: str = "openai_compat"
    api_key_secret_ref: str | None = None
    vendor: str = "custom"
    suggested_models: list[str] = Field(default_factory=list)

    @field_validator("provider_class", mode="before")
    @classmethod
    def _class(cls, value: Any) -> Any:
        return as_provider_class(value) if value is not None else None


class ProviderSpec(GovernanceModel):
    """What a caller registers (S5IF-GOV02).  ``adapter`` names the ``LLMProvider``."""
    name: str = Field(min_length=1)
    provider_class: ProviderClass
    endpoint: str | None = None
    api_key_secret_ref: str | None = None
    adapter: str = "openai_compat"
    enabled: bool = True
    #: label only: where the form's starting values came from, not a capability switch
    vendor: str = "custom"
    #: candidate names for the model form; ``ai.model`` remains the registry
    suggested_models: list[str] = Field(default_factory=list)

    @field_validator("provider_class", mode="before")
    @classmethod
    def _class(cls, value: Any) -> Any:
        return as_provider_class(value)


class ProviderPatch(GovernanceModel):
    """What a caller edits on an existing provider (S5IF-GOV03).

    Every field is optional and an absent one means "leave that column as it is" — the
    difference between re-registering and correcting a typo.
    """

    provider_class: ProviderClass | None = None
    endpoint: str | None = None
    api_key_secret_ref: str | None = None
    adapter: str | None = None
    enabled: bool | None = None
    vendor: str | None = None
    suggested_models: list[str] | None = None

    @field_validator("provider_class", mode="before")
    @classmethod
    def _class(cls, value: Any) -> Any:
        return as_provider_class(value) if value is not None else None


class CapabilitiesView(GovernanceModel):
    """What a caller declares a model to be.

    Both defaults are the *usable* reading, not the neutral one: a registration that says
    nothing about capabilities is an operator who left the box alone, and the runtime then
    needs both values to be something other than "refuse everything".  ``structured_output``
    off makes every stage call skip the model (AI-POL-007 asks for a checkable answer), and a
    ``0`` window fails the token cutoff on any request at all — so the pair used here is the
    one the registry itself falls back to (``llm/registry.py`` ``_capabilities``) and the one
    the stand-in answers with (``S1/src/factory_control/testing/fakes.py``).  No positivity
    check is added: turning a silent zero into a 422 is a contract change for callers that
    already post one.
    """

    structured_output: bool = True
    vision: bool = False
    max_context_tokens: int = 8000


class ModelView(GovernanceModel):
    """One registered model with the price, capability and evaluation data AI-REG-004 keeps."""

    id: str
    provider: str
    model: str
    ref: str
    provider_class: ProviderClass
    capabilities: CapabilitiesView
    enabled: bool = True
    #: spelled with a lower-case ``k`` on the wire (S5IF-GOV04/GOV05).  The generator would
    #: turn ``…_per_1k`` into ``…Per1K``, and a price posted under the documented name would
    #: land as this field's default — which makes every model call silently free.
    unit_price_input_per_1k: float = Field(
        default=0.0,
        validation_alias=AliasChoices("unitPriceInputPer1k", "unit_price_input_per_1k"),
        serialization_alias="unitPriceInputPer1k",
    )
    unit_price_output_per_1k: float = Field(
        default=0.0,
        validation_alias=AliasChoices("unitPriceOutputPer1k", "unit_price_output_per_1k"),
        serialization_alias="unitPriceOutputPer1k",
    )
    applicable_agents: dict[str, int] = Field(default_factory=dict)
    last_eval_score: float | None = None
    last_eval_run_id: str | None = None
    #: AI-LLM-003: a model may be served at its own address and Key, so the two values a
    #: call actually uses are reported here rather than only on the provider row.
    endpoint: str | None = None
    api_key_secret_ref: str | None = None
    adapter: str = "openai_compat"

    @field_validator("provider_class", mode="before")
    @classmethod
    def _class(cls, value: Any) -> Any:
        return as_provider_class(value) if value is not None else None


class ModelSpec(GovernanceModel):
    """What a caller registers (S5IF-GOV05)."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    capabilities: CapabilitiesView = Field(default_factory=CapabilitiesView)
    unit_price_input_per_1k: float = Field(
        default=0.0,
        validation_alias=AliasChoices("unitPriceInputPer1k", "unit_price_input_per_1k"),
        serialization_alias="unitPriceInputPer1k",
    )
    unit_price_output_per_1k: float = Field(
        default=0.0,
        validation_alias=AliasChoices("unitPriceOutputPer1k", "unit_price_output_per_1k"),
        serialization_alias="unitPriceOutputPer1k",
    )
    applicable_agents: dict[str, int] = Field(default_factory=dict)
    enabled: bool = True
    endpoint: str | None = None
    api_key_secret_ref: str | None = None

    @field_validator("applicable_agents", mode="before")
    @classmethod
    def _priorities(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("applicableAgents must map an agent type to a priority")
        return {str(key): int(item) for key, item in value.items()}


class ModelPatch(GovernanceModel):
    """What a caller edits on an existing model (S5IF-GOV06).

    Every field is optional and an absent one means "leave that column as it is" — the difference
    between correcting a price and re-registering the row.  :func:`register_model` writes all
    seven columns, and two of them (`endpoint`, `api_key_secret_ref`) are nullable, so a
    re-registration that omitted them does not leave them alone: it clears AI-LLM-003's per-model
    override, and S1's read path then back-fills the provider's own address so the console shows a
    value the operator never set.

    ``capabilities`` is a bare mapping rather than a :class:`CapabilitiesView` on purpose: a typed
    view cannot tell "the caller did not name this key" apart from "the caller named it as its own
    default", which is exactly the distinction this patch exists to keep.  The keys are the wire
    spellings, and an unknown one is refused by the registry rather than ignored.
    """

    capabilities: dict[str, Any] | None = None
    unit_price_input_per_1k: float | None = Field(
        default=None,
        validation_alias=AliasChoices("unitPriceInputPer1k", "unit_price_input_per_1k"),
        serialization_alias="unitPriceInputPer1k",
    )
    unit_price_output_per_1k: float | None = Field(
        default=None,
        validation_alias=AliasChoices("unitPriceOutputPer1k", "unit_price_output_per_1k"),
        serialization_alias="unitPriceOutputPer1k",
    )
    applicable_agents: dict[str, int] | None = None
    enabled: bool | None = None
    endpoint: str | None = None
    api_key_secret_ref: str | None = None


class EnableResult(GovernanceModel):
    """The answer of a set-*-enabled call (S5IF-GOV03/GOV06)."""

    ref: str
    enabled: bool


class ProviderHealthView(GovernanceModel):
    """What a reachability probe of one provider answered (S5IF-GOV20).

    A pydantic view rather than the internal ``HealthStatus`` dataclass: S1's adapter coerces
    ``BaseModel``/``dict``/``list`` and its governance projector does ``dict(value)`` on anything
    else, so handing back the dataclass would raise at request time instead of answering.

    ``detail`` is the transport's own sentence, and never a credential — a provider's key travels
    only as its reference (AC-13).
    """

    ref: str
    healthy: bool
    detail: str | None = None


# --- prompts and activations (AI-REG-001/002, AI-GOV-003) ----------------------


class PromptView(GovernanceModel):
    id: str
    name: str
    version: int
    qualified_name: str
    agent_type: str | None = None
    blueprint_schema_range: str | None = None
    dhis2_version_range: str | None = None
    prohibitions: list[Any] = Field(default_factory=list)
    source_hash: str | None = None
    #: AI-REG-002 distinguishes "a version that exists" from "the version in use"
    active: bool = False


class PromptDetailView(PromptView):
    system_rules: str | None = None
    body: str | None = None
    input_schema_ref: str | None = None
    output_schema_ref: str | None = None
    applicable_stages: list[Any] = Field(default_factory=list)


class ActivationView(GovernanceModel):
    """One agent's currently effective prompt (AI-REG-002, S5IF-GOV09/GOV10)."""

    agent_type: str
    prompt_version_id: str
    name: str = ""
    version: int = 0
    model_list: list[str] = Field(default_factory=list)
    eval_run_id: str | None = None
    regression_passed_ref: str | None = None
    activated_by: str | None = None


class RegressionView(GovernanceModel):
    """The registered claim that a Golden Project regression passed (AI-REG-003)."""

    id: str
    passed: bool
    prompt_version_id: str


# --- policy (AI-POL-001/002/004, AI-GOV-006) -----------------------------------


class PolicyMatrixView(GovernanceModel):
    """``{allowed, sources, warnings}`` — the form ``ai.policy_decision`` stores."""

    allowed: dict[str, list[str]] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OrgPolicyView(GovernanceModel):
    effective: PolicyMatrixView
    is_default: bool = False
    updated_by: str | None = None
    #: The outermost policy a relaxation may reach (AI-POL-002/AI-POL-005).  Shipped with the
    #: effective matrix rather than as a separate call because a form needs both to offer a
    #: choice: with only the first it cannot tell a legal widening from one S3 will refuse.
    ceiling: PolicyMatrixView = Field(default_factory=PolicyMatrixView)


class RelaxationView(GovernanceModel):
    id: str
    scope: str
    operator: str
    reason: str = ""
    change: dict[str, Any] = Field(default_factory=dict)
    role_checked: bool = False
    #: D-16: which regime answered — until M4 the operator's name is the identity
    role_gate: str = "operator-name (MVP)"
    created_at: datetime.datetime | None = None


class EffectivePolicyView(PolicyMatrixView):
    """The strictest of the organisation policy and every target environment (D-07)."""


# --- evaluation (AI-EVAL-001, AI-GOV-004) --------------------------------------


class EvaluationSummary(GovernanceModel):
    id: str
    agent_type: str
    prompt: str
    model: str
    eval_set_version: str
    score: float
    passed: bool
    case_count: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime.datetime | None = None


class EvalCaseView(GovernanceModel):
    case_id: str
    passed: bool
    score: float = 0.0
    kind: str = "golden"
    diff: dict[str, Any] = Field(default_factory=dict)


class EvaluationDetail(EvaluationSummary):
    """AI-GOV-004: the per-case results and the injection samples' standing, not just the score."""

    cases: list[EvalCaseView] = Field(default_factory=list)
    injection_cases: int = 0
    injection_passed: int = 0


# --- agent catalogue (AI-GOV-008) ----------------------------------------------


class AgentCatalogEntry(GovernanceModel):
    agent_type: AgentType
    stage: str
    milestone: Milestone
    enabled: bool
    prompt_name: str
    #: AI-GOV-008 asks for the *currently effective* prompt, which is registry state, not
    #: the spec's default — an agent can be enabled with no active version at all.
    active_prompt: str | None = None
    per_page: bool = False
    patch_mode: str = "PATCH"


class AgentCatalog(GovernanceModel):
    milestone: Milestone = Milestone.M2
    items: list[AgentCatalogEntry] = Field(default_factory=list)


# --- input parse read models (AI-INP-013/014/015) ------------------------------


class ParseRecord(GovernanceModel):
    """The most recent parse of one input artifact (AI-INP-013, S1 CP-UI-007 item 2)."""

    parse_id: str
    project_id: str
    input_artifact_id: str
    input_hash: str
    parser_type: str
    parser_version: str
    used_model: bool
    status: ParseStatus
    nrr_artifact_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime.datetime | None = None


class SheetColumnMapping(GovernanceModel):
    """One worksheet's proposal (AI-INP-014): headers, category, mapping, basis, keys."""

    sheet: str
    headers: list[str] = Field(default_factory=list)
    #: the list category the header row was read as (AI-INP-005's vocabulary)
    category: str | None = None
    #: source column -> target field, in the spelling ``options.columnMapping`` takes
    mapping: dict[str, str] = Field(default_factory=dict)
    #: how the parser recognised the sheet, so a reviewer can contest one rule
    recognised_by: str | None = None
    affected_keys: list[str] = Field(default_factory=list)
    question: str | None = None


class ColumnMappingProposal(GovernanceModel):
    input_artifact_id: str
    project_id: str
    parse_id: str | None = None
    #: NEEDS_REVIEW when the mapping was proposed rather than supplied (AI-INP-006)
    status: ParseStatus | None = None
    sheets: list[SheetColumnMapping] = Field(default_factory=list)


class NrrFilters(GovernanceModel):
    """AI-INP-015's filter set: category, input artifact, confidence interval."""

    categories: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confirmed_only: bool | None = None

    @field_validator("categories", "input_artifact_ids", mode="before")
    @classmethod
    def _as_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value]


class Page(GovernanceModel):
    """Offset pagination in the 00 doc 6.11 envelope shape."""

    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


# --- list envelopes for the ``governance`` façade (AI-GOV-009) --------------------
#
# Every façade action answers with one of these rather than a hand-built dict: the declared
# result type is what the compatibility test checks, and "a dict assembled on the spot" is the
# shape AI-GOV-001 removed for good reason (S1 could not type a REST response against it).

class ProviderList(GovernanceModel):
    items: list[ProviderView] = Field(default_factory=list)


class ModelList(GovernanceModel):
    items: list[ModelView] = Field(default_factory=list)


class PromptList(GovernanceModel):
    items: list[PromptView] = Field(default_factory=list)


class EvaluationList(GovernanceModel):
    items: list[EvaluationSummary] = Field(default_factory=list)


class ParseRecordList(GovernanceModel):
    items: list[ParseRecord] = Field(default_factory=list)


class UsageRollupResult(GovernanceModel):
    """``usage.rollup`` — the grouping it answered for, so a cached page cannot be re-labelled."""

    group_by: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    items: list[UsageRollup] = Field(default_factory=list)


class PromptReloadResult(GovernanceModel):
    """``prompt.source.reload`` — what the re-read put in the catalogue."""

    registered: int = 0
    names: list[str] = Field(default_factory=list)


class NrrEntryView(GovernanceModel):
    business_key: str
    category: str
    statement: str
    #: provenance AI-INP-005 requires of every entry: which input, and where in it
    artifact_id: str | None = None
    location: str | None = None
    confidence: float = 0.0
    confirmed: bool = False
    parse_id: str | None = None


class NrrQueryResult(GovernanceModel):
    items: list[NrrEntryView] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


# --- parse-time LLM exchange log (operational transcript) -----------------------


class LlmExchangeImage(GovernanceModel):
    """One picture the request carried — metadata only; the base64 never leaves the call."""

    media_type: str
    label: str
    content_hash: str
    size_bytes: int


class LlmExchangeMessage(GovernanceModel):
    role: str
    content: str
    images: list[LlmExchangeImage] = Field(default_factory=list)


class LlmExchangeRequest(GovernanceModel):
    """The request as it went to the provider: messages, schema and call parameters."""

    messages: list[LlmExchangeMessage] = Field(default_factory=list)
    schema_name: str | None = None
    json_schema: dict[str, Any] | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    agent_hint: dict[str, Any] | None = None


class LlmExchangeResponse(GovernanceModel):
    text: str | None = None
    payload: dict[str, Any] | None = None
    finish_reason: str | None = None
    truncated: bool = False
    model: str | None = None
    request_id: str | None = None
    usage: dict[str, int] | None = None
    #: the answer exceeded the storage bound and was cut *for the log*, not for the parse
    storage_truncated: bool = False


class LlmExchangeSummary(GovernanceModel):
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
    created_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None


class LlmExchangePage(GovernanceModel):
    items: list[LlmExchangeSummary] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class LlmExchangeDetail(LlmExchangeSummary):
    input_artifact_id: str
    request: LlmExchangeRequest
    response: LlmExchangeResponse | None = None
    expires_at: datetime.datetime | None = None
