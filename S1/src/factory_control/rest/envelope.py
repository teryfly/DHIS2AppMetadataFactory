"""The :class:`FactoryError` handler's response (SRS 8.3, CP-API-008).

The body itself comes from :mod:`factory_control.rest.failure`, which the middleware uses
too; this module only puts the status and the correlation header around it.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from factory_control.errors import http_status_for, normalize_provider_error
from factory_control.foundation import FactoryError
from factory_control.rest.failure import error_body
from factory_control.rest.middleware import CORRELATION_HEADER, context_of


def envelope(request: Request, error: FactoryError) -> JSONResponse:
    holder = context_of(request)
    # A provider code that duplicates an S1 one is reported as the S1 one, so S5 maps
    # a single catalogue for every endpoint — forwarded or not (S5IF-G07.2).
    reported = normalize_provider_error(error)
    return JSONResponse(
        status_code=http_status_for(reported.error_code),
        content=error_body(
            code=reported.error_code,
            message=reported.message,
            language=holder.language,
            correlation_id=holder.correlation_id,
            stage=reported.stage,
            artifact_id=reported.artifact_id,
            retryable=reported.retryable,
            details=reported.details,
            request=request,
        ),
        headers={CORRELATION_HEADER: holder.correlation_id},
    )


__all__ = ["envelope"]
