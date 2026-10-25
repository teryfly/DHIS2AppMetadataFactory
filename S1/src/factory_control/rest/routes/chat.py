"""In-stage dialogue endpoints (AI-CHAT-001/002/003, FR-UI-002).

The SRS's 8.1 table predates these paths — S3 scheduled in-stage chat for M3 and S1's endpoint
list was written for M1/M2 — so the operations cite the requirements that govern them rather
than a new number: ``CP-API-007`` (a forwarded answer is S3's, not cached), ``CP-API-008`` and
``CP-API-010`` (the envelope and traceability every operation carries), plus FR-UI-002 and
AI-CHAT-001/002/003 from the SRS that asks for the capability.

A turn is synchronous, like ``POST /artifacts/{id}/explain``: it is one model call answering one
question, not a stage run, so it is not in CP-API-012's 202 list.  The ``Idempotency-Key`` is
honoured anyway, because a client that retries a turn it did not receive should not pay for a
second one (00 doc 6.4).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from factory_control import iam
from factory_control.rest import route_meta
from factory_control.rest.deps import authorize_stage, language_of, operator_of, runtime_of
from factory_control.rest.schemas import chat as CHS
from factory_control.services import chat as CHAT

router = APIRouter(prefix="/api/v1", tags=["dialogue"])

_ERRORS = route_meta.error_responses(400, 403, 404, 409, 422, 502, 503, 504)

#: The SRS numbers that govern these operations.  ``CP-*`` is what the contract test requires
#: every operation to carry (S5IF-AC15); the rest are the requirements the endpoints answer to.
_REQUIREMENTS = (
    "CP-API-007",
    "CP-API-008",
    "CP-API-010",
    "FR-UI-002",
    "AI-CHAT-001",
    "AI-CHAT-002",
    "AI-CHAT-003",
)


def _extras(*, mutating: bool = False, idempotent: bool = False) -> dict[str, Any]:
    return route_meta.openapi_extra(*_REQUIREMENTS, mutating=mutating, idempotent=idempotent)


@router.post(
    "/stages/{stage_id}/chat",
    response_model=CHS.ChatTurnView,
    response_model_exclude_none=True,
    summary="Ask the stage's Review Assistant one question, and change the draft if you asked it to",
    responses=_ERRORS,
    openapi_extra=_extras(mutating=True, idempotent=True),
)
def chat_turn(
    request: Request, stage_id: str, payload: CHS.ChatTurnRequest = Body(...)
) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.STAGE_CHAT)
    return CHAT.turn(
        runtime_of(request),
        stage_id,
        payload.message,
        operator=operator_of(request),
        language=language_of(request),
        idempotency_key=request.headers.get("Idempotency-Key"),
    )


@router.get(
    "/stages/{stage_id}/chat",
    response_model=CHS.ChatTranscriptView,
    summary="The stage's conversation so far, with the context header beside it",
    responses=_ERRORS,
    openapi_extra=_extras(),
)
def chat_transcript(request: Request, stage_id: str) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.PROJECT_VIEW)
    return CHAT.transcript(runtime_of(request), stage_id, operator=operator_of(request))


@router.delete(
    "/stages/{stage_id}/chat",
    response_model=CHS.ChatClearedView,
    summary="Start the stage's conversation again; the previous transcript stays auditable",
    responses=_ERRORS,
    openapi_extra=_extras(mutating=True),
)
def clear_chat(request: Request, stage_id: str) -> dict[str, Any]:
    authorize_stage(request, stage_id, iam.STAGE_CHAT)
    return CHAT.clear(runtime_of(request), stage_id, operator=operator_of(request))
