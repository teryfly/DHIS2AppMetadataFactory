"""Environment and capability endpoints (S5IF-ENV01 … ENV12).

Every handler here forwards to :data:`ExecutionPort` and projects the answer; S1
stores nothing (CP-API-007).  ``environmentKey`` always appears explicitly in the
path or the body — there is no default environment (DX-ENV-004, FR-ENV-003).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request

from factory_control import iam
from factory_control.errors import IdempotencyKeyRequiredError
from factory_control.rest import route_meta
from factory_control.rest.deps import authorize, operator_of, runtime_of
from factory_control.rest.middleware import IDEMPOTENCY_HEADER
from factory_control.rest.schemas import environments as ES
from factory_control.services import environments as ENV

router = APIRouter(prefix="/api/v1", tags=["environments"])

_EXTRAS = route_meta.openapi_extra
_ERRORS = route_meta.error_responses(400, 404, 409, 422, 502, 503)


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return _EXTRAS(*ids, mutating=mutating, idempotent=idempotent)


@router.get(
    "/environments",
    response_model=ES.EnvironmentPage,
    summary="List the target environments S4 manages",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV01"),
)
def list_environments(
    request: Request,
    environmentClass: str | None = None,  # noqa: N803
    status: str | None = None,  # noqa: A002 - the query parameter is literally `status`
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW)
    return ENV.list_environments(
        runtime_of(request),
        environment_class=environmentClass,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/environments/expiring-credentials",
    response_model=ES.ExpiringCredentialList,
    summary="Credentials whose secret reference expires soon",
    responses=_ERRORS,
    # Declared before ``/environments/{environment_key}``: FastAPI matches in
    # registration order, and this literal would otherwise be read as a key.
    openapi_extra=_extras("S5IF-ENV12"),
)
def expiring_credentials(
    request: Request, withinDays: int = Query(default=30, ge=1, le=365)  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.ENVIRONMENT_MANAGE)
    return ENV.expiring_credentials(runtime_of(request), withinDays)


@router.post(
    "/environments",
    response_model=ES.EnvironmentView,
    status_code=201,
    summary="Register a target environment",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV03", mutating=True, idempotent=True),
)
def create_environment(
    request: Request, payload: ES.CreateEnvironmentRequest = Body(...)
) -> dict[str, Any]:
    authorize(request, iam.ENVIRONMENT_CREATE)
    return ENV.create_environment(runtime_of(request), payload.to_spec())


@router.get(
    "/environments/{environment_key}",
    response_model=ES.EnvironmentView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV02"),
)
def get_environment(request: Request, environment_key: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW)
    return ENV.get_environment(runtime_of(request), environment_key)


@router.patch(
    "/environments/{environment_key}",
    response_model=ES.EnvironmentView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV04", mutating=True),
)
def update_environment(
    request: Request, environment_key: str, payload: ES.UpdateEnvironmentRequest = Body(...)
) -> dict[str, Any]:
    authorize(request, iam.ENVIRONMENT_EDIT)
    return ENV.update_environment(runtime_of(request), environment_key, payload.to_patch())


@router.post(
    "/environments/{environment_key}/disable",
    response_model=ES.DisableResultView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV05", mutating=True),
)
def disable_environment(
    request: Request, environment_key: str, payload: ES.DisableEnvironmentRequest = Body(default={})
) -> dict[str, Any]:
    authorize(request, iam.ENVIRONMENT_DISABLE)
    return ENV.disable_environment(runtime_of(request), environment_key, payload.reason)


@router.get(
    "/environments/{environment_key}/protection",
    response_model=ES.EnvironmentProtectionView,
    summary="Live protection facts; never cached (CP-WF-027)",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV06"),
)
def get_protection(request: Request, environment_key: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW)
    return ENV.get_protection(runtime_of(request), environment_key)


@router.post(
    "/environments/{environment_key}/emergency-lock",
    response_model=ES.EmergencyLockView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV07", mutating=True),
)
def set_emergency_lock(
    request: Request, environment_key: str, payload: ES.EmergencyLockRequest = Body(...)
) -> dict[str, Any]:
    authorize(request, iam.ENVIRONMENT_LOCK)
    return ENV.set_emergency_lock(
        runtime_of(request),
        environment_key,
        locked=payload.locked,
        reason=payload.reason,
        operator=operator_of(request),
    )


@router.post(
    "/environments/{environment_key}/test-connection",
    response_model=ES.ConnectionCheckView,
    summary="Run S4's eight read-only connection checks",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV08", mutating=True, idempotent=True),
)
def test_connection(
    request: Request,
    environment_key: str,
    payload: ES.TestConnectionRequest = Body(default={}),
) -> dict[str, Any]:
    authorize(request, iam.ENVIRONMENT_TEST)
    return ENV.test_connection(
        runtime_of(request),
        environment_key,
        required_scopes=payload.scopes_or_default(),
        operator=operator_of(request),
    )


@router.get(
    "/environments/{environment_key}/capability-profile",
    response_model=ES.CapabilityProfileView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV09"),
)
def capability_profile(
    request: Request, environment_key: str, includeContent: bool = False  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW)
    return ENV.capability_profile(
        runtime_of(request), environment_key, include_content=includeContent
    )


@router.post(
    "/environments/{environment_key}/probe-capabilities",
    response_model=ES.ProbeResultView,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV10", mutating=True, idempotent=True),
)
def probe_capabilities(request: Request, environment_key: str) -> dict[str, Any]:
    authorize(request, iam.ENVIRONMENT_PROBE)
    return ENV.probe_capabilities(runtime_of(request), environment_key, operator=operator_of(request))


@router.get(
    "/environments/{environment_key}/discovery-results",
    response_model=ES.DiscoveryResultPage,
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-ENV11"),
)
def discovery_results(
    request: Request,
    environment_key: str,
    projectId: str | None = None,  # noqa: N803
    scope: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW)
    return ENV.discovery_results(
        runtime_of(request),
        environment_key,
        project_id=projectId,
        scope=scope,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/environments/{environment_key}/delete-objects",
    response_model=ES.ObjectDeletionView,
    summary="Delete a payload's metadata objects from this environment — Idempotency-Key required",
    responses=_ERRORS,
    # S5IF-G08: the header is the only source of the key, exactly as for COMMIT.  CP-API-007:
    # the endpoint owns no state — it forwards one S4 call and projects its answer.
    openapi_extra=_extras("S5IF-G08", "CP-API-007", mutating=True, idempotent=True),
)
def delete_objects(
    request: Request, environment_key: str, payload: ES.ObjectDeletionRequest = Body(...)
) -> dict[str, Any]:
    """Forward S4's deletion execution (DX-EXE-038) for one environment.

    ``environmentKey`` stays in the path rather than the body: an operation is always bound to an
    explicit environment and there is no default one (DX-BR-02, FR-ENV-003), and the same is true
    of every other write in this module.

    Why this answers synchronously with S4's execution outcome, while ``POST
    /deployment-runs/{runId}/commit`` answers 202 with an acceptance envelope (CP-API-012): a
    COMMIT is a *step* — S1 has a Run, a StageRun and a Gate approval to accept work into, and the
    engine reports back later.  A deletion belongs to no workflow, so S1 has no ``stageId`` or
    ``stepRunId`` to hand out; promising a 202 whose identifiers it cannot name would send the
    caller to a resource that does not exist.  The environment-scoped writes beside this one
    (``test-connection``, ``probe-capabilities``) already answer with what the provider said, and
    the execution id in this body is the observable handle from here — ``GET
    /executions/{executionId}`` and the SSE stream carry the rest (S5IF-G03).

    The key is mandatory rather than derived: an irreversible call must not inherit a key S1
    invented from the environment and the operator, because the second, different deletion by the
    same hand would then be answered with the first one's verdict (NFR-REL-003).  ``operator``
    comes from ``X-Operator`` (CP-API-002) and never from the body.
    """
    key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
    if not key:
        raise IdempotencyKeyRequiredError(
            "a deletion must carry an Idempotency-Key header",
            details={"header": IDEMPOTENCY_HEADER, "environmentKey": environment_key},
        )
    # ``environment.manage`` is 附录 B's environment-management right and M4 hands it to the
    # platform administrator alone (identity/roles.py).  It is reused rather than a new
    # ``environment.delete_objects`` coined: the appendix is the live vocabulary (its own heading
    # says 现行生效，非预留), so a new name would need the two consoles' ACTION tables and the
    # allowed-actions list changed in step — and ``allowed-actions`` answers per environment from
    # ENVIRONMENT_ACTIONS, where a name nobody can ask about is a control S5 has to grey out.
    # ``execution.commit.<CLASS>`` was rejected for the same reason in the other direction: it
    # means 提交, and reading it as "may also destroy" would silently widen every commit grant.
    authorize(request, iam.ENVIRONMENT_MANAGE)
    return ENV.delete_objects(
        runtime_of(request),
        environment_key,
        project_id=payload.project_id,
        deletion_artifact_id=payload.deletion_artifact_id,
        expected_hash=payload.expected_hash,
        operator=operator_of(request),
        idempotency_key=key,
    )
