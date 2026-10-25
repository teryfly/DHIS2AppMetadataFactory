"""REST middleware: operator, correlation, idempotency, error envelope.

CP-API-002 — every state-changing request must declare ``X-Operator``; the value
is written into S0's operation context so audit, approvals and question answers all
carry it.

CP-API-006 — a request either continues the incoming ``X-Correlation-Id`` /
``traceparent`` or gets a fresh pair, again in the operation context.

CP-API-003 — create and execute requests honour ``Idempotency-Key`` with the scope
set to the endpoint.  S0 holds the state machine of the record; the replayable
response itself is stored by S1, because S0 only keeps a ``resultRef``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from factory_control.errors import (
    IDEMPOTENCY_HEADER,
    OPERATOR_HEADER,
    OperatorRequiredError,
    RequestInProgressError,
    http_status_for,
)
from factory_control.foundation import (
    IdempotencyConflictError,
    OperatorType,
    bind_operation_context,
    idempotency,
    new_correlation_id,
    new_id,
    structure_hash,
    utcnow,
)
from factory_control.i18n import negotiate_language
from factory_control.rest.failure import error_body, scope_of

CORRELATION_HEADER = "X-Correlation-Id"
TRACEPARENT_HEADER = "traceparent"

#: Only these methods are state-changing and therefore require an operator.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Methods whose idempotency key is honoured (CP-API-003).
IDEMPOTENT_METHODS = frozenset({"POST", "PUT", "PATCH"})

#: Endpoints that stream or upload and are therefore excluded from replay.
#:
#: ``/secrets`` joins them for a different reason: a replayed response is stored verbatim in
#: ``cp.idempotent_responses``, and the answer to a credential write is the reference that
#: outlives it.  Caching that would put a copy of "which secrets exist" into a table whose
#: retention is a configuration value, for no benefit — the write is already idempotent by
#: being a PUT.
NON_IDEMPOTENT_PATH_FRAGMENTS = ("/events", "/secrets")


class RequestContextHolder:
    """Where middleware stashes what later handlers need (operator, ids)."""

    def __init__(self) -> None:
        self.operator: str = "system"
        self.correlation_id: str = ""
        self.traceparent: str = ""
        self.language: str = "en"
        # --- M4 identity (CP-IAM-002).  All empty while FACTORY_AUTH_MODE=off. ----------
        #: the sign-in name the policy resolves roles by — never the display name, because two
        #: sources could use the same display and an authorization must not depend on which row
        #: was read first
        self.user_id: str = ""
        self.display_name: str = ""
        self.session_id: str = ""
        self.csrf_token_hash: str = ""
        self.auth_source: str = ""
        self.roles: tuple[str, ...] = ()

    @property
    def authenticated(self) -> bool:
        return bool(self.user_id)


def context_of(request: Request) -> RequestContextHolder:
    holder = getattr(request.state, "cp_context", None)
    if holder is None:
        holder = RequestContextHolder()
        request.state.cp_context = holder
    return holder


#: one `%XX` escape; anything else that looks like a percent sign is part of the name.
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def _repair_latin1(value: str) -> str:
    """Put the bytes a UTF-8 terminal sent back into the name the operator typed.

    Starlette decodes a header value as ISO-8859-1, so the documented `curl -H 'X-Operator: 张三'`
    arrives as `å¼ ä¸‰` — a name nobody typed, recorded in the audit trail forever. Re-reading those
    bytes as UTF-8 recovers the original. A genuinely Latin-1 name is left alone, because a lone
    `é` byte is not valid UTF-8 and the strict decode refuses to guess.
    """
    if value.isascii():
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def decode_operator(raw: str | None) -> str:
    """Read the declared name out of ``X-Operator`` (CP-API-002).

    A browser cannot put 张三 in a header value at all: the Fetch standard limits header strings to
    ISO-8859-1 and Chromium throws before the request leaves the page — which is worse than a wrong
    name, because it reads as "the server is unreachable". Both consoles therefore send
    ``encodeURIComponent(name)`` when the name does not fit in Latin-1, and this is where it comes
    back apart.

    A name that is already transportable is used verbatim, so every client written before this rule
    keeps working, including a `curl -H` with an ASCII name. And a percent sign that is not an
    escape ("100%") survives, because decoding something the client did not encode would invent an
    identity the operator never typed.
    """
    value = (raw or "").strip()
    if "%" not in value:
        return _repair_latin1(value)
    if _PERCENT_ESCAPE.sub("", value).find("%") >= 0:
        return _repair_latin1(value)
    try:
        return _repair_latin1(unquote(value, encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, ValueError):
        return _repair_latin1(value)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Establish operator + correlation + language for every request (CP-API-002/006)."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        holder = context_of(request)
        holder.correlation_id = request.headers.get(CORRELATION_HEADER) or new_correlation_id()
        holder.traceparent = request.headers.get(TRACEPARENT_HEADER) or _synthetic_traceparent(holder.correlation_id)
        holder.language = negotiate_language(request.headers.get("Accept-Language"))

        operator = decode_operator(request.headers.get(OPERATOR_HEADER))
        # CP-API-002's declared name is what identifies a change in the MVP; once M4 is on,
        # SessionMiddleware has already put the authenticated subject in its place, and asking
        # for a header the client cannot authorise with would make the two systems disagree.
        enforced = _auth_is_enforced(request)
        if request.method in MUTATING_METHODS and not operator and not enforced:
            return _error_response(
                request,
                code=OperatorRequiredError.code,
                message="X-Operator is required for state-changing requests",
                status=400,
                language=holder.language,
                correlation_id=holder.correlation_id,
                # SRS 8.3: name the header, so a client can add it rather than guess it.
                details={"header": OPERATOR_HEADER},
            )
        holder.operator = operator or "system"

        with bind_operation_context(
            correlation_id=holder.correlation_id,
            operator_name=holder.operator,
            operator_type=OperatorType.USER if operator else OperatorType.SYSTEM,
            source_subsystem="S1",
        ):
            response = await call_next(request)

        response.headers[CORRELATION_HEADER] = holder.correlation_id
        response.headers[TRACEPARENT_HEADER] = holder.traceparent
        return response


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Replay, conflict and in-progress handling (CP-API-003, CP-AC-02)."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        holder = context_of(request)
        key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
        if request.method not in IDEMPOTENT_METHODS or not key:
            return await call_next(request)
        if any(fragment in request.url.path for fragment in NON_IDEMPOTENT_PATH_FRAGMENTS):
            return await call_next(request)

        body = await request.body()
        scope = scope_of(request)
        digest = structure_hash({"body": body.decode("utf-8", errors="replace"), "query": str(request.url.query)})


        try:
            begun = idempotency.begin(scope, key, digest, expires_at=_expiry())
        except IdempotencyConflictError as error:
            return _error_response(
                request,
                code=error.error_code,
                message=error.message,
                status=http_status_for(error.error_code),
                language=holder.language,
                correlation_id=holder.correlation_id,
                details=error.details,
            )

        if not begun.created:
            if begun.status.value == "IN_PROGRESS":
                in_progress = RequestInProgressError(
                    "a request with this idempotency key is still running",
                    details={"scope": scope, "idempotencyKey": key},
                )
                return _error_response(
                    request,
                    code=in_progress.error_code,
                    message=in_progress.message,
                    status=409,
                    language=holder.language,
                    correlation_id=holder.correlation_id,
                    details=in_progress.details,
                    retryable=True,
                )
            replayed = _replay(scope, key)
            if replayed is not None:
                return replayed

        response = await call_next(request)
        stored = await _record_response(response, scope=scope, key=key)
        return stored if stored is not None else response


def _is_replayable(response: Response) -> bool:
    """Anything with a JSON body, or no body media type at all, is worth storing.

    Starlette hands middleware a streaming response whose ``media_type`` is often
    unset, so treating that as "not replayable" would leave the idempotency record
    stuck in IN_PROGRESS and turn a legitimate replay into 409 REQUEST_IN_PROGRESS.
    """
    media = str(getattr(response, "media_type", "") or "")
    if int(response.status_code) >= 500:
        return False
    return not media or media.startswith("application/json")


def _expiry() -> Any:
    from datetime import timedelta

    with _short_session() as session:
        from factory_control.settings import load_settings_snapshot

        hours = load_settings_snapshot(session).idempotency_retention_hours()
    return utcnow() + timedelta(hours=hours)


def _short_session() -> Any:
    from factory_control.foundation import unit_of_work

    return unit_of_work()


def _replay(scope: str, key: str) -> Response | None:
    from factory_control import store as S
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        row = S.idempotent_response(session, scope, key)
        if row is None:
            return None
        body = row.body.encode("utf-8")
        return Response(
            content=body,
            status_code=row.status_code,
            media_type=row.content_type,
            headers={"X-Idempotent-Replay": "true"},
        )


async def _record_response(response: Response, *, scope: str, key: str) -> Response | None:
    """Buffer a JSON response, store it for replay, and return the rebuilt one."""
    from factory_control import store as S
    from factory_control.foundation import unit_of_work

    if not _is_replayable(response):
        with unit_of_work() as session:
            if int(response.status_code) >= 500:
                idempotency.fail(scope, key, error_code="INTERNAL_ERROR", session=session)
        return None

    chunks: list[bytes] = []
    async for chunk in response.body_iterator:  # type: ignore[attr-defined]
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    body = b"".join(chunks)

    with unit_of_work() as session:
        row = S.idempotent_response(session, scope, key)
        response_id = row.id if row is not None else new_id("idr")
        if row is None:
            S.save_idempotent_response(
                session,
                row_id=response_id,
                scope=scope,
                key=key,
                status_code=int(response.status_code),
                content_type=str(response.media_type or "application/json"),
                body=body.decode("utf-8", errors="replace"),
                expires_at=_expiry(),
            )
        idempotency.complete(scope, key, {"responseId": response_id}, session=session)

    rebuilt = Response(
        content=body,
        status_code=int(response.status_code),
        media_type=str(response.media_type or "application/json"),
        headers=dict(response.headers),
    )
    rebuilt.headers["X-Idempotency-Stored"] = response_id
    return rebuilt


def _synthetic_traceparent(correlation_id: str) -> str:
    """A W3C traceparent with a deterministic-enough span id from the correlation id."""
    hexish = correlation_id.replace("-", "").encode("utf-8").hex()
    trace = (hexish + "0" * 32)[:32]
    span = (hexish[::-1] + "0" * 16)[:16]
    return f"00-{trace}-{span}-01"


def _error_response(
    request: Request,
    *,
    code: str,
    message: str,
    status: int,
    language: str,
    correlation_id: str,
    details: dict[str, Any] | None = None,
    retryable: bool | None = None,
) -> JSONResponse:
    body = error_body(
        code=code,
        message=message,
        language=language,
        correlation_id=correlation_id,
        retryable=retryable,
        details=details,
        request=request,
    )
    return JSONResponse(status_code=status, content=body, headers={CORRELATION_HEADER: correlation_id})


class SessionMiddleware(BaseHTTPMiddleware):
    """Turn a token or a session cookie into an identity (CP-IAM-002, FR-SEC-001/005).

    Inert when ``FACTORY_AUTH_MODE=off``: the declared ``X-Operator`` name stays the identity and
    nothing here refuses a request, which is what lets an M1/M2 deployment and its client keep
    working unchanged.  From ``local``/``oidc``/``both`` onward every ``/api/v1`` request must
    present a live session, and a state-changing one presented by *cookie* must also carry the
    CSRF value — a cookie is sent by the browser on its own, which is precisely the property
    double-submit exists to break.
    """

    #: the endpoints that cannot require a session, because establishing one is their job
    PUBLIC_PATHS = frozenset(
        {
            "/api/v1/auth/login",
            "/api/v1/auth/oidc/request",
            "/api/v1/auth/oidc/exchange",
            # The session read is public for a different reason: its whole purpose is to tell a
            # signed-out caller which regime is running (`authMode`), so that S5 can distinguish
            # "no accounts here" from "sign in" without inferring it from a 401.  Guarding it would
            # leave the 401 as the only signal — and the 401 carries no regime (UI-AUTH-001).
            "/api/v1/auth/session",
        }
    )

    def __init__(self, app: Any, *, runtime: Any = None) -> None:
        super().__init__(app)
        self._runtime_override = runtime

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        holder = context_of(request)
        config = self._settings(request)
        if config is None or not config.auth_is_enforced():
            return await call_next(request)
        if not request.url.path.startswith("/api/v1") or request.url.path in UNAUTHENTICATED_PATHS:
            return await call_next(request)

        from factory_control.foundation import unit_of_work
        from factory_control.identity import directory, sessions
        from factory_control.models import User

        token, from_cookie = _session_token(request, config.cookie_name)
        if token is None:
            if request.url.path in self.PUBLIC_PATHS:
                return await call_next(request)
            return _error_response(
                request,
                code="UNAUTHENTICATED",
                message="a session is required; sign in at POST /api/v1/auth/login",
                status=401,
                language=holder.language,
                correlation_id=holder.correlation_id,
                details={"header": "Authorization", "cookie": config.cookie_name},
            )
        # one short transaction: the session row, the account behind it and the roles the
        # policy will re-check per action.  ``resolve`` slides the idle clock, so this write is
        # the request's own bookkeeping and commits with it (FR-SEC-005).
        with unit_of_work() as session:
            resolved = sessions.resolve(session, token, idle_minutes=config.session_idle_minutes)
            principal_user_id = principal_subject = principal_display = ""
            principal_roles: tuple[str, ...] = ()
            if resolved is not None:
                account: User | None = session.get(User, resolved.user_id)
                # a disabled account is no session: an administrator's "disable" must end the
                # person's access now, not when their token happens to expire
                if account is None or account.status != "ACTIVE":
                    resolved = None
                else:
                    principal_user_id = resolved.user_id
                    principal_subject = str(account.subject)
                    principal_display = str(account.display_name or account.subject)
                    principal_roles = tuple(directory.roles_of(session, resolved.user_id, None))
        if resolved is None:
            if request.url.path in self.PUBLIC_PATHS:
                return await call_next(request)
            return _error_response(
                request,
                code="UNAUTHENTICATED",
                message="the session is expired, revoked or unknown",
                status=401,
                language=holder.language,
                correlation_id=holder.correlation_id,
            )
        if request.method in MUTATING_METHODS and from_cookie:
            presented = request.headers.get(config.csrf_header)
            if not sessions.csrf_matches(resolved.csrf_token_hash, presented):
                return _error_response(
                    request,
                    code="CSRF_FAILED",
                    message=f"a cookie-authenticated change must carry {config.csrf_header}",
                    status=403,
                    language=holder.language,
                    correlation_id=holder.correlation_id,
                    details={"header": config.csrf_header},
                )
        holder.user_id = principal_user_id
        holder.operator = principal_subject or "system"
        holder.display_name = principal_display or principal_subject
        holder.session_id = resolved.session_id
        holder.csrf_token_hash = resolved.csrf_token_hash
        holder.auth_source = resolved.auth_source
        holder.roles = principal_roles

        # ``RequestContextMiddleware`` wraps this call, and it runs *outside* here: it bound the
        # operation context from ``X-Operator`` before this middleware knew who was signed in
        # (app.py adds TrafficLog → RequestContext → Session, and the last one added is outermost).
        # Anything that reads the S0 context instead of ``ControlContext.operator`` therefore saw
        # ``operator_name="system"`` for a fully authenticated request — ``PUT /api/v1/settings``
        # is the concrete case, auditing and stamping ``updated_by`` from
        # ``current_context_or_default()``.  Re-binding here is what makes the innermost context
        # the truth for everything downstream: S0's bind layers (``OperationContext.with_``), so a
        # nested re-bind replaces the fields it names and keeps correlation id and subsystem.
        # Under enforcement the session principal wins over the ``X-Operator`` header, which is
        # the point: a declared name is not evidence of identity once there is one.
        with bind_operation_context(
            operator_name=holder.operator,
            operator_type=OperatorType.USER,
        ):
            response = await call_next(request)
        return response

    def _settings(self, request: Request) -> Any:
        runtime = self._runtime_override or getattr(request.app.state, "runtime", None)
        return getattr(runtime, "settings", None)


def _session_token(request: Request, cookie_name: str) -> tuple[str | None, bool]:
    """Bearer first, then the session cookie (FR-SEC-005: both are HTTPS-only paths)."""
    header = (request.headers.get("Authorization") or "").strip()
    if header.lower().startswith("bearer ") and header[7:].strip():
        return header[7:].strip(), False
    cookie = (request.cookies.get(cookie_name) or "").strip()
    return (cookie, True) if cookie else (None, False)


#: Liveness and the document itself are not identity-bearing endpoints; a deployment's health
#: check has no session, and ``/docs`` is served from the same document as ``/openapi.json``.
UNAUTHENTICATED_PATHS = frozenset(
    {
        "/healthz",
        "/readyz",
        "/api/v1/openapi.json",
        "/api/v1/docs",
        "/api/v1/redoc",
    }
)


__all__ = [
    "CORRELATION_HEADER",
    "IDEMPOTENCY_HEADER",
    "IdempotencyMiddleware",
    "OPERATOR_HEADER",
    "RequestContextMiddleware",
    "SessionMiddleware",
    "UNAUTHENTICATED_PATHS",
    "context_of",
]


def _auth_is_enforced(request: Request) -> bool:
    """Whether M4 identity is running, without importing the config at module scope."""
    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    return bool(getattr(settings, "auth_is_enforced", lambda: False)())
