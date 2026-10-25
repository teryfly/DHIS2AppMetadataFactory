"""Projects, their target environments, settings and business inputs (SRS 8.1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, Form, Query, Request, UploadFile

from factory_control import iam
from factory_control import store as S
from factory_control.foundation import DataClassification
from factory_control.rest import route_meta
from factory_control.rest.deps import (
    authorize,
    operator_of,
    runtime_of,
    user_id_of,
    visible_project_ids,
)
from factory_control.rest.schemas import common as CS
from factory_control.rest.schemas import inputs as IS
from factory_control.rest.schemas import projects as PS
from factory_control.rest.schemas import requests as RQ
from factory_control.services import inputs as INPUT
from factory_control.services import project as PRJ

router = APIRouter(prefix="/api/v1", tags=["projects"])

_ERRORS = route_meta.error_responses(400, 403, 404, 409, 422, 503)


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    """Header references and requirement ids (S5IF-G05 / G11)."""
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.post("/projects", status_code=201,
    response_model=PS.ProjectView)
def create_project(request: Request, payload: RQ.CreateProjectBody = Body(...)) -> dict[str, Any]:
    authorize(request, iam.PROJECT_EDIT)
    authorize(request, "project.create")
    return PRJ.create_project(
        runtime_of(request),
        PRJ.CreateProjectRequest.from_payload(payload.to_payload()),
        operator=operator_of(request),
        creator_user_id=user_id_of(request),
    )


@router.get("/projects", response_model=PS.ProjectPage)
def list_projects(
    request: Request,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    includeArchived: bool = Query(default=True),  # noqa: N803 - public API casing
    name: str | None = None,
    ownerName: str | None = None,  # noqa: N803
    sortBy: str | None = Query(default=None),  # noqa: N803
    sortDir: str | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    # FR-SEC-004: this listing has no project in its path to authorize against, so it answers the
    # visibility question instead — an organization-scope grant sees the whole organization, anyone
    # else sees the projects they belong to.
    visible = visible_project_ids(request)
    from factory_control.rest.paging import sort_direction, sortable

    return PRJ.list_projects(
        runtime_of(request),
        operator=operator_of(request),
        include_archived=includeArchived,
        name_like=name,
        owner=ownerName,
        limit=limit,
        offset=offset,
        sort_by=sortable(sortBy, S.PROJECT_SORT_COLUMNS),
        sort_dir=sort_direction(sortDir),
        only_project_ids=visible,
    )


@router.get("/projects/{project_id}", response_model=PS.ProjectView, response_model_exclude_none=True)
def get_project(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return PRJ.get_project_view(runtime_of(request), project_id, operator=operator_of(request))


@router.patch(
    "/projects/{project_id}",
    response_model=PS.ProjectView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-G02", mutating=True),
)
def patch_project(request: Request, project_id: str, payload: RQ.UpdateProjectBody = Body(...)) -> dict[str, Any]:
    authorize(request, iam.PROJECT_EDIT, project_id)
    return PRJ.update_project(runtime_of(request), project_id, operator=operator_of(request), changes=payload.to_changes())


@router.post(
    "/projects/{project_id}/archive",
    # Archiving answers with the project as it now stands: UI-PRJ-003 re-reads the header
    # (archived, milestone) rather than a bare flag.
    response_model=PS.ProjectView,
    openapi_extra=_extras(mutating=True),
)
def archive_project(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_ARCHIVE, project_id)
    return PRJ.archive_project(runtime_of(request), project_id, operator=operator_of(request))


@router.post(
    "/projects/{project_id}/upgrade-mode",
    response_model=PS.UpgradeModeView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True, idempotent=True),
)
def upgrade_mode(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_UPGRADE_MODE, project_id)
    from factory_control.services.transfers import upgrade_delivery_mode

    return upgrade_delivery_mode(runtime_of(request), project_id, operator=operator_of(request))


@router.delete(
    "/projects/{project_id}",
    status_code=202,
    response_model=PS.DeleteAcceptedView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True),
)
def delete_project(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_DELETE, project_id)
    from factory_control.services.transfers import delete_project as do_delete

    return do_delete(runtime_of(request), project_id, operator=operator_of(request))


@router.get(
    "/projects/{project_id}/export",
    status_code=202,
    response_model=CS.AcceptanceView,
    summary="Accept a project export — the answer names the PROJECT_EXPORT artifact",
    response_model_exclude_none=True,
    openapi_extra=_extras("CP-API-012", "S5IF-FLD09"),
)
def export_project(request: Request, project_id: str) -> dict[str, Any]:
    """CP-PRJ-008 runs as a step, so the body says only what was started (§6.5).

    The export's own record is the PROJECT_EXPORT artifact it registers, and that is the
    id handed back: ``GET /artifacts/{id}`` answers whether the pack is there yet, and
    ``?download=true`` fetches it once it is.
    """
    authorize(request, iam.PROJECT_EXPORT, project_id)
    from factory_control.services.transfers import export_project

    bundle = export_project(runtime_of(request), project_id, operator=operator_of(request))
    return {"stepRunId": str(bundle["exportArtifactId"]), "accepted": True}


@router.post(
    "/projects/import",
    status_code=202,
    response_model=CS.AcceptanceView,
    summary="Accept a project import — the answer names the project being restored",
    openapi_extra=_extras("CP-API-012", mutating=True, idempotent=True),
)
def import_project(request: Request, payload: RQ.ImportProjectBody = Body(...)) -> dict[str, Any]:
    """CP-PRJ-009: the restored project is the step's record.

    It arrives with no target environment and its design line back in draft (SRS 6.5),
    which S5 reads off ``GET /projects/{id}`` and ``GET /projects/{id}/target-environments``
    rather than off this answer.
    """
    authorize(request, iam.PROJECT_IMPORT)
    from factory_control.services.transfers import import_project

    restored = import_project(
        runtime_of(request), artifact_id=payload.artifact_id, operator=operator_of(request)
    )
    return {"stepRunId": str(restored["projectId"]), "accepted": True}


@router.get(
    "/projects/{project_id}/target-environments",
    response_model=PS.TargetEnvironmentListView,
    openapi_extra=_extras(),
)
def get_target_environments(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    with runtime_of(request).transaction(operator=operator_of(request), project_id=project_id) as ctx:
        rows = S.target_environments(ctx.session, project_id)
        return {
            "items": [
                {
                    "environmentKey": r.environment_key,
                    "promotionOrder": r.promotion_order,
                    "isDesignReference": r.is_design_reference,
                }
                for r in rows
            ]
        }


@router.put(
    "/projects/{project_id}/target-environments",
    response_model=PS.TargetEnvironmentReplaceView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-G02", mutating=True),
)
def put_target_environments(request: Request, project_id: str, payload: RQ.TargetEnvironmentListBody = Body(...)) -> dict[str, Any]:
    authorize(request, iam.PROJECT_EDIT, project_id)
    targets = [
        PRJ.TargetEnvironment(
            environment_key=t.environment_key,
            promotion_order=t.promotion_order,
            is_design_reference=t.is_design_reference,
        )
        for t in payload.target_environments
    ]
    return PRJ.replace_target_environments(
        runtime_of(request), project_id, operator=operator_of(request), targets=targets
    )


@router.get(
    "/projects/{project_id}/settings",
    response_model=PS.ProjectSettingsView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-FLD10"),
)
def get_project_settings(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    with runtime_of(request).transaction(operator=operator_of(request), project_id=project_id) as ctx:
        row = S.project_settings(ctx.session, project_id)
        return {
            "gateReminderHours": row.gate_reminder_hours,
            "notificationEmails": list(row.notification_emails or []),
            "notificationWebhookRef": row.notification_webhook_ref,
        }


@router.put(
    "/projects/{project_id}/settings",
    response_model=PS.UpdatedIdsView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-FLD10", mutating=True),
)
def put_project_settings(request: Request, project_id: str, payload: RQ.ProjectSettingsBody = Body(...)) -> dict[str, Any]:
    authorize(request, iam.PROJECT_EDIT, project_id)
    with runtime_of(request).transaction(operator=operator_of(request), project_id=project_id) as ctx:
        row = S.project_settings(ctx.session, project_id)
        applied: list[str] = []
        if payload.gate_reminder_hours is not None:
            row.gate_reminder_hours = payload.gate_reminder_hours
            applied.append("gateReminderHours")
        if payload.notification_emails is not None:
            row.notification_emails = [str(e) for e in payload.notification_emails]
            applied.append("notificationEmails")
        if payload.notification_webhook_ref is not None:
            row.notification_webhook_ref = payload.notification_webhook_ref
            applied.append("notificationWebhookRef")
        ctx.session.flush()
        from factory_control.services.recording import write_audit

        write_audit(
            ctx,
            event_type="cp.project.updated",
            object_type="ProjectSetting",
            object_id=project_id,
            project_id=project_id,
            after={"settings": payload.to_payload()},
        )
        # The keys that actually changed.  A submitted key whose value was absent was not
        # written, and telling the caller it was would make the audit trail and the answer
        # disagree about the same request.
        return {"projectId": project_id, "updated": applied}


@router.post(
    "/projects/{project_id}/inputs",
    status_code=201,
    response_model=PS.UploadInputView,
    openapi_extra=_extras("CP-API-009", mutating=True),
)
async def upload_input(
    request: Request,
    project_id: str,
    file: UploadFile = File(...),
    mediaType: str | None = Form(default=None),  # noqa: N803
    dataClassification: DataClassification = Form(...),  # noqa: N803
    phiDeclared: bool = Form(default=False),  # noqa: N803
) -> dict[str, Any]:
    """CP-API-009: the data classification is declared here, by the uploader (FR-REQ-001)."""
    authorize(request, iam.INPUT_UPLOAD, project_id)
    data = await file.read()
    return INPUT.record_input(
        runtime_of(request),
        project_id,
        filename=file.filename or "input",
        media_type=mediaType or file.content_type,
        data=data,
        operator=operator_of(request),
        data_classification=dataClassification,
        phi_declared=phiDeclared,
    )


@router.get(
    "/projects/{project_id}/inputs",
    response_model=IS.InputPage,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-INP02"),
)
def list_inputs(request: Request, project_id: str, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return INPUT.list_inputs(
        runtime_of(request), project_id, operator=operator_of(request), limit=limit, offset=offset
    )
