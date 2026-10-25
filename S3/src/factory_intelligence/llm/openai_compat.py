"""OpenAI-compatible provider adapter (AI-LLM-003, AI-LLM-004, AI-LLM-005).

One adapter serves the hosted APIs *and* the local ones: AI-LLM-003 puts vLLM and Ollama
behind the same code path by registering *their* address, because both expose
``/v1/chat/completions``.  That is also why the endpoint is data in the Model Registry
rather than a constant here — a deployment adds a model by registering it, not by adding
an adapter file (AI-NFR-05).

The key never lives here.  ``api_key_secret_ref`` is resolved through S0 at request time,
so a key is absent from every log line, audit payload, exception and DTO this module can
produce (AI-LLM-005, 00 doc 6.9).  The exception paths are deliberately the narrowest
code in the file: a provider failure may report a status, a request id and a truncated
provider message, and never ``repr()`` of the request it failed on — that object holds
the ``Authorization`` header.  Even the vendor's own words are redacted first, because a
gateway that quotes the rejected request back would otherwise hand the key to a log line.

The ``transport`` parameter is an ``httpx.BaseTransport`` and exists so
``tests/integration/test_openai_compat.py`` can assert the whole error matrix without a
network; a real deployment leaves it unset.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from factory_foundation.api import (
    Secret,
    SecretUnavailableError,
    get_logger,
    resolve_secret,
)

from factory_intelligence.config import settings
from factory_intelligence.deps import get_http_transport
from factory_intelligence.llm.errors import (
    RETRYABLE_STATUS,
    LlmError,
    LlmSecretMissing,
    LlmTransportError,
)
from factory_intelligence.llm.tokens import estimate_text
from factory_intelligence.llm.types import (
    ChatMessage,
    HealthStatus,
    ModelResponse,
    ProviderInfo,
    StructuredRequest,
    Usage,
)

_logger = get_logger(__name__)

#: A provider message is cut to this before it goes into an exception, and it is the
#: only free text from the vendor that may: enough for an operator to read, too little to
#: carry a prompt back out of the process.
_MESSAGE_LIMIT = 400

#: Some gateways answer a rejection by quoting the request that caused it back in their own
#: message — including the ``Authorization`` header this adapter just assembled from a
#: resolved Secret.  A vendor's words are therefore only ever reported after a credential has
#: been struck out of them (AI-LLM-005).  One alternation, so one pass: applying these
#: patterns one after another would re-match the text the previous one substituted.
_CREDENTIALS = re.compile(
    r"""(?xi)
      \bbearer\s+[^\s,;\"']{8,}                      # the scheme this adapter sends
    | \bsk-[A-Za-z0-9_-]{12,}\b                      # the shape a key itself has
    | \b(?:api[-_ ]?key|secret|authorization)\b\s*[:=]\s*[\"']?[^\s,;\"']{8,}
    """
)
_REDACTED = "[credential redacted]"


class OpenAICompatProvider:
    """One registered model on an OpenAI-compatible endpoint."""

    def __init__(
        self,
        info: ProviderInfo,
        *,
        endpoint: str,
        api_key_secret_ref: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._info = info
        self._endpoint = endpoint.rstrip("/")
        self._secret_ref = api_key_secret_ref
        self._transport = transport
        self._timeout = timeout_seconds or settings().model_request_timeout_seconds

    @property
    def info(self) -> ProviderInfo:
        return self._info

    # --- generation ------------------------------------------------------------

    def generate_structured(self, request: StructuredRequest) -> ModelResponse:
        """POST ``/chat/completions`` and decode the answer.

        Raises :class:`LlmTransportError` for anything AI-RT-021 lists as auto-retryable
        and :class:`LlmError` otherwise.  An answer that is well-formed JSON but fails the
        schema is *returned*, not raised — deciding that is the runtime's corrective
        retry (AI-RT-011), not the transport's.
        """
        body = self._body(request)
        response, secret = self._post("/chat/completions", body)
        document = self._json_document(response, secret=secret)
        message = _first_choice_message(document)
        text = str(message.get("content") or "")
        finish_reason = _first_choice_field(document, "finish_reason")
        usage = _usage(document)
        return ModelResponse(
            payload=_decode_json(text, truncated=finish_reason == "length"),
            raw_text=text,
            usage=usage,
            model=str(document.get("model") or self._info.model),
            finish_reason=finish_reason,
            request_id=_request_id(response),
            truncated=finish_reason == "length",
        )

    def _body(self, request: StructuredRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._info.model,
            "messages": [_chat_message(message) for message in request.messages],
            "temperature": request.temperature,
        }
        if request.max_output_tokens is not None:
            body["max_tokens"] = request.max_output_tokens
        if self._info.capabilities.structured_output:
            # ``json_schema`` mode with strict=false: vendors reject a schema they cannot
            # compile into a grammar, and a refusal to *constrain* must not fail the run —
            # the runtime validates the answer either way (AI-RT-011).
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.json_schema,
                    "strict": False,
                },
            }
        return body

    # --- the two informational members -----------------------------------------

    def count_tokens(self, text: str) -> int:
        return estimate_text(text)

    def health(self) -> HealthStatus:
        """``GET /models`` with a short budget; never a billable generation."""
        try:
            response, _secret = self._request("GET", "/models", None, timeout=5.0)
        except LlmTransportError as exc:
            return HealthStatus(healthy=False, detail=str(exc.message))
        except LlmError as exc:  # a refusal is still an answer: the endpoint is alive
            return HealthStatus(healthy=True, detail=f"answered with error: {exc.message}")
        return HealthStatus(
            healthy=response.status_code < 400,
            detail=None if response.status_code < 400 else f"HTTP {response.status_code}",
        )

    # --- HTTP plumbing ---------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any]) -> tuple[httpx.Response, Secret | None]:
        return self._request("POST", path, body, timeout=self._timeout)

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None, *, timeout: float
    ) -> tuple[httpx.Response, Secret | None]:
        """One HTTP exchange, and the credential that authorised it, to the caller.

        The two are returned together so the caller can strip the key out of a body the
        vendor echoes back, and so the value exists only as a local in one stack frame —
        never as an attribute of this object (AI-LLM-005).
        """
        secret = self._resolve_secret()
        client_kwargs: dict[str, Any] = {
            "base_url": self._endpoint,
            "timeout": timeout,
            "headers": _auth_headers(secret),
        }
        transport = self._transport or get_http_transport()
        if transport is not None:
            client_kwargs["transport"] = transport
        try:
            with httpx.Client(**client_kwargs) as client:
                response = client.request(method, path, json=body)
        except httpx.TimeoutException as exc:
            raise LlmTransportError(
                f"model request timed out after {timeout}s: {exc.__class__.__name__}",
                details={"path": path},
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            # Connection reset, DNS, protocol errors: transport class, retryable.
            raise LlmTransportError(
                f"model endpoint unreachable: {exc.__class__.__name__}",
                details={"path": path},
            ) from exc
        if response.status_code in RETRYABLE_STATUS:
            # A 429 row in ``ai.model_call`` has to cite the vendor's log the same way a 400
            # row does (AI-LLM-004), so the request id is read for the retryable class too.
            raise LlmTransportError(
                f"provider answered HTTP {response.status_code}",
                status_code=response.status_code,
                retry_after_seconds=_retry_after(response),
                request_id=_request_id(response),
                details={"path": path},
            )
        if response.status_code >= 400:
            raise LlmError(
                _failure_message(response, secret=secret),
                status_code=response.status_code,
                request_id=_request_id(response),
                details={"path": path},
            )
        return response, secret

    def _resolve_secret(self) -> Secret | None:
        """The registration's key reference, or ``None`` for an endpoint that needs none."""
        if not self._secret_ref:
            return None
        try:
            return resolve_secret(self._secret_ref)
        except SecretUnavailableError as exc:
            raise LlmSecretMissing(
                f"the API key reference {self._secret_ref} could not be resolved",
                details={"ref": self._secret_ref},
            ) from exc

    def _json_document(self, response: httpx.Response, *, secret: Secret | None = None) -> dict[str, Any]:
        try:
            document = json.loads(response.text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise LlmError(
                "provider answered with a body that is not JSON: "
                f"{_redact(_truncate(response.text), secret=secret)}",
                status_code=response.status_code,
                request_id=_request_id(response),
            ) from exc
        if not isinstance(document, dict):
            raise LlmError("provider answered with a JSON array instead of an object")
        return document


def provider_for(info: ProviderInfo) -> OpenAICompatProvider:
    """The default factory installed at start-up (AI-LLM-003).

    An endpoint is required: a registered model with no address cannot be reached, and
    guessing one (``api.openai.com``) would send a project's data to a vendor nobody
    approved — which is the exact failure AI-POL-002's deny-by-default exists to stop.
    """
    if not info.endpoint:
        raise LlmError(
            f"model {info.model} has no endpoint configured; register one with the provider",
            details={"model": info.model, "provider": info.provider},
        )
    return OpenAICompatProvider(
        info,
        endpoint=info.endpoint,
        api_key_secret_ref=info.api_key_secret_ref,
    )


# --- response decoding, module level so the tests can exercise each branch -------


def _chat_message(message: ChatMessage) -> dict[str, Any]:
    if not message.images:
        return {"role": message.role, "content": message.content}
    parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
    for image in message.images:
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.media_type};base64,{image.data_base64}"},
            }
        )
    return {"role": message.role, "content": parts}


def _choices(document: dict[str, Any]) -> list[dict[str, Any]]:
    raw = document.get("choices")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _first_choice_message(document: dict[str, Any]) -> dict[str, Any]:
    choices = _choices(document)
    if not choices:
        return {}
    message = choices[0].get("message")
    if isinstance(message, dict):
        return message
    # some gateways answer in the older completions shape
    text = choices[0].get("text")
    return {"content": text} if isinstance(text, str) else {}


def _first_choice_field(document: dict[str, Any], field: str) -> str | None:
    choices = _choices(document)
    if not choices:
        return None
    value = choices[0].get(field)
    return value if isinstance(value, str) else None


def _usage(document: dict[str, Any]) -> Usage:
    raw = document.get("usage")
    if not isinstance(raw, dict):
        return Usage()
    return Usage(
        input_tokens=_int(raw.get("prompt_tokens") or raw.get("input_tokens")),
        output_tokens=_int(raw.get("completion_tokens") or raw.get("output_tokens")),
    )


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _decode_json(text: str, *, truncated: bool) -> dict[str, Any]:
    """Parse the answer, tolerating a fenced block and reporting nothing else.

    A model that wraps its JSON in ```` ```json ```` is answering correctly in form, so the
    fence is removed; a model that answers prose is *wrong*, and the empty mapping lets
    the runtime's schema check say so and spend the one corrective retry on it.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        _, _, tail = stripped.partition("\n")
        stripped = tail.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _request_id(response: httpx.Response) -> str | None:
    for header in ("x-request-id", "request-id", "x-amzn-requestid"):
        value = response.headers.get(header)
        if value:
            return str(value)
    return None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _failure_message(response: httpx.Response, *, secret: Secret | None = None) -> str:
    """Status, and at most a slice of the vendor's own words (AI-LLM-005)."""
    document: Any = None
    try:
        document = response.json()
    except (json.JSONDecodeError, ValueError):
        document = None
    detail = ""
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif isinstance(error, str):
            detail = error
    if not detail:
        detail = _truncate(response.text)
    return f"HTTP {response.status_code}: {_truncate(_redact(detail, secret=secret))}"


def _auth_headers(secret: Secret | None) -> dict[str, str]:
    """The two headers this adapter ever sends.

    ``.reveal()`` is the only place the value becomes a string, in one expression, and it
    goes straight into the header dict; nothing here copies it into a message, a detail or a
    log field.
    """
    headers = {"content-type": "application/json"}
    if secret is not None:
        headers["authorization"] = f"Bearer {secret.reveal()}"
    return headers


def _redact(value: str, *, secret: Secret | None = None) -> str:
    """Strike a credential out of text this adapter did not author.

    Two passes because they catch different things: the shapes a key is echoed in, then the
    exact value this request resolved — a key of unusual format quoted bare is still the
    string we are holding.  The literal replace runs last so its output cannot be re-matched.
    """
    value = _CREDENTIALS.sub(_REDACTED, value)
    if secret is not None:
        credential = secret.reveal()
        if credential:
            value = value.replace(credential, _REDACTED)
    return value


def _truncate(value: str, limit: int = _MESSAGE_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


__all__ = ["OpenAICompatProvider", "provider_for"]
