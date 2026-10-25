"""The public REST + SSE surface (SRS 8.1, CP-API-001 … CP-API-009).

Handlers live in :mod:`factory_control.rest.routes`, one module per resource group,
and stay thin: negotiate the operator, call the authorization hook, delegate to a
service, and let any :class:`FactoryError` fall through to the single error
envelope.  This module only assembles the application.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from factory_control.errors import ProviderUnavailableControlError, ValidationFailedError
from factory_control.events.bus import EventBus
from factory_control.foundation import FactoryError
from factory_control.i18n import message_for
from factory_control.ports.protocols import ProviderUnavailableError
from factory_control.rest.envelope import envelope
from factory_control.rest.failure import error_body
from factory_control.rest.middleware import (
    CORRELATION_HEADER,
    IdempotencyMiddleware,
    RequestContextMiddleware,
    SessionMiddleware,
    context_of,
)
from factory_control.rest.routes import routers
from factory_control.rest.traffic import TrafficLogMiddleware
from factory_control.runtime import Runtime


def create_app(runtime: Runtime | None = None, *, bus: EventBus | None = None) -> FastAPI:
    """Build the ASGI application.  ``runtime`` is injected by tests."""
    app = FastAPI(
        title="S1 Factory Control Plane",
        version="0.1.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.add_middleware(IdempotencyMiddleware)
    # between the request context and the replay guard: the identity has to exist before an
    # idempotent replay is judged, or a stolen key could replay somebody else's answer
    app.add_middleware(SessionMiddleware)
    app.add_middleware(RequestContextMiddleware)
    # added last, so it runs outermost: every refusal below this layer — a handler raising a
    # Gate conflict, the replay guard, the session check — is a status some operation answered
    # with, and CP-API-010 asks the document to describe all of them.
    app.add_middleware(TrafficLogMiddleware)

    resolved_runtime = runtime or _get_runtime()
    app.state.runtime = resolved_runtime
    app.state.bus = bus or resolved_runtime.bus

    @app.exception_handler(FactoryError)
    async def _factory_error_handler(request: Request, exc: FactoryError) -> JSONResponse:
        return envelope(request, exc)

    @app.exception_handler(ProviderUnavailableError)
    async def _provider_error_handler(request: Request, exc: ProviderUnavailableError) -> JSONResponse:
        # A provider that is not installed, or that has not exported the function S1
        # calls (S5IF-UP08), is an unavailable dependency — not an S1 crash.
        return envelope(request, ProviderUnavailableControlError(str(exc)))

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # The document already promises every 422 answers with the SRS 8.3 envelope;
        # FastAPI's own body ({"detail": [...]}) would be the one error a generated
        # client could not parse.  Pydantic's message is English and can run to several
        # sentences, so the shown text is the catalogue's own.
        holder = context_of(request)
        return JSONResponse(
            status_code=422,
            content=error_body(
                code=ValidationFailedError.code,
                message=_without_secret_input(str(exc), exc),
                language=holder.language,
                correlation_id=holder.correlation_id,
                details={"fields": _rejected_fields(exc)},
                request=request,
            ),
            headers={CORRELATION_HEADER: holder.correlation_id},
        )

    @app.exception_handler(Exception)
    async def _unknown_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # CP-API-008: an unexpected failure never leaks internals.
        holder = context_of(request)
        return JSONResponse(
            status_code=500,
            content={
                "errorCode": "INTERNAL_ERROR",
                "message": message_for("INTERNAL_ERROR", holder.language),
                "stage": None,
                "artifactId": None,
                "retryable": False,
                "correlationId": holder.correlation_id,
                "details": {},
            },
        )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        from factory_control.adapters.registry import provider_modes
        from factory_control.api_service import get_startup_report

        engine = resolved_runtime.engine
        return {
            "status": "ok",
            "engine": engine.status().__dict__ if engine is not None else None,
            "providers": provider_modes(
                {
                    "blueprint": resolved_runtime.blueprint,
                    "intelligence": resolved_runtime.intelligence,
                    "execution": resolved_runtime.execution,
                }
            ),
            "startup": get_startup_report().as_dict() if get_startup_report() else None,
        }

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        """The database is actually reachable, which ``/healthz`` never proved (CP-RT-004).

        ``/healthz`` answers from process wiring: it says 200 with PostgreSQL down, which is
        exactly what a load detector must not be shown.  This one runs one statement through the
        runtime's own unit of work, so it fails when the engine, the connection or the ``cp``
        schema fails.

        ``include_in_schema=False`` is load-bearing, not tidiness: this is an infrastructure
        probe, never a client-facing operation, and every documented path is part of the exported
        contract S5 and S5M generate from (S5IF-G11).  It stays off the document so the contract
        does not acquire an endpoint no console may call.  It is whitelisted from authentication
        in :data:`factory_control.rest.middleware.UNAUTHENTICATED_PATHS` and is not proxied by
        nginx — liveness and readiness are checked on the machine that runs the service.
        """
        from sqlalchemy import text

        try:
            with resolved_runtime.read_only(operator="system") as ctx:
                ctx.session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - readiness reports, it never raises a 500
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": type(exc).__name__},
            )
        return JSONResponse(status_code=200, content={"status": "ready"})

    for router in routers():
        app.include_router(router)
    _install_document_augmentation(app)
    return app


def _install_document_augmentation(app: FastAPI) -> None:
    """Make the published document and the served one identical (S5IF-G05 / G11).

    ``scripts/export_openapi.py`` calls ``create_app(...).openapi()``, so overriding
    the generator here means the file S5 reads cannot drift from ``/api/v1/openapi.json``.
    """
    from factory_control.rest.openapi import augment

    build = app.openapi

    def openapi() -> dict[str, Any]:
        return augment(build())

    app.openapi = openapi  # type: ignore[method-assign]


def _get_runtime() -> Runtime:
    from factory_control.api_service import get_runtime

    return get_runtime()


def _rejected_fields(error: RequestValidationError) -> list[dict[str, str]]:
    """FastAPI's error locations into SRS 8.3 ``fields[{path, message}]``.

    The first element of a pydantic location is the *kind* of input (`body`, `query`, `path`,
    `header`), not a field of the resource, so it is dropped — `body.name` reads as `name`, which is
    the name a form or a generated client already uses. Only that first element is dropped: a real
    field called `path` must still come back as `path`, not as the empty string.
    """
    fields: list[dict[str, str]] = []
    for item in error.errors():
        parts = [str(part) for part in (item.get("loc") or ())]
        if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
            parts = parts[1:]
        fields.append({"path": ".".join(parts), "message": str(item.get("msg") or "invalid value")})
    return fields


#: Request fields that carry a secret value (S5IF-SEC01).  A validation failure on one of them
#: must not quote it back — see :func:`_without_secret_input`.  ``password`` joined ``material``
#: because the account endpoints have the same exposure: a ``password`` submitted as the wrong type
#: is rendered by pydantic with the value in it, and a 422 body is exactly the text an operator
#: pastes into a chat when the login screen confuses them (FR-SEC-008).
OPAQUE_FIELDS: frozenset[str] = frozenset({"material", "password"})


def _without_secret_input(message: str, error: RequestValidationError) -> str:
    """Strip a rejected value out of pydantic's own rendering of the failure.

    Two layers keep a credential out of a 422, and this is the second one.  The first is
    :func:`factory_control.rest.failure.error_body`: the `message` it returns is the catalogue's own
    sentence for the code, so pydantic's rendering — which carries the submitted value as
    `input_value=...` — is normally discarded here.  The second layer exists because that first one
    is a fallback chain, not a guarantee: `message_for` returns what it is handed when a code has no
    catalogue text, and this same string is what a future log line or error report would carry.
    A defence that only works while a translation table stays populated is not a defence.

    The per-field messages in :func:`_rejected_fields` never contained the value, so the form still
    says what is wrong with the field.

    A credential sent as the wrong type arrives as whatever the client sent — a number, an object —
    so what has to be masked is the input's string form *and* every value inside it, at any depth,
    which is how a dict body stops smuggling the token through a type error.
    """
    for item in error.errors():
        path = [str(part) for part in (item.get("loc") or ())]
        if not any(part in OPAQUE_FIELDS for part in path):
            continue
        # pydantic v2 puts the submitted value under "input"; "ctx" holds the rule's own extras
        # (min_length and friends) and may also carry an input rendering on some error types.
        value = item.get("input")
        if value is None:
            value = (item.get("ctx") or {}).get("input")
        for candidate in _renderings(value):
            message = message.replace(candidate, _SECRET_MASK)
    return message


def _renderings(value: object) -> list[str]:
    """Every way pydantic might have written this input into the message, longest first."""
    found: set[str] = set()
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            found |= {str(current)}
            stack.extend(current.values())
        elif isinstance(current, (list, tuple, set)):
            found |= {str(current)}
            stack.extend(current)
        elif current is not None:
            found.add(str(current))
    return sorted((text for text in found if text and text != _SECRET_MASK), key=len, reverse=True)


_SECRET_MASK = "***"

__all__ = ["create_app"]
