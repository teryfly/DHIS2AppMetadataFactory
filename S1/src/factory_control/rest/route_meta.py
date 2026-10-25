"""Per-operation OpenAPI annotation: the ``x-requirements`` tag and shared header refs.

``openapi_extra`` is merged onto the *finished* operation by FastAPI, and its
``parameters`` list is **concatenated** with the generated one — so a route can
point at a header component without disturbing its path and query parameters.
The components themselves are declared once in :mod:`factory_control.rest.openapi`.
"""

from __future__ import annotations

from typing import Any

#: Cross-cutting headers (S5IF-G05). Bodies live in ``components.parameters``.
OPERATOR_REF = {"$ref": "#/components/parameters/XOperator"}
IDEMPOTENCY_REF = {"$ref": "#/components/parameters/XIdempotencyKey"}
LANGUAGE_REF = {"$ref": "#/components/parameters/XAcceptLanguage"}
CORRELATION_REF = {"$ref": "#/components/parameters/XCorrelationId"}
TRACEPARENT_REF = {"$ref": "#/components/parameters/XTraceparent"}

_ERROR_REF = {"$ref": "#/components/responses/Error"}


def openapi_extra(
    *requirements: str,
    mutating: bool = False,
    idempotent: bool = False,
) -> dict[str, Any]:
    """The ``openapi_extra=`` payload for one operation.

    ``mutating`` marks a state-changing method, where ``X-Operator`` is not merely
    allowed but required — the middleware rejects the call with 400 without it, so
    the contract has to say so or a generated client will build requests that
    cannot succeed.

    The ids are S1's own ``CP-*`` numbers and the ``S5IF-*`` numbers of the gap
    document, in one generic field (S5IF-G11): S5's drift check and the test team's
    coverage matrix read the same array.
    """
    parameters: list[dict[str, Any]] = [LANGUAGE_REF, CORRELATION_REF, TRACEPARENT_REF]
    if mutating:
        parameters.insert(0, OPERATOR_REF)
    if idempotent:
        parameters.insert(0, IDEMPOTENCY_REF)
    extra: dict[str, Any] = {"parameters": parameters}
    if requirements:
        extra["x-requirements"] = list(requirements)
    return extra


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Attach the shared error envelope to the listed status codes."""
    return {code: dict(_ERROR_REF) for code in codes}


__all__ = ["error_responses", "openapi_extra"]
