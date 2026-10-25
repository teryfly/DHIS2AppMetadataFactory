"""Change requests across their lifecycle (SRS 8.1, CP-CR-*)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from factory_control import iam
from factory_control.rest import route_meta
from factory_control.rest.deps import authorize, authorize_change_request, operator_of, runtime_of
from factory_control.rest.schemas import change_requests as CRS
from factory_control.rest.schemas import requests as RQ
from factory_control.services import change_requests as CR

router = APIRouter(prefix="/api/v1", tags=["change-requests"])


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    """Header references and requirement ids (S5IF-G05 / G11)."""
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.post("/projects/{project_id}/change-requests", status_code=201,
    response_model=CRS.ChangeRequestView)
def create_cr(request: Request, project_id: str, payload: RQ.CreateChangeRequestBody = Body(...)) -> dict[str, Any]:
    authorize(request, iam.CHANGE_REQUEST_CREATE, project_id)
    return CR.create_change_request(
        runtime_of(request),
        project_id,
        operator=operator_of(request),
        reason=payload.reason,
        affected_business_keys=list(payload.affected_business_keys),
        deletion_candidates=list(payload.deletion_candidates),
    )


@router.get(
    "/projects/{project_id}/change-requests",
    response_model=CRS.ChangeRequestList,
    openapi_extra=_extras(),
)
def list_crs(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return {"items": CR.list_change_requests(runtime_of(request), project_id, operator=operator_of(request))}


@router.get("/change-requests/{cr_id}", response_model=CRS.ChangeRequestView)
def get_cr(request: Request, cr_id: str) -> dict[str, Any]:
    authorize_change_request(request, cr_id, iam.PROJECT_VIEW)
    return CR.get_change_request(runtime_of(request), cr_id, operator=operator_of(request))


@router.post("/change-requests/{cr_id}/impact", response_model=CRS.ChangeRequestImpactView, response_model_exclude_none=True, openapi_extra=_extras(mutating=True))
def cr_impact(request: Request, cr_id: str) -> dict[str, Any]:
    authorize_change_request(request, cr_id, iam.CHANGE_REQUEST_CREATE)
    return CR.analyze(runtime_of(request), cr_id, operator=operator_of(request))


@router.post("/change-requests/{cr_id}/approve", response_model=CRS.ChangeRequestView, response_model_exclude_none=True, openapi_extra=_extras(mutating=True, idempotent=True))
def cr_approve(request: Request, cr_id: str) -> dict[str, Any]:
    authorize_change_request(request, cr_id, iam.CHANGE_REQUEST_APPROVE)
    return CR.approve(runtime_of(request), cr_id, operator=operator_of(request))


@router.post("/change-requests/{cr_id}/reject", response_model=CRS.ChangeRequestView, response_model_exclude_none=True, openapi_extra=_extras(mutating=True, idempotent=True))
def cr_reject(request: Request, cr_id: str, payload: RQ.RejectChangeRequestBody = Body(default={})) -> dict[str, Any]:
    authorize_change_request(request, cr_id, iam.CHANGE_REQUEST_APPROVE)
    return CR.reject(runtime_of(request), cr_id, operator=operator_of(request), reason=payload.reason)


@router.post("/change-requests/{cr_id}/start", response_model=CRS.ChangeRequestView, response_model_exclude_none=True, openapi_extra=_extras(mutating=True, idempotent=True))
def cr_start(request: Request, cr_id: str) -> dict[str, Any]:
    authorize_change_request(request, cr_id, iam.CHANGE_REQUEST_APPROVE)
    return CR.start(runtime_of(request), cr_id, operator=operator_of(request))


@router.post("/change-requests/{cr_id}/complete", response_model=CRS.ChangeRequestView, response_model_exclude_none=True, openapi_extra=_extras(mutating=True, idempotent=True))
def cr_complete(request: Request, cr_id: str) -> dict[str, Any]:
    authorize_change_request(request, cr_id, iam.CHANGE_REQUEST_APPROVE)
    return CR.complete(runtime_of(request), cr_id, operator=operator_of(request))


@router.post("/change-requests/{cr_id}/cancel", response_model=CRS.ChangeRequestView, response_model_exclude_none=True, openapi_extra=_extras(mutating=True, idempotent=True))
def cr_cancel(request: Request, cr_id: str) -> dict[str, Any]:
    authorize_change_request(request, cr_id, iam.CHANGE_REQUEST_CREATE)
    return CR.cancel(runtime_of(request), cr_id, operator=operator_of(request))
