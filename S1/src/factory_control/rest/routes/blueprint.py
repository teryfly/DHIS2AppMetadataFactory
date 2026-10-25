"""Blueprint, Registry, Schema and Decision endpoints (S5IF-BP01 … BP15)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request, Response

from factory_control import iam
from factory_control import store as S
from factory_control.rest import route_meta
from factory_control.rest.deps import (
    authorize,
    authorize_run,
    authorize_workflow,
    operator_of,
    runtime_of,
)
from factory_control.rest.download import content_disposition, package_file_filename
from factory_control.rest.schemas import blueprint as BS
from factory_control.services import blueprint_reads as BP

router = APIRouter(prefix="/api/v1", tags=["blueprint"])

_ERRORS = route_meta.error_responses(400, 404, 409, 422, 502, 503)


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.get(
    "/projects/{project_id}/blueprint/section-schemas",
    response_model=BS.SectionSchemaIndexView,
    summary="List the sections a Blueprint document may contain",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP01"),
)
def section_schema_index(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.section_index(runtime_of(request))


@router.get(
    "/projects/{project_id}/blueprint/section-schemas/{section}",
    response_model=BS.SectionSchemaView,
    summary="One self-contained Draft 2020-12 section document",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP02"),
)
def section_schema(
    request: Request, project_id: str, section: str, schemaVersion: int | None = None  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.section_schema(runtime_of(request), section, schemaVersion)


@router.get(
    "/projects/{project_id}/blueprint/versions",
    response_model=BS.VersionPage,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP03"),
)
def list_versions(
    request: Request,
    project_id: str,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=200),  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.list_versions(runtime_of(request), project_id, page=page, page_size=pageSize)


@router.get(
    "/projects/{project_id}/blueprint/entries",
    response_model=BS.EntryPage,
    summary="Selected section entries, with S2's own _section/_path locators",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP04"),
)
def get_entries(
    request: Request,
    project_id: str,
    sections: list[str] | None = Query(default=None),
    businessKeys: list[str] | None = Query(default=None),  # noqa: N803
    decisionIds: list[str] | None = Query(default=None),  # noqa: N803
    pageKeys: list[str] | None = Query(default=None),  # noqa: N803
    source: str = "APPROVED",
    version: str | None = None,
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    query = BS.EntriesQuery(
        sections=list(sections or []),
        business_keys=list(businessKeys or []),
        decision_ids=list(decisionIds or []),
        page_keys=list(pageKeys or []),
        source=source,
        version=version,
    )
    return BP.entries(runtime_of(request), project_id, query.to_selector(), version)


@router.get(
    "/projects/{project_id}/blueprint/draft",
    response_model=BS.DraftView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP05"),
)
def get_draft(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.draft(runtime_of(request), project_id)


@router.get(
    "/projects/{project_id}/registry",
    response_model=BS.RegistryPage,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP06"),
)
def get_registry(
    request: Request, project_id: str, version: str | None = None, environmentKey: str | None = None  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.registry(runtime_of(request), project_id, version, environmentKey)


@router.get(
    "/projects/{project_id}/registry/managed-scope",
    response_model=BS.ManagedScopePage,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP07"),
)
def get_managed_scope(request: Request, project_id: str, environmentKey: str, version: str | None = None) -> dict[str, Any]:  # noqa: N803,E501
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.managed_scope(runtime_of(request), project_id, version, environmentKey)


@router.get(
    "/workflows/{workflow_id}/object-graph",
    response_model=BS.WorkflowObjectGraphView,
    summary="The dependency graph of this workflow's build, read whole from S2",
    responses=_ERRORS,
    openapi_extra=_extras("CP-UI-020"),
)
def get_workflow_object_graph(
    request: Request,
    workflow_id: str,
    runId: str | None = Query(default=None),  # noqa: N803
    blueprintVersion: str | None = Query(default=None),  # noqa: N803
    dhis2Type: list[str] | None = Query(default=None),  # noqa: N803
    businessKey: list[str] | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    """CP-UI-020 (V1.1.3): ``runId`` names the Run's environment-bound compile, ``blueprintVersion``
    that version's latest baseline compile, neither the current version's; both together is a 422.
    The graph — nodes with S2's whole-graph degrees, edges, ``orphans``, ``unresolvedRegistryRefs`` —
    is S2's answer (BP-TD-009); S1 only resolves which build and projects camelCase (CP-AC-33).
    A build that has not happened is 404 NOT_FOUND with ``reason=NOT_GENERATED`` (CP-UI-022).
    """
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
    return BP.workflow_object_graph(
        runtime_of(request),
        workflow_id,
        operator=operator_of(request),
        run_id=runId,
        blueprint_version=blueprintVersion,
        dhis2_types=dhis2Type,
        business_keys=businessKey,
    )


@router.get(
    "/projects/{project_id}/object-graph",
    response_model=BS.WorkflowObjectGraphView,
    summary="Deprecated alias of GET /workflows/{id}/object-graph",
    responses=_ERRORS,
    deprecated=True,
    openapi_extra=_extras("S5IF-BP16", "CP-UI-020"),
)
def get_object_graph(
    request: Request,
    project_id: str,
    version: str | None = None,
    view: str = "domain",
    environmentKey: str | None = Query(default=None),  # noqa: N803
    dhis2Type: list[str] | None = Query(default=None),  # noqa: N803
    q: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, Any]:
    """The S5IF-BP16 path, kept as a transitional alias so the graph page loads until S5 migrates.

    SRS V1.1.3 moved the question from a *version* to a *build*, and the version question
    survived as ``blueprintVersion`` on the workflow endpoint: this alias maps its old
    ``version`` onto that parameter (CP-AC-33's "按版本取图平滑过渡").  ``view``,
    ``environmentKey``, ``q`` and ``limit`` have nothing left to answer — S2's degrees and
    ``orphans`` are computed on the complete graph and need no client-side arithmetic — and
    are accepted only to be ignored; ``dhis2Type`` still filters.  The response shape is the
    workflow endpoint's; delete this alias when no caller is left.
    """
    authorize(request, iam.PROJECT_VIEW, project_id)
    runtime = runtime_of(request)
    with runtime.transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow_for_project(ctx.session, project_id)
    return BP.workflow_object_graph(
        runtime,
        workflow.id,
        operator=operator_of(request),
        blueprint_version=version,
        dhis2_types=dhis2Type,
    )


@router.get(
    "/projects/{project_id}/package-files",
    response_model=BS.PackageFilesView,
    summary="The files one build put in the metadata package, with their hashes",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP17"),
)
def get_package_files(
    request: Request,
    project_id: str,
    version: str | None = None,
    environmentKey: str | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    """S5IF-BP17: UI-REV-003's third view. The grouping is S2's build record, not a browser parse."""
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.package_files(runtime_of(request), project_id, version, environmentKey)


@router.get(
    "/projects/{project_id}/frontend-package/files",
    response_model=BS.FrontendPackageFilesView,
    summary="The file listing of the project's development package",
    responses=_ERRORS,
    openapi_extra=_extras("CP-UI-021"),
)
def get_frontend_package_files(request: Request, project_id: str) -> dict[str, Any]:
    """CP-UI-021 (M3): what the development package holds — S2's own listing forwarded whole.

    The package layout belongs to S2 (BP-HO-004): S1 asks for the listing and forwards it, it
    never unzips the artifact.  No package yet is 404 NOT_FOUND with ``reason=NOT_GENERATED``
    and ``requiredStage=HANDOFF``, the stage that delivers one (CP-UI-022).
    """
    authorize(request, iam.PROJECT_VIEW, project_id)
    body, _classification = BP.frontend_package_files(
        runtime_of(request), project_id, operator=operator_of(request)
    )
    return body


@router.get(
    "/projects/{project_id}/frontend-package/files/{path:path}",
    # One file's text, forwarded from S2 — the body is content, not a JSON component
    # (S5IF-G05), so the document says what it actually sends, like the artifact content
    # exit it borrows the download rules from (CP-UI-016).
    response_class=Response,
    responses={
        200: {
            "description": "One development-package file's content, inline or as a file (CP-UI-021).",
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            "headers": {
                "X-Data-Classification": {
                    "description": "The S0 classification of the package the file came from.",
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
        **_ERRORS,
    },
    openapi_extra=_extras("CP-UI-021"),
)
def get_frontend_package_file(
    request: Request,
    project_id: str,
    path: str,
    download: bool = Query(default=False),
) -> Response:
    """CP-UI-021: one file inside the development package, read through S2.

    Membership is answered against S2's listing — a path the listing does not carry is
    404 ``NOT_EXIST``, no unpack happens here.  ``download=true`` names the file by the
    CP-UI-016 rule for artifacts without a user-known name: type, package artifact id,
    and the extension of the requested path.
    """
    authorize(request, iam.PROJECT_VIEW, project_id)
    content, artifact_id, classification = BP.frontend_package_file(
        runtime_of(request), project_id, path, operator=operator_of(request)
    )
    headers = {"X-Data-Classification": classification} if classification else {}
    if download:
        headers["Content-Disposition"] = content_disposition(package_file_filename(artifact_id, path))
    media_type = content.media_type or "application/octet-stream"
    return Response(content=content.content.encode("utf-8"), media_type=media_type, headers=headers)


@router.get(
    "/projects/{project_id}/uid-pool",
    response_model=BS.UidPoolView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP08"),
)
def uid_pool(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.uid_pool(runtime_of(request), project_id)


@router.post(
    "/projects/{project_id}/uid-pool/replenish",
    response_model=BS.UidPoolView,
    summary="Draw another batch of UIDs from the target instance into the project's pool",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP09", mutating=True, idempotent=True),
)
def uid_pool_replenish(
    request: Request,
    project_id: str,
    payload: BS.UidPoolReplenishRequest | None = Body(default=None),
) -> dict[str, Any]:
    """The write behind the ``uid_pool.replenish`` action S1 has always advertised.

    S2's ``replenish`` is marked ``@external_io``: it calls the instance's own UID endpoint,
    so the provider call happens after S1's unit of work is closed.
    """
    authorize(request, iam.UID_POOL_REPLENISH, project_id)
    return BP.replenish_uid_pool(
        runtime_of(request),
        project_id,
        operator=operator_of(request),
        environment_key=(payload.environment_key if payload is not None else None),
    )


@router.get(
    "/workflows/{workflow_id}/validation-issues",
    response_model=BS.ValidationIssuePage,
    summary="Issues across this workflow's validation and import reports",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP14"),
)
def validation_issues(
    request: Request,
    workflow_id: str,
    runId: str | None = Query(default=None),  # noqa: N803
    level: str | None = None,
    sourceStage: str | None = Query(default=None),  # noqa: N803
    ruleId: str | None = Query(default=None),  # noqa: N803
    businessKey: str | None = Query(default=None),  # noqa: N803
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sortBy: str | None = Query(default=None),  # noqa: N803
    sortDir: str | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    """S5IF-BP14: UI-REV-005's four filters are parameters, not a client-side loop."""
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)
    from factory_control.rest.paging import sort_direction, sortable

    return BP.validation_issues(
        runtime_of(request),
        workflow_id,
        operator=operator_of(request),
        run_id=runId,
        level=level,
        source_stage=sourceStage,
        rule_id=ruleId,
        business_key=businessKey,
        sort_by=sortable(sortBy, {
            "level": "level",
            "ruleId": "ruleId",
            "sourceStage": "sourceStage",
            "businessKey": "businessKey",
            "objectKey": "objectKey",
        }),
        sort_dir=sort_direction(sortDir),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/deployment-runs/{run_id}/environment-bindings/{business_key}/candidates",
    response_model=BS.CandidatePage,
    summary="Objects in the stored discovery result this binding could resolve to",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP11"),
)
def binding_candidates(
    request: Request,
    run_id: str,
    business_key: str,
    dhis2Type: str | None = Query(default=None),  # noqa: N803
    query: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """S5IF-BP11: S1 resolves which discovery result to search; S4 reads its content."""
    authorize_run(request, run_id, iam.PROJECT_VIEW)
    from factory_control.services import environments as ENV

    return ENV.binding_candidates(
        runtime_of(request),
        run_id,
        business_key,
        operator=operator_of(request),
        dhis2_type=dhis2Type,
        query=query,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/deployment-runs/{run_id}/environment-bindings",
    response_model=BS.BindingPage,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP09"),
)
def list_bindings(request: Request, run_id: str) -> dict[str, Any]:
    authorize_run(request, run_id, iam.PROJECT_VIEW)
    return BP.bindings_for_run(runtime_of(request), run_id, operator=operator_of(request))


@router.put(
    "/deployment-runs/{run_id}/environment-bindings/{business_key}",
    response_model=BS.BindingView,
    summary="Resolve an object by hand; version and environment come from the Run",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP10", mutating=True),
)
def set_binding(
    request: Request, run_id: str, business_key: str, payload: BS.BindingSetRequest = Body(...)
) -> dict[str, Any]:
    authorize_run(request, run_id, iam.REGISTRY_BINDING_SET)
    return BP.set_binding_for_run(
        runtime_of(request),
        run_id,
        business_key=business_key,
        uid=payload.uid,
        note=payload.note,
        operator=operator_of(request),
    )


@router.get(
    "/projects/{project_id}/blueprint/diff",
    response_model=BS.BlueprintDiffView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP12"),
)
def blueprint_diff(
    request: Request,
    project_id: str,
    fromVersion: str = Query(...),  # noqa: N803
    toVersion: str = Query(...),  # noqa: N803
    environmentKey: str | None = None,  # noqa: N803
    sections: list[str] | None = Query(default=None),
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.diff(
        runtime_of(request),
        project_id,
        from_version=fromVersion,
        to_version=toVersion,
        environment_key=environmentKey,
        sections=sections,
    )


@router.get(
    "/projects/{project_id}/decisions",
    response_model=BS.DecisionList,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP13"),
)
def list_decisions(
    request: Request, project_id: str, decisionIds: list[str] | None = Query(default=None)  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    return BP.decisions(runtime_of(request), project_id, list(decisionIds or []))


@router.get(
    "/workflows/{workflow_id}/nrr",
    response_model=BS.NrrPage,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-BP15"),
)
def list_nrr(
    request: Request,
    workflow_id: str,
    category: str | None = None,
    inputArtifactId: str | None = Query(default=None),  # noqa: N803
    minConfidence: float | None = Query(default=None, ge=0, le=1),  # noqa: N803
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """S5IF-BP15 / CP-UI-003: category, source input and confidence narrow the list here."""
    authorize_workflow(request, workflow_id, iam.PROJECT_VIEW)
    return BP.nrr_items(
        runtime_of(request),
        workflow_id,
        operator=operator_of(request),
        category=category,
        input_artifact_id=inputArtifactId,
        min_confidence=minConfidence,
        limit=limit,
        offset=offset,
    )
