"""Governance endpoints (S5IF-GOV01 … GOV19).

S3 offers all of this through one untyped façade; the whole point of this module is
that each action gets its own request model, its own response component and its own
``x-s5if`` tag, so S5 generates a typed client for the admin screens (UI-ADM-003)
instead of transcribing dictionaries by hand.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request

from factory_control import iam
from factory_control.domain import provider_catalogue as CATALOGUE
from factory_control.rest import route_meta
from factory_control.rest.deps import authorize, operator_of, runtime_of
from factory_control.rest.schemas import common as CS
from factory_control.rest.schemas import governance as GS
from factory_control.services import governance as GOV

router = APIRouter(prefix="/api/v1", tags=["governance"])

_ERRORS = route_meta.error_responses(400, 403, 404, 409, 422, 502, 503)


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.get(
    "/governance/providers",
    response_model=GS.ProviderList,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV01"),
)
def list_providers(request: Request) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.providers(runtime_of(request))


@router.post(
    "/governance/providers",
    response_model=GS.ProviderView,
    status_code=201,
    summary="Register a model provider by secret reference, never by key value",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV02", mutating=True, idempotent=True),
)
def register_provider(
    request: Request, payload: GS.RegisterProviderRequest = Body(...)
) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_PROVIDER_REGISTER)
    return GOV.register_provider(
        runtime_of(request), payload.to_payload(operator_of(request)), operator=operator_of(request)
    )


@router.patch(
    "/governance/providers/{name}",
    response_model=GS.ProviderView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV03", mutating=True),
)
def update_provider(
    request: Request, name: str, payload: GS.PatchProviderRequest = Body(...)
) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_PROVIDER_ENABLE)
    return GOV.update_provider(
        runtime_of(request), name, payload.to_patch(), operator=operator_of(request)
    )


@router.post(
    "/governance/providers/{name}/test-connection",
    response_model=GS.ProviderHealthView,
    summary="Ask one provider's address whether it answers, without generating anything",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV20", mutating=True, idempotent=True),
)
def test_provider_connection(request: Request, name: str) -> dict[str, Any]:
    """S5IF-GOV20, and the same shape as an environment's test-connection (S5IF-ENV08).

    Deliberately POST although nothing changes: the call reaches outside the deployment, and a GET
    that a browser prefetches would put a five-second socket on someone's page load.
    """
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.check_provider(runtime_of(request), name)


@router.get(
    "/governance/models",
    response_model=GS.ModelList,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV04"),
)
def list_models(
    request: Request, agentType: str | None = None, includeDisabled: bool = True  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.models(
        runtime_of(request), agent_type=agentType, include_disabled=includeDisabled
    )


@router.post(
    "/governance/models",
    response_model=GS.ModelView,
    status_code=201,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV05", mutating=True, idempotent=True),
)
def register_model(request: Request, payload: GS.RegisterModelRequest = Body(...)) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MODEL_REGISTER)
    return GOV.register_model(
        runtime_of(request), payload.to_payload(operator_of(request)), operator=operator_of(request)
    )


@router.patch(
    # ``:path`` because S3's model reference is ``provider/model`` and carries a slash.
    "/governance/models/{ref:path}",
    response_model=GS.ModelView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV06", mutating=True),
)
def update_model(
    request: Request, ref: str, payload: GS.PatchModelRequest = Body(...)
) -> dict[str, Any]:
    """Correct one model; a field the body omits keeps its stored value.

    Authorised with the same permission the switch-off uses, exactly as the provider edit reuses
    the provider switch: the row is one object, and an operator who may not enable it may not
    rewrite it either.
    """
    authorize(request, iam.GOVERNANCE_MODEL_ENABLE)
    return GOV.update_model(
        runtime_of(request), ref, payload.to_patch(), operator=operator_of(request)
    )


@router.get(
    "/governance/prompts",
    response_model=GS.PromptList,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV07"),
)
def list_prompts(request: Request, agentType: str | None = None) -> dict[str, Any]:  # noqa: N803
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.prompts(runtime_of(request), agentType)


@router.get(
    "/governance/prompts/{name}/versions/{version}",
    response_model=GS.PromptDetailView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV08"),
)
def get_prompt(request: Request, name: str, version: int) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.prompt_detail(runtime_of(request), name, version)


@router.post(
    "/governance/prompts/activations",
    response_model=GS.ActivationView,
    summary="Activate a prompt; a missing passing evaluation answers as 409",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV09", mutating=True),
)
def activate_prompt(request: Request, payload: GS.ActivatePromptRequest = Body(...)) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_PROMPT_ACTIVATE)
    return GOV.activate_prompt(runtime_of(request), payload.to_payload(operator_of(request)))


@router.get(
    "/governance/prompts/activations",
    response_model=GS.ActivationList,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV10"),
)
def list_activations(request: Request) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.activations(runtime_of(request))


@router.get(
    "/governance/regression-records",
    response_model=GS.RegressionList,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV11"),
)
def list_regressions(
    request: Request,
    promptName: str | None = Query(default=None),  # noqa: N803
    promptVersion: int | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    """Which prompt versions already carry a Golden Project result (AI-REG-003)."""
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.regression_records(
        runtime_of(request), prompt_name=promptName, prompt_version=promptVersion
    )


@router.post(
    "/governance/regression-records",
    response_model=GS.RegressionRecordView,
    status_code=201,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV11", mutating=True, idempotent=True),
)
def record_regression(
    request: Request, payload: GS.RecordRegressionRequest = Body(...)
) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_REGRESSION_RECORD)
    return GOV.record_regression(runtime_of(request), payload.to_payload(operator_of(request)))


@router.get(
    "/governance/policy",
    response_model=GS.PolicyView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV12"),
)
def organisation_policy(request: Request) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.organisation_policy(runtime_of(request))


@router.get(
    "/projects/{project_id}/effective-policy",
    response_model=GS.ProjectPolicyView,
    summary="Organisation policy intersected with every target environment (D-07)",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV12"),
)
def project_effective_policy(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return GOV.project_effective_policy(
        runtime_of(request), project_id, operator=operator_of(request)
    )


@router.post(
    "/governance/policy/relaxations",
    response_model=GS.RelaxationView,
    status_code=201,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV13", mutating=True),
)
def relax_policy(request: Request, payload: GS.RelaxPolicyRequest = Body(...)) -> dict[str, Any]:
    authorize(request, iam.POLICY_RELAX)
    return GOV.relax_policy(runtime_of(request), payload.to_payload(operator_of(request)))


@router.get(
    "/governance/evaluations",
    response_model=GS.EvaluationList,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV14"),
)
def list_evaluations(
    request: Request, agentType: str | None = None, limit: int = Query(default=50, ge=1, le=200)  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.evaluations(runtime_of(request), agentType, limit)


@router.get(
    "/governance/evaluations/{eval_run_id}",
    response_model=GS.EvaluationView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV15"),
)
def get_evaluation(request: Request, eval_run_id: str) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.evaluation_detail(runtime_of(request), eval_run_id)


@router.post(
    "/governance/evaluations",
    response_model=CS.AcceptanceView,
    status_code=202,
    summary="Start an evaluation through run_evaluation — the answer names the evaluation run",
    responses=_ERRORS,
    openapi_extra=_extras("CP-API-012", "S5IF-GOV16", mutating=True, idempotent=True),
)
def run_evaluation(request: Request, payload: GS.RunEvaluationRequest = Body(...)) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_EVALUATION_RUN)
    answer = GOV.run_evaluation(
        runtime_of(request),
        {
            "agentType": payload.agent_type,
            "qualifiedPrompt": payload.qualified_prompt,
            "model": payload.model,
            "evalSetVersion": payload.eval_set_version,
        },
        operator=operator_of(request),
    )
    return {"stepRunId": str(answer["id"]), "accepted": True}


@router.get(
    "/governance/usage",
    response_model=GS.UsageView,
    summary="Cost rollup; an unknown dimension is rejected here rather than 500",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV17"),
)
def usage_rollup(
    request: Request,
    projectId: str | None = None,  # noqa: N803
    groupBy: list[str] | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    query = GS.UsageQuery(projectId=projectId, groupBy=list(groupBy or ["stage", "agent", "model"]))
    return GOV.usage(runtime_of(request), query.project_id, query.group_by)


@router.get(
    "/governance/budget",
    response_model=GS.BudgetView,
    summary="The project's cost budget, readable since M1 (AI-GOV-007)",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV18"),
)
def get_budget(request: Request, projectId: str = Query(...)) -> dict[str, Any]:  # noqa: N803
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.budget(runtime_of(request), projectId)


@router.put(
    "/governance/budget",
    response_model=GS.BudgetView,
    summary="Set or clear the project's cost budget and warning thresholds (M4)",
    responses=_ERRORS,
    openapi_extra=_extras("CP-UI-006", "S5IF-GOV18", mutating=True),
)
def set_budget(
    request: Request,
    payload: GS.BudgetSetRequest,
    projectId: str = Query(...),  # noqa: N803
) -> dict[str, Any]:
    """AI-COST-002's switch, forwarded to S3 (AI-GOV-007).

    A PUT rather than a POST: the statement is the whole budget of one project, so writing the
    same body twice is the same budget and needs no idempotency key (CP-API-003).
    """
    authorize(request, iam.BUDGET_SET)
    return GOV.set_budget(
        runtime_of(request),
        projectId,
        {"limit": payload.limit, "thresholds": payload.thresholds, "currency": payload.currency},
        operator=operator_of(request),
    )


@router.get(
    "/governance/agents",
    response_model=GS.AgentListView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-GOV19"),
)
def list_agents(request: Request) -> dict[str, Any]:
    authorize(request, iam.GOVERNANCE_MANAGE)
    return GOV.agents(runtime_of(request))


@router.get(
    "/governance/provider-catalogue",
    response_model=GS.ProviderCatalogueView,
    summary="Built-in vendor addresses and model names to start from",
    responses=route_meta.error_responses(401, 403),
    openapi_extra=_extras("S5IF-GOV07", "AI-CAT-001"),
)
def provider_catalogue(request: Request) -> dict[str, Any]:
    """Suggestions, not a capability list — see :mod:`factory_control.domain.provider_catalogue`.

    Served from S1 rather than hardcoded in each console because the two front-ends must not be
    able to disagree about which addresses work, and because the constraint that makes an entry
    honest (this build speaks one wire protocol) lives in the code that speaks it.
    """
    authorize(request, iam.GOVERNANCE_MANAGE)
    items = CATALOGUE.presets()
    return {"items": items, "total": len(items)}
