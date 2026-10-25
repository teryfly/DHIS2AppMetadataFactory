"""The parse-time LLM exchange log (operational transcript).

``ai.model_call`` is the billing ledger and holds no prompt or answer (AI-LLM-004,
AI-DEL-001).  Diagnosing a parse needs the opposite: what the model was asked and what it
said.  This module writes that transcript, but only for calls a business-input parse made —
``CallContext.input_artifact_id`` is the switch, and no other caller sets it.

The write is split around the provider call so a slow answer is observable while it is
still in flight.  Both halves use their own short transaction: a provider call must never
run inside a unit of work (00 doc 6.2, ``test_no_model_call_sits_inside_a_transaction``).

The transcript quotes the input, so it is not a permanent product: ``expires_at`` bounds it
(the daily purge job deletes expired rows), and ``delete_project_data`` removes it with the
project.  Images are stored as metadata — the base64 payload stays in the call.
"""

from __future__ import annotations

import base64
import datetime
from collections.abc import Callable
from typing import Any, TypeVar

from factory_foundation.api import (
    current_session,
    has_active_uow,
    new_id,
    structure_hash,
    unit_of_work,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factory_intelligence.config import settings
from factory_intelligence.cost.usage import CallContext
from factory_intelligence.deps import get_clock
from factory_intelligence.dto.governance import (
    LlmExchangeDetail,
    LlmExchangeImage,
    LlmExchangeMessage,
    LlmExchangePage,
    LlmExchangeRequest,
    LlmExchangeResponse,
    LlmExchangeSummary,
)
from factory_intelligence.llm.registry import RegisteredModel
from factory_intelligence.llm.types import ChatMessage, ModelResponse, StructuredRequest
from factory_intelligence.models.exchange import LlmExchangeRow

T = TypeVar("T")

#: A stored error is a diagnostic, not the vendor's whole response body.
_ERROR_LIMIT = 2000


def _read(session: Session | None, action: Callable[[Session], T]) -> T:
    if session is not None or has_active_uow():
        return action(session if session is not None else current_session())
    with unit_of_work() as sess:
        return action(sess)


def _image_document(data: str, *, media_type: str, label: str) -> dict[str, Any]:
    try:
        size = len(base64.b64decode(data, validate=False))
    except (ValueError, TypeError):  # pragma: no cover - the adapter built this string
        size = 0
    return {
        "mediaType": media_type,
        "label": label,
        # the picture is identified, never inlined: a screenshot would dominate the row
        "contentHash": structure_hash(data),
        "sizeBytes": size,
    }


def _message_document(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "images": [
            _image_document(image.data_base64, media_type=image.media_type, label=image.label)
            for image in message.images
        ],
    }


def request_document(request: StructuredRequest) -> dict[str, Any]:
    """What the provider received, in the shape the log page renders."""
    hint = request.agent_hint
    return {
        "schemaName": request.schema_name,
        "jsonSchema": request.json_schema,
        "temperature": request.temperature,
        "maxOutputTokens": request.max_output_tokens,
        "agentHint": (
            {
                "agentType": hint.agent_type,
                "stage": hint.stage,
                "runId": hint.run_id,
                "attempt": hint.attempt,
            }
            if hint is not None
            else None
        ),
        "messages": [_message_document(message) for message in request.messages],
    }


def _response_documents(response: ModelResponse) -> tuple[str, dict[str, Any], dict[str, Any]]:
    limit = int(settings().llm_exchange_response_limit_chars)
    text = response.raw_text or ""
    storage_truncated = len(text) > limit
    meta = {
        "usage": {
            "inputTokens": response.usage.input_tokens,
            "outputTokens": response.usage.output_tokens,
        },
        "finishReason": response.finish_reason,
        "truncated": response.truncated,
        "model": response.model,
        "requestId": response.request_id,
        "storageTruncated": storage_truncated,
    }
    return text[:limit], response.payload, meta


def open_exchange(
    context: CallContext,
    *,
    model: RegisteredModel,
    request: StructuredRequest,
) -> str | None:
    """Record the call before it is made; ``None`` when this call is not a parse."""
    if not context.input_artifact_id or not context.project_id:
        return None
    expiry = get_clock().now() + datetime.timedelta(
        days=int(settings().llm_exchange_retention_days)
    )
    exchange_id = new_id("aexg")
    row = LlmExchangeRow(
        id=exchange_id,
        project_id=context.project_id,
        input_artifact_id=context.input_artifact_id,
        workflow_id=context.workflow_id,
        sequence=context.sequence,
        call_role=context.call_role,
        provider=model.provider,
        provider_class=model.provider_class.value,
        model=model.model,
        status="IN_FLIGHT",
        request=request_document(request),
        expires_at=expiry,
    )
    with unit_of_work() as session:
        session.add(row)
    return exchange_id


def close_exchange(
    exchange_id: str | None,
    *,
    response: ModelResponse | None,
    error: BaseException | None,
    latency_ms: int,
) -> None:
    """Finish the row started by :func:`open_exchange`; safe if it is already gone."""
    if exchange_id is None:
        return
    with unit_of_work() as session:
        row = session.get(LlmExchangeRow, exchange_id)
        if row is None:  # pragma: no cover - only a retention race can remove it
            return
        row.latency_ms = latency_ms
        row.completed_at = get_clock().now()
        if response is not None:
            text, payload, meta = _response_documents(response)
            row.status = "OK"
            row.response_text = text
            row.response_payload = payload
            row.response_meta = meta
            row.input_tokens = response.usage.input_tokens
            row.output_tokens = response.usage.output_tokens
            request_id = (response.request_id or "").strip()
            row.request_id = request_id[:128] or None
            return
        row.status = "ERROR"
        row.error_code = _error_code(error)
        row.error = _bounded(_error_message(error))
        request_id = str(getattr(error, "request_id", "") or "").strip()
        row.request_id = request_id[:128] or None


def _error_code(error: BaseException | None) -> str:
    if error is None:
        return "PARSE_EXCHANGE_ERROR"
    return str(getattr(error, "error_code", None) or type(error).__name__)[:64]


def _error_message(error: BaseException | None) -> str:
    if error is None:
        return ""
    message = getattr(error, "message", None)
    return str(message if message is not None else error)


def _bounded(text: str) -> str:
    return text if len(text) <= _ERROR_LIMIT else text[:_ERROR_LIMIT] + "…"


def _display_status(row: LlmExchangeRow, *, now: datetime.datetime) -> str:
    """An ``IN_FLIGHT`` row older than any possible attempt never finished."""
    if row.status != "IN_FLIGHT":
        return row.status
    configured = settings()
    stale_after = (
        float(configured.model_request_timeout_seconds)
        + float(configured.transport_wait_budget_seconds)
        + 60.0
    )
    created = row.created_at
    if created is not None and (now - created).total_seconds() > stale_after:
        return "INTERRUPTED"
    return row.status


def _summary(row: LlmExchangeRow, *, now: datetime.datetime) -> LlmExchangeSummary:
    return LlmExchangeSummary(
        exchange_id=row.id,
        sequence=row.sequence,
        status=_display_status(row, now=now),
        provider=row.provider,
        provider_class=row.provider_class,
        model=row.model,
        request_id=row.request_id,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        latency_ms=row.latency_ms,
        error_code=row.error_code,
        error_message=row.error,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _request_of(row: LlmExchangeRow) -> LlmExchangeRequest:
    document = dict(row.request or {})
    messages = [
        LlmExchangeMessage(
            role=str(message.get("role") or ""),
            content=str(message.get("content") or ""),
            images=[
                LlmExchangeImage(
                    media_type=str(image.get("mediaType") or ""),
                    label=str(image.get("label") or ""),
                    content_hash=str(image.get("contentHash") or ""),
                    size_bytes=int(image.get("sizeBytes") or 0),
                )
                for image in message.get("images") or []
            ],
        )
        for message in document.get("messages") or []
    ]
    return LlmExchangeRequest(
        messages=messages,
        schema_name=document.get("schemaName"),
        json_schema=document.get("jsonSchema"),
        temperature=document.get("temperature"),
        max_output_tokens=document.get("maxOutputTokens"),
        agent_hint=document.get("agentHint"),
    )


def _response_of(row: LlmExchangeRow) -> LlmExchangeResponse | None:
    if row.status == "IN_FLIGHT":
        return None
    meta = dict(row.response_meta or {})
    usage = meta.get("usage")
    return LlmExchangeResponse(
        text=row.response_text,
        payload=row.response_payload,
        finish_reason=meta.get("finishReason"),
        truncated=bool(meta.get("truncated")),
        model=meta.get("model"),
        request_id=row.request_id,
        usage={str(k): int(v) for k, v in usage.items()} if isinstance(usage, dict) else None,
        storage_truncated=bool(meta.get("storageTruncated")),
    )


def list_exchanges(
    project_id: str,
    input_artifact_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    session: Session | None = None,
) -> LlmExchangePage:
    """The transcript summaries of one input, oldest first (the order the parse ran them)."""

    def _list(sess: Session) -> LlmExchangePage:
        now = get_clock().now()
        base = select(LlmExchangeRow).where(
            LlmExchangeRow.project_id == project_id,
            LlmExchangeRow.input_artifact_id == input_artifact_id,
        )
        total = sess.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()
        rows = sess.execute(
            base.order_by(LlmExchangeRow.created_at, LlmExchangeRow.id)
            .limit(max(1, min(limit, 200)))
            .offset(max(0, offset))
        ).scalars()
        return LlmExchangePage(
            items=[_summary(row, now=now) for row in rows],
            total=int(total),
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
        )

    return _read(session, _list)


def get_exchange(
    project_id: str,
    input_artifact_id: str,
    exchange_id: str,
    *,
    session: Session | None = None,
) -> LlmExchangeDetail | None:
    """One exchange with its full request and answer, scoped to its input and project."""

    def _get(sess: Session) -> LlmExchangeDetail | None:
        row = sess.execute(
            select(LlmExchangeRow).where(
                LlmExchangeRow.id == exchange_id,
                LlmExchangeRow.project_id == project_id,
                LlmExchangeRow.input_artifact_id == input_artifact_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        summary = _summary(row, now=get_clock().now())
        return LlmExchangeDetail(
            **summary.model_dump(),
            input_artifact_id=row.input_artifact_id,
            request=_request_of(row),
            response=_response_of(row),
            expires_at=row.expires_at,
        )

    return _read(session, _get)


def purge_expired(*, session: Session | None = None) -> int:
    """Delete transcripts past their retention; bounded rows, called by a daily job."""
    from sqlalchemy import delete

    def _purge(sess: Session) -> int:
        result = sess.execute(
            delete(LlmExchangeRow).where(LlmExchangeRow.expires_at < get_clock().now())
        )
        return int(getattr(result, "rowcount", 0) or 0)

    return _read(session, _purge)


__all__ = [
    "close_exchange",
    "get_exchange",
    "list_exchanges",
    "open_exchange",
    "purge_expired",
    "request_document",
]
