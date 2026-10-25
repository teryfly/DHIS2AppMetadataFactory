"""``governance(action, payload)`` — S1's forwarding entry point, over the typed functions.

AI-GOV-001 makes every governance capability a named function with typed parameters, so the
real surface is :mod:`factory_intelligence.governance.capabilities`, exported from
:mod:`factory_intelligence.api`.  This module is what is left of the old "action name plus a
free dictionary" shape: S1 still forwards ``governance`` verbatim for its REST endpoints
(CP-API-007), so each action here decodes its payload into the same typed spec, calls the
same capability, and serialises the same model.  There is one implementation of each rule,
and a caller that reaches it by action name cannot get a different answer than a caller that
reaches it by function name.

**No handler performs external I/O.**  ``governance`` is *not* in
``IntelligenceAdapter.EXTERNAL_IO_METHODS``, which means S1 may call it with its own
transaction open — so an action that would have to read an artifact or call a model is
refused here with a pointer to the public function that may (``eval.run`` →
``run_evaluation``, ``similar.search`` → the Mapping Agent's context).  Silently opening a
nested transaction instead would break 00 doc 6.2 and be indistinguishable from success.

Payload keys are read tolerantly (camelCase or snake_case): S1 hands the dictionary through
untouched from an HTTP body, and a governance call that failed because a UI wrote
``providerClass`` while S3 expected ``provider_class`` would be a bug nobody could see.  The
spec models accept both spellings, so the tolerance lives in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from factory_foundation.api import ValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from factory_intelligence.cost import usage
from factory_intelligence.dto.governance import (
    ActivationView,
    AgentCatalog,
    ColumnMappingProposal,
    EffectivePolicyView,
    EnableResult,
    EvaluationDetail,
    EvaluationList,
    ModelList,
    ModelPatch,
    ModelSpec,
    ModelView,
    NrrFilters,
    NrrQueryResult,
    OrgPolicyView,
    Page,
    ParseRecordList,
    PromptDetailView,
    PromptList,
    PromptReloadResult,
    ProviderList,
    ProviderPatch,
    ProviderSpec,
    ProviderView,
    RegressionView,
    RelaxationView,
    UsageRollupResult,
    wire,
)
from factory_intelligence.dto.result import BudgetView
from factory_intelligence.errors import GovernanceActionUnknown
from factory_intelligence.governance import capabilities
from factory_intelligence.parsing import records as parse_records
from factory_intelligence.prompts import registry as prompt_registry

Handler = Callable[[dict[str, Any], Session | None], Any]


def _get(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    """Read the first present key, in either spelling."""
    for name in names:
        if name in payload:
            return payload[name]
    return default


def _require(payload: dict[str, Any], *names: str) -> Any:
    value = _get(payload, *names)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(
            f"governance payload needs {names[0]}",
            code="VALIDATION_ERROR",
            details={"missing": names[0], "given": sorted(payload)},
        )
    return value


Spec = TypeVar("Spec", bound=BaseModel)


def _decode(kind: type[Spec], payload: dict[str, Any]) -> Spec:
    """Decode a payload into a spec model, keeping the failure a ``FactoryError``.

    A pydantic ``ValidationError`` is not a ``FactoryError``, and S1 maps only the latter to
    the common REST error envelope (00 doc 6.5) — so a UI that posts a provider with no
    ``providerClass`` would get a 500 for what is a 422.  The names that were missing or
    wrong are reported in ``details`` instead, in the spelling the caller used.
    """
    try:
        return kind.model_validate(dict(payload))
    except PydanticValidationError as exc:
        given = dict(payload)
        problems: list[dict[str, Any]] = []
        details: dict[str, Any] = {"given": sorted(given)}
        for error in exc.errors():
            field = ".".join(str(item) for item in error["loc"]) or kind.__name__.lower()
            problems.append({"field": field, "reason": error["msg"]})
            # the name the caller used, with what it sent under it: an operator surface that
            # says only "invalid" cannot be fixed without reading the source
            details.setdefault(field, given.get(field))
            # a required key that was absent, blank or unusable is one failure to a caller:
            # ``missing`` names the first of them, the way the payload used to be checked
            details.setdefault("missing", field)
        raise ValidationError(
            f"governance payload is not a valid {kind.__name__}: "
            + ", ".join(f"{item['field']}: {item['reason']}" for item in problems),
            code="VALIDATION_ERROR",
            details={**details, "problems": problems},
        ) from exc


# --- providers and models -------------------------------------------------------


def _provider_register(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        capabilities.register_provider(
            _decode(ProviderSpec, payload),
            str(_get(payload, "operator", default="system")),
            session=session,
        )
    )


def _provider_enable(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        capabilities.set_provider_enabled(
            str(_require(payload, "name", "ref")),
            bool(_get(payload, "enabled", default=True)),
            str(_get(payload, "operator", default="system")),
            session=session,
        )
    )


def _provider_update(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    """The façade spelling of the typed edit; `name`/`ref` selects the row, the rest is the patch."""
    patch = {k: v for k, v in payload.items() if k not in ("name", "ref", "operator")}
    return wire(
        capabilities.update_provider(
            str(_require(payload, "name", "ref")),
            _decode(ProviderPatch, patch),
            str(_get(payload, "operator", default="system")),
            session=session,
        )
    )


def _provider_list(_payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(ProviderList(items=capabilities.list_providers(session=session)))


def _model_register(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        capabilities.register_model(
            _decode(ModelSpec, payload),
            str(_get(payload, "operator", default="system")),
            session=session,
        )
    )


def _model_update(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    """The façade spelling of the typed edit; `ref` selects the row, the rest is the patch."""
    patch = {k: v for k, v in payload.items() if k not in ("ref", "providerModel", "operator")}
    return wire(
        capabilities.update_model(
            str(_require(payload, "ref", "providerModel")),
            _decode(ModelPatch, patch),
            str(_get(payload, "operator", default="system")),
            session=session,
        )
    )


def _model_enable(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        capabilities.set_model_enabled(
            str(_require(payload, "ref", "providerModel")),
            bool(_get(payload, "enabled", default=True)),
            str(_get(payload, "operator", default="system")),
            session=session,
        )
    )


def _model_list(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        ModelList(
            items=capabilities.list_models(
                _get(payload, "agentType", "agent_type"),
                include_disabled=bool(
                    _get(payload, "includeDisabled", "include_disabled", default=True)
                ),
                session=session,
            )
        )
    )


# --- prompts, activations and regressions ---------------------------------------


def _prompt_list(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        PromptList(
            items=capabilities.list_prompts(
                _get(payload, "agentType", "agent_type"), session=session
            )
        )
    )


def _prompt_get(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    name = _get(payload, "name")
    version = _get(payload, "version")
    if name is None or version is None:
        # one half is not an address: resolve by id rather than guessing a name of None
        row_id = _require(payload, "id", "promptVersionId")
        row = prompt_registry.get_prompt_by_id(str(row_id), session=session)
        name, version = row.name, row.version
    return wire(capabilities.get_prompt(str(name), int(version), session=session))


def _prompt_activate(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        capabilities.activate_prompt(
            str(_require(payload, "agentType", "agent_type")),
            str(_require(payload, "promptName", "prompt_name", "name")),
            int(_require(payload, "promptVersion", "prompt_version", "version")),
            [str(item) for item in _require(payload, "modelList", "model_list")],
            str(_get(payload, "operator", default="system")),
            session=session,
        )
    )


def _prompt_reload(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(capabilities.reload_prompt_sources(_get(payload, "root"), session=session))


def _regression_record(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        capabilities.record_regression(
            str(_require(payload, "promptName", "prompt_name")),
            int(_require(payload, "promptVersion", "prompt_version")),
            bool(_get(payload, "passed", default=True)),
            _get(payload, "model"),
            str(_get(payload, "source", default="golden-project")),
            _get(payload, "note"),
            str(_get(payload, "recordedBy", "recorded_by", "operator", default="test-group")),
            session=session,
        )
    )


# --- policy, budget, usage -----------------------------------------------------


def _policy_org_get(_payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(capabilities.get_org_policy(session=session))


def _policy_org_relax(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        capabilities.relax_org_policy(
            dict(_require(payload, "change")),
            str(_require(payload, "reason")),
            str(_require(payload, "operator")),
            str(_get(payload, "scope", default="organization")),
            session=session,
        )
    )


def _policy_effective(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    """AI-GOV-006, S5IF-GOV12: D-07's strictest-of, for the project's own page."""
    return wire(
        capabilities.get_effective_policy(
            _get(payload, "environmentPolicies", "environment_policies") or {},
            session=session,
        )
    )


def _budget_set(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    """AI-GOV-007's write side (M4): the same typed result as ``budget.get``."""
    view = capabilities.set_budget(
        str(_require(payload, "projectId", "project_id")),
        _get(payload, "limit"),
        [float(item) for item in (_get(payload, "thresholds") or [])],
        currency=str(_get(payload, "currency", default="USD")),
        operator=str(_require(payload, "operator")),
        session=session,
    )
    return view.model_dump(mode="json")


def _budget_get(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    view = capabilities.get_budget(
        str(_require(payload, "projectId", "project_id")), session=session
    )
    return view.model_dump(mode="json")


def _usage_rollup(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    group_by = [
        str(item) for item in (_get(payload, "groupBy", "group_by") or ["stage", "agent", "model"])
    ]
    # ``projectId`` may be absent: AI-COST-001 asks for a rollup that spans projects
    rows = capabilities.usage_rollup(
        _get(payload, "projectId", "project_id"), group_by=group_by, session=session
    )
    return wire(
        UsageRollupResult(
            group_by=group_by,
            dimensions=list(usage.ROLLUP_DIMENSIONS),
            items=rows,
        )
    )


# --- evaluation ----------------------------------------------------------------


def _eval_list(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        EvaluationList(
            items=capabilities.list_evaluations(
                _get(payload, "agentType", "agent_type"),
                limit=int(_get(payload, "limit", default=50)),
                session=session,
            )
        )
    )


def _eval_get(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    run_id = str(_require(payload, "id", "evalRunId"))
    return wire(capabilities.get_evaluation(run_id, session=session))


def _eval_run(_payload: dict[str, Any], _session: Session | None) -> dict[str, Any]:
    raise ValidationError(
        "running an evaluation performs external I/O (model calls) and cannot be served "
        "inside a governance call; call factory_intelligence.api.run_evaluation instead "
        "(00 doc 6.2)",
        code="VALIDATION_ERROR",
        details={"use": "run_evaluation"},
    )


# --- parse records, column mapping and NRR reads (AI-INP-013/014/015) ------------


def _input_parse_list(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    rows = parse_records.list_input_parses(
        str(_require(payload, "projectId", "project_id")),
        _get(payload, "inputArtifactIds", "input_artifact_ids"),
        session=session,
    )
    return wire(ParseRecordList(items=list(rows)))


def _column_mapping_get(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        parse_records.get_column_mapping_proposal(
            str(_require(payload, "projectId", "project_id")),
            str(_require(payload, "inputArtifactId", "input_artifact_id")),
            session=session,
        )
    )


def _nrr_query(payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    return wire(
        parse_records.query_nrr(
            str(_require(payload, "projectId", "project_id")),
            _decode(NrrFilters, dict(_get(payload, "filters") or {})),
            _decode(Page, dict(_get(payload, "page") or {})),
            session=session,
        )
    )


# --- agents --------------------------------------------------------------------


def _agent_list(_payload: dict[str, Any], session: Session | None) -> dict[str, Any]:
    """Which agents may run here — what S5 needs to offer manual authoring instead."""
    return wire(capabilities.list_agents(session=session))


def _similar_search(_payload: dict[str, Any], _session: Session | None) -> dict[str, Any]:
    raise ValidationError(
        "similar-object search reads a DISCOVERY_RESULT artifact, which is external I/O and "
        "cannot be served inside a governance call; it runs as part of a Mapping Agent run "
        "(AI-AG-011), and api.governance exposes no read of artifact content (00 doc 6.2)",
        code="VALIDATION_ERROR",
        details={"use": "start_agent_run with agentType=MAPPING"},
    )


#: The action vocabulary.  Anything S1 forwards that is absent here fails loudly with the
#: known list, because a typo in a UI button would otherwise look like a working endpoint.
#: Every entry is a thin spelling of the typed capability function of the same name.
ACTIONS: dict[str, Handler] = {
    "provider.register": _provider_register,
    "provider.update": _provider_update,
    "provider.enable": _provider_enable,
    "provider.list": _provider_list,
    "model.register": _model_register,
    "model.update": _model_update,
    "model.enable": _model_enable,
    "model.list": _model_list,
    "prompt.list": _prompt_list,
    "prompt.get": _prompt_get,
    "prompt.activate": _prompt_activate,
    "prompt.source.reload": _prompt_reload,
    "regression.record": _regression_record,
    "policy.org.get": _policy_org_get,
    "policy.org.relax": _policy_org_relax,
    "policy.effective.get": _policy_effective,
    "budget.set": _budget_set,
    "budget.get": _budget_get,
    "usage.rollup": _usage_rollup,
    "input.parseList": _input_parse_list,
    "input.columnMapping": _column_mapping_get,
    "nrr.query": _nrr_query,
    "eval.list": _eval_list,
    "eval.get": _eval_get,
    "eval.run": _eval_run,
    "agent.list": _agent_list,
    "similar.search": _similar_search,
}



#: Every façade action, the typed public function it delegates to, and the declared model its
#: answer is projected from (AI-GOV-009). The façade may not grow an action without an entry
#: here, and an entry without a real function is what the compatibility test fails on -- the
#: table is also the single map S1's adapter follows when the façade is removed at the end of M2.
ACTION_CAPABILITIES: dict[str, str | None] = {
    "provider.register": "register_provider",
    "provider.update": "update_provider",
    "provider.enable": "set_provider_enabled",
    "provider.list": "list_providers",
    "model.register": "register_model",
    "model.update": "update_model",
    "model.enable": "set_model_enabled",
    "model.list": "list_models",
    "prompt.list": "list_prompts",
    "prompt.get": "get_prompt",
    "prompt.activate": "activate_prompt",
    "prompt.source.reload": "reload_prompt_sources",
    "regression.record": "record_regression",
    "policy.org.get": "get_org_policy",
    "policy.org.relax": "relax_org_policy",
    "policy.effective.get": "get_effective_policy",
    "budget.set": "set_budget",
    "budget.get": "get_budget",
    "usage.rollup": "usage_rollup",
    "input.parseList": "list_input_parses",
    "input.columnMapping": "get_column_mapping_proposal",
    "nrr.query": "query_nrr",
    "eval.list": "list_evaluations",
    "eval.get": "get_evaluation",
    "eval.run": "run_evaluation",
    "agent.list": "list_agents",
    # no typed exit and none planned: the action exists only to refuse, because the
    # similarity search needs external I/O the facade is never allowed to perform
    "similar.search": None,
}

#: The models the declared results are projected from, per action.
FACADE_RESULT_MODELS: dict[str, type[BaseModel] | None] = {
    "provider.register": ProviderView,
    "provider.update": ProviderView,
    "provider.enable": EnableResult,
    "provider.list": ProviderList,
    "model.register": ModelView,
    "model.update": ModelView,
    "model.enable": EnableResult,
    "model.list": ModelList,
    "prompt.list": PromptList,
    "prompt.get": PromptDetailView,
    "prompt.activate": ActivationView,
    "prompt.source.reload": PromptReloadResult,
    "regression.record": RegressionView,
    "policy.org.get": OrgPolicyView,
    "policy.org.relax": RelaxationView,
    "policy.effective.get": EffectivePolicyView,
    "budget.set": BudgetView,
    "budget.get": BudgetView,
    "usage.rollup": UsageRollupResult,
    "input.parseList": ParseRecordList,
    "input.columnMapping": ColumnMappingProposal,
    "nrr.query": NrrQueryResult,
    "eval.list": EvaluationList,
    "eval.get": EvaluationDetail,
    "eval.run": None,
    "agent.list": AgentCatalog,
    "similar.search": None,
}

#: The actions that answer by refusing, so ``None`` above means "no result exists" and not
#: "a result whose type nobody declared".  Each refusal is loud — it raises with the reason and
#: the pointer below — and AI-GOV-009's compatibility test walks this table, so an action that
#: starts answering with an undeclared dict fails instead of passing unnoticed.
FACADE_REFUSALS: dict[str, str] = {
    "eval.run": "model calls are external I/O; use api.run_evaluation (00 doc 6.2)",
    "similar.search": "artifact content is external I/O; use a MAPPING agent run (AI-AG-011)",
}


def known_actions() -> list[str]:
    return sorted(ACTIONS)


def dispatch(action: str, payload: dict[str, Any], *, session: Session | None = None) -> Any:
    """Route one governance action.  Transactional or read-only, never external I/O."""
    handler = ACTIONS.get(action)
    if handler is None:
        raise GovernanceActionUnknown(
            f"unknown governance action {action!r}",
            details={"action": action, "known": known_actions()},
        )
    return handler(dict(payload or {}), session)


__all__ = [
    "ACTIONS",
    "ACTION_CAPABILITIES",
    "FACADE_REFUSALS",
    "FACADE_RESULT_MODELS",
    "dispatch",
    "known_actions",
]
