"""``factory_intelligence.api`` — the single public entry point of S3 (00 doc 6.1, D-13).

Other subsystems import only from this module.  The names below are grouped as in SRS
chapter 7; the ten functions S1's ``IntelligenceAdapter`` resolves
(``factory_control/adapters/registry.py:198``) are the contract, and
``tests/unit/test_contract.py`` keeps this module's exports aligned with it.  Nine of the ten
are SRS 7's own; ``list_prompt_activations`` is S1's addition (S5IF-GOV10 reads a readiness
list that SRS 7 left inside ``governance``), so ``list_activations`` answers it here rather
than making S1 parse a ``prompt.list`` payload for it.

Three deviations from the SRS signature table, all forced and all of the kind S2 documents
the same way (``factory_blueprint/api.py:11``):

* ``explain_issues`` and ``run_evaluation`` take keyword-only ``idempotency_key``,
  ``progress`` and ``cancel``.  SRS 7 lists them without, but 00 doc 6.3 requires a long
  task to report progress and respond to cancellation and 6.4 requires an idempotency key
  on anything with a side effect — and both write artifacts.  They are keyword-only with
  defaults, so S1's existing call shape is unaffected.
* ``delete_project_data`` takes ``session``.  SRS 7 marks it 事务内, and S1 calls it with
  its own live session (``factory_control/services/transfers.py:331``), so S3 must enlist
  rather than commit.
* ``run_evaluation``'s second argument is SRS 7's own ``prompt_version`` in the qualified
  spelling AI-REG-001 uses (``mapping.decide.v3``); the registry stores a name and an
  integer, so the two are split at this boundary rather than letting a dotted string reach
  every lookup behind it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from factory_foundation.api import CancellationToken, ProgressReporter, external_io
from sqlalchemy.orm import Session

from factory_intelligence.bootstrap import MIGRATION_CONFIG, initialize
from factory_intelligence.cleanup import delete_project_data as _cleanup
from factory_intelligence.config import (
    IntelligenceSettings,
    configure,
    settings,
)
from factory_intelligence.config import (
    reset as reset_settings,
)
from factory_intelligence.cost import exchange as exchange_log
from factory_intelligence.deps import (
    get_blueprint,
    get_clock,
    get_llm_provider,
    reset_providers,
    set_blueprint,
    set_clock,
    set_http_transport,
    set_llm_provider,
    set_llm_provider_factory,
    set_sleeper,
)
from factory_intelligence.dto.agent import AgentOutput, ModelQuestion, PatchEnvelope
from factory_intelligence.dto.common import (
    Answer,
    ApiModel,
    Issue,
    Question,
    RejectedArtifact,
    Suggestion,
)
from factory_intelligence.dto.governance import (
    ActivationView,
    AgentCatalog,
    ColumnMappingProposal,
    EffectivePolicyView,
    EnableResult,
    EvaluationDetail,
    EvaluationSummary,
    LlmExchangeDetail,
    LlmExchangePage,
    ModelPatch,
    ModelSpec,
    ModelView,
    NrrFilters,
    NrrQueryResult,
    OrgPolicyView,
    Page,
    ParseRecord,
    PromptDetailView,
    PromptReloadResult,
    PromptView,
    ProviderHealthView,
    ProviderPatch,
    ProviderSpec,
    ProviderView,
    RegressionView,
    RelaxationView,
)
from factory_intelligence.dto.request import (
    AgentRunRequest,
    ContextItemRef,
    ParseOptions,
)
from factory_intelligence.dto.result import (
    AgentRunResult,
    BudgetView,
    ChatReply,
    DeniedItem,
    DesensitizationOutcome,
    EvalCaseOutcome,
    EvalResult,
    ParsedInput,
    PolicyOutcome,
    UsageRollup,
)
from factory_intelligence.enums import (
    AgentRunStatus,
    AgentType,
    NrrStatus,
    ParseStatus,
    PolicyResult,
    ProviderClass,
    ProviderKind,
    QuestionSeverity,
    RunState,
)
from factory_intelligence.errors import (
    AiError,
    BudgetExceeded,
    ConstraintViolation,
    ContextContainsPhi,
    ContextTooLarge,
    EvalSetMissing,
    FeatureNotEnabled,
    GovernanceActionUnknown,
    OutputInvalid,
    PatchRejected,
    PromptActivationBlocked,
    PromptNotActivated,
    PromptNotFound,
    ProviderUnavailable,
    RunNotResumable,
    ScannedDocumentUnsupported,
)
from factory_intelligence.eval.runner import run_evaluation as _run_evaluation
from factory_intelligence.governance import capabilities
from factory_intelligence.governance.dispatcher import dispatch as _governance
from factory_intelligence.llm.errors import LlmError, LlmSecretMissing
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.registry import registered_provider as _registered_provider
from factory_intelligence.llm.types import (
    Capabilities,
    ChatMessage,
    HealthStatus,
    ImageInput,
    ModelResponse,
    ProviderInfo,
    StructuredRequest,
    Usage,
)
from factory_intelligence.parsing import records as parse_records
from factory_intelligence.parsing.service import run_parse as _run_parse
from factory_intelligence.prompts.registry import split_qualified_prompt
from factory_intelligence.runtime.cancel import request_cancel as _request_cancel
from factory_intelligence.runtime.chat import chat as _chat
from factory_intelligence.runtime.chat import clear as _chat_clear
from factory_intelligence.runtime.chat import history as _chat_history
from factory_intelligence.runtime.explain import explain as _explain
from factory_intelligence.runtime.get import project as _project
from factory_intelligence.runtime.resume import resume as _resume
from factory_intelligence.runtime.start import start as _start

# --- 7.1 input parsing (AI-INP-001 … AI-INP-012) ---------------------------------


@external_io
def parse_input(
    project_id: str,
    input_artifact_id: str,
    *,
    options: ParseOptions | Mapping[str, Any] | None = None,
    operator: str,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> ParsedInput:
    """Turn one business input into an NRR artifact (AI-INP-001..012).

    ``options`` is accepted as S1's plain dict, its mirror model or S3's own —
    ``factory_control/services/inputs.py:157`` forwards a dict and the adapter passes it
    untouched.  A deterministic format is never routed to a model (AI-INP-003); a PHI_PII
    input never reaches one (AC-POL-02).
    """
    return _run_parse(
        project_id,
        input_artifact_id,
        options=options,
        operator=operator,
        idempotency_key=idempotency_key,
        progress=progress,
        cancel=cancel,
    )


# --- 7.2 agent runtime (AI-RT-001 .. AI-RT-032) ------------------------------
#
# SRS 7 lists no idempotency/progress/cancel arguments on these; 00 doc 6.3 and 6.4
# require them of a long task with side effects, so they are keyword-only with defaults
# and S1's existing call shape is unchanged (``delegating.py:101`` forwards verbatim).


@external_io
def start_agent_run(
    request: Any,
    *,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> AgentRunResult:
    """Run one agent turn; see :mod:`factory_intelligence.runtime.start` (AI-RT-002)."""
    return _start(request, progress=progress, cancel=cancel)


@external_io
def resume_agent_run(
    run_id: str,
    answers: Sequence[Answer | Mapping[str, Any]],
    idempotency_key: str,
    *,
    language: str | None = None,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> AgentRunResult:
    """Continue a waiting run in its persisted context (AI-RT-015, AI-RT-016)."""
    return _resume(run_id, answers, idempotency_key, language=language, progress=progress, cancel=cancel)


def cancel_agent_run(run_id: str, *, reason: str | None = None) -> None:
    """Ask a running run to stop at its next safe point (AI-AC-08)."""
    _request_cancel(run_id, reason=reason)


def get_agent_run(run_id: str) -> AgentRunResult | None:
    """The durable state of one run, answered from the ``ai`` rows only."""
    return _project(run_id)


@external_io
def explain_issues(
    project_id: str,
    report_artifact_id: str,
    *,
    environment_policies: Mapping[str, Any],
    operator: str,
    language: str | None = None,
    idempotency_key: str | None = None,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> str | None:
    """AI-AG-014: explain a validation or import report; returns the ``AGENT_OUTPUT`` id.

    ``None`` means the report held nothing to explain.  A policy refusal or an unavailable
    model raises instead of returning ``None``, because S1's port type would otherwise
    collapse "nothing wrong" and "we could not look" into the same answer.
    """
    return _explain(
        project_id,
        report_artifact_id,
        environment_policies=environment_policies,
        operator=operator,
        language=language,
        idempotency_key=idempotency_key,
        progress=progress,
        cancel=cancel,
    )


def delete_project_data(project_id: str, *, session: Session | None = None) -> None:
    """Remove this project's S3 rows, keeping the usage ledger (AI-DEL-001).

    Takes the caller's session and does no external I/O: S1 passes its live transaction and
    purges artifact content itself afterwards, and SRS 7 marks this 事务内.
    """
    _cleanup(project_id, session=session)


def evaluate_policy(
    project_id: str,
    item_refs: Sequence[ContextItemRef | str],
    provider_class: ProviderClass | str,
    environment_policies: Mapping[str, Any] | None = None,
    *,
    session: Session | None = None,
) -> PolicyOutcome:
    """Judge a prospective context without running anything (SRS 7, read-only).

    Not external I/O: it reads artifact *metadata* only, so S1 may call it with a
    transaction open.  The classification of a Blueprint reference is resolved per
    AI-POL-006.
    """
    from factory_intelligence.policy.refs import judge_refs

    return judge_refs(
        project_id,
        item_refs,
        provider_class,
        environment_policies,
        session=session,
    )


@external_io
def run_evaluation(
    agent_type: AgentType | str,
    prompt_version: str,
    model: str,
    eval_set_version: str | None = None,
    *,
    idempotency_key: str,
    operator: str = "system",
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> EvalResult:
    """AI-EVAL-001/002: score one prompt version against its agent's set on one model.

    ``prompt_version`` is the SRS's qualified spelling (``mapping.decide.v3``); the
    registry keys a version on a name and an integer, so the two are split here rather
    than letting an ad-hoc string reach every registry lookup.
    """
    name, version = split_qualified_prompt(prompt_version)
    return _run_evaluation(
        agent_type,
        name,
        version,
        model,
        idempotency_key=idempotency_key,
        eval_set_version=eval_set_version,
        operator=operator,
        progress=progress,
        cancel=cancel,
    )


def usage_rollup(
    project_id: str | None = None,
    *,
    group_by: Sequence[str] = ("stage", "agent", "model"),
    session: Session | None = None,
) -> list[UsageRollup]:
    """AI-COST-001: calls, tokens and estimated cost by any subset of dimensions.

    Read-only, so it is not external I/O: a caller may ask from inside its own
    transaction, and the usage ledger is never edited by this path (the migration guard
    makes ``ai.model_call`` append-only).

    ``project_id=None`` spans every project (SRS 7's own ``project_id=None``), and an
    illegal dimension is a ``VALIDATION_ERROR``, never a bare ``ValueError``
    (AI-GOV-005, AI-AC-11).
    """
    return capabilities.usage_rollup(project_id, group_by=group_by, session=session)


def list_activations(*, session: Session | None = None) -> list[ActivationView]:
    """Every agent that has an active prompt now — the deployment's readiness view.

    S1 serialises this straight into ``GET /api/v1/governance/prompts/activations``
    (``factory_control/services/governance.py:77``), so each row carries the prompt's
    ``name`` and ``version`` as well as its id (S5IF-GOV10): a caller that learned an
    activation by making one and a caller that lists them read the same fields.  The prompt
    *body* is absent from both — that is ``get_prompt``'s answer.
    """
    return capabilities.list_activations(session=session)


def reload_prompt_sources(
    root: str | None = None, *, session: Session | None = None
) -> PromptReloadResult:
    """Re-read the prompt catalogue from disk (AI-PROMPT-011).

    AI-GOV-009: this was reachable only as the facade action ``prompt.source.reload``, which is
    exactly the shape the rule forbids -- an action with no typed exit at all.
    """
    return capabilities.reload_prompt_sources(root, session=session)


def list_providers(*, session: Session | None = None) -> list[ProviderView]:
    """AI-GOV-002: address and Key *reference* only, never a value, mask or length (AC-13)."""
    return capabilities.list_providers(session=session)


def register_provider(
    spec: ProviderSpec | Mapping[str, Any],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ProviderView:
    """Register or replace one provider, audited (AI-REG-004, AI-GOV-002)."""
    return capabilities.register_provider(spec, operator, session=session)


def update_provider(
    name: str,
    patch: ProviderPatch | Mapping[str, Any],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ProviderView:
    """Correct one provider in place; fields the caller omitted stay as they were."""
    return capabilities.update_provider(name, patch, operator, session=session)


def set_provider_enabled(
    name: str,
    enabled: bool,
    operator: str = "system",
    *,
    session: Session | None = None,
) -> EnableResult:
    """Enable or disable a provider; the change is audited (AI-GOV-002)."""
    return capabilities.set_provider_enabled(name, enabled, operator, session=session)


@external_io
def check_provider_health(name: str, *, session: Session | None = None) -> ProviderHealthView:
    """Ask one registered provider's address whether it answers (S5IF-GOV20).

    The probe is the adapter's ``GET /models`` on a five-second budget: no generation, so nothing
    is billable and no project content leaves (AI-LLM-005). Every failure mode is an *answer* here,
    not an exception, because the operator's question is "why could the stage not get a reply" — a
    refused HTTP answer still means the address is alive, while a transport timeout, a Key
    reference that resolves to nothing and an address nobody filled in are three different problems
    that need three different fixes. ``detail`` carries the adapter's own sentence, and a vendor
    that quotes the request back is already scrubbed of the credential by the adapter (AI-LLM-005).
    """
    provider = _registered_provider(name, session=session)
    info = ProviderInfo(
        provider=provider.name,
        provider_class=provider.provider_class,
        # a provider-level probe names no model; the field is only what the adapter's own
        # messages quote when a call fails
        model="",
        capabilities=Capabilities(),
        endpoint=provider.endpoint,
        api_key_secret_ref=provider.api_key_secret_ref,
    )
    try:
        status = get_llm_provider(info).health()
    except (LlmError, LlmSecretMissing) as error:
        return ProviderHealthView(ref=provider.name, healthy=False, detail=str(error.message))
    return ProviderHealthView(ref=provider.name, healthy=status.healthy, detail=status.detail)


def list_models(
    agent_type: str | None = None,
    *,
    include_disabled: bool = True,
    session: Session | None = None,
) -> list[ModelView]:
    """Model registry view: capabilities, prices, applicable agents and last evaluation."""
    return capabilities.list_models(agent_type, include_disabled=include_disabled, session=session)


def register_model(
    spec: ModelSpec | Mapping[str, Any],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ModelView:
    """Register one model under a provider (AI-REG-004), at its own address if it has one."""
    return capabilities.register_model(spec, operator, session=session)


def update_model(
    ref: str,
    patch: ModelPatch | Mapping[str, Any],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ModelView:
    """Correct one model in place; only the fields the caller names change (S5IF-GOV06)."""
    return capabilities.update_model(ref, patch, operator, session=session)


def set_model_enabled(
    ref: str,
    enabled: bool,
    operator: str = "system",
    *,
    session: Session | None = None,
) -> EnableResult:
    """Enable or disable ``provider/model``; the change is audited."""
    return capabilities.set_model_enabled(ref, enabled, operator, session=session)


def list_prompts(
    agent_type: str | None = None,
    *,
    session: Session | None = None,
) -> list[PromptView]:
    """Every stored prompt version, each marked with whether it is in use (AI-REG-002)."""
    return capabilities.list_prompts(agent_type, session=session)


def get_prompt(
    name: str,
    version: int,
    *,
    session: Session | None = None,
) -> PromptDetailView:
    """One prompt version with its body, schema references and prohibitions (AI-REG-001)."""
    return capabilities.get_prompt(name, version, session=session)


def activate_prompt(
    agent_type: str,
    prompt_name: str,
    prompt_version: int,
    model_list: Sequence[str],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ActivationView:
    """Switch one agent's effective prompt, subject to both AI-REG-003 gates.

    Refusal is ``AI_PROMPT_ACTIVATION_BLOCKED`` with machine-readable ``details`` naming
    which gate is missing and what score was required (AI-GOV-003, AI-AC-12).
    """
    return capabilities.activate_prompt(
        agent_type, prompt_name, prompt_version, model_list, operator, session=session
    )


def record_regression(
    prompt_name: str,
    prompt_version: int,
    passed: bool = True,
    model: str | None = None,
    source: str = "golden-project",
    note: str | None = None,
    operator: str = "test-group",
    *,
    session: Session | None = None,
) -> RegressionView:
    """Register the test group's Golden Project result (AI-REG-003, AI-GOV-003)."""
    return capabilities.record_regression(
        prompt_name,
        prompt_version,
        passed,
        model,
        source,
        note,
        operator,
        session=session,
    )



def list_regressions(
    prompt_name: str | None = None,
    prompt_version: int | None = None,
    *,
    session: Session | None = None,
) -> list[RegressionView]:
    """Registered regression results, optionally for one prompt version (AI-REG-003, S5IF-GOV11).

    Read side of ``record_regression``: without it the "no regression result, no activation" rule
    could not be shown to the operator who is about to activate.
    """
    return capabilities.list_regressions(
        prompt_name=prompt_name, prompt_version=prompt_version, session=session
    )

def get_org_policy(*, session: Session | None = None) -> OrgPolicyView:
    """The organisation's data-egress policy and whether it is still the default (AI-POL-002)."""
    return capabilities.get_org_policy(session=session)


def relax_org_policy(
    change: Mapping[str, Any],
    reason: str,
    operator: str,
    scope: str = "organization",
    *,
    session: Session | None = None,
) -> RelaxationView:
    """Widen the policy with a recorded reason; PHI_PII is never relaxable (AI-GOV-006)."""
    return capabilities.relax_org_policy(change, reason, operator, scope, session=session)


def get_effective_policy(
    environment_policies: Mapping[str, Any] | None = None,
    *,
    session: Session | None = None,
) -> EffectivePolicyView:
    """AI-GOV-006: D-07's strictest of the organisation policy and every target environment."""
    return capabilities.get_effective_policy(environment_policies, session=session)


def list_evaluations(
    agent_type: str | None = None,
    *,
    limit: int = 50,
    session: Session | None = None,
) -> list[EvaluationSummary]:
    """Evaluation history, newest first (AI-REG-005)."""
    return capabilities.list_evaluations(agent_type, limit=limit, session=session)


def get_evaluation(eval_run_id: str, *, session: Session | None = None) -> EvaluationDetail:
    """AI-GOV-004: the run with its per-case results and the injection samples' standing."""
    return capabilities.get_evaluation(eval_run_id, session=session)


def get_budget(project_id: str, *, session: Session | None = None) -> BudgetView:
    """AI-GOV-007: readable from M1 as ``UNSET`` / ``OK`` / ``WARNING`` / ``EXCEEDED``."""
    return capabilities.get_budget(project_id, session=session)


def list_agents(*, session: Session | None = None) -> AgentCatalog:
    """AI-GOV-008: each agent's stage, milestone, enablement, effective prompt and patching."""
    return capabilities.list_agents(session=session)


# --- 7.1b parse records and NRR queries (AI-INP-013/014/015) -------------------


def list_input_parses(
    project_id: str,
    input_artifact_ids: Sequence[str] | None = None,
    *,
    session: Session | None = None,
) -> list[ParseRecord]:
    """AI-INP-013: the latest parse of each input, with its status and error.

    Read-only and answered from ``ai.input_parse``, so S1 may call it inside its own
    transaction; the per-input status a workbench shows is S3's record, not S1's step row.
    """
    return parse_records.list_input_parses(project_id, input_artifact_ids, session=session)


def get_column_mapping_proposal(
    project_id: str,
    input_artifact_id: str,
    *,
    session: Session | None = None,
) -> ColumnMappingProposal:
    """AI-INP-014: each worksheet's headers, category, proposed mapping, basis, affected keys.

    The confirmed mapping is handed back as ``options.columnMapping`` and the input is
    re-parsed with it (AI-INP-006).
    """
    return parse_records.get_column_mapping_proposal(
        project_id, input_artifact_id, session=session
    )


def query_nrr(
    project_id: str,
    filters: NrrFilters | Mapping[str, Any] | None = None,
    page: Page | Mapping[str, Any] | None = None,
    *,
    session: Session | None = None,
) -> NrrQueryResult:
    """AI-INP-015: NRR entries by category, input artifact and confidence interval.

    Only the latest parse of an input contributes entries, and the answer is ordered
    deterministically, so a paged view cannot show one requirement twice.
    """
    return parse_records.query_nrr(project_id, filters, page, session=session)


def list_llm_exchanges(
    project_id: str,
    input_artifact_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    session: Session | None = None,
) -> LlmExchangePage:
    """The parse-time transcript of one input, oldest attempt first.

    Summaries only — the request and raw answer are fetched per exchange by
    :func:`get_llm_exchange`, so a 40-chunk parse does not arrive as one payload.
    """
    return exchange_log.list_exchanges(
        project_id, input_artifact_id, limit=limit, offset=offset, session=session
    )


def get_llm_exchange(
    project_id: str,
    input_artifact_id: str,
    exchange_id: str,
    *,
    session: Session | None = None,
) -> LlmExchangeDetail | None:
    """One exchange with its full messages, raw answer and usage; ``None`` when not found."""
    return exchange_log.get_exchange(
        project_id, input_artifact_id, exchange_id, session=session
    )


def purge_llm_exchanges(*, session: Session | None = None) -> int:
    """Delete transcripts past their retention (the daily scheduled job calls this)."""
    return exchange_log.purge_expired(session=session)


@external_io
def stage_chat(
    project_id: str,
    stage: str,
    draft_id: str | None,
    message: str,
    environment_policies: Mapping[str, Any],
    operator: str,
    *,
    language: str | None = None,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> ChatReply:
    """M3 (AI-CHAT-001/002): one turn of dialogue with the stage's Review Assistant.

    A turn is a model call, so this is external I/O and answers synchronously — S1 shows the
    reply in the panel the person is typing into rather than making them watch a run.
    """
    return _chat(
        project_id,
        stage,
        draft_id,
        message,
        environment_policies,
        operator,
        language=language,
        idempotency_key=idempotency_key,
        progress=progress,
        cancel=cancel,
    )


def clear_chat(project_id: str, stage: str, *, session: Session | None = None) -> None:
    """M3 (AI-CHAT-003): the stage's session starts again; nothing is deleted."""
    _chat_clear(project_id, stage, session=session)


def get_chat(project_id: str, stage: str) -> Any:
    """AI-CHAT-003's read side, for S1's context header and history panel (FR-UI-002).

    The transcript is S3's artifact pointer, and S1 reads no other subsystem's schema (D-08),
    so the history it displays has to come from here rather than from ``ai.chat_session``.
    Reading it touches object storage, so S1 calls it with no transaction open (00 doc 6.2).
    """
    return _chat_history(project_id, stage)


@external_io
def desensitize(
    project_id: str,
    artifact_id: str,
    rules: Mapping[str, Any],
    *,
    operator: str,
    idempotency_key: str,
) -> DesensitizationOutcome:
    """M4 (AI-POL-009): substitution of CONFIDENTIAL content into a lower-classified copy."""
    raise FeatureNotEnabled(
        "desensitisation is delivered in M4 (SRS 3.5, 12)",
        details={"capability": "desensitize", "milestone": "M4"},
    )


def set_budget(
    project_id: str,
    limit: float | None,
    thresholds: Sequence[float],
    *,
    currency: str = "USD",
    operator: str,
    session: Session | None = None,
) -> BudgetView:
    """M4 (AI-COST-002, AI-GOV-007): write the project's limit and warning steps.

    The enforcement this feeds was delivered with the MVP — any row in ``ai.budget`` is
    already obeyed at run start — so this function is the switch, not the rule.  Who may
    call it is S1's decision (``governance.budget.set``); S3 holds no users (D-14).
    """
    return capabilities.set_budget(
        project_id,
        limit,
        thresholds,
        currency=currency,
        operator=operator,
        session=session,
    )


#: AI-GOV-009: kept as a compatibility entry point until the end of M2, delegating per action
#: to the typed functions below. It grows no new action and no new logic, and the next major
#: version removes it; ``governance/dispatcher.py`` carries the action -> function table.
DEPRECATED_SINCE = "1.1"
REMOVE_AT_MILESTONE = "M2"


def governance(action: str, payload: Mapping[str, Any]) -> Any:
    """**Deprecated (AI-GOV-009).** Use the typed function for the action you need.

    Kept only because S1's adapter was written against it and a breaking change needs one
    milestone of overlap (00 doc 6.1). It is a thin delegate: each action looks up the typed
    public function named in ``ACTION_CAPABILITIES`` and projects the declared model, so there
    is no behaviour here that the functions do not also have -- the compatibility tests
    ``test_the_facade_delegates_every_action_to_a_typed_public_function`` and
    ``test_only_a_declared_refusal_may_answer_with_no_model_at_all`` are what keep that true.

    Never external I/O: S1 may call it with its own transaction open, so an action that
    would need to read an artifact or call a model is refused with a pointer to the public
    function that may.
    """
    return _governance(action, dict(payload or {}))


# PEP 702 readers -- ``help()``, and any tool that walks ``__deprecated__`` -- find the marker
# here rather than through ``warnings.deprecated``: that decorator is 3.13+ (S3 targets 3.12),
# and S3 imports no third-party module outside the dependency list ``test_architecture`` keeps
# honest.  It also stays silent at call time, which is right while AI-GOV-009 still permits S1
# to use this entry point: a warning would punish the transition it authorises.
governance.__deprecated__ = (  # type: ignore[attr-defined]
    f"AI-GOV-009: deprecated since {DEPRECATED_SINCE}, removed at the end of "
    f"{REMOVE_AT_MILESTONE}; call the typed governance function for the capability you need"
)


__all__ = [
    # 7.0 startup and 7.1/7.2 the names S1 resolves (``IntelligenceAdapter.METHOD_MAP``)
    "parse_input",
    "start_agent_run",
    "resume_agent_run",
    "cancel_agent_run",
    "get_agent_run",
    "governance",
    "explain_issues",
    "delete_project_data",
    "run_evaluation",
    "list_activations",
    "reload_prompt_sources",
    "evaluate_policy",
    "usage_rollup",
    # governance, one typed function per capability (AI-GOV-001, SRS 7)
    "list_providers",
    "register_provider",
    "update_provider",
    "set_provider_enabled",
    "check_provider_health",
    "list_models",
    "register_model",
    "update_model",
    "set_model_enabled",
    "list_prompts",
    "get_prompt",
    "activate_prompt",
    "record_regression",
    "list_regressions",
    "get_org_policy",
    "relax_org_policy",
    "get_effective_policy",
    "list_evaluations",
    "get_evaluation",
    "get_budget",
    "list_agents",
    # parse records and NRR queries (AI-INP-013/014/015)
    "list_input_parses",
    "get_column_mapping_proposal",
    "query_nrr",
    "list_llm_exchanges",
    "get_llm_exchange",
    "purge_llm_exchanges",
    "stage_chat",
    "get_chat",
    "clear_chat",
    "desensitize",
    "set_budget",
    # 7.0 startup and configuration
    "initialize",
    "MIGRATION_CONFIG",
    "IntelligenceSettings",
    "settings",
    "configure",
    "reset_settings",
    # provider injection (AI-LLM-005, AI-NFR-05, AI-API-001)
    "set_llm_provider",
    "set_llm_provider_factory",
    "get_llm_provider",
    "set_blueprint",
    "get_blueprint",
    "set_clock",
    "get_clock",
    "set_sleeper",
    "set_http_transport",
    "reset_providers",
    # contracts
    "LLMProvider",
    "ProgressReporter",
    "CancellationToken",
    # enums
    "AgentType",
    "AgentRunStatus",
    "RunState",
    "ParseStatus",
    "ProviderClass",
    "ProviderKind",
    "QuestionSeverity",
    "PolicyResult",
    "NrrStatus",
    # DTOs
    "ApiModel",
    # the governance surface's typed parameters and results (AI-GOV-001)
    "ProviderSpec",
    "ProviderPatch",
    "ProviderView",
    "ModelSpec",
    "ModelView",
    "EnableResult",
    "PromptView",
    "PromptDetailView",
    "PromptReloadResult",
    "ActivationView",
    "RegressionView",
    "OrgPolicyView",
    "RelaxationView",
    "EffectivePolicyView",
    "EvaluationSummary",
    "EvaluationDetail",
    "AgentCatalog",
    "ParseRecord",
    "ColumnMappingProposal",
    "NrrFilters",
    "Page",
    "NrrQueryResult",
    "AgentRunRequest",
    "ParseOptions",
    "ContextItemRef",
    "AgentRunResult",
    "ParsedInput",
    "PolicyOutcome",
    "DeniedItem",
    "Question",
    "Suggestion",
    "Answer",
    "Issue",
    "RejectedArtifact",
    "AgentOutput",
    "PatchEnvelope",
    "ModelQuestion",
    "UsageRollup",
    "BudgetView",
    "EvalResult",
    "EvalCaseOutcome",
    "ChatReply",
    "DesensitizationOutcome",
    "ChatMessage",
    "ImageInput",
    "StructuredRequest",
    "ModelResponse",
    "ProviderInfo",
    "Capabilities",
    "HealthStatus",
    "Usage",
    # errors
    "AiError",
    "FeatureNotEnabled",
    "RunNotResumable",
    "ScannedDocumentUnsupported",
    "ContextTooLarge",
    "ContextContainsPhi",
    "OutputInvalid",
    "ConstraintViolation",
    "PatchRejected",
    "PromptActivationBlocked",
    "PromptNotFound",
    "PromptNotActivated",
    "ProviderUnavailable",
    "GovernanceActionUnknown",
    "EvalSetMissing",
    "BudgetExceeded",
]
