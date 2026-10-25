"""Login, sessions and the permission tables (CP-IAM-002, FR-SEC-001/003/005).

Two things are worth stating because they are the parts a reader would otherwise have to infer
from the code:

* **the login endpoints answer in every mode.**  With ``FACTORY_AUTH_MODE=off`` there is nothing
  to sign in to, so ``/auth/session`` reports ``authenticated: false`` and ``authMode: off``
  rather than a 401 — S5 draws the difference between "no accounts here" and "you must sign in"
  from that one field, and inferring it from an error code would be guessing (UI-APP-008);
* **a session cookie is set HttpOnly, Secure and SameSite=Strict.**  The token is therefore
  unreadable to script, sent only over HTTPS, and never attached to another site's request —
  which is what the separate ``X-CSRF-Token`` value protects, because a bearer header is not
  ambient and a cookie is (FR-SEC-005).

The OIDC pair keeps ``state`` and ``nonce`` in a short-lived HttpOnly cookie instead of a table:
the value must survive a round trip through the user's browser, and a deployment runs several api
replicas behind one Service (CP-EVT-005), so process memory would lose it between the two calls.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Cookie, Query, Request, Response

from factory_control import iam
from factory_control.errors import FeatureNotEnabledError
from factory_control.foundation import resolve_secret
from factory_control.identity import authn, directory, service, sessions
from factory_control.identity.passwords import new_token
from factory_control.models import User
from factory_control.rest import route_meta
from factory_control.rest.deps import authorize, operator_of, runtime_of
from factory_control.rest.middleware import context_of
from factory_control.rest.schemas import identity as IDS

router = APIRouter(prefix="/api/v1", tags=["identity"])

_ERRORS = route_meta.error_responses(400, 401, 403, 404, 409, 422)

#: How long a browser may hold the OIDC state/nonce pair.  Longer than the round trip it takes,
#: shorter than a session: a stale state is a login attempt nobody finished, not a credential.
_STATE_COOKIE_TTL_SECONDS = 300
_STATE_COOKIE_NAME = "factory_oidc_state"


def _extras(*ids: str, mutating: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*("CP-IAM-002", *ids), mutating=mutating)


def _config(request: Request) -> Any:
    return runtime_of(request).settings


def _mode_off(request: Request) -> bool:
    return not _config(request).auth_is_enforced()


def _require_m4(request: Request, feature: str) -> None:
    """CP-UI-023 / CP-AC-32: members, grants and exemptions are M4 writes.

    MVP ships the tables and the reads but refuses every one of these writes with
    409 ``FEATURE_NOT_ENABLED``, which is what the ``allowed-actions`` reason code
    says about the same button (appendix B).  Reading the tables stays open and
    decides nothing — the MVP authorization hook is allow-everything (CP-IAM-001).
    Once the milestone arrives, or a deployment opts in with
    ``FACTORY_M4_FEATURES_ENABLED``, the same call sites fall through to
    ``authorize`` and the role table untouched.
    """
    if not _config(request).m4_features_are_live():
        raise FeatureNotEnabledError(
            f"{feature} is an M4 capability and is not enabled on this deployment",
            feature=feature,
            milestone="M4",
        )


# --- the session --------------------------------------------------------------------


@router.post(
    "/auth/login",
    response_model=IDS.SessionView,
    response_model_exclude_none=True,
    summary="Sign in with a local account or the organisation's single sign-on",
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-001", mutating=True),
)
def login(request: Request, response: Response, payload: IDS.LoginRequest = Body(...)) -> dict[str, Any]:
    """Exchange credentials for a session, and set the cookie the browser will send back."""
    config = _config(request)
    if _mode_off(request):
        return _off_mode_view(request)
    if config.auth_mode == "oidc":
        raise _refuse(
            request,
            code="UNAUTHENTICATED",
            status=401,
            message="this deployment signs in through the organisation; use /auth/oidc/request",
        )
    principal, issued = _authenticate_with(
        authn.LocalAccounts(), authn.PasswordCredential(subject=payload.subject, password=payload.password), config
    )
    _set_session(response, config, issued)
    return _session_view(request, principal, issued, config)


@router.post(
    "/auth/logout",
    response_model=IDS.ClearedView,
    summary="End this session",
    responses=_ERRORS,
    openapi_extra=_extras(mutating=True),
)
def logout(request: Request, response: Response) -> dict[str, Any]:
    config = _config(request)
    holder = context_of(request)
    _clear_session_cookies(response, config)
    if _mode_off(request) or not holder.session_id:
        return {"cleared": True, "authMode": config.auth_mode}
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        sessions.revoke(session, holder.session_id)
    return {"cleared": True, "sessionId": holder.session_id}


@router.get(
    "/auth/session",
    response_model=IDS.SessionView,
    response_model_exclude_none=True,
    summary="Who this request is, and which identity regime is running",
    responses=_ERRORS,
    openapi_extra=_extras(),
)
def get_session(request: Request) -> dict[str, Any]:
    config = _config(request)
    holder = context_of(request)
    if _mode_off(request):
        return _off_mode_view(request)
    view: dict[str, Any] = {
        "authenticated": holder.authenticated,
        "userId": holder.user_id or None,
        "subject": holder.operator or None,
        "displayName": holder.display_name or None,
        "roles": list(holder.roles),
        "authMode": config.auth_mode,
    }
    if holder.authenticated and holder.session_id:
        # ``expiresAt`` and ``mustChangePassword`` are the two things a reloaded page cannot
        # recover from its own memory, and reading them here changes nothing — the idle clock is
        # slid by the middleware's resolve, not by this view.
        from factory_control.foundation import unit_of_work
        from factory_control.models import Session as SessionRow

        with unit_of_work() as session:
            record = session.get(SessionRow, holder.session_id)
            account = session.get(User, holder.user_id) if holder.user_id else None
        if record is not None:
            view["expiresAt"] = record.expires_at
        view["mustChangePassword"] = bool(account is not None and account.must_change_password)
    return view


# --- the organisation's single sign-on ------------------------------------------------


@router.get(
    "/auth/oidc/request",
    response_model=IDS.OidcRequestView,
    summary="Where to send the browser, with the state and nonce to bring back",
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-001"),
)
def oidc_request(
    request: Request, response: Response, redirectUri: str = Query(...)  # noqa: N803
) -> dict[str, Any]:
    config = _config(request)
    if config.auth_mode not in ("oidc", "both"):
        raise _refuse(
            request, code="INVALID_STATE", status=409, message="this deployment does not use OIDC sign-in"
        )
    party = _oidc_party(config)
    state = new_token(16)
    nonce = new_token(16)
    view = party.authorization_request(state, nonce, redirectUri)
    response.set_cookie(
        _STATE_COOKIE_NAME,
        f"{state}|{nonce}",
        max_age=_STATE_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/api/v1/auth",
    )
    return view


@router.post(
    "/auth/oidc/exchange",
    response_model=IDS.SessionView,
    response_model_exclude_none=True,
    summary="Trade the authorization code the browser came back with for a session",
    responses=_ERRORS,
    openapi_extra=_extras(mutating=True),
)
def oidc_exchange(
    request: Request,
    response: Response,
    payload: IDS.OidcExchangeRequest = Body(...),
    factory_oidc_state: str | None = Cookie(default=None, alias=_STATE_COOKIE_NAME),
) -> dict[str, Any]:
    config = _config(request)
    if config.auth_mode not in ("oidc", "both"):
        raise _refuse(
            request, code="INVALID_STATE", status=409, message="this deployment does not use OIDC sign-in"
        )
    expected = (factory_oidc_state or "").split("|")
    state = request.query_params.get("state", "") or ""
    if len(expected) != 2 or not state or state != expected[0]:
        # the state is what makes a code handed to this endpoint somebody else's, not ours
        raise _refuse(request, code="CSRF_FAILED", status=403, message="the login state did not match")
    principal, issued = _authenticate_with(
        _oidc_party(config),
        authn.AuthorizationCode(
            code=payload.code, state=state, redirect_uri=payload.redirect_uri, nonce=expected[1] if len(expected) > 1 else None
        ),
        config,
    )
    _set_session(response, config, issued)
    response.delete_cookie(_STATE_COOKIE_NAME, path="/api/v1/auth")
    return _session_view(request, principal, issued, config)


# --- users, roles, members, grants, exemptions ---------------------------------------


@router.get(
    "/roles",
    response_model=IDS.RoleList,
    summary="The built-in roles and the actions each one grants",
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-002"),
)
def list_roles(request: Request) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW)
    return {"items": service.roles_with_actions()}


@router.get(
    "/users",
    response_model=IDS.UserList,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-002"),
)
def list_users(request: Request, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    authorize(request, iam.USER_MANAGE)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return {"items": [service.user_view(row, session) for row in directory.list_users(session, limit=limit, offset=offset)]}


@router.post(
    "/users",
    status_code=201,
    response_model=IDS.UserView,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-002", mutating=True),
)
def create_user(request: Request, payload: IDS.UserCreateRequest = Body(...)) -> dict[str, Any]:
    authorize(request, iam.USER_MANAGE)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return service.create_user(
            session,
            operator_of(request),
            subject=payload.subject,
            display_name=payload.display_name,
            email=payload.email,
            password=payload.password,
            source=payload.source,
            must_change_password=payload.must_change_password,
        )


@router.patch(
    "/users/{user_id}",
    response_model=IDS.UserView,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-002", mutating=True),
)
def patch_user(request: Request, user_id: str, payload: IDS.UserPatchRequest = Body(...)) -> dict[str, Any]:
    authorize(request, iam.USER_MANAGE)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return service.update_user(
            session,
            operator_of(request),
            user_id,
            display_name=payload.display_name,
            email=payload.email,
            status=payload.status,
        )


@router.post(
    "/users/{user_id}/password",
    response_model=IDS.UserView,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-001", mutating=True),
)
def set_password(request: Request, user_id: str, payload: IDS.PasswordSetRequest = Body(...)) -> dict[str, Any]:
    """A reset by an administrator, or the forced change of a ``mustChangePassword`` account.

    Self-service is allowed: a person may change their own password without ``user.manage``, and
    nobody may change somebody else's without it.  That is the one place in this module where the
    target of the action is a permission of its own, because a role that can reset passwords can
    otherwise impersonate anyone.
    """
    holder = context_of(request)
    if holder.user_id != user_id:
        authorize(request, iam.USER_MANAGE)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return service.set_password(
            session, operator_of(request), user_id, payload.password, must_change=payload.must_change_password
        )


@router.put(
    "/users/{user_id}/roles",
    response_model=IDS.RoleAssignmentView,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-002", mutating=True),
)
def assign_role(request: Request, user_id: str, payload: IDS.RoleAssignmentRequest = Body(...)) -> dict[str, Any]:
    authorize(request, iam.USER_MANAGE)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return service.assign_role(
            session, operator_of(request), user_id, payload.role, project_id=payload.project_id
        )


@router.delete(
    "/users/{user_id}/roles",
    response_model=IDS.RoleAssignmentView,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-002", mutating=True),
)
def revoke_role(
    request: Request,
    user_id: str,
    role: str = Query(...),
    projectId: str | None = Query(default=None),  # noqa: N803
) -> dict[str, Any]:
    authorize(request, iam.USER_MANAGE)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return service.revoke_role(
            session, operator_of(request), user_id, role, project_id=projectId
        )


@router.get(
    "/projects/{project_id}/members",
    response_model=IDS.MemberList,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-004", "CP-UI-023"),
)
def list_members(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        items = []
        for row in directory.list_members(session, project_id):
            account = session.get(User, row.user_id)
            items.append(service.member_view(row, subject="" if account is None else account.subject))
        return {"items": items}


@router.put(
    "/projects/{project_id}/members",
    response_model=IDS.MemberList,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-004", "CP-UI-023", mutating=True),
)
def put_members(request: Request, project_id: str, payload: IDS.MemberPutRequest = Body(...)) -> dict[str, Any]:
    _require_m4(request, iam.MEMBER_MANAGE)
    authorize(request, iam.MEMBER_MANAGE, project_id)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        wanted = {item.user_id: item.role for item in payload.members}
        for row in directory.list_members(session, project_id):
            if row.user_id not in wanted:
                service.remove_member(session, operator_of(request), project_id, row.user_id)
        for user_id, role in wanted.items():
            service.add_member(session, operator_of(request), project_id, user_id, role=role)
        items = []
        for row in directory.list_members(session, project_id):
            account = session.get(User, row.user_id)
            items.append(
                service.member_view(row, subject="" if account is None else account.subject)
            )
        return {"items": items}


@router.delete(
    "/projects/{project_id}/members/{user_id}",
    response_model=IDS.ClearedView,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-004", "CP-UI-023", mutating=True),
)
def remove_member(request: Request, project_id: str, user_id: str) -> dict[str, Any]:
    _require_m4(request, iam.MEMBER_MANAGE)
    authorize(request, iam.MEMBER_MANAGE, project_id)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        service.remove_member(session, operator_of(request), project_id, user_id)
    return {"cleared": True, "userId": user_id, "projectId": project_id}


@router.get(
    "/projects/{project_id}/artifact-grants",
    response_model=IDS.GrantList,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-003", "CP-UI-023"),
)
def list_grants(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.RESTRICTED_GRANT_MANAGE, project_id)
    from factory_control.foundation import unit_of_work
    from factory_control.models import User

    with unit_of_work() as session:
        items = []
        for row in directory.grant_rows(session, project_id):
            account = session.get(User, row.user_id)
            items.append(
                service.grant_view(row, subject="" if account is None else account.subject)
            )
        return {"items": items}


@router.post(
    "/projects/{project_id}/artifact-grants",
    status_code=201,
    response_model=IDS.GrantView,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-003", "CP-UI-023", mutating=True),
)
def create_grant(request: Request, project_id: str, payload: IDS.GrantCreateRequest = Body(...)) -> dict[str, Any]:
    _require_m4(request, iam.RESTRICTED_GRANT_MANAGE)
    authorize(request, iam.RESTRICTED_GRANT_MANAGE, project_id)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return service.grant_restricted(
            session,
            operator_of(request),
            project_id,
            payload.user_id,
            payload.classification,
            reason=payload.reason,
            expires_at=payload.expires_at,
        )


@router.delete(
    "/projects/{project_id}/artifact-grants/{user_id}",
    response_model=IDS.ClearedView,
    responses=_ERRORS,
    openapi_extra=_extras("FR-SEC-003", "CP-UI-023", mutating=True),
)
def revoke_grant(
    request: Request, project_id: str, user_id: str, classification: str = Query(...)
) -> dict[str, Any]:
    _require_m4(request, iam.RESTRICTED_GRANT_MANAGE)
    authorize(request, iam.RESTRICTED_GRANT_MANAGE, project_id)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        service.revoke_restricted(session, operator_of(request), project_id, user_id, classification)
    return {"cleared": True, "userId": user_id, "projectId": project_id, "classification": classification}


@router.get(
    "/projects/{project_id}/production-exemptions",
    response_model=IDS.ExemptionList,
    responses=_ERRORS,
    openapi_extra=_extras("CP-WF-020", "CP-UI-023"),
)
def list_exemptions(request: Request, project_id: str) -> dict[str, Any]:
    authorize(request, iam.PROJECT_VIEW, project_id)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return {
            "items": [
                service.exemption_view(row) for row in directory.exemption_rows(session, project_id)
            ]
        }


@router.post(
    "/projects/{project_id}/production-exemptions",
    status_code=201,
    response_model=IDS.ExemptionView,
    responses=_ERRORS,
    openapi_extra=_extras("CP-WF-020", "CP-UI-023", mutating=True),
)
def create_exemption(
    request: Request, project_id: str, payload: IDS.ExemptionCreateRequest = Body(...)
) -> dict[str, Any]:
    _require_m4(request, iam.PRODUCTION_EXEMPTION_MANAGE)
    authorize(request, iam.PRODUCTION_EXEMPTION_MANAGE, project_id)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        return service.grant_exemption(
            session,
            operator_of(request),
            project_id,
            waiver=payload.waiver,
            environment_key=payload.environment_key,
            reason=payload.reason,
            expires_at=payload.expires_at,
        )


@router.delete(
    "/production-exemptions/{exemption_id}",
    response_model=IDS.ClearedView,
    responses=_ERRORS,
    openapi_extra=_extras("CP-WF-020", "CP-UI-023", mutating=True),
)
def revoke_exemption(request: Request, exemption_id: str) -> dict[str, Any]:
    _require_m4(request, iam.PRODUCTION_EXEMPTION_MANAGE)
    authorize(request, iam.PRODUCTION_EXEMPTION_MANAGE)
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        row = service.revoke_exemption(session, operator_of(request), exemption_id)
    return {"cleared": True, "projectId": row["projectId"], "waiver": row["waiver"]}


# --- helpers ---------------------------------------------------------------------


def _authenticate_with(authenticator: Any, credential: Any, config: Any) -> tuple[Any, Any]:
    """Run one authenticator and mint the session it earned, in one short transaction."""
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        principal = authenticator.authenticate(session, credential)
        issued = sessions.issue(
            session,
            principal.user_id,
            auth_source=principal.source,
            idle_minutes=config.session_idle_minutes,
            absolute_hours=config.session_absolute_hours,
        )
    return principal, issued


def _oidc_party(config: Any) -> authn.OidcRelyingParty:
    return authn.OidcRelyingParty(
        issuer=config.oidc_issuer,
        client_id=config.oidc_client_id,
        client_secret=resolve_secret(config.oidc_client_secret_ref).reveal(),
        auto_provision=config.oidc_auto_provision,
    )


def _set_session(response: Response, config: Any, issued: Any) -> None:
    response.set_cookie(
        config.cookie_name,
        issued.token,
        max_age=config.session_idle_minutes * 60,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    # The CSRF value is mirrored into a cookie the page can read, because `issue` gives it out
    # once and only its digest is stored: without this a reloaded browser holds a live session
    # and no way to name the header the middleware demands, so every write after a refresh would
    # be a 403 (FR-SEC-005's second clause).  It authenticates nothing — the session cookie does
    # that — so being readable costs nothing a cross-site page could use: SameSite=Strict keeps
    # it off another origin's requests and the attacker's script cannot read a same-origin value.
    response.set_cookie(
        config.csrf_cookie_name,
        issued.csrf_token,
        max_age=config.session_idle_minutes * 60,
        httponly=False,
        secure=True,
        samesite="strict",
        path="/",
    )


def _clear_session_cookies(response: Response, config: Any) -> None:
    response.delete_cookie(config.cookie_name, path="/")
    response.delete_cookie(config.csrf_cookie_name, path="/")


def _session_view(request: Request, principal: Any, issued: Any, config: Any) -> dict[str, Any]:
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        roles = directory.roles_of(session, principal.user_id, None)
        account = session.get(User, principal.user_id)
    return {
        "authenticated": True,
        "userId": principal.user_id,
        "subject": principal.subject,
        "displayName": principal.display_name,
        "roles": sorted(roles),
        "authMode": config.auth_mode,
        "expiresAt": issued.expires_at,
        "csrfToken": issued.csrf_token,
        "mustChangePassword": bool(account is not None and account.must_change_password),
    }


def _off_mode_view(request: Request) -> dict[str, Any]:
    """What ``/auth/*`` says when there is no authentication to speak of (CP-IAM-001)."""
    holder = context_of(request)
    return {
        "authenticated": False,
        "subject": holder.operator or None,
        "displayName": holder.operator or None,
        "roles": [],
        "authMode": _config(request).auth_mode,
    }


def _refuse(request: Request, *, code: str, status: int, message: str) -> Any:
    """A refusal with the standard envelope's code (SRS 8.3): never a bare 500."""
    from factory_control.errors import ControlPlaneError

    raise ControlPlaneError(message, code=code, details={"route": request.url.path})


__all__ = ["router"]
