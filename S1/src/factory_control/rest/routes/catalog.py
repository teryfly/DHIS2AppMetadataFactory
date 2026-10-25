"""Artifacts, audit, notifications and system settings (SRS 8.1, CP-API-004/005)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Query, Request, Response

from factory_control import iam
from factory_control import store as S
from factory_control.errors import NotFoundControlError
from factory_control.rest import route_meta
from factory_control.rest.deps import (
    authorize,
    authorize_execution,
    authorize_notification,
    operator_of,
    runtime_of,
)
from factory_control.rest.download import content_disposition, download_filename
from factory_control.rest.schemas import catalog as CS
from factory_control.rest.schemas import projects as PS
from factory_control.rest.schemas import requests as RQ
from factory_control.rest.schemas import workflow as WS
from factory_control.services import notifications as NTF
from factory_control.services import workflow as WF
from factory_control.settings import SETTING_KEYS, load_settings_snapshot, set_setting

router = APIRouter(prefix="/api/v1", tags=["catalog"])


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


def artifact_view(artifact: Any) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "projectId": artifact.project_id,
        "workflowId": artifact.workflow_id,
        "type": artifact.type,
        "version": artifact.version,
        "status": artifact.status.value,
        "contentHash": artifact.content_hash,
        "size": artifact.size,
        "mediaType": artifact.media_type,
        "dataClassification": artifact.data_classification.value,
        "creatorSubsystem": artifact.creator_subsystem,
        "creatorOperator": artifact.creator_operator,
        "sourceArtifactIds": list(artifact.source_artifact_ids or []),
        "prevVersionId": artifact.prev_version_id,
        "contentPurged": artifact.content_purged,
        "createdAt": artifact.created_at,
    }


def setting_value(resolved: Any, key: str) -> Any:
    return (resolved.values.get(key) or {}).get("value")


@router.get(
    "/allowed-actions",
    response_model=WS.AllowedActionsView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-G10", "FR-UI-009"),
)
def platform_allowed_actions(request: Request) -> dict[str, Any]:
    """Availability for the Administration pages, which have no workflow to ask with.

    Environments, Governance and Settings are reached before any project exists. Until this
    existed they had no source for whether a control was usable and had to decide locally --
    which for an emergency lock or a policy relaxation is precisely what FR-UI-009 forbids.
    """
    authorize(request, iam.PROJECT_VIEW)
    return WF.allowed_actions_for_platform(runtime_of(request), operator=operator_of(request))


@router.get(
    "/workflows/{workflow_id}/artifacts",
    response_model=CS.ArtifactPage,
    response_model_exclude_none=True,
)
def list_artifacts(
    request: Request,
    workflow_id: str,
    type: str | None = None,  # noqa: A002
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    sortBy: str | None = Query(default=None),  # noqa: N803
    sortDir: str | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    """S5IF-G04: a cursor list, sorted by the database rather than in the handler."""
    from factory_control.foundation import ArtifactQuery, query_artifacts
    from factory_control.rest.paging import clamp_limit, decode_cursor, next_cursor, sort_direction, sortable

    order_by = {
        "createdAt": "created_at",
        "version": "version",
        "type": "type",
        "size": "size",
        "status": "status",
    }
    column = sortable(sortBy, order_by)
    descending = sort_direction(sortDir) == "DESC"
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)

    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.ARTIFACT_READ, workflow.project_id)
        page = query_artifacts(
            ArtifactQuery(
                project_id=workflow.project_id,
                workflow_id=workflow_id,
                type=type,
                order_by=column,
                descending=descending,
                limit=page_size,
                offset=offset,
            ),
            session=ctx.session,
        )
        return {
            "items": [artifact_view(a) for a in page.items],
            "limit": page_size,
            "nextCursor": next_cursor(offset=offset, returned=len(page.items), total=page.total),
        }


@router.get(
    "/artifacts/{artifact_id}",
    response_model=CS.ArtifactView,
    response_model_exclude_none=True,
    openapi_extra=_extras(),
)
def get_artifact(request: Request, artifact_id: str) -> dict[str, Any]:
    from factory_control.foundation import get_artifact as s0_get

    _authorize_artifact_read(request, artifact_id)
    artifact = s0_get(artifact_id)
    if artifact is None:
        raise NotFoundControlError(f"unknown artifact {artifact_id}")
    return artifact_view(artifact)


@router.get(
    "/artifacts/{artifact_id}/content",
    # Raw bytes with the classification attached, so the body is not a JSON component
    # (S5IF-G05): the document has to say what it actually sends.
    response_class=Response,
    responses={
        200: {
            "description": "The artifact's bytes, inline or as a file (S5IF-FLD09).",
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            "headers": {
                "X-Data-Classification": {
                    "description": "The S0 classification of the returned content.",
                    "schema": {"type": "string"},
                },
                "Content-Disposition": {
                    "description": (
                        "``attachment`` with a filename only when ``download=true``; absent "
                        "for an inline read."
                    ),
                    "schema": {"type": "string"},
                },
            },
        },
        410: {"$ref": "#/components/responses/Error"},
    },
)
def artifact_content(
    request: Request, artifact_id: str, download: bool = Query(default=False)
) -> Response:
    """S5IF-FLD09 【裁决】: one exit per artifact, the file name asked for on the same one.

    A second ``/download`` endpoint would have to duplicate the classification header,
    the purge verdict and the authorization, and would then be free to drift from them.
    """
    from factory_control.foundation import get_artifact as s0_get
    from factory_control.foundation import read_content_bytes

    _authorize_artifact_read(request, artifact_id)
    artifact = s0_get(artifact_id)
    if artifact is None:
        raise NotFoundControlError(f"unknown artifact {artifact_id}")
    data, classification, media_type = read_content_bytes(artifact_id)
    headers = {"X-Data-Classification": classification.value}
    if download:
        headers["Content-Disposition"] = content_disposition(
            download_filename(artifact, project_name=_project_name(request, artifact))
        )
    return Response(content=data, media_type=media_type, headers=headers)


def _project_name(request: Request, artifact: Any) -> str | None:
    """The project's own name, for the export file name — read, never guessed."""
    project_id = getattr(artifact, "project_id", None)
    if not project_id:
        return None
    from factory_control.models import Project

    with runtime_of(request).transaction(operator=operator_of(request), project_id=project_id) as ctx:
        project = ctx.session.get(Project, project_id)
        return project.name if project is not None else None


@router.get(
    "/artifacts/{artifact_id}/diff",
    response_model=CS.ArtifactDiffView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-BP12"),
)
def artifact_diff(request: Request, artifact_id: str, against: str = Query(...)) -> dict[str, Any]:
    from factory_control.foundation import diff

    _authorize_artifact_read(request, artifact_id)
    result = diff(artifact_id, against)
    return {
        "kind": result.kind,
        "aId": result.a_id,
        "bId": result.b_id,
        "changed": result.changed,
        "lines": list(result.lines),
    }


@router.get(
    "/artifacts/{artifact_id}/lineage",
    response_model=CS.LineageView,
    response_model_exclude_none=True,
    openapi_extra=_extras(),
)
def artifact_lineage(request: Request, artifact_id: str) -> dict[str, Any]:
    from factory_control.foundation import lineage

    _authorize_artifact_read(request, artifact_id)
    chain = lineage(artifact_id)
    return {"artifactId": chain.artifact_id, "sources": list(chain.sources), "derived": list(chain.derived)}


@router.post(
    "/artifacts/{artifact_id}/purge",
    response_model=CS.ArtifactPurgeView,
    openapi_extra=_extras(mutating=True),
)
def artifact_purge(request: Request, artifact_id: str) -> dict[str, Any]:
    authorize(request, iam.ARTIFACT_PURGE)
    from factory_control.services.transfers import purge_artifact

    return purge_artifact(runtime_of(request), artifact_id, operator=operator_of(request))


@router.post(
    "/artifacts/{artifact_id}/reproduce",
    response_model=CS.ReproduceView,
    response_model_exclude_none=True,
    openapi_extra=_extras(mutating=True),
)
def artifact_reproduce(request: Request, artifact_id: str) -> dict[str, Any]:
    _authorize_artifact_read(request, artifact_id)
    return runtime_of(request).blueprint.reproduce(artifact_id)


@router.get("/projects/{project_id}/audit", response_model=CS.AuditPage, response_model_exclude_none=True)
def project_audit(
    request: Request,
    project_id: str,
    limit: int = 50,
    cursor: str | None = Query(default=None),
    eventType: str | None = None,  # noqa: N803
    operatorName: str | None = None,  # noqa: N803
    objectId: str | None = None,  # noqa: N803
    occurredAfter: datetime | None = None,  # noqa: N803
    occurredBefore: datetime | None = None,  # noqa: N803
) -> dict[str, Any]:
    """UI-OPS-002: object, event type, operator and time narrow the query, not the page."""
    authorize(request, iam.AUDIT_VIEW, project_id)
    from factory_control.services.audit_query import query_for_project

    return query_for_project(
        runtime_of(request),
        project_id,
        operator=operator_of(request),
        limit=limit,
        cursor=cursor,
        event_type=eventType,
        operator_name=operatorName,
        object_id=objectId,
        occurred_after=occurredAfter,
        occurred_before=occurredBefore,
    )


@router.get("/audit", response_model=CS.AuditPage, response_model_exclude_none=True)
def audit_list(
    request: Request,
    eventType: str | None = None,  # noqa: N803
    operatorName: str | None = None,  # noqa: N803
    objectId: str | None = None,  # noqa: N803
    occurredAfter: datetime | None = None,  # noqa: N803
    occurredBefore: datetime | None = None,  # noqa: N803
    limit: int = 50,
    cursor: str | None = Query(default=None),
) -> dict[str, Any]:
    authorize(request, iam.AUDIT_VIEW)
    from factory_control.services.audit_query import query_all

    return query_all(
        runtime_of(request),
        operator=operator_of(request),
        event_type=eventType,
        operator_name=operatorName,
        object_id=objectId,
        occurred_after=occurredAfter,
        occurred_before=occurredBefore,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/audit/verify",
    response_model=CS.AuditVerifyView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-G02", mutating=True),
)
def audit_verify(request: Request, payload: RQ.AuditVerifyBody = Body(default={})) -> dict[str, Any]:
    authorize(request, iam.AUDIT_VERIFY)
    from factory_control.services.audit_query import verify

    return verify(
        runtime_of(request),
        operator=operator_of(request),
        first_seq=payload.first_sequence,
        last_seq=payload.last_sequence,
    )


@router.get(
    "/audit/commit-trace/{execution_id}",
    response_model=CS.CommitTraceView,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-G03"),
)
def commit_trace(request: Request, execution_id: str) -> dict[str, Any]:
    authorize_execution(request, execution_id, iam.AUDIT_VIEW)
    from factory_control.services.audit_query import commit_trace as trace

    return trace(runtime_of(request), execution_id, operator=operator_of(request))


@router.get(
    "/notifications",
    response_model=CS.NotificationPage,
    response_model_exclude_none=True,
    openapi_extra=_extras("S5IF-G04"),
)
def list_notifications(
    request: Request,
    projectId: str | None = None,  # noqa: N803
    unreadOnly: bool = False,  # noqa: N803
    mine: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, projectId)
    return NTF.list_notifications(
        runtime_of(request),
        operator=operator_of(request),
        project_id=projectId,
        unread_only=unreadOnly,
        mine=mine,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/notifications/{notification_id}/read",
    response_model=CS.MarkReadView,
    openapi_extra=_extras(mutating=True),
)
def read_notification(request: Request, notification_id: str) -> dict[str, Any]:
    authorize_notification(request, notification_id, iam.PROJECT_VIEW)
    return NTF.mark_read(runtime_of(request), notification_id, operator=operator_of(request))


@router.get("/settings", response_model=CS.SystemSettingList,
    responses=route_meta.error_responses(400, 403),
    openapi_extra=_extras("S5IF-FLD10"),
)
def get_system_settings(request: Request) -> dict[str, Any]:
    """The seven keys with their values, plus the same seven as one typed object.

    The list is what an audit or a settings table iterates over; ``values`` is what a
    form generator reads, because a per-key type cannot be recovered from a list of
    pairs (S5IF-FLD10).
    """
    authorize(request, iam.SETTINGS_MANAGE)
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        resolved = load_settings_snapshot(ctx.session)
        return {
            "items": [{"key": key, "value": setting_value(resolved, key)} for key in sorted(SETTING_KEYS)],
            "values": _settings_view(resolved),
        }


@router.get("/upload-limits", response_model=CS.UploadLimitsView,
    openapi_extra=_extras("S5IF-FLD10"),
)
def upload_limits(request: Request, projectId: str | None = Query(default=None)) -> dict[str, Any]:  # noqa: N803
    """S5IF-FLD10: the browser pre-check must read the same two numbers S1 enforces.

    Kept off ``GET /settings`` because that one needs ``settings.manage``: an operator
    uploading a requirements file is not an administrator, and UI-REQS-001 cannot show a
    limit it is not allowed to ask for.

    ``input.upload`` is a name the project roles hold, so the check needs a project to be
    answerable: the console asks with the project it is about to upload into.  Without one the
    check falls back to the organisation-wide roles it always used — the numbers are global
    settings, and a page must not be unable to show a limit because the caller forgot to say
    where it was uploading.
    """
    authorize(request, iam.INPUT_UPLOAD, projectId)
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        policy = load_settings_snapshot(ctx.session).upload_policy()
        return {"maxUploadBytes": policy.max_bytes, "allowedUploadTypes": list(policy.allowed_types)}


def _settings_view(resolved: Any) -> dict[str, Any]:
    """The typed sibling of the key/value list, from the accessors that own the defaults."""
    return {
        "idempotencyRetentionHours": resolved.idempotency_retention_hours(),
        "eventRetentionDays": resolved.event_retention_days(),
        "defaultGateReminderHours": resolved.gate_reminder_hours(),
        "maxUploadBytes": resolved.upload_policy().max_bytes,
        "allowedUploadTypes": list(resolved.upload_policy().allowed_types),
        "archivedProjectRetentionDays": resolved.archived_retention_days(),
        "platformNotificationChannels": resolved.notification_channels(),
        "stageAutoContinue": list(resolved.auto_continue_stages()),
    }


@router.put("/settings", response_model=PS.SettingsUpdateView,
    responses=route_meta.error_responses(400, 403, 404, 422),
    openapi_extra=_extras("S5IF-FLD10", mutating=True),
)
def put_system_settings(request: Request, payload: RQ.SystemSettingsBody = Body(...)) -> dict[str, Any]:
    authorize(request, iam.SETTINGS_MANAGE)
    updates = payload.to_updates()
    updated: list[str] = []
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        from factory_control.foundation import current_context_or_default

        for key, value in updates.items():
            if key not in SETTING_KEYS:
                raise NotFoundControlError(f"unknown system setting {key}")
            set_setting(ctx.session, key, value, context=current_context_or_default())
            updated.append(key)
    return {"updated": sorted(updated)}


def _authorize_artifact_read(request: Request, artifact_id: str) -> None:
    """Kept as the module-local spelling; the decision lives in ``rest.artifact_access``."""
    from factory_control.rest.artifact_access import authorize_artifact_read

    authorize_artifact_read(request, artifact_id)
