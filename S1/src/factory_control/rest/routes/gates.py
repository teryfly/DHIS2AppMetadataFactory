"""Gates and questions (SRS 8.1, CP-GATE-*, CP-Q-*)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request

from factory_control import iam
from factory_control import store as S
from factory_control.rest import route_meta
from factory_control.rest.deps import (
    authorize,
    authorize_gate_approval,
    authorize_question,
    language_of,
    operator_of,
    runtime_of,
)
from factory_control.rest.schemas import gates as GS
from factory_control.rest.schemas import requests as RQ
from factory_control.services import gates as G
from factory_control.services import questions as Q

router = APIRouter(prefix="/api/v1", tags=["gates"])


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    """Header references and requirement ids (S5IF-G05 / G11)."""
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.get("/workflows/{workflow_id}/gates",
    response_model=GS.GateList)
def list_gates(request: Request, workflow_id: str) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
        gates = S.gates_of(ctx.session, workflow_id)
        return {"items": [G.gate_view(g, S.approvals_of(ctx.session, g.id)) for g in gates]}


@router.get("/gates",
    response_model=GS.GateSummaryList,
    openapi_extra=route_meta.openapi_extra("S5IF-FLD03"))
def list_all_gates(
    request: Request,
    status: str | None = None,
    mine: bool = Query(default=False),
    projectId: str | None = Query(default=None),  # noqa: N803
    type: str | None = Query(default=None),  # noqa: A002
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sortBy: str | None = Query(default=None),  # noqa: N803
    sortDir: str | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    """S5IF-FLD03: one request for the Dashboard's Gate panel, across projects."""
    authorize(request, iam.PROJECT_VIEW, projectId)
    from factory_control.rest.paging import sort_direction, sortable

    return G.gate_summaries(
        runtime_of(request),
        operator=operator_of(request),
        mine=mine,
        status=status,
        project_id=projectId,
        gate_type=type,
        sort_by=sortable(sortBy, S.GATE_SORT_COLUMNS),
        sort_dir=sort_direction(sortDir),
        limit=limit,
        offset=offset,
    )


@router.get("/gates/{gate_id}", response_model=GS.GateView, openapi_extra=_extras("S5IF-FLD07"))
def get_gate(request: Request, gate_id: str) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        gate = S.get_gate(ctx.session, gate_id)
        authorize(request, iam.PROJECT_VIEW, None)
        return G.gate_view(gate, S.approvals_of(ctx.session, gate_id))


@router.get(
    "/gates/{gate_id}/explanation",
    response_model=GS.GateExplanationView,
    openapi_extra=_extras("S5IF-G03", "S5IF-BP13", "S5IF-FLD07"),
)
def gate_explanation(request: Request, gate_id: str) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        gate = S.get_gate(ctx.session, gate_id)
        workflow = S.get_workflow(ctx.session, gate.workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
        ctx.project_id = workflow.project_id
        return G.explanation(ctx, gate)


@router.post(
    "/gates/{gate_id}/approve",
    response_model=GS.GateDecisionView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-G03", mutating=True, idempotent=True),
)
def approve_gate(request: Request, gate_id: str, payload: RQ.ApproveGateBody = Body(...)) -> dict[str, Any]:
    authorize_gate_approval(request, gate_id, payload.gate_type)
    return G.approve_gate(
        runtime_of(request),
        gate_id,
        operator=operator_of(request),
        seen_bound_hashes=list(payload.seen_bound_hashes),
        warning_dispositions=[w.model_dump(by_alias=True) for w in payload.warning_dispositions],
        confirmations=[c.model_dump(by_alias=True) for c in payload.confirmations],
        comment=payload.comment,
    )


@router.post(
    "/gates/{gate_id}/reject",
    response_model=GS.GateRejectionView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-FLD02", mutating=True, idempotent=True),
)
def reject_gate(request: Request, gate_id: str, payload: RQ.RejectGateBody = Body(...)) -> dict[str, Any]:
    authorize_gate_approval(request, gate_id, payload.gate_type)
    return G.reject_gate(
        runtime_of(request),
        gate_id,
        operator=operator_of(request),
        reason=payload.reason,
        warning_dispositions=[w.model_dump(by_alias=True) for w in payload.warning_dispositions],
    )


@router.get(
    "/workflows/{workflow_id}/questions",
    response_model=GS.QuestionList,
    openapi_extra=_extras("S5IF-FLD08"),
)
def list_questions(request: Request, workflow_id: str, status: str | None = None) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
        rows = S.questions_of(ctx.session, workflow_id, stage_id=None, status=status)
        return {"items": [Q.question_view(q) for q in rows]}


@router.get("/questions", response_model=GS.QuestionPage, openapi_extra=_extras("S5IF-FLD08"))
def list_all_questions(
    request: Request,
    status: str | None = None,
    projectId: str | None = Query(default=None),  # noqa: N803
    workflowId: str | None = Query(default=None),  # noqa: N803
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """CP-Q-002 across projects: what is unanswered, newest first.

    Reads only. The answer endpoint stays per question, so a list can never be mistaken for
    permission to act on what it shows (UI-BR-01: availability comes from ``allowed-actions``).
    """
    authorize(request, iam.PROJECT_VIEW, projectId)
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        rows, total = S.questions_across(
            ctx.session,
            status=status,
            project_id=projectId,
            workflow_id=workflowId,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [Q.question_view(q) for q in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.post(
    "/questions/answers",
    response_model=GS.QuestionnaireAnswerView,
    response_model_exclude_none=True,
    summary="Answer a whole questionnaire once, then resume the run once",
    openapi_extra=_extras("S5IF-FLD08", mutating=True, idempotent=True),
)
def answer_questions(
    request: Request, payload: RQ.AnswerQuestionsBody = Body(...)
) -> dict[str, Any]:
    """The review screen's one submit (CP-Q-005).

    Availability is still per question: every question named must be open and the operator
    must hold ``question.answer`` on its project.  The endpoint is idempotent because a
    retried submit with the same key must not write a second answer.
    """
    for item in payload.answers:
        authorize_question(request, item.question_id, iam.QUESTION_ANSWER)
    return Q.answer_questions(
        runtime_of(request),
        payload.as_service_answers(),
        operator=operator_of(request),
        language=language_of(request),
    )


@router.get("/questions/{question_id}", response_model=GS.QuestionView, openapi_extra=_extras("S5IF-FLD08"))
def get_question(request: Request, question_id: str) -> dict[str, Any]:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        row = S.get_question(ctx.session, question_id)
        authorize(request, iam.PROJECT_VIEW, row.project_id)
        return Q.question_view(row)


@router.post(
    "/questions/{question_id}/answer",
    response_model=GS.AnswerView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-FLD08", mutating=True, idempotent=True),
)
def answer_question(request: Request, question_id: str, payload: RQ.AnswerQuestionBody = Body(...)) -> dict[str, Any]:
    authorize_question(request, question_id, iam.QUESTION_ANSWER)
    return Q.answer_question(
        runtime_of(request),
        question_id,
        operator=operator_of(request),
        suggestion_id=payload.suggestion_id,
        suggestion_ids=list(payload.suggestion_ids),
        custom_text=payload.custom_text,
        language=language_of(request),
    )
