"""``/api/v1/secrets`` — the credential the operator types (S5IF-SEC01…03).

Three operations, and the shapes are dictated by what must *not* happen:

* No ``GET /secrets/{name}``.  A per-name read would answer 403-or-404 for a name the caller
  invented, which is a working oracle for enumerating credentials — the list endpoint is gated on
  the same right and gives the same information without the oracle.
* ``PUT`` rather than ``POST``.  Creating and rotating are the same statement ("this is the value
  now"), so a retry of a request whose response was lost on the wire is harmless instead of a
  second, different write.  That safety is a property of the operation, not of an
  ``Idempotency-Key``: this path is excluded from the replay table, so the header is neither
  required nor honoured here and the contract says so.
* The response carries a reference, the timestamps and a key fingerprint only.  Together with the
  middleware exclusion for this path — the idempotency replay table stores response bodies verbatim
  — nothing this endpoint returns can be mistaken for the value it just accepted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Path, Request

from factory_control import errors as E
from factory_control import iam
from factory_control.rest import route_meta
from factory_control.rest.deps import authorize, operator_of, runtime_of
from factory_control.rest.schemas import secrets as KS
from factory_control.services import secrets as SECRET

router = APIRouter(prefix="/api/v1", tags=["secrets"])

# Declared per operation rather than shared: a document that advertises a 404 on a route with
# nothing to look up invites a client to write handling for an answer that can never arrive, and
# the contract guard only checks observed ⊆ declared, so a over-broad list stays green forever.
_WRITE_ERRORS = route_meta.error_responses(400, 401, 403, 422, 503)
_DELETE_ERRORS = route_meta.error_responses(400, 401, 403, 404, 409, 422, 503)


def _extras(*ids: str, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*ids, mutating=mutating, idempotent=idempotent)


@router.put(
    "/secrets/{name}",
    response_model=KS.SecretView,
    summary="Store or rotate one credential; the response returns the reference, never the value",
    responses=_WRITE_ERRORS,
    openapi_extra=_extras("S5IF-SEC01", "FR-SEC-006", mutating=True),
)
def write_secret(
    request: Request,
    payload: KS.SecretWriteRequest = Body(...),
    name: str = Path(..., min_length=2, max_length=64),
) -> dict[str, Any]:
    """503 ``SECRET_UNAVAILABLE`` when the deployment has no master key (CP-SEC-001).

    The refusal is deliberate rather than a silent fallback to plaintext storage: an operator who
    typed a token into a browser has already made one risky move, and the answer has to be loud
    enough that they do not assume it was saved.
    """
    authorize(request, iam.SECRET_MANAGE)
    try:
        return SECRET.write(
            runtime_of(request), name=name, material=payload.material, operator=operator_of(request)
        )
    except SECRET.InvalidCredentialName as exc:
        raise E.ValidationFailedError(str(exc), details={"field": "name", "reason": "pattern"}) from exc


@router.get(
    "/secrets",
    response_model=KS.SecretListView,
    summary="Which credentials exist — names, references and timestamps only",
    responses=route_meta.error_responses(401, 403, 503),
    openapi_extra=_extras("S5IF-SEC02"),
)
def list_secrets(request: Request) -> dict[str, Any]:
    """What the console offers to point at, so an operator can reuse a token instead of retyping it."""
    authorize(request, iam.SECRET_MANAGE)
    return SECRET.listing(runtime_of(request), operator=operator_of(request))


@router.delete(
    "/secrets/{name}",
    response_model=KS.SecretDeletedView,
    summary="Remove a credential, once nothing references it",
    responses=_DELETE_ERRORS,
    openapi_extra=_extras("S5IF-SEC03", "CP-SEC-003", mutating=True),
)
def delete_secret(request: Request, name: str = Path(..., min_length=2, max_length=64)) -> dict[str, Any]:
    """409 ``SECRET_IN_USE`` with ``details.referencedBy`` while an environment or provider names it."""
    authorize(request, iam.SECRET_MANAGE)
    try:
        return SECRET.remove(runtime_of(request), name=name, operator=operator_of(request))
    except SECRET.InvalidCredentialName as exc:
        raise E.ValidationFailedError(str(exc), details={"field": "name", "reason": "pattern"}) from exc
