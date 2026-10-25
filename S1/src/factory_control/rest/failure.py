"""The one SRS 8.3 failure body (CP-API-008).

Both renderers — the :class:`FactoryError` handler and the middleware that refuses a
request before any handler runs — build their response here, so an error cannot take a
different shape depending on which layer raised it.  This is also where the per-code
``details`` shape is completed: three codes need something only the request knows.

Lives apart from :mod:`factory_control.rest.envelope` because that module reads what this
one's middleware callers set, and Python would otherwise have a cycle.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request

from factory_control.errors import (
    ARTIFACT_PURGED,
    IDEMPOTENCY_CONFLICT,
    REQUEST_IN_PROGRESS,
    VALIDATION_ERROR,
    field_entries,
)
from factory_control.foundation import error_catalog
from factory_control.i18n import message_for

#: The two codes whose ``details`` name the endpoint scope a key collided on.
_SCOPE_CODES = frozenset({IDEMPOTENCY_CONFLICT, REQUEST_IN_PROGRESS})

#: Starlette keeps a converter suffix in the route path (``{path:path}``); OpenAPI names the
#: same parameter ``{path}``.  The endpoint key is dropped into the contract guard and into
#: idempotency scopes, so it is normalised to the spelling the published document uses — a
#: scope that matches no operation is a guard that quietly measures nothing.
_CONVERTER = re.compile(r"\{(\w+):[^}]+\}")


def scope_of(request: Request) -> str:
    """Endpoint scope: method plus the *route template*, so path ids do not fork a key."""
    route = request.scope.get("route")
    template = getattr(route, "path", None) or request.url.path
    return f"{request.method} " + _CONVERTER.sub(r"{\1}", template)


def error_body(
    *,
    code: str,
    message: str,
    language: str,
    correlation_id: str,
    stage: str | None = None,
    artifact_id: str | None = None,
    retryable: bool | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> dict[str, Any]:
    """One failure body, localised, with the code's ``details`` shape filled in.

    ``VALIDATION_ERROR`` gets ``fields[{path, message}]`` whatever the site listed, an
    idempotency refusal gets the endpoint ``scope``, and a purged artifact gets its id
    back from the path — none of those three are knowable where the error is raised.
    """
    shaped = dict(details or {})
    if code == VALIDATION_ERROR:
        shaped["fields"] = field_entries(shaped, message)
    elif request is not None and code in _SCOPE_CODES:
        shaped.setdefault("scope", scope_of(request))
    if code == ARTIFACT_PURGED and request is not None:
        shaped.setdefault("artifactId", str(request.path_params.get("artifact_id") or ""))
    if code == "NOT_FOUND":
        # CP-UI-022: every 404 says whether the object is gone or merely not produced
        # yet; a raise site (or a provider's own details) may already have said so.
        shaped.setdefault("reason", "NOT_EXIST")
    return {
        "errorCode": code,
        "message": message_for(code, language, fallback=message),
        "stage": stage,
        "artifactId": artifact_id,
        "retryable": error_catalog.is_retryable(code) if retryable is None else retryable,
        "correlationId": correlation_id,
        "details": shaped,
    }


__all__ = ["error_body", "scope_of"]
