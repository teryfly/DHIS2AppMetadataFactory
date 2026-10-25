"""Governance contracts (S5IF-GOV01 … GOV19).

S3's façade answers each action with a hand-built dict and no model at all
(S5IF-UP08), so these are the typed shapes S1 promises S5 — field-for-field what
``governance/dispatcher.py`` returns today.  Nothing here is invented: an absent
upstream field is declared optional and answered ``null``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from factory_control.errors import ValidationFailedError
from factory_control.rest.schemas.common import CamelModel, ListEnvelope
from factory_control.rest.schemas.environments import require_reference

PROVIDER_CLASSES = ("LOCAL_ONLY", "PRIVATE_NETWORK", "APPROVED_EXTERNAL", "UNRESTRICTED")
USAGE_DIMENSIONS = ("project", "workflow", "stage", "agent", "model", "provider")
BUDGET_STATES = ("UNSET", "OK", "WARNING", "EXCEEDED")


class ProviderView(CamelModel):
    id: str | None = None
    name: str | None = None
    provider_class: str | None = None
    endpoint: str | None = None
    enabled: bool = True
    adapter: str | None = None
    api_key_secret_ref: str | None = None
    #: the catalogue preset the row started from; a console label, not a capability
    vendor: str = "custom"
    #: candidate model names for the model form; ``ai.model`` remains the registry
    suggested_models: list[str] = Field(default_factory=list)


class ProviderList(ListEnvelope):
    items: list[ProviderView] = Field(default_factory=list)


class ProviderHealthView(CamelModel):
    """S5IF-GOV20 — what one reachability probe answered.

    ``detail`` is the transport's own sentence (``provider answered HTTP 401``, ``model endpoint
    unreachable: ConnectError``); the credential is resolved inside the adapter and never appears
    here, in a mask or as a length (AI-LLM-005).
    """

    ref: str | None = None
    healthy: bool = False
    detail: str | None = None


class ModelCapabilities(CamelModel):
    structured_output: bool = False
    vision: bool = False
    max_context_tokens: int | None = None


class ModelView(CamelModel):
    id: str | None = None
    provider: str | None = None
    model: str | None = None
    ref: str | None = None
    provider_class: str | None = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    enabled: bool = True
    # S3 spells these with a lower-case ``k``, which no alias generator produces from
    # a snake field, so the wire name is stated literally.
    unit_price_input_per_1k: float = Field(default=0.0, alias="unitPriceInputPer1k")
    unit_price_output_per_1k: float = Field(default=0.0, alias="unitPriceOutputPer1k")
    applicable_agents: dict[str, int | str] = Field(default_factory=dict)
    last_eval_score: float | None = None
    last_eval_run_id: str | None = None
    # UI-ADM-003 shows the interface address and the Key's secret reference.  S3's
    # ``_model_view`` does not project them (S5IF-UP08), so S1 fills them from the
    # provider list it already has — reference only, never a value (S5IF-G09).
    endpoint: str | None = None
    api_key_secret_ref: str | None = None


class ModelList(ListEnvelope):
    items: list[ModelView] = Field(default_factory=list)


class PromptListView(CamelModel):
    id: str | None = None
    name: str | None = None
    version: int | None = None
    qualified_name: str | None = None
    agent_type: str | None = None
    blueprint_schema_range: str | None = None
    dhis2_version_range: str | None = None
    prohibitions: list[Any] = Field(default_factory=list)
    source_hash: str | None = None
    active: bool = False


class PromptList(ListEnvelope):
    items: list[PromptListView] = Field(default_factory=list)


class PromptDetailView(CamelModel):
    id: str | None = None
    name: str | None = None
    version: int | None = None
    agent_type: str | None = None
    system_rules: str | None = None
    body: str | None = None
    input_schema_ref: str | None = None
    output_schema_ref: str | None = None
    blueprint_schema_range: str | None = None
    dhis2_version_range: str | None = None
    prohibitions: list[Any] = Field(default_factory=list)
    applicable_stages: list[str] = Field(default_factory=list)
    source_hash: str | None = None


class ActivationView(CamelModel):
    agent_type: str | None = None
    prompt_version_id: str | None = None
    name: str | None = None
    version: int | None = None
    model_list: list[str] = Field(default_factory=list)
    activated_by: str | None = None
    eval_run_id: str | None = None
    regression_passed_ref: str | None = None


class ActivationList(ListEnvelope):
    items: list[ActivationView] = Field(default_factory=list)


class EffectivePolicyView(CamelModel):
    allowed: dict[str, list[str]] = Field(default_factory=dict)
    sources: list[Any] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)


class PolicyView(CamelModel):
    effective: EffectivePolicyView = Field(default_factory=EffectivePolicyView)
    is_default: bool = True
    #: The ceiling a relaxation may widen *to* (AI-POL-002).  Answered next to the effective
    #: matrix because the two only mean something together: what is allowed below the ceiling
    #: is the choice the operator can actually make.
    ceiling: EffectivePolicyView = Field(default_factory=EffectivePolicyView)


class ProjectPolicyView(PolicyView):
    """S5IF-GOV12 — the organisation policy narrowed by every target environment."""

    project_id: str | None = None
    environment_policies: dict[str, str] = Field(default_factory=dict)
    binding_environment: str | None = None


class RelaxationView(CamelModel):
    """What a granted relaxation recorded.

    ``reason`` and ``change`` are carried back on purpose: a relaxation is permanent and
    org-wide, so the answer an operator just pressed has to be readable in the same call.  S3
    stores both; a response that named only the id left the operator to trust that the matrix
    they see after a reload is the one they asked for.
    """

    id: str | None = None
    scope: str | None = None
    operator: str | None = None
    reason: str | None = None
    change: RelaxationChangeView | None = None
    role_checked: bool | None = None
    #: D-16: which regime answered.  Until M4 the operator's name is the identity, and saying
    #: so here keeps the field from reading as a role check that passed.
    role_gate: str | None = None
    created_at: datetime | None = None


class RegressionRecordView(CamelModel):
    id: str | None = None
    passed: bool = True
    prompt_version_id: str | None = None

class RegressionList(ListEnvelope):
    """`GET /governance/regression-records` (S5IF-GOV11): the results a prompt version has."""

    items: list[RegressionRecordView] = Field(default_factory=list)




class EvalCaseView(CamelModel):
    """One evaluation case and its verdict (GOV15, S3 ``EvalResult.cases``).

    AI-GOV-004 puts these in the detail, so the shape is S3's declared item type and not
    a guess: ``diff`` is S3's structured difference, which a string could not carry.
    """

    case_id: str | None = None
    passed: bool | None = None
    score: float | None = None
    kind: str | None = None
    diff: dict[str, Any] | None = None


class EvaluationView(CamelModel):
    id: str | None = None
    agent_type: str | None = None
    prompt: str | None = None
    model: str | None = None
    eval_set_version: str | None = None
    score: float | None = None
    passed: bool | None = None
    #: S3's summary is the per-kind counts it computed, not a sentence.
    summary: dict[str, Any] | None = None
    case_count: int | None = None
    # AI-GOV-004: the detail answers with the per-case results and the injection outcome.
    cases: list[EvalCaseView] | None = None
    injection_cases: int | None = None
    injection_passed: int | None = None


class EvaluationList(ListEnvelope):
    items: list[EvaluationView] = Field(default_factory=list)


class UsageItemView(CamelModel):
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


class UsageView(CamelModel):
    group_by: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    items: list[UsageItemView] = Field(default_factory=list)


class BudgetView(CamelModel):
    project_id: str | None = None
    state: str = "UNSET"
    spent: float = 0.0
    limit: float | None = None
    currency: str = "USD"
    #: the threshold actually crossed, so S5 can say "80% of the limit" rather than a flag
    threshold_reached: float | None = None


class BudgetSetRequest(CamelModel):
    """The budget statement S5's administration page writes (UI-ADM-003, S5IF-GOV18, M4).

    S1 checks the shape it can see — a threshold outside ``(0, 1)`` and a currency that is not
    a three-letter code are answered here as ``VALIDATION_ERROR`` naming the field, because a
    client that mistyped one of these should learn which one rather than receive S3's own
    wording.  What the limit *means* is S3's decision (AI-COST-002), and its row is what the
    runtime obeys; S1 holds neither.
    """

    #: ``None`` states that the project has no cap; it is a statement, not an absence
    limit: float | None = None
    thresholds: list[float] = Field(default_factory=list)
    currency: str = "USD"

    @model_validator(mode="after")
    def _shape(self) -> BudgetSetRequest:
        problems: list[dict[str, str]] = []
        if self.limit is not None and self.limit < 0:
            problems.append({"path": "limit", "message": "a budget limit is 0 or more, or null for no limit"})
        for index, step in enumerate(self.thresholds):
            if not 0.0 < step < 1.0:
                problems.append(
                    {
                        "path": f"thresholds[{index}]",
                        "message": "a threshold is a fraction of the limit, strictly between 0 and 1",
                    }
                )
        code = (self.currency or "").strip().upper()
        if len(code) != 3 or not code.isalpha():
            problems.append({"path": "currency", "message": "currency is a three-letter ISO-4217 code"})
        if problems:
            raise ValidationFailedError(
                "the budget statement is not shaped correctly", details={"fields": problems}
            )
        return self


class AgentSpecView(CamelModel):
    """One Agent type and the stage it serves (GOV19, used by UI-WB-009)."""

    agent_type: str | None = None
    stage: str | None = None
    milestone: str | None = None
    enabled: bool | None = None
    prompt_name: str | None = None
    #: AI-GOV-008 asks *whether* the agent runs per page, which is a flag and not a size.
    per_page: bool | None = None
    patch_mode: str | None = None


class AgentListView(CamelModel):
    milestone: str | None = None
    items: list[AgentSpecView] = Field(default_factory=list)


# --- Requests ---------------------------------------------------------------

_VENDOR_MAX_LENGTH = 64
_SUGGESTED_MODEL_MAX_LENGTH = 128
_MAX_SUGGESTED_MODELS = 100


def _normalise_vendor(value: str) -> str:
    """Trim a preset key and refuse a blank one: absence means ``custom``, blank is a typo."""
    vendor = value.strip()
    if not vendor or len(vendor) > _VENDOR_MAX_LENGTH:
        raise ValidationFailedError(
            f"vendor must be 1..{_VENDOR_MAX_LENGTH} characters",
            details={
                "fields": [
                    {"path": "vendor", "message": f"must be 1..{_VENDOR_MAX_LENGTH} characters"}
                ]
            },
        )
    return vendor


def _normalise_suggested_models(values: list[str] | None) -> list[str]:
    """Trim, drop blanks and duplicates, and enforce the registry's own limits here.

    S3 applies the same normalisation, but a console that posts a 200-name list should be
    told which field is wrong rather than receiving the provider package's refusal.  An
    empty list is meaningful: it clears the candidates.
    """
    if not values:
        return []
    if len(values) > _MAX_SUGGESTED_MODELS:
        raise ValidationFailedError(
            f"at most {_MAX_SUGGESTED_MODELS} suggested model names may be supplied",
            details={
                "fields": [
                    {
                        "path": "suggestedModels",
                        "message": f"at most {_MAX_SUGGESTED_MODELS} names",
                    }
                ]
            },
        )
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        name = item.strip()
        if not name:
            continue
        if len(name) > _SUGGESTED_MODEL_MAX_LENGTH:
            raise ValidationFailedError(
                f"a suggested model name may be at most {_SUGGESTED_MODEL_MAX_LENGTH} characters",
                details={
                    "fields": [
                        {
                            "path": "suggestedModels",
                            "message": (
                                f"a name may be at most {_SUGGESTED_MODEL_MAX_LENGTH} characters"
                            ),
                        }
                    ]
                },
            )
        if name not in seen:
            out.append(name)
            seen.add(name)
    return out


class RegisterProviderRequest(CamelModel):
    # the maxima are `ai.provider`'s column widths; past them SQLAlchemy raises and the operator
    # gets a 500 with no field named, which is the worst possible answer to a typo
    name: str = Field(min_length=1, max_length=64)
    provider_class: str = Field(json_schema_extra={"enum": list(PROVIDER_CLASSES)})
    endpoint: str = Field(min_length=1, max_length=512)
    #: Empty means "this endpoint authenticates with nothing", which is the normal case for a local
    #: server (Ollama, vLLM, LM Studio on loopback). S3 already models it that way — an empty
    #: reference resolves to no secret and the adapter then sends no `Authorization` header
    #: (`openai_compat.py:_auth_headers`). Forcing a non-empty reference here meant the only way to
    #: register a local model server was to invent an environment variable for it to fail on.
    api_key_secret_ref: str = Field(default="", max_length=255)
    adapter: str = Field(default="openai_compat", min_length=1, max_length=32)
    enabled: bool = True
    #: the catalogue preset the form started from; ``custom`` is a hand-typed address
    vendor: str = Field(default="custom", max_length=_VENDOR_MAX_LENGTH)
    #: model names the console offers when a model is registered under this provider.
    #: These are suggestions only; S3's ``ai.model`` remains the model registry.
    suggested_models: list[str] = Field(default_factory=list, max_length=_MAX_SUGGESTED_MODELS)

    @model_validator(mode="after")
    def _validate(self) -> RegisterProviderRequest:
        if self.provider_class not in PROVIDER_CLASSES:
            raise ValidationFailedError(
                "providerClass is not one of the known classes",
                details={"providerClass": self.provider_class, "allowed": list(PROVIDER_CLASSES)},
            )
        if (self.api_key_secret_ref or "").strip():
            require_reference(self.api_key_secret_ref, "apiKeySecretRef")
        self.vendor = _normalise_vendor(self.vendor)
        self.suggested_models = _normalise_suggested_models(self.suggested_models)
        return self

    def to_payload(self, operator: str) -> dict[str, Any]:
        return {
            "name": self.name,
            "providerClass": self.provider_class,
            "endpoint": self.endpoint,
            "apiKeySecretRef": self.api_key_secret_ref,
            "adapter": self.adapter,
            "enabled": self.enabled,
            "vendor": self.vendor,
            "suggestedModels": self.suggested_models,
            "operator": operator,
        }


class PatchProviderRequest(CamelModel):
    """Edit one registered provider (S5IF-GOV03); an absent field means "leave it alone".

    This is deliberately *not* :class:`RegisterProviderRequest`.  Registration replaces the row,
    so an operator who only wanted to fix a mistyped address would have to re-supply the Key
    reference, and one who omitted it would silently clear it.

    ``extra="forbid"`` because a partial update cannot afford silence: a body that misspells
    ``apiKeySecretRef`` would otherwise be accepted, change nothing, and read as a successful save.
    A field sent as JSON ``null`` is dropped rather than stored, so ``to_patch`` and the emptiness
    check below measure the same set — and the contract says so, or a generated client will try.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    provider_class: str | None = Field(
        default=None, max_length=32, json_schema_extra={"enum": list(PROVIDER_CLASSES)}
    )
    # min_length=1 because `str_strip_whitespace` turns "  " into "", and a blank address would
    # otherwise reach S3 and come back as an error that names no field
    endpoint: str | None = Field(default=None, min_length=1, max_length=512)
    api_key_secret_ref: str | None = Field(default=None, min_length=1, max_length=255)
    adapter: str | None = Field(default=None, min_length=1, max_length=32)
    enabled: bool | None = None
    vendor: str | None = Field(default=None, max_length=_VENDOR_MAX_LENGTH)
    suggested_models: list[str] | None = Field(
        default=None, max_length=_MAX_SUGGESTED_MODELS
    )

    @model_validator(mode="after")
    def _validate(self) -> PatchProviderRequest:
        # `exclude_none` and not `model_fields_set`: a body that only names fields as null is
        # asking for something this operation cannot do, and the honest answer is 422, not a
        # silent no-op the client reads as success.
        if not self.to_patch():
            raise ValidationFailedError(
                "at least one field must be supplied to update a provider",
                details={
                    "fields": [
                        {
                            "path": "",
                            "message": "at least one field must be supplied to update a provider",
                        }
                    ]
                },
            )
        if self.provider_class is not None and self.provider_class not in PROVIDER_CLASSES:
            raise ValidationFailedError(
                "providerClass is not one of the known classes",
                details={"providerClass": self.provider_class, "allowed": list(PROVIDER_CLASSES)},
            )
        if self.api_key_secret_ref is not None:
            require_reference(self.api_key_secret_ref, "apiKeySecretRef")
        if self.vendor is not None:
            self.vendor = _normalise_vendor(self.vendor)
        if self.suggested_models is not None:
            self.suggested_models = _normalise_suggested_models(self.suggested_models)
        return self

    def to_patch(self) -> dict[str, Any]:
        """What to change, in the wire spelling S3's edit takes; nulls are not a change."""
        return self.model_dump(by_alias=True, exclude_none=True)


class RegisterModelRequest(CamelModel):
    provider: str
    model: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    # S3 spells these with a lower-case ``k``, which no alias generator produces from
    # a snake field, so the wire name is stated literally.
    unit_price_input_per_1k: float = Field(default=0.0, alias="unitPriceInputPer1k")
    unit_price_output_per_1k: float = Field(default=0.0, alias="unitPriceOutputPer1k")
    applicable_agents: dict[str, int | str] = Field(default_factory=dict)
    enabled: bool = True
    #: AI-LLM-003's per-model override.  S3 has stored both since the column pair landed; the REST
    #: body simply never exposed them, so a deployment whose models sit on different addresses
    #: could not say so from the console.  Absent means "use the provider's own", which is what a
    #: single-vendor setup wants.
    endpoint: str | None = None
    api_key_secret_ref: str | None = Field(default=None, alias="apiKeySecretRef")

    @model_validator(mode="after")
    def _override_key_is_a_reference(self) -> RegisterModelRequest:
        if self.api_key_secret_ref:
            require_reference(self.api_key_secret_ref, "apiKeySecretRef")
        return self

    def to_payload(self, operator: str) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "capabilities": dict(self.capabilities),
            "unitPriceInputPer1k": self.unit_price_input_per_1k,
            "unitPriceOutputPer1k": self.unit_price_output_per_1k,
            "applicableAgents": dict(self.applicable_agents),
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "apiKeySecretRef": self.api_key_secret_ref,
            "operator": operator,
        }


class PatchModelRequest(CamelModel):
    """Edit one registered model (S5IF-GOV06); an absent field means "leave it alone".

    This is deliberately *not* :class:`RegisterModelRequest`.  Registration replaces the row, and
    two of its columns — AI-LLM-003's per-model ``endpoint`` and Key reference — are nullable, so
    an operator who reopened the form to fix a price and did not re-type the address would have
    written NULL over it.  S1's read path then back-fills the provider's own address, so the
    console shows a value nobody set and the override can never come off.

    ``extra="forbid"`` for the same reason the provider patch has it: a partial update cannot
    afford silence, and a body that misspells ``apiKeySecretRef`` would otherwise be accepted,
    change nothing, and read as a successful save.  A field sent as JSON ``null`` is dropped rather
    than stored, so ``to_patch`` and the emptiness check below measure the same set.

    ``capabilities`` stays a free mapping rather than a typed view, in the body and in what S3 is
    handed: the patch has to be able to say "this one capability, and only this one", which a typed
    view cannot express because it fills what is missing with its own defaults.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    capabilities: dict[str, Any] | None = None
    unit_price_input_per_1k: float | None = Field(default=None, alias="unitPriceInputPer1k")
    unit_price_output_per_1k: float | None = Field(default=None, alias="unitPriceOutputPer1k")
    #: an empty map is a value here ("no agent may use this model"), not an absence
    applicable_agents: dict[str, int | str] | None = None
    enabled: bool | None = None
    # min_length=1 because `str_strip_whitespace` turns "  into "", and a blank address would
    # otherwise reach S3 and come back as an error that names no field
    endpoint: str | None = Field(default=None, min_length=1, max_length=512)
    api_key_secret_ref: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _validate(self) -> PatchModelRequest:
        # `exclude_none` and not `model_fields_set`: a body that only names fields as null is
        # asking for something this operation cannot do, and the honest answer is 422, not a
        # silent no-op the client reads as success.
        if not self.to_patch():
            raise ValidationFailedError(
                "at least one field must be supplied to update a model",
                details={
                    "fields": [
                        {
                            "path": "",
                            "message": "at least one field must be supplied to update a model",
                        }
                    ]
                },
            )
        if self.api_key_secret_ref is not None:
            require_reference(self.api_key_secret_ref, "apiKeySecretRef")
        return self

    def to_patch(self) -> dict[str, Any]:
        """What to change, in the wire spelling S3's edit takes; nulls are not a change."""
        return self.model_dump(by_alias=True, exclude_none=True)


class ActivatePromptRequest(CamelModel):
    agent_type: str
    prompt_name: str
    prompt_version: int
    model_list: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _model_list_required(self) -> ActivatePromptRequest:
        if not self.model_list:
            raise ValidationFailedError(
                "activating a prompt needs at least one model", details={"field": "modelList"}
            )
        return self

    def to_payload(self, operator: str) -> dict[str, Any]:
        return {
            "agentType": self.agent_type,
            "promptName": self.prompt_name,
            "promptVersion": self.prompt_version,
            "modelList": list(self.model_list),
            "operator": operator,
        }


class RecordRegressionRequest(CamelModel):
    prompt_name: str
    prompt_version: int
    recorded_by: str | None = None
    passed: bool = True
    model: str | None = None
    source: str | None = None
    note: str | None = None

    def to_payload(self, operator: str) -> dict[str, Any]:
        return {
            "promptName": self.prompt_name,
            "promptVersion": self.prompt_version,
            "recordedBy": self.recorded_by or operator,
            "passed": self.passed,
            "model": self.model,
            "source": self.source,
            "note": self.note,
        }


class RelaxationChangeView(CamelModel):
    """The one widening a relaxation recorded: a level, and what it may now also reach.

    S3 accepts several spellings of both halves (``classification`` for ``level``,
    ``allowedClasses``/``classes`` for ``addClasses``) because its own callers predate the
    contract. S1 normalises on the way in so the response has exactly one shape to render.
    """

    level: str
    add_classes: list[str] = Field(default_factory=list)


#: The aliases S3's ``policy.orgpolicy._level_of``/``_classes_of`` read, mapped to S1's one
#: spelling. Anything outside this set is a field S3 would ignore, so S1 refuses it by name.
_CHANGE_LEVEL_KEYS = ("level", "classification")
_CHANGE_CLASSES_KEYS = ("addClasses", "allowedClasses", "classes")


def _canonical_change(change: dict[str, Any]) -> dict[str, Any]:
    level = next((change[key] for key in _CHANGE_LEVEL_KEYS if change.get(key)), None)
    classes = next((change[key] for key in _CHANGE_CLASSES_KEYS if key in change), None)
    unknown = sorted(
        key
        for key in change
        if key not in {*_CHANGE_LEVEL_KEYS, *_CHANGE_CLASSES_KEYS}
    )
    if unknown:
        raise ValidationFailedError(
            "change names fields this operation does not read",
            details={
                "fields": [{"path": f"change.{key}", "message": "unknown field"} for key in unknown],
                "allowed": [*_CHANGE_LEVEL_KEYS, *_CHANGE_CLASSES_KEYS],
            },
        )
    if not level:
        raise ValidationFailedError(
            "change must name the data classification to widen",
            details={
                "fields": [{"path": "change.level", "message": "required"}],
                "allowed": list(_CHANGE_LEVEL_KEYS),
            },
        )
    wanted = [str(item) for item in (classes if isinstance(classes, list) else [classes] if classes else [])]
    if not wanted:
        raise ValidationFailedError(
            "change must name at least one provider class to allow",
            details={"fields": [{"path": "change.addClasses", "message": "required"}]},
        )
    return {"level": str(level), "addClasses": wanted}


class RelaxPolicyRequest(CamelModel):
    #: Free here on purpose: S3's own vocabulary is the alias list above, and S1 canonicalises
    #: it in the validator rather than inventing a second request shape for callers to guess.
    change: dict[str, Any]
    reason: str
    scope: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> RelaxPolicyRequest:
        # UI-ADM-003's "must give a reason" is enforced here, not left to S3.
        if not (self.reason or "").strip():
            raise ValidationFailedError(
                "a policy relaxation needs a reason",
                details={"fields": [{"path": "reason", "message": "a policy relaxation needs a reason"}]},
            )
        self.change = _canonical_change(self.change or {})
        return self

    def to_payload(self, operator: str) -> dict[str, Any]:
        return {
            "change": dict(self.change),
            "reason": self.reason,
            "operator": operator,
            "scope": self.scope,
        }


class RunEvaluationRequest(CamelModel):
    agent_type: str
    qualified_prompt: str
    model: str
    eval_set_version: str | None = None

    @model_validator(mode="after")
    def _qualified_prompt(self) -> RunEvaluationRequest:
        # S3's public entry takes ``name.vN``; checking here keeps a malformed
        # string from reaching the provider and coming back as an internal error.
        head, _, tail = self.qualified_prompt.rpartition(".v")
        if not head or not tail.isdigit():
            raise ValidationFailedError(
                "qualifiedPrompt must look like name.vN",
                details={"qualifiedPrompt": self.qualified_prompt},
            )
        return self


class UsageQuery(CamelModel):
    project_id: str | None = None
    group_by: list[str] = Field(default_factory=lambda: ["stage", "agent", "model"])

    @model_validator(mode="after")
    def _dimensions(self) -> UsageQuery:
        unknown = [d for d in self.group_by if d not in USAGE_DIMENSIONS]
        if unknown:
            raise ValidationFailedError(
                "groupBy contains an unknown dimension",
                details={"unknown": unknown, "allowed": list(USAGE_DIMENSIONS)},
            )
        return self


class ProviderCatalogueEntry(CamelModel):
    """One built-in vendor suggestion (AI-CAT-001).

    A *suggestion*: the address is what this build's OpenAI-compatible client can use, but nothing
    here has been probed against the deployment, and a console must not present it as verified.
    """

    key: str
    display_name: str
    endpoint: str
    #: The only wire protocol implemented in this build; reported per entry so the console does not
    #: have to infer it from the ``adapter`` column, which the runtime ignores.
    wire_protocol: str = "openai_compat"
    models: list[str] = Field(default_factory=list)
    key_ref_suggestion: str = ""
    docs_url: str = ""
    local: bool = False
    note: str = ""


class ProviderCatalogueView(CamelModel):
    items: list[ProviderCatalogueEntry] = Field(default_factory=list)
    total: int = 0


__all__ = [
    "AgentSpecView",
    "EvalCaseView",
    "ActivatePromptRequest",
    "ActivationList",
    "ActivationView",
    "AgentListView",
    "BudgetView",
    "EvaluationList",
    "EvaluationView",
    "ModelList",
    "ModelView",
    "PolicyView",
    "ProjectPolicyView",
    "ProviderCatalogueEntry",
    "ProviderCatalogueView",
    "PromptDetailView",
    "PromptList",
    "ProviderList",
    "ProviderView",
    "RecordRegressionRequest",
    "RegisterModelRequest",
    "RegisterProviderRequest",
    "PatchProviderRequest",
    "PatchModelRequest",
    "RegressionRecordView",
    "RelaxPolicyRequest",
    "RelaxationChangeView",
    "RelaxationView",
    "RunEvaluationRequest",
    "UsageQuery",
    "UsageView",
]
