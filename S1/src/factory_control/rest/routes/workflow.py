"""Workflows, stages and stage-level actions (SRS 8.1, CP-WF-*)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from factory_control import iam
from factory_control import store as S
from factory_control.errors import IDEMPOTENCY_HEADER, NotFoundControlError
from factory_control.rest.deps import (
    authorize,
    authorize_stage,
    authorize_workflow,
    language_of,
    operator_of,
    runtime_of,
)
from factory_control.rest.schemas import common as CS
from factory_control.rest.schemas import requests as RQ
from factory_control.rest.schemas import workflow as WS
from factory_control.services import questions as Q
from factory_control.services import steps as STEPS
from factory_control.services import workflow as WF
from factory_control.services.stage_rollup import rollups

router = APIRouter(prefix="/api/v1", tags=["workflow"])


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    """Header references and requirement ids, as in the newer route modules."""
    from factory_control.rest import route_meta

    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.get("/projects/{project_id}/workflow",
    response_model=WS.WorkflowView)
def project_workflow(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    with runtime_of(request).transaction(operator=operator_of(request), project_id=project_id) as ctx:
        workflow = S.get_workflow_for_project(ctx.session, project_id)
        project = S.get_project(ctx.session, project_id)
        WF.refresh_derived_state(ctx, workflow)
        view = WF.workflow_view(
            ctx, workflow, project, language=language_of(request),
            active_draft_id=_active_draft_id(runtime_of(request), project_id),
        )
    # A provider read, so it happens beside the transaction and not inside it (00 doc 6.2).
    view["activeDraft"] = WF.active_draft(runtime_of(request), project_id)
    return view


@router.get("/workflows/{workflow_id}", response_model=WS.WorkflowView)
def get_workflow(request: Request, workflow_id: str) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        project = S.get_project(ctx.session, workflow.project_id)
        authorize(request, iam.PROJECT_VIEW, project.id)
        WF.refresh_derived_state(ctx, workflow)
        view = WF.workflow_view(ctx, workflow, project, language=language_of(request))
    view["activeDraft"] = WF.active_draft(runtime_of(request), project.id)
    return view


def _active_draft_id(runtime: Any, project_id: str) -> str | None:
    """Just the id, for the stage rows that need it inside the same response."""
    draft = WF.active_draft(runtime, project_id)
    return str(draft["draftId"]) if draft else None


@router.get("/workflows/{workflow_id}/stages", response_model=WS.StageList)
def list_stages(request: Request, workflow_id: str) -> dict[str, Any]:
    """S5IF-FLD01: every stage with its aggregates, from one pass over the workflow."""
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
        stages = S.stages_of(ctx.session, workflow_id)
        aggregates = rollups(
            ctx.session, workflow_id, stages, language=language_of(request),
            active_draft_id=_active_draft_id(runtime_of(request), workflow.project_id),
            current_stage_id=workflow.current_stage_id,
        )
        return {"items": [WF.stage_view(s, aggregates.get(s.id)) for s in stages]}


@router.get("/stages/{stage_id}", response_model=WS.StageDetailView)
def get_stage(request: Request, stage_id: str) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        stage = S.get_stage(ctx.session, stage_id)
        workflow = S.get_workflow(ctx.session, stage.workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
        view = WF.stage_view(
            stage,
            rollups(
                ctx.session, workflow.id, S.stages_of(ctx.session, workflow.id),
                language=language_of(request),
                active_draft_id=_active_draft_id(runtime_of(request), workflow.project_id),
                current_stage_id=workflow.current_stage_id,
            ).get(stage.id),
        )
        view["stageRuns"] = [
            {
                "id": r.id,
                "attempt": r.attempt,
                "agentRunIds": r.agent_run_ids,
                "executionIds": r.execution_ids,
                "buildIds": r.build_ids,
                "result": r.result,
            }
            for r in S.stage_runs(ctx.session, stage_id)
        ]
        view["questions"] = [
            Q.question_view(q) for q in S.questions_of(ctx.session, workflow.id, stage_id=stage_id)
        ]
        return view


@router.post(
    "/workflows/{workflow_id}/pause",
    response_model=WS.WorkflowFlagView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True),
)
def pause(request: Request, workflow_id: str) -> dict[str, Any]:
    authorize_workflow(request, workflow_id, iam.PROJECT_EDIT)
    return WF.pause_workflow(runtime_of(request), workflow_id, operator=operator_of(request))


@router.post(
    "/workflows/{workflow_id}/resume",
    response_model=WS.WorkflowFlagView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True),
)
def resume(request: Request, workflow_id: str) -> dict[str, Any]:
    authorize_workflow(request, workflow_id, iam.PROJECT_EDIT)
    return WF.resume_workflow(runtime_of(request), workflow_id, operator=operator_of(request))


@router.post(
    "/workflows/{workflow_id}/cancel",
    response_model=WS.WorkflowFlagView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True),
)
def cancel(request: Request, workflow_id: str) -> dict[str, Any]:
    authorize_workflow(request, workflow_id, iam.PROJECT_EDIT)
    engine = runtime_of(request).engine
    if engine is not None:
        engine.request_cancel(workflow_id)
    return WF.cancel_workflow(runtime_of(request), workflow_id, operator=operator_of(request))


@router.get(
    "/workflows/{workflow_id}/allowed-actions",
    response_model=WS.AllowedActionsView,
    openapi_extra=_extras("S5IF-G10", "S5IF-FLD12"),
)
def allowed_actions(request: Request, workflow_id: str) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
    return WF.allowed_actions_for_workflow(runtime_of(request), workflow_id, operator=operator_of(request))


@router.post(
    "/stages/{stage_id}/run",
    status_code=202,
    response_model=CS.AcceptanceView,
    response_model_exclude_none=True,
)
def run_stage(request: Request, stage_id: str) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.STAGE_RUN)
    return _start(request, stage_id, rerun=False)


@router.post(
    "/stages/{stage_id}/rerun",
    status_code=202,
    response_model=CS.AcceptanceView,
    response_model_exclude_none=True,
)
def rerun_stage(request: Request, stage_id: str) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.STAGE_RUN)
    return _start(request, stage_id, rerun=True)


@router.post(
    "/stages/{stage_id}/submit-for-approval",
    response_model=WS.SubmitApprovalView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True, idempotent=True),
)
def submit_for_approval(request: Request, stage_id: str) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.STAGE_RUN)
    return STEPS.submit_for_approval(runtime_of(request), stage_id, operator=operator_of(request))


@router.post(
    "/stages/{stage_id}/resolve-manual-check",
    response_model=WS.ManualCheckView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True),
)
def resolve_manual_check(request: Request, stage_id: str, payload: RQ.ManualCheckBody = Body(...)) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.STAGE_RUN)
    return STEPS.resolve_manual_check(
        runtime_of(request),
        stage_id,
        operator=operator_of(request),
        outcome=payload.outcome,
        note=payload.note,
    )


@router.post(
    "/stages/{stage_id}/patches",
    response_model=WS.PatchSubmitView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-FLD11", mutating=True, idempotent=True),
)
def submit_patch(request: Request, stage_id: str, payload: RQ.StagePatchBody = Body(...)) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.PATCH_SUBMIT)
    # The key the console sent names the *action*, and only the caller can tell a dropped request
    # from a new edit of the same content — so it is forwarded to the S2 call rather than left for
    # this server's middleware alone (CP-API-003, NFR-REL-003).
    key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
    return STEPS.submit_manual_patch(
        runtime_of(request), stage_id, operator=operator_of(request), patch=payload.to_patch(), client_key=key
    )


@router.post(
    "/stages/{stage_id}/questions",
    status_code=201,
    response_model=WS.QuestionCreatedView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True),
)
def add_stage_question(request: Request, stage_id: str, payload: RQ.StageQuestionBody = Body(...)) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.QUESTION_ANSWER)
    from factory_control.models import Stage as StageModel
    from factory_control.ports.dto import QuestionDraft

    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        stage = ctx.session.get(StageModel, stage_id)
        if stage is None:
            raise NotFoundControlError(f"unknown stage {stage_id}")
        workflow = S.get_workflow(ctx.session, stage.workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
        created = Q.record_questions(
            ctx.bind(workflow_id=workflow.id, stage_id=stage.id),
            stage=stage,
            questions=[
                QuestionDraft(
                    local_id=payload.local_id or "user",
                    text=payload.text,
                    severity=payload.severity,
                    affects=list(payload.affects),
                    suggestions=list(payload.suggestions),
                    blocking=payload.blocking,
                )
            ],
            source_kind="USER",
        )
        return {"questionId": created[0].id if created else None}


def _start(request: Request, stage_id: str, *, rerun: bool) -> dict[str, Any]:
    """Accept a stage run: the same three fields whichever engine executes it.

    The engine name stays inside the process (S5IF-G06) — which executor a deployment
    chose is not information a client can act on, and a response that varied with it
    would force S5 to branch on deployment topology.  The outcome is observed through
    SSE and ``GET /stages/{id}``.
    """
    runtime = runtime_of(request)
    engine = runtime.engine
    result: dict[str, Any]
    if engine is None:
        result = STEPS.start_stage(
            runtime,
            stage_id,
            operator=operator_of(request),
            rerun=rerun,
            language=language_of(request),
        )
    else:
        result = engine.start_stage(
            stage_id,
            operator=operator_of(request),
            rerun=rerun,
            language=language_of(request),
        )
    return {
        "stageId": result.get("stageId") or stage_id,
        "stepRunId": result.get("stepRunId") or result.get("stageRunId"),
        "accepted": bool(result.get("accepted", True)),
    }
