"""The cold-start facts endpoint (S5IF-SET01).

One GET that answers "is this deployment configured yet, and what does the server actually see",
so a console can draw a checklist instead of rendering four empty tables and a wizard that cannot be
completed.  It reports facts only — the availability of every control keeps coming from
``allowed-actions`` (FR-UI-009), and this module writes nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from factory_control import iam
from factory_control.rest import route_meta
from factory_control.rest.deps import authorize, operator_of, runtime_of, user_id_of
from factory_control.rest.schemas import setup as SS
from factory_control.services import setup as SETUP

router = APIRouter(prefix="/api/v1", tags=["setup"])

_ERRORS = route_meta.error_responses(400, 403, 422, 502, 503)


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.get(
    "/setup",
    response_model=SS.SetupView,
    summary="What the deployment is configured with, and what is still missing",
    responses=_ERRORS,
    openapi_extra=_extras("S5IF-SET01", "FR-UI-009"),
)
def read_setup(request: Request) -> dict[str, Any]:
    """The read right is the platform listing's, so an answer discloses no more than it does.

    ``GET /environments``, ``GET /governance/providers`` and ``GET /projects`` each already answer
    for themselves; this endpoint is their counts in one round trip plus the two things no console
    can otherwise see (the provider real/fake mode and ``validate_runtime``'s problems).  A caller
    who may not read governance gets ``intelligence: null`` rather than a zero.
    """
    authorize(request, iam.PROJECT_VIEW)
    return SETUP.readiness(
        runtime_of(request), operator=operator_of(request), user_id=user_id_of(request)
    )
