"""Each governance capability as one typed function (AI-GOV-001).

AI-GOV-001 is not a style rule: S1 has to declare a named REST request and response
component for every one of these (00 doc 6.11), and a payload it must first decode from a
free dictionary cannot become one.  So every capability of SRS chapter 7's Governance rows
is a function here with Pydantic parameters and results, ``api.py`` exports it, and the
``governance(action, payload)`` façade — which S1 still forwards (CP-API-007) — builds the
same typed call and serialises the same model.  One implementation, two spellings.

Two rules the handlers already obeyed and this module keeps:

* **No external I/O.**  ``governance`` is not in S1's external-I/O list, so S1 may call it
  with its own transaction open.  A capability that would have to read an artifact or call
  a model is refused here with a pointer to the public function that may (00 doc 6.2).
* **Session discipline.**  Every function takes an optional ``session`` and, when none is
  given and none is open, runs in its own short read-only transaction.  The services under
  neath already do this; the wrappers here must not be the thing that breaks it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

from factory_foundation.api import (
    NotFoundError,
    ValidationError,
    current_session,
    has_active_uow,
    unit_of_work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_intelligence.agents.spec import AGENT_SPECS
from factory_intelligence.cost import budget as budget_service
from factory_intelligence.cost import usage
from factory_intelligence.dto.governance import (
    ActivationView,
    AgentCatalog,
    AgentCatalogEntry,
    CapabilitiesView,
    EffectivePolicyView,
    EnableResult,
    EvalCaseView,
    EvaluationDetail,
    EvaluationSummary,
    ModelPatch,
    ModelSpec,
    ModelView,
    OrgPolicyView,
    PolicyMatrixView,
    PromptDetailView,
    PromptReloadResult,
    PromptView,
    ProviderPatch,
    ProviderSpec,
    ProviderView,
    RegressionView,
    RelaxationView,
)
from factory_intelligence.dto.result import BudgetView, UsageRollup
from factory_intelligence.enums import (
    CURRENT_MILESTONE,
    Milestone,
    milestone_at_least,
)
from factory_intelligence.feature import agent_enabled
from factory_intelligence.llm import registry as model_registry
from factory_intelligence.llm.types import Capabilities
from factory_intelligence.models.eval import EvalCaseResultRow, EvalRunRow
from factory_intelligence.models.policy import OrgPolicyRow
from factory_intelligence.models.registry import PromptVersionRow
from factory_intelligence.policy import orgpolicy
from factory_intelligence.policy.model import EffectivePolicy
from factory_intelligence.prompts import activation as prompt_activation
from factory_intelligence.prompts import registry as prompt_registry

__all__ = [
    "list_providers",
    "register_provider",
    "update_provider",
    "set_provider_enabled",
    "list_models",
    "register_model",
    "set_model_enabled",
    "list_prompts",
    "get_prompt",
    "list_activations",
    "activate_prompt",
    "record_regression",
    "list_regressions",
    "reload_prompt_sources",
    "get_org_policy",
    "relax_org_policy",
    "get_effective_policy",
    "list_evaluations",
    "get_evaluation",
    "get_budget",
    "usage_rollup",
    "list_agents",
]


T = TypeVar("T")


def _read(session: Session | None, action: Callable[[Session], T]) -> T:
    """Read in the caller's transaction if one is open, else in a short one of our own."""
    if session is not None or has_active_uow():
        return action(session if session is not None else current_session())
    with unit_of_work() as sess:
        return action(sess)


# --- providers (AI-REG-004, AI-GOV-002) ----------------------------------------


def _provider_view(row: model_registry.RegisteredProvider) -> ProviderView:
    return ProviderView(
        id=row.id,
        name=row.name,
        provider_class=row.provider_class,
        endpoint=row.endpoint,
        enabled=row.enabled,
        adapter=row.adapter,
        # the reference is governance-visible; the value never is (AI-LLM-005, AC-13)
        api_key_secret_ref=row.api_key_secret_ref,
        vendor=row.vendor,
        suggested_models=list(row.suggested_models),
    )


def list_providers(*, session: Session | None = None) -> list[ProviderView]:
    """Every registered provider, with address and Key reference only (AI-GOV-002)."""
    return [
        _provider_view(row)
        for row in model_registry.list_providers(session=session)
    ]


def register_provider(
    spec: ProviderSpec | Mapping[str, Any],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ProviderView:
    """Register or replace one provider (AI-REG-004); the change is audited."""
    model = spec if isinstance(spec, ProviderSpec) else ProviderSpec.model_validate(dict(spec))
    row = model_registry.register_provider(
        name=model.name,
        provider_class=model.provider_class,
        endpoint=model.endpoint,
        api_key_secret_ref=model.api_key_secret_ref,
        adapter=model.adapter,
        enabled=model.enabled,
        vendor=model.vendor,
        suggested_models=list(model.suggested_models),
        operator=operator,
        session=session,
    )
    return _provider_view(row)


def update_provider(
    name: str,
    patch: ProviderPatch | Mapping[str, Any],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ProviderView:
    """Correct one provider in place; only the supplied fields change (S5IF-GOV03).

    Deliberately separate from :func:`register_provider`, which replaces the whole row: an
    operator fixing a mistyped address must not have to re-supply the Key reference, and a
    re-registration that omitted it would clear it.
    """
    model = patch if isinstance(patch, ProviderPatch) else ProviderPatch.model_validate(dict(patch))
    row = model_registry.update_provider(
        name=name,
        provider_class=model.provider_class,
        endpoint=model.endpoint,
        api_key_secret_ref=model.api_key_secret_ref,
        adapter=model.adapter,
        enabled=model.enabled,
        vendor=model.vendor,
        suggested_models=model.suggested_models,
        operator=operator,
        session=session,
    )
    return _provider_view(row)


def set_provider_enabled(
    name: str,
    enabled: bool,
    operator: str = "system",
    *,
    session: Session | None = None,
) -> EnableResult:
    """Enable or disable a provider (AI-REG-004); audited as a registration change."""
    model_registry.set_enabled(
        kind="provider",
        ref=name,
        enabled=bool(enabled),
        operator=operator,
        session=session,
    )
    return EnableResult(ref=name, enabled=bool(enabled))


# --- models (AI-REG-004, AI-LLM-003) -------------------------------------------


def _model_view(row: model_registry.RegisteredModel) -> ModelView:
    return ModelView(
        id=row.model_id,
        provider=row.provider,
        model=row.model,
        ref=row.ref,
        provider_class=row.provider_class,
        capabilities=CapabilitiesView(
            structured_output=row.capabilities.structured_output,
            vision=row.capabilities.vision,
            max_context_tokens=row.capabilities.max_context_tokens,
        ),
        enabled=row.enabled,
        unit_price_input_per_1k=row.unit_price_input_per_1k,
        unit_price_output_per_1k=row.unit_price_output_per_1k,
        applicable_agents=dict(row.applicable_agents),
        last_eval_score=row.last_eval_score,
        last_eval_run_id=row.last_eval_run_id,
        endpoint=row.endpoint,
        api_key_secret_ref=row.api_key_secret_ref,
        adapter=row.adapter,
    )


def list_models(
    agent_type: str | None = None,
    *,
    include_disabled: bool = True,
    session: Session | None = None,
) -> list[ModelView]:
    """Registered models, optionally narrowed to one agent's preference list."""
    return [
        _model_view(row)
        for row in model_registry.list_models(
            agent_type=agent_type,
            include_disabled=include_disabled,
            session=session,
        )
    ]


def register_model(
    spec: ModelSpec | Mapping[str, Any],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ModelView:
    """Register one model under an existing provider, with its prices and capabilities."""
    model = spec if isinstance(spec, ModelSpec) else ModelSpec.model_validate(dict(spec))
    row = model_registry.register_model(
        provider=model.provider,
        model=model.model,
        capabilities=Capabilities(
            structured_output=model.capabilities.structured_output,
            vision=model.capabilities.vision,
            max_context_tokens=model.capabilities.max_context_tokens,
        ),
        unit_price_input_per_1k=model.unit_price_input_per_1k,
        unit_price_output_per_1k=model.unit_price_output_per_1k,
        applicable_agents=dict(model.applicable_agents),
        enabled=model.enabled,
        endpoint=model.endpoint,
        api_key_secret_ref=model.api_key_secret_ref,
        operator=operator,
        session=session,
    )
    return _model_view(row)


def update_model(
    ref: str,
    patch: ModelPatch | Mapping[str, Any],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ModelView:
    """Correct one model in place; only the supplied fields change (S5IF-GOV06).

    Deliberately separate from :func:`register_model`, which replaces the whole row: an operator
    fixing a price must not have to re-supply AI-LLM-003's per-model address and Key reference,
    and a re-registration that omitted them would clear them.
    """
    model = patch if isinstance(patch, ModelPatch) else ModelPatch.model_validate(dict(patch))
    row = model_registry.update_model(
        ref=ref,
        capabilities=model.capabilities,
        unit_price_input_per_1k=model.unit_price_input_per_1k,
        unit_price_output_per_1k=model.unit_price_output_per_1k,
        applicable_agents=model.applicable_agents,
        enabled=model.enabled,
        endpoint=model.endpoint,
        api_key_secret_ref=model.api_key_secret_ref,
        operator=operator,
        session=session,
    )
    return _model_view(row)


def set_model_enabled(
    ref: str,
    enabled: bool,
    operator: str = "system",
    *,
    session: Session | None = None,
) -> EnableResult:
    """Enable or disable ``provider/model`` (AI-REG-004); audited."""
    model_registry.set_enabled(
        kind="model",
        ref=ref,
        enabled=bool(enabled),
        operator=operator,
        session=session,
    )
    return EnableResult(ref=ref, enabled=bool(enabled))


# --- prompts and activations (AI-REG-001/002/003, AI-GOV-003) ------------------


def _active_version_ids(session: Session | None) -> set[str]:
    return {
        item.prompt_version_id for item in prompt_activation.list_activations(session=session)
    }


def _prompt_view(row: PromptVersionRow, active_ids: set[str]) -> PromptView:
    return PromptView(
        id=row.id,
        name=row.name,
        version=row.version,
        qualified_name=f"{row.name}.v{row.version}",
        agent_type=row.agent_type,
        blueprint_schema_range=row.blueprint_schema_range,
        dhis2_version_range=row.dhis2_version_range,
        prohibitions=list(row.prohibitions or []),
        source_hash=row.source_hash,
        active=row.id in active_ids,
    )


def list_prompts(
    agent_type: str | None = None,
    *,
    session: Session | None = None,
) -> list[PromptView]:
    """Every stored prompt version, each marked with whether it is in use (AI-REG-002)."""
    rows = prompt_registry.list_prompts(agent_type, session=session)
    active_ids = _active_version_ids(session)
    return [_prompt_view(row, active_ids) for row in rows]


def get_prompt(
    name: str,
    version: int,
    *,
    prompt_version_id: str | None = None,
    session: Session | None = None,
) -> PromptDetailView:
    """One prompt version with its body and schema references (SRS 7, S5IF-GOV08)."""
    row = prompt_registry.get_prompt(str(name), int(version), session=session)
    base = _prompt_view(row, _active_version_ids(session))
    detail = PromptDetailView(
        **{
            key: value
            for key, value in base.model_dump().items()
            if key in PromptDetailView.model_fields
        },
        system_rules=row.system_rules,
        body=row.body,
        input_schema_ref=row.input_schema_ref,
        output_schema_ref=row.output_schema_ref,
        applicable_stages=list(row.applicable_stages or []),
    )
    if prompt_version_id is not None and prompt_version_id != detail.id:
        raise ValidationError(
            f"prompt version {prompt_version_id} is not {name}.v{version}",
            code="VALIDATION_ERROR",
            details={"promptVersionId": prompt_version_id, "resolvedId": detail.id},
        )
    return detail


def list_activations(*, session: Session | None = None) -> list[ActivationView]:
    """The agent -> effective-prompt table S5 reads (S5IF-GOV10)."""
    return [
        ActivationView(
            agent_type=item.agent_type,
            prompt_version_id=item.prompt_version_id,
            name=item.name,
            version=item.version,
            model_list=list(item.model_list),
            eval_run_id=item.eval_run_id,
            regression_passed_ref=item.regression_passed_ref,
            activated_by=item.activated_by,
        )
        for item in prompt_activation.list_activations(session=session)
    ]


def activate_prompt(
    agent_type: str,
    prompt_name: str,
    prompt_version: int,
    model_list: Sequence[str],
    operator: str = "system",
    *,
    session: Session | None = None,
) -> ActivationView:
    """Switch one agent's effective prompt, or refuse with AI_PROMPT_ACTIVATION_BLOCKED.

    Both gates of AI-REG-003 are checked inside
    :func:`factory_intelligence.prompts.activation.activate`; this function only shapes the
    answer, so a caller cannot reach an activation past them by another route.
    """
    row = prompt_activation.activate(
        agent_type=agent_type,
        prompt_name=prompt_name,
        prompt_version=int(prompt_version),
        model_list=[str(item) for item in model_list],
        operator=operator,
        session=session,
    )
    resolved = prompt_registry.get_prompt_by_id(row.prompt_version_id, session=session)
    return ActivationView(
        agent_type=row.agent_type,
        prompt_version_id=row.prompt_version_id,
        name=resolved.name,
        version=resolved.version,
        model_list=list(row.model_list),
        eval_run_id=row.eval_run_id,
        regression_passed_ref=row.regression_passed_ref,
        activated_by=row.activated_by,
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
    """Register the test group's Golden Project result (AI-REG-003, S5IF-GOV11)."""
    row = prompt_activation.record_regression(
        prompt_name=prompt_name,
        prompt_version=int(prompt_version),
        recorded_by=operator,
        passed=bool(passed),
        model=model,
        source=source,
        note=note,
        session=session,
    )
    return RegressionView(id=row.id, passed=row.passed, prompt_version_id=row.prompt_version_id)


def list_regressions(
    prompt_name: str | None = None,
    prompt_version: int | None = None,
    *,
    session: Session | None = None,
) -> list[RegressionView]:
    """The regression results S5 lists before it lets a prompt version be activated (S5IF-GOV11)."""
    return [
        RegressionView(id=row.id, passed=row.passed, prompt_version_id=row.prompt_version_id)
        for row in prompt_activation.list_regressions(
            prompt_name=prompt_name or None,
            prompt_version=int(prompt_version) if prompt_version else None,
            session=session,
        )
    ]


def reload_prompt_sources(
    root: str | Path | None = None,
    *,
    session: Session | None = None,
) -> PromptReloadResult:
    """Re-read the shipped prompt sources into the registry (AI-REG-002)."""
    rows = prompt_registry.reload_sources(
        session=session, root=Path(str(root)) if root else None
    )
    return PromptReloadResult(
        registered=len(rows), names=[f"{row.name}.v{row.version}" for row in rows]
    )


# --- policy (AI-POL-001/002/004, AI-GOV-006) -----------------------------------


def get_org_policy(*, session: Session | None = None) -> OrgPolicyView:
    """The organisation policy, whether it is still the system default, and its ceiling.

    The ceiling travels with the answer because nobody can ask for a legal widening without it:
    ``effective`` alone cannot tell a classification that still has headroom from one already at
    AI-POL-001's outer edge, and the second is a request this module would refuse.
    """
    policy = orgpolicy.read_org_policy(session=session)

    def _who_updated(sess: Session) -> str | None:
        row = sess.get(OrgPolicyRow, "org")
        # absent row means nobody wrote one: the organisation is still on AI-POL-002's default
        return row.updated_by if row is not None else None

    updated_by = _read(session, _who_updated)
    return OrgPolicyView(
        effective=PolicyMatrixView(**policy.to_payload()),
        is_default=updated_by is None,
        updated_by=updated_by,
        # read from the same table the relaxation checks itself against, so a form that offers
        # only what is inside this ceiling cannot be answered with AI_POLICY_RELAXATION_DENIED
        ceiling=PolicyMatrixView(**EffectivePolicy.ceiling().to_payload()),
    )


def relax_org_policy(
    change: Mapping[str, Any],
    reason: str,
    operator: str,
    scope: str = "organization",
    *,
    session: Session | None = None,
) -> RelaxationView:
    """Widen the organisation policy, audited, with the reason recorded (AI-POL-002).

    PHI_PII is unrelaxable and a widening past AI-POL-001's ceiling is refused, both with
    ``AI_POLICY_RELAXATION_DENIED`` (AC-POL-02) — that judgement is
    :func:`factory_intelligence.policy.orgpolicy.relax`'s, not a copy of it here.
    """
    row = orgpolicy.relax(
        change=dict(change),
        reason=str(reason),
        operator=str(operator),
        scope=str(scope),
        session=session,
    )
    return RelaxationView(
        id=row.id,
        scope=row.scope,
        operator=row.operator,
        reason=row.reason,
        change=dict(row.change or {}),
        role_checked=row.role_checked,
        # D-16: from M4 a relaxation needs a platform administrator; until then the name is
        # the identity, and the caller is told which regime answered.
        role_gate=(
            "platform-admin"
            if milestone_at_least(Milestone.M4)
            else "operator-name (MVP)"
        ),
        created_at=row.created_at,
    )


def get_effective_policy(
    environment_policies: Mapping[str, Any] | None = None,
    *,
    session: Session | None = None,
) -> EffectivePolicyView:
    """AI-GOV-006: the strictest of the organisation policy and every target environment.

    S3 holds no project table (D-14), so S1 passes the egress policies of the project's
    target environments and this function only combines them — D-07's rule, with the
    sources and normalisation warnings named for the audit.
    """
    policy = orgpolicy.effective_policy(
        session=session, environment_policies=environment_policies
    )
    return EffectivePolicyView(**policy.to_payload())


# --- budget and usage (AI-COST-001/002, AI-GOV-005/007) ------------------------


def get_budget(project_id: str, *, session: Session | None = None) -> BudgetView:
    """AI-GOV-007: readable from M1, with ``UNSET`` when no limit was ever written."""
    return budget_service.view(project_id, session=session)


def set_budget(
    project_id: str,
    limit: float | None,
    thresholds: Sequence[float],
    *,
    currency: str = "USD",
    operator: str,
    session: Session | None = None,
) -> BudgetView:
    """AI-GOV-007's write side, delivered with M4: the limit an operator sets, audited.

    A bad shape raises :class:`factory_intelligence.errors.BudgetInvalid` rather than
    storing something ``classify`` would half-obey.  Who may call this is S1's decision
    (``governance.budget.set``), not S3's — S3 holds no users (D-14).
    """
    return budget_service.put(
        project_id,
        limit,
        thresholds,
        currency=currency,
        operator=operator,
        session=session,
    )


def usage_rollup(
    project_id: str | None = None,
    *,
    group_by: Sequence[str] = ("stage", "agent", "model"),
    session: Session | None = None,
) -> list[UsageRollup]:
    """AI-COST-001 by any subset of ``project/workflow/stage/agent/model/provider``."""
    return usage.rollup(project_id, group_by=list(group_by), session=session)


# --- evaluation (AI-EVAL-001/003, AI-GOV-004) ----------------------------------


def _summary_of(row: EvalRunRow) -> EvaluationSummary:
    return EvaluationSummary(
        id=row.id,
        agent_type=row.agent_type,
        prompt=f"{row.prompt_name}.v{row.prompt_version}",
        model=row.model,
        eval_set_version=row.eval_set_version,
        score=row.score,
        passed=row.passed,
        case_count=row.case_count,
        summary=dict(row.summary or {}),
        created_at=row.created_at,
    )


def list_evaluations(
    agent_type: str | None = None,
    *,
    limit: int = 50,
    session: Session | None = None,
) -> list[EvaluationSummary]:
    """Evaluation history, newest first, optionally for one agent (S5IF-GOV14)."""

    def _query(sess: Session) -> list[EvaluationSummary]:
        query = select(EvalRunRow).order_by(EvalRunRow.created_at.desc()).limit(int(limit))
        if agent_type:
            query = query.where(EvalRunRow.agent_type == str(agent_type))
        return [_summary_of(row) for row in sess.execute(query).scalars().all()]

    return _read(session, _query)


def get_evaluation(eval_run_id: str, *, session: Session | None = None) -> EvaluationDetail:
    """AI-GOV-004: the run, its per-case results, and how the injection samples fared.

    The per-case rows are written by the runner (``ai.eval_case_result``) and were never
    read back: a score on its own cannot show *which* case a prompt version lost, which is
    exactly what a reviewer needs before approving a switch (AI-REG-003).
    """

    def _query(sess: Session) -> EvaluationDetail:
        row = sess.get(EvalRunRow, eval_run_id)
        if row is None:
            raise NotFoundError(f"evaluation run {eval_run_id} not found")
        cases = [
            EvalCaseView(
                case_id=item.case_id,
                passed=item.passed,
                score=item.score,
                kind=item.kind,
                diff=dict(item.diff or {}),
            )
            for item in sess.execute(
                select(EvalCaseResultRow)
                .where(EvalCaseResultRow.eval_run_id == row.id)
                .order_by(EvalCaseResultRow.case_id)
            )
            .scalars()
            .all()
        ]
        injection = [item for item in cases if item.kind == "injection"]
        summary = _summary_of(row)
        return EvaluationDetail(
            **summary.model_dump(),
            cases=cases,
            injection_cases=len(injection),
            injection_passed=sum(1 for item in injection if item.passed),
        )

    return _read(session, _query)


# --- agent catalogue (AI-GOV-008) ----------------------------------------------


def list_agents(*, session: Session | None = None) -> AgentCatalog:
    """Every agent type with its stage, milestone, effective prompt and patch behaviour.

    AI-GOV-008 asks for the *currently effective* prompt, which is registry state rather
    than the spec's default: an agent can be enabled and still have no active version, and
    S1 must be able to tell "no prompt in use" from "the prompt named in the table".
    """
    active = {
        item.agent_type: f"{item.name}.v{item.version}"
        for item in prompt_activation.list_activations(session=session)
    }
    return AgentCatalog(
        milestone=CURRENT_MILESTONE,
        items=[
            AgentCatalogEntry(
                agent_type=spec.agent_type,
                stage=spec.stage.value,
                milestone=spec.milestone.value,
                enabled=agent_enabled(spec.agent_type),
                prompt_name=spec.prompt_name,
                active_prompt=active.get(spec.agent_type.value),
                per_page=spec.per_page,
                patch_mode=spec.patch_mode.value,
            )
            for spec in AGENT_SPECS.values()
        ],
    )
