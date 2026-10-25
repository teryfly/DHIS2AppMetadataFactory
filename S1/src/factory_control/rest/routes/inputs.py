"""Input parsing, column mapping, explanation and agent-run endpoints (S5IF-INP)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request

from factory_control import iam
from factory_control import store as S
from factory_control.errors import NotFoundControlError
from factory_control.rest import route_meta
from factory_control.rest.artifact_access import authorize_artifact_read
from factory_control.rest.deps import (
    authorize,
    authorize_workflow,
    language_of,
    operator_of,
    runtime_of,
)
from factory_control.rest.middleware import IDEMPOTENCY_HEADER
from factory_control.rest.schemas import common as CS
from factory_control.rest.schemas import inputs as IS
from factory_control.services import agent_runs as RUN
from factory_control.services import inputs as INPUT
from factory_control.services import llm_log as LOG

router = APIRouter(prefix="/api/v1", tags=["inputs"])

_ERRORS = route_meta.error_responses(400, 404, 409, 422, 502, 503)

#: The two report kinds S3 will explain (CP-UI-007 第4项；S3 侧 AI-AG-014); anything else is a 404 here.
EXPLAINABLE_TYPES = frozenset({"VALIDATION_REPORT", "DHIS2_IMPORT_REPORT"})


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.post(
    "/projects/{project_id}/inputs/{artifact_id}/parse",
    status_code=202,
    response_model=CS.AcceptanceView,
    summary="Accept a parse; the answer is recorded on the step run and pushed over SSE",
    responses=_ERRORS,
    openapi_extra=_extras("CP-API-012", "S5IF-INP01", "S5IF-INP04", mutating=True, idempotent=True),
)
def parse_input(
    request: Request,
    project_id: str,
    artifact_id: str,
    payload: IS.ParseInputRequest = Body(default={}),
) -> dict[str, Any]:
    authorize(request, iam.INPUT_PARSE, project_id)
    options = payload.options.to_options() if payload.options else {}
    outcome = INPUT.parse_input(
        runtime_of(request),
        artifact_id,
        project_id=project_id,
        operator=operator_of(request),
        stage_id=payload.stage_id,
        options=options,
        request_key=(request.headers.get(IDEMPOTENCY_HEADER) or "").strip(),
    )
    return {
        "stageId": outcome.get("stageId"),
        "stepRunId": outcome.get("runId"),
        "accepted": True,
    }


@router.get(
    "/projects/{project_id}/inputs/{artifact_id}/llm-log",
    response_model=IS.LlmLogView,
    response_model_exclude_none=True,
    summary="The model calls this input made, with the parse status that explains them",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-INP08"),
)
def llm_log(
    request: Request,
    project_id: str,
    artifact_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    # The transcript quotes the input, so it is read at the input's own classification bar.
    authorize_artifact_read(request, artifact_id)
    return LOG.list_log(
        runtime_of(request),
        project_id,
        artifact_id,
        operator=operator_of(request),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/projects/{project_id}/inputs/{artifact_id}/llm-log/{exchange_id}",
    response_model=IS.LlmExchangeDetailView,
    response_model_exclude_none=True,
    summary="One exchange: the request messages and the model's raw answer",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-INP09"),
)
def llm_exchange(
    request: Request, project_id: str, artifact_id: str, exchange_id: str
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    authorize_artifact_read(request, artifact_id)
    return LOG.get_log(
        runtime_of(request),
        project_id,
        artifact_id,
        exchange_id,
        operator=operator_of(request),
    )


@router.get(
    "/projects/{project_id}/inputs/{artifact_id}/column-mapping",
    response_model=IS.ColumnMappingView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-INP03"),
)
def column_mapping(request: Request, project_id: str, artifact_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return INPUT.column_mapping(
        runtime_of(request), project_id, artifact_id, operator=operator_of(request)
    )


@router.post(
    "/artifacts/{artifact_id}/explain",
    response_model=IS.ExplainResultView,
    summary="Ask S3 to explain a report; the text comes back as an artifact id",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-INP05", mutating=True),
)
def explain_artifact(
    request: Request, artifact_id: str, payload: IS.ExplainRequest = Body(...)
) -> dict[str, Any]:
    from factory_control.foundation import get_artifact

    authorize(request, iam.ARTIFACT_EXPLAIN, payload.project_id)
    artifact = get_artifact(artifact_id)
    if artifact is None or artifact.type not in EXPLAINABLE_TYPES:
        raise NotFoundControlError(
            f"{artifact_id} is not an explainable report",
            details={
                "artifactId": artifact_id,
                "type": getattr(artifact, "type", None),
                "explainable": sorted(EXPLAINABLE_TYPES),
            },
        )
    explanation_id = runtime_of(request).intelligence.explain_issues(
        payload.project_id,
        artifact_id,
        environment_policies=_environment_policies(
            runtime_of(request), payload.project_id, operator=operator_of(request)
        ),
        operator=operator_of(request),
        language=language_of(request),
    )
    # The answer is an AGENT_OUTPUT artifact id or null, never the body itself
    # (S5IF-INP05): S5 reads the text through the existing artifact-content endpoint.
    return {"explanationArtifactId": explanation_id}


@router.get(
    "/workflows/{workflow_id}/agent-runs/{run_id}",
    response_model=IS.AgentRunView,
    summary="Run status assembled from S3's result, S1's questions and the event stream",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-INP06"),
)
def get_agent_run(request: Request, workflow_id: str, run_id: str) -> dict[str, Any]:
    authorize_workflow(request, workflow_id, iam.PROJECT_VIEW)
    return RUN.get_run(runtime_of(request), workflow_id, run_id, operator=operator_of(request))


@router.post(
    "/workflows/{workflow_id}/agent-runs/{run_id}/cancel",
    response_model=IS.CancelRunView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-INP07", mutating=True),
)
def cancel_agent_run(request: Request, workflow_id: str, run_id: str) -> dict[str, Any]:
    authorize_workflow(request, workflow_id, iam.AGENT_CANCEL)
    return RUN.cancel_run(runtime_of(request), workflow_id, run_id, operator=operator_of(request))


def _environment_policies(runtime: Any, project_id: str, *, operator: str) -> dict[str, Any]:
    """D-07 inputs for S3: the policy of each target environment of the project."""
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        keys = [t.environment_key for t in S.target_environments(ctx.session, project_id)]
    policies: dict[str, Any] = {}
    for key in keys:
        environment = runtime.execution.get_environment(key)
        if environment is not None:
            policies[key] = environment.llm_data_policy
    return policies
