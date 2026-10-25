"""Deployment Runs, COMMIT and executions (SRS 8.1, CP-WF-020 … 024)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request

from factory_control import iam
from factory_control import store as S
from factory_control.errors import IdempotencyKeyRequiredError, NotFoundControlError
from factory_control.rest import route_meta
from factory_control.rest.deps import (
    authorize,
    authorize_execution,
    authorize_run,
    authorize_workflow,
    operator_of,
    runtime_of,
    visible_project_ids,
)
from factory_control.rest.middleware import IDEMPOTENCY_HEADER
from factory_control.rest.schemas import common as CS
from factory_control.rest.schemas import requests as RQ
from factory_control.rest.schemas import runs as RS
from factory_control.services import runs as RUNS

router = APIRouter(prefix="/api/v1", tags=["runs"])

_ERRORS = route_meta.error_responses(400, 403, 404, 409, 422, 502, 503)


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.post(
    "/workflows/{workflow_id}/deployment-runs",
    status_code=201,
    response_model=RS.CreateRunView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-FLD06", mutating=True, idempotent=True),
)
def create_run(request: Request, workflow_id: str, payload: RQ.CreateRunBody = Body(...)) -> dict[str, Any]:
    authorize_workflow(request, workflow_id, iam.DEPLOYMENT_CREATE)
    return RUNS.create_run(
        runtime_of(request),
        workflow_id,
        environment_key=payload.environment_key,
        operator=operator_of(request),
    )


@router.get(
    "/workflows/{workflow_id}/deployment-runs",
    response_model=RS.RunList,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-FLD06"),
)
def list_runs(request: Request, workflow_id: str) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
    return {"items": RUNS.list_runs(runtime_of(request), workflow_id, operator=operator_of(request))}


@router.get(
    "/deployment-runs",
    response_model=RS.RunPage,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-FLD04"),
)
def list_all_runs(
    request: Request,
    status: str | None = None,
    environmentKey: str | None = Query(default=None),  # noqa: N803
    outdated: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sortBy: str | None = Query(default=None),  # noqa: N803
    sortDir: str | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    """S5IF-FLD04: "最近执行" on the Dashboard, without walking every project."""
    # FR-SEC-004: no project in the path to authorize against, so ask who may see a cross-project
    # list rather than letting the organization-scope question decide that everyone sees everything.
    visible = visible_project_ids(request)
    from factory_control.rest.paging import sort_direction, sortable

    return RUNS.list_runs_across(
        runtime_of(request),
        operator=operator_of(request),
        status=status,
        environment_key=environmentKey,
        outdated=outdated,
        sort_by=sortable(sortBy, S.RUN_SORT_COLUMNS),
        sort_dir=sort_direction(sortDir),
        limit=limit,
        offset=offset,
        only_project_ids=visible,
    )


@router.get(
    "/deployment-runs/{run_id}",
    response_model=RS.RunDetailView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-FLD06"),
)
def get_run(request: Request, run_id: str) -> dict[str, Any]:
    authorize_run(request, run_id, iam.PROJECT_VIEW)
    return RUNS.get_run_view(runtime_of(request), run_id, operator=operator_of(request))


@router.post(
    "/deployment-runs/{run_id}/commit",
    status_code=202,
    response_model=CS.AcceptanceView,
    summary="Accept a COMMIT — the Idempotency-Key header is mandatory",
    responses=_ERRORS,
    openapi_extra=_extras("CP-API-012", "S5IF-G08", "S5IF-G06", mutating=True, idempotent=True),
)
def commit_run(request: Request, run_id: str) -> dict[str, Any]:
    """S5IF-G08 【裁决】: one source for the key, so a retry cannot be ambiguous.

    A COMMIT that is re-sent without a key would apply the package twice, and the
    request body used to be a second place a key could come from — with no rule for
    which won.  The header is now required and is the only place S1 reads.

    CP-API-012: a COMMIT is a step, so the answer is the acceptance envelope and the
    execution it started — ``GET /executions/{id}`` and the SSE stream carry the rest.
    A refusal of the pre-flight checks (gate, window, lock, drift) still answers the
    caller here and now, because nothing was started.
    """
    key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
    if not key:
        raise IdempotencyKeyRequiredError(
            "a COMMIT must carry an Idempotency-Key header",
            details={"header": IDEMPOTENCY_HEADER, "runId": run_id},
        )
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        run = S.get_run(ctx.session, run_id)
        protection = ctx.execution.get_protection(run.environment_key)
    # scoped to the Run's project: `execution.commit.<CLASS>` is not a PROJECT_SCOPED name, so
    # without a project the check reads only the caller's organisation-wide roles and a person
    # whose role lives on the project — how DHIS2_IMPLEMENTER is granted — could never commit.
    authorize_run(request, run_id, iam.execution_commit_action(protection.environment_class))
    outcome = RUNS.commit_run(
        runtime_of(request), run_id, operator=operator_of(request), idempotency_key=f"commit:{key[:40]}"
    )
    return {
        "stageId": outcome.get("stageId"),
        "stepRunId": outcome.get("executionId") or run_id,
        "accepted": True,
    }


@router.get(
    "/deployment-runs/{run_id}/summary",
    response_model=RS.RunSummaryView,
    response_model_exclude_none=True,
    summary="The deployment console in one request, with no call to the target instance",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-FLD05"),
)
def run_summary(request: Request, run_id: str) -> dict[str, Any]:
    authorize_run(request, run_id, iam.PROJECT_VIEW)
    return RUNS.run_summary(runtime_of(request), run_id, operator=operator_of(request))


@router.post(
    "/deployment-runs/{run_id}/drift-check",
    status_code=202,
    response_model=CS.AcceptanceView,
    summary="Accept a drift re-check — the verdict is recorded on the Run and pushed",
    responses=_ERRORS,
    openapi_extra=_extras(
        "CP-API-012", "CP-UI-013", "S5IF-FLD05", "S5IF-FLD12", mutating=True, idempotent=True
    ),
)
def drift_check(request: Request, run_id: str) -> dict[str, Any]:
    authorize_run(request, run_id, iam.DEPLOYMENT_RUN_DRIFT_CHECK)
    verdict = RUNS.check_run_drift(runtime_of(request), run_id, operator=operator_of(request))
    return {
        "stageId": verdict.get("stageId"),
        "stepRunId": verdict["stepRunId"],
        "accepted": True,
    }


@router.post(
    "/deployment-runs/{run_id}/cancel",
    response_model=RS.CancelRunView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-FLD06", mutating=True),
)
def cancel_run(request: Request, run_id: str) -> dict[str, Any]:
    authorize_run(request, run_id, iam.DEPLOYMENT_CREATE)
    return RUNS.cancel_run(runtime_of(request), run_id, operator=operator_of(request))


@router.get(
    "/executions/{execution_id}",
    response_model=RS.ExecutionView,
    response_model_exclude_none=True,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-G03"),
)
def get_execution(request: Request, execution_id: str) -> dict[str, Any]:
    authorize_execution(request, execution_id, iam.PROJECT_VIEW)
    outcome = runtime_of(request).execution.get_execution(execution_id)
    if outcome is None:
        raise NotFoundControlError(f"unknown execution {execution_id}")
    return outcome.model_dump()


@router.get(
    "/executions/{execution_id}/report",
    response_model=RS.ExecutionReportView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-FLD05"),
)
def execution_report(request: Request, execution_id: str) -> dict[str, Any]:
    authorize_execution(request, execution_id, iam.PROJECT_VIEW)
    outcome = runtime_of(request).execution.get_execution(execution_id)
    if outcome is None:
        raise NotFoundControlError(f"unknown execution {execution_id}")
    return {
        "executionId": execution_id,
        "reportArtifactId": outcome.report_artifact_id,
        "importReportArtifactId": outcome.import_report_artifact_id,
    }


@router.post(
    "/executions/{execution_id}/reconcile",
    response_model=RS.ReconcileView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-G03", mutating=True, idempotent=True),
)
def reconcile(request: Request, execution_id: str) -> dict[str, Any]:
    authorize_execution(request, execution_id, iam.PROJECT_EDIT)
    return runtime_of(request).execution.reconcile(execution_id).model_dump()
