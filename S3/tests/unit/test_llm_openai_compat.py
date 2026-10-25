"""AI-LLM-003 / 004 / 005: one OpenAI-compatible adapter, driven by an injected transport.

Every test here hands the adapter an ``httpx`` transport and asserts on the request that
arrives and on what the adapter raises, so the whole matrix — the wire format, the
capability-dependent body, the retry classification and the response decoding — is covered
without a network (AI-NFR-05 says the adapter is the *only* place one lives).

The three things a reviewer would otherwise have to take on trust:

* **AI-LLM-003's "vLLM and Ollama come in through the same adapter"** is asserted by
  registering their addresses and checking where the request actually went, so a hardcoded
  vendor host would fail here rather than at a customer's site;
* **AI-RT-021's classification** is asserted in both directions per status: a retryable
  status is a ``LlmTransportError`` and a client error is a ``LlmError``; the runtime spends
  a different budget on each and AI-AC-01 counts calls exactly;
* **AI-RT-011's boundary** is asserted by returning a well-formed answer that fails the
  schema and checking that nothing was raised: the provider decodes, the runtime judges.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from factory_intelligence.config import configure
from factory_intelligence.config import reset as reset_settings
from factory_intelligence.enums import ProviderClass
from factory_intelligence.llm.errors import LlmError, LlmTransportError
from factory_intelligence.llm.openai_compat import OpenAICompatProvider, provider_for
from factory_intelligence.llm.tokens import estimate_text
from factory_intelligence.llm.types import (
    Capabilities,
    ChatMessage,
    ImageInput,
    ProviderInfo,
    StructuredRequest,
    Usage,
)

#: what one scripted call may do: answer with a response, or fail with this exception
_Reply = httpx.Response | Exception

COMPLETIONS = "/chat/completions"

CONTEXT = "consolidate the statements into requirements. " * 40


class ScriptedTransport(httpx.BaseTransport):
    """The whole of the "network" these tests have: record, then replay a script."""

    def __init__(self, *replies: _Reply) -> None:
        self.replies: list[_Reply] = list(replies)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def request_count(self) -> int:
        return len(self.requests)

    def body(self, index: int = 0) -> dict[str, Any]:
        document: Any = json.loads(self.requests[index].content)
        assert isinstance(document, dict)
        return document


def _info(
    *,
    endpoint: str | None = "http://gpu.internal:8000/v1",
    model: str = "qwen-local",
    provider: str = "vllm",
    structured_output: bool = True,
    vision: bool = False,
) -> ProviderInfo:
    return ProviderInfo(
        provider=provider,
        provider_class=ProviderClass.LOCAL_ONLY,
        model=model,
        capabilities=Capabilities(
            structured_output=structured_output, vision=vision, max_context_tokens=32_000
        ),
        endpoint=endpoint,
    )


def _provider(
    transport: httpx.BaseTransport,
    *,
    info: ProviderInfo | None = None,
    endpoint: str = "http://gpu.internal:8000/v1",
    timeout_seconds: float | None = None,
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        info or _info(), endpoint=endpoint, transport=transport, timeout_seconds=timeout_seconds
    )


def _completed(
    content: str = '{"answer": "ok"}',
    *,
    finish_reason: str | None = "stop",
    model: str | None = "qwen-local-2024-06",
    usage: dict[str, Any] | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    choice: dict[str, Any] = {"message": {"role": "assistant", "content": content}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    document: dict[str, Any] = {"choices": [choice]}
    if model is not None:
        document["model"] = model
    if usage is not None:
        document["usage"] = usage
    return httpx.Response(status_code, json=document, headers=headers)


def _request(
    *messages: ChatMessage,
    schema_name: str = "requirements.output",
    schema: dict[str, Any] | None = None,
    temperature: float = 0.0,
    max_output_tokens: int | None = None,
) -> StructuredRequest:
    return StructuredRequest(
        messages=messages or (ChatMessage(role="user", content="extract the fields"),),
        json_schema=schema if schema is not None else {"type": "object", "required": ["answer"]},
        schema_name=schema_name,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


@pytest.fixture()
def transport() -> ScriptedTransport:
    return ScriptedTransport(_completed())


@pytest.fixture(autouse=True)
def _packaged_settings() -> Iterator[None]:
    reset_settings()
    yield
    reset_settings()


# --- AI-LLM-003: the endpoint is registration data, not a constant ----------------


@pytest.mark.parametrize(
    ("endpoint", "expected_url"),
    [
        ("http://gpu.internal:8000/v1", "http://gpu.internal:8000/v1/chat/completions"),
        ("http://localhost:11434/v1/", "http://localhost:11434/v1/chat/completions"),
        ("https://api.internal.example/llama", "https://api.internal.example/llama/chat/completions"),
    ],
    ids=["vllm", "ollama-trailing-slash", "private-gateway"],
)
def test_a_locally_deployed_model_is_reached_at_its_own_address(
    endpoint: str, expected_url: str
) -> None:
    """AI-LLM-003: vLLM and Ollama come in through this same adapter because both expose
    ``/v1/chat/completions``; nothing may be hardcoded to a vendor's host."""
    scripted = ScriptedTransport(_completed())
    provider = _provider(scripted, info=_info(endpoint=endpoint), endpoint=endpoint)
    provider.generate_structured(_request())
    assert scripted.requests[0].method == "POST"
    assert str(scripted.requests[0].url) == expected_url


def test_the_registered_model_name_is_the_one_sent_to_the_endpoint(
    transport: ScriptedTransport,
) -> None:
    provider = _provider(transport, info=_info(model="llama-3.1-70b"))
    provider.generate_structured(_request())
    assert transport.body()["model"] == "llama-3.1-70b"


def test_the_body_carries_exactly_the_fields_the_adapter_controls(
    transport: ScriptedTransport,
) -> None:
    """A body field added casually here becomes a vendor-side rejection there, so the set
    is asserted rather than assumed."""
    _provider(transport).generate_structured(_request(max_output_tokens=512))
    assert set(transport.body()) == {"model", "messages", "temperature", "max_tokens", "response_format"}


def test_the_content_type_is_json_and_no_authorization_is_sent_without_a_secret(
    transport: ScriptedTransport,
) -> None:
    """The negative half of AI-LLM-005: a registration with no key reference must not grow
    one, and a local model that needs no credential is sent no credential-shaped header."""
    _provider(transport).generate_structured(_request())
    headers = transport.requests[0].headers
    assert headers["content-type"] == "application/json"
    assert "authorization" not in headers
    assert not [name for name in headers if "key" in name.lower()]


# --- the constructed messages -----------------------------------------------------


def test_the_turns_are_sent_in_order_with_their_roles(transport: ScriptedTransport) -> None:
    _provider(transport).generate_structured(
        _request(
            ChatMessage(role="system", content="you extract fields"),
            ChatMessage(role="user", content="the document"),
            ChatMessage(role="assistant", content="understood"),
        )
    )
    assert transport.body()["messages"] == [
        {"role": "system", "content": "you extract fields"},
        {"role": "user", "content": "the document"},
        {"role": "assistant", "content": "understood"},
    ]


def test_a_message_with_an_image_is_sent_as_content_parts(transport: ScriptedTransport) -> None:
    """AI-LLM-001's vision half as the wire sees it: the picture travels with the text that
    describes it, which is what keeps a screenshot extraction's provenance in one call
    (AI-INP-005)."""
    provider = _provider(transport, info=_info(vision=True))
    provider.generate_structured(
        _request(
            ChatMessage(
                role="user",
                content="read the form",
                images=(ImageInput("image/png", "iVBORw0KGgo=", "screenshot"),),
            )
        )
    )
    assert transport.body()["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "read the form"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            ],
        }
    ]


def test_a_text_only_message_is_not_sent_as_a_part_list(transport: ScriptedTransport) -> None:
    """The negative half: gateways with no vision support reject a list-shaped ``content``
    outright, so the plain case must stay plain."""
    _provider(transport).generate_structured(_request(ChatMessage(role="user", content="text only")))
    assert transport.body()["messages"] == [{"role": "user", "content": "text only"}]


# --- response_format, capability gated --------------------------------------------


def test_the_schema_is_bound_only_when_the_model_declared_it_can_be() -> None:
    """AI-LLM-002's declaration is what the request shape is built from: a model that did
    not claim structured output gets the schema nowhere in its body, because a vendor
    rejects a ``response_format`` it cannot compile, and that refusal would fail a run whose
    answer was always going to be validated afterwards."""
    declared = ScriptedTransport(_completed())
    _provider(declared, info=_info(structured_output=True)).generate_structured(
        _request(schema_name="mapping.decide", schema={"type": "object", "required": ["answer"]})
    )
    assert declared.body()["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "mapping.decide",
            "schema": {"type": "object", "required": ["answer"]},
            "strict": False,
        },
    }

    undeclared = ScriptedTransport(_completed())
    _provider(undeclared, info=_info(structured_output=False)).generate_structured(
        _request(schema_name="mapping.decide")
    )
    assert "response_format" not in undeclared.body(), sorted(undeclared.body())


def test_the_schema_name_and_the_schema_itself_reach_the_vendor_by_value(
    transport: ScriptedTransport,
) -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    _provider(transport).generate_structured(_request(schema=schema, schema_name="ui.pages"))
    sent = transport.body()["response_format"]["json_schema"]
    assert sent["name"] == "ui.pages"
    assert sent["schema"] == schema
    assert sent["strict"] is False, (
        "a vendor that cannot compile this grammar must degrade, not fail the run (AI-RT-011)"
    )


@pytest.mark.parametrize(
    ("temperature", "max_output_tokens"),
    [(0.0, None), (0.7, None), (0.0, 512), (1.0, 4000)],
    ids=["zero-temp", "warm", "capped", "warm-and-capped"],
)
def test_the_generation_parameters_are_sent_even_when_their_default_is_falsy(
    temperature: float, max_output_tokens: int | None
) -> None:
    """``temperature=0.0`` and ``max_output_tokens=None`` are the two places a truthiness
    test in the builder would silently drop a caller's decision — and AI-LLM-004 records
    both per call, so both must actually go out."""
    scripted = ScriptedTransport(_completed())
    _provider(scripted).generate_structured(
        _request(temperature=temperature, max_output_tokens=max_output_tokens)
    )
    body = scripted.body()
    assert body["temperature"] == temperature
    if max_output_tokens is None:
        assert "max_tokens" not in body, "an unset output cap must not be invented"
    else:
        assert body["max_tokens"] == max_output_tokens


# --- timeouts ---------------------------------------------------------------------


def test_the_request_budget_comes_from_the_settings(transport: ScriptedTransport) -> None:
    """AI-LLM-003: the per-request timeout is a deployment knob, not a literal in the
    adapter, and it is applied to the generation call."""
    configure(model_request_timeout_seconds=33.5)
    _provider(transport).generate_structured(_request())
    assert transport.requests[0].extensions["timeout"]["read"] == pytest.approx(33.5)


def test_an_explicit_budget_overrides_the_configured_one(transport: ScriptedTransport) -> None:
    configure(model_request_timeout_seconds=33.5)
    _provider(transport, timeout_seconds=7.0).generate_structured(_request())
    assert transport.requests[0].extensions["timeout"]["read"] == pytest.approx(7.0)


# --- AI-LLM-004: what an answer carries ------------------------------------------


def test_an_answer_reports_usage_model_and_request_id() -> None:
    """The per-call record AI-LLM-004 asks for is built from what the provider returns: the
    concrete model that answered, the vendor's request id and both token counts."""
    scripted = ScriptedTransport(
        _completed(usage={"prompt_tokens": 1200, "completion_tokens": 340}, headers={"x-request-id": "req-77"})
    )
    response = _provider(scripted).generate_structured(_request())
    assert response.usage == Usage(input_tokens=1200, output_tokens=340)
    assert response.usage.total_tokens == 1540
    assert response.model == "qwen-local-2024-06"
    assert response.request_id == "req-77"
    assert response.finish_reason == "stop"
    assert response.truncated is False
    assert response.payload == {"answer": "ok"}


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 11, "completion_tokens": 7}, Usage(11, 7)),
        ({"input_tokens": 5, "output_tokens": 6}, Usage(5, 6)),
        ({"prompt_tokens": 3}, Usage(3, 0)),
        ({"prompt_tokens": "not-a-number"}, Usage(0, 0)),
        ({"prompt_tokens": -8}, Usage(0, 0)),
        (None, Usage(0, 0)),
    ],
    ids=["openai-spelling", "annotated-spelling", "input-only", "garbage", "negative", "absent"],
)
def test_the_usage_block_is_read_in_whichever_spelling_the_gateway_used(
    usage: dict[str, Any] | None, expected: Usage
) -> None:
    """AI-NFR-03 wants a usage row for every call; the two spellings vendors actually ship
    are both accepted, and a gateway that reports nothing yields a real zero rather than a
    missing measurement."""
    scripted = ScriptedTransport(_completed(usage=usage))
    assert _provider(scripted).generate_structured(_request()).usage == expected


@pytest.mark.parametrize(
    ("document_model", "expected"),
    [("gpt-ish-2024-08", "gpt-ish-2024-08"), (None, "qwen-local")],
    ids=["vendor-names-it", "registration-is-the-fallback"],
)
def test_the_model_that_answered_is_recorded_even_when_the_vendor_stays_silent(
    document_model: str | None, expected: str
) -> None:
    scripted = ScriptedTransport(_completed(model=document_model))
    assert _provider(scripted).generate_structured(_request()).model == expected


@pytest.mark.parametrize(
    ("raw", "payload"),
    [
        ('{"answer": "ok"}', {"answer": "ok"}),
        ('```json\n{"answer": "ok"}\n```', {"answer": "ok"}),
        ('```\n{"answer": "ok"}\n```', {"answer": "ok"}),
        ('  {"answer": "ok"}  ', {"answer": "ok"}),
        ("I am not JSON at all", {}),
        ('["a json array", "not an object"]', {}),
        ('{"a": ', {}),
        ("", {}),
    ],
    ids=["plain", "fenced-json", "fenced-bare", "padded", "prose", "array", "cut-off", "empty"],
)
def test_the_answer_is_decoded_leniently_and_never_raises_on_being_wrong(
    raw: str, payload: dict[str, Any]
) -> None:
    """AI-RT-011's boundary: a fenced block is still a correct answer so the fence comes
    off, while prose or half a document is simply *wrong* — the provider hands back an empty
    payload and the runtime's schema check spends the one corrective retry on it."""
    response = _provider(ScriptedTransport(_completed(raw))).generate_structured(_request())
    assert response.payload == payload
    assert response.raw_text == raw, "the model's own text is kept for accounting and the eval diff"


def test_a_valid_json_answer_that_fails_the_registered_schema_is_returned_not_raised() -> None:
    """The single most load-bearing distinction in the layer: this answer satisfies the
    transport and violates the schema, so raising here would rob AI-RT-011 of its retry."""
    scripted = ScriptedTransport(_completed('{"wrong_field": 1}'))
    response = _provider(scripted).generate_structured(
        _request(schema={"type": "object", "required": ["answer"]})
    )
    assert response.payload == {"wrong_field": 1}
    assert response.finish_reason == "stop"


@pytest.mark.parametrize(
    ("finish_reason", "truncated"),
    [("length", True), ("stop", False), ("content_filter", False), (None, False)],
    ids=["cut-off", "complete", "refused", "unreported"],
)
def test_a_truncated_answer_is_flagged_for_the_caller(
    finish_reason: str | None, truncated: bool
) -> None:
    """A reply cut off at ``max_tokens`` arrives as a 200 holding half a document; the flag
    is the only way the accounting layer learns the call was paid for and not delivered."""
    scripted = ScriptedTransport(_completed('{"answer": "trunc', finish_reason=finish_reason))
    response = _provider(scripted).generate_structured(_request())
    assert response.truncated is truncated
    if truncated:
        assert response.payload == {}, "a half-written object must not be passed off as an answer"


def test_a_gateway_answering_in_the_legacy_completions_shape_is_still_read() -> None:
    scripted = ScriptedTransport(
        httpx.Response(200, json={"choices": [{"text": '{"answer": "legacy"}'}]})
    )
    response = _provider(scripted).generate_structured(_request())
    assert response.payload == {"answer": "legacy"}
    assert response.finish_reason is None


def test_a_choice_that_is_not_a_choice_is_ignored_rather_than_crashing() -> None:
    scripted = ScriptedTransport(
        httpx.Response(200, json={"choices": [{"index": 0}, "not-a-choice", {"message": None}]})
    )
    response = _provider(scripted).generate_structured(_request())
    assert response.payload == {}
    assert response.raw_text == ""


# --- AI-RT-021: the classification -----------------------------------------------


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 503, 504])
def test_a_retryable_status_is_a_transport_error_carrying_the_status(status: int) -> None:
    scripted = ScriptedTransport(httpx.Response(status, json={"error": {"message": "busy"}}))
    with pytest.raises(LlmTransportError) as raised:
        _provider(scripted).generate_structured(_request())
    error = raised.value
    assert error.status_code == status
    assert error.message == f"provider answered HTTP {status}"
    assert error.retryable is True
    assert error.details == {"path": COMPLETIONS}
    assert not isinstance(error, LlmError), "a retryable event must not read as a final failure"


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 405, 413, 422, 451])
def test_a_final_status_is_a_llm_error_carrying_the_vendors_own_words(status: int) -> None:
    scripted = ScriptedTransport(
        httpx.Response(
            status,
            json={"error": {"message": "schema is not a valid json_schema"}},
            headers={"x-request-id": f"rid-{status}"},
        )
    )
    with pytest.raises(LlmError) as raised:
        _provider(scripted).generate_structured(_request())
    error = raised.value
    assert not isinstance(error, LlmTransportError)
    assert error.status_code == status
    assert error.retryable is False
    assert error.message == f"HTTP {status}: schema is not a valid json_schema"
    assert error.request_id == f"rid-{status}"


def test_a_string_shaped_vendor_error_is_used_as_the_message() -> None:
    scripted = ScriptedTransport(httpx.Response(400, json={"error": "unknown model"}))
    with pytest.raises(LlmError) as raised:
        _provider(scripted).generate_structured(_request())
    assert raised.value.message == "HTTP 400: unknown model"


def test_an_error_body_that_is_not_json_is_described_but_cut_short() -> None:
    """AI-LLM-005: a provider's own words may reach an operator; the provider's whole
    response may not, because it is the largest thing the adapter could echo back out."""
    scripted = ScriptedTransport(httpx.Response(400, text="x" * 5_000))
    with pytest.raises(LlmError) as raised:
        _provider(scripted).generate_structured(_request())
    detail = raised.value.message.partition(": ")[2]
    assert detail == "x" * 400 + "…"


@pytest.mark.parametrize(
    ("header_value", "expected"),
    [("2.5", 2.5), ("0", 0.0), ("-4", 0.0), ("next week", None), (None, None)],
    ids=["seconds", "immediately", "negative", "nonsense", "absent"],
)
def test_a_retry_after_hint_becomes_a_backoff_number(
    header_value: str | None, expected: float | None
) -> None:
    headers: dict[str, str] = {} if header_value is None else {"retry-after": header_value}
    scripted = ScriptedTransport(
        httpx.Response(429, json={"error": {"message": "slow down"}}, headers=headers)
    )
    with pytest.raises(LlmTransportError) as raised:
        _provider(scripted).generate_structured(_request())
    assert raised.value.retry_after_seconds == expected


@pytest.mark.parametrize(
    "header", ["x-request-id", "request-id", "x-amzn-requestid"], ids=["openai", "generic", "bedrock"]
)
def test_the_provider_request_id_is_the_one_diagnostic_that_survives(header: str) -> None:
    """AI-LLM-005 keeps exactly one identifier: enough to correlate a vendor-side log, not
    enough to replay anything."""
    scripted = ScriptedTransport(
        httpx.Response(403, json={"error": {"message": "denied"}}, headers={header: "abc-1"})
    )
    with pytest.raises(LlmError) as raised:
        _provider(scripted).generate_structured(_request())
    assert raised.value.request_id == "abc-1"


def test_a_transport_error_echoes_neither_the_body_nor_the_headers() -> None:
    """The adapter may report a status and a path; it may not ``repr()`` the request it
    failed on, because that object holds the ``Authorization`` header (AI-LLM-005)."""
    scripted = ScriptedTransport(httpx.Response(503, text="gateway said: /models secret-sweep"))
    with pytest.raises(LlmTransportError) as raised:
        _provider(scripted).generate_structured(_request())
    rendered = " ".join([str(raised.value), repr(raised.value), json.dumps(raised.value.to_dict())])
    assert "gateway said" not in rendered
    assert "authorization" not in rendered.lower()
    assert "503" in rendered


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("connect timed out"),
        httpx.ReadTimeout("read timed out"),
        httpx.WriteTimeout("write timed out"),
        httpx.PoolTimeout("pool timed out"),
    ],
    ids=["connect", "read", "write", "pool"],
)
def test_every_timeout_shape_is_a_transport_error_naming_the_budget(failure: Exception) -> None:
    """AI-RT-021 lists a timeout first; the message carries the seconds actually used, so an
    operator can tell a 5s health probe from a two-minute generation."""
    scripted = ScriptedTransport(failure)
    with pytest.raises(LlmTransportError) as raised:
        _provider(scripted, timeout_seconds=17.5).generate_structured(_request())
    assert "17.5" in raised.value.message
    assert raised.value.status_code is None
    assert raised.value.retryable is True


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("peer closed connection"),
        httpx.ReadError("connection reset"),
        OSError("network is unreachable"),
    ],
    ids=["connect", "protocol", "reset", "oserror"],
)
def test_a_dropped_connection_is_a_transport_error(failure: Exception) -> None:
    scripted = ScriptedTransport(failure)
    with pytest.raises(LlmTransportError) as raised:
        _provider(scripted).generate_structured(_request())
    assert raised.value.message == f"model endpoint unreachable: {type(failure).__name__}"
    assert raised.value.retryable is True


@pytest.mark.parametrize(
    ("reply", "expected_message"),
    [
        (httpx.Response(200, text="not json at all"), "provider answered with a body that is not JSON"),
        (httpx.Response(200, json=["an", "array"]), "provider answered with a JSON array instead of an object"),
    ],
    ids=["not-json", "json-array"],
)
def test_an_unusable_success_body_is_a_final_error(
    reply: httpx.Response, expected_message: str
) -> None:
    """Neither shape is a transport event — asking again returns the same nonsense — so both
    are ``LlmError`` (AI-RT-021's other half)."""
    scripted = ScriptedTransport(reply)
    with pytest.raises(LlmError) as raised:
        _provider(scripted).generate_structured(_request())
    assert raised.value.message.startswith(expected_message)
    assert not isinstance(raised.value, LlmTransportError)


# --- health -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "healthy", "detail_prefix"),
    [
        (httpx.Response(200, json={"object": "list", "data": []}), True, None),
        (httpx.Response(204), True, None),
        (httpx.Response(404, json={"error": {"message": "no /models route"}}), True, "answered with error"),
        (httpx.Response(401, json={"error": {"message": "bad key"}}), True, "answered with error"),
        (httpx.Response(500, text="boom"), False, "provider answered HTTP 500"),
        (httpx.Response(503), False, "provider answered HTTP 503"),
        (httpx.ConnectTimeout("slow"), False, "model request timed out after 5.0s"),
    ],
    ids=["200", "204", "404", "401", "500", "503", "timeout"],
)
def test_health_reports_liveness_without_requiring_an_agreement(
    reply: httpx.Response, healthy: bool, detail_prefix: str | None
) -> None:
    """AI-LLM-001's fourth capability: a refusal is still an answer — the endpoint is alive,
    it merely dislikes this credential or this path — while a status AI-RT-021 calls
    transient, or a timeout, is reported as unhealthy."""
    status = _provider(ScriptedTransport(reply), timeout_seconds=17.5).health()
    assert status.healthy is healthy
    if detail_prefix is None:
        assert status.detail is None
    else:
        assert status.detail is not None
        assert status.detail.startswith(detail_prefix)


def test_health_probes_the_model_list_and_never_generates() -> None:
    """AI-LLM-001: a health check may not be a billable call, or every probe of the candidate
    walk would cost the project real tokens.  It also gets its own short budget, not the
    generation's two minutes."""
    scripted = ScriptedTransport(httpx.Response(200, json={"data": []}))
    _provider(scripted, timeout_seconds=120.0).health()
    request = scripted.requests[0]
    assert scripted.request_count == 1
    assert request.method == "GET"
    assert request.url.path.endswith("/models")
    assert request.extensions["timeout"]["read"] == pytest.approx(5.0)


def test_generation_and_health_share_the_endpoint_and_differ_only_in_path() -> None:
    scripted = ScriptedTransport(_completed(), httpx.Response(200, json={"data": []}))
    provider = _provider(scripted, endpoint="http://gpu.internal:8000/v1")
    provider.generate_structured(_request())
    provider.health()
    assert [request.url.path for request in scripted.requests] == [
        "/v1/chat/completions",
        "/v1/models",
    ]
    assert {request.url.host for request in scripted.requests} == {"gpu.internal"}


def test_a_health_probe_that_fails_never_raises_through_the_walk() -> None:
    """AI-POL-007 walks the candidate list asking each model whether it is usable; a probe
    that raised would abort the walk on the first dead one."""
    assert _provider(ScriptedTransport(httpx.ConnectError("refused"))).health().healthy is False


# --- AI-LLM-001's token counting --------------------------------------------------


def test_count_tokens_is_stable_and_costs_no_request() -> None:
    """AI-LLM-001's third capability, and the fit test AI-POL-007 runs before choosing: the
    estimate must not vary between two calls on the same text, and computing it may not
    require a call (a context that fit at decision time must still fit at commit time)."""
    scripted = ScriptedTransport(_completed())
    provider = _provider(scripted)
    assert provider.count_tokens(CONTEXT) == estimate_text(CONTEXT) > 0
    assert provider.count_tokens(CONTEXT) == provider.count_tokens(CONTEXT)
    assert provider.count_tokens("") == 0
    assert scripted.request_count == 0


# --- the default factory ----------------------------------------------------------


def test_the_default_factory_refuses_to_guess_an_endpoint() -> None:
    """AI-LLM-003 / AI-POL-002: a registered model with no address cannot be reached, and
    falling back to a public vendor would send a project's data somewhere nobody approved."""
    unregistered = ProviderInfo(
        provider="acme",
        provider_class=ProviderClass.APPROVED_EXTERNAL,
        model="acme-large",
        capabilities=Capabilities(structured_output=True),
        endpoint=None,
    )
    with pytest.raises(LlmError) as raised:
        provider_for(unregistered)
    assert raised.value.details == {"model": "acme-large", "provider": "acme"}
    assert "no endpoint configured" in str(raised.value)
    assert "openai.com" not in str(raised.value)


def test_the_default_factory_declares_the_registration_it_was_given() -> None:
    """AI-LLM-002: the class and capabilities a policy check reads are the registered ones,
    not a per-adapter default; the reference to the key travels with them (AI-LLM-005)."""
    registered = ProviderInfo(
        provider="vllm",
        provider_class=ProviderClass.PRIVATE_NETWORK,
        model="qwen",
        capabilities=Capabilities(structured_output=True, vision=True, max_context_tokens=64_000),
        endpoint="http://gpu.internal:8000/v1/",
        api_key_secret_ref="env://MODEL_API_KEY",
    )
    provider = provider_for(registered)
    assert provider.info == registered
    assert provider.info.provider_class is ProviderClass.PRIVATE_NETWORK
    assert provider.info.api_key_secret_ref == "env://MODEL_API_KEY"


def test_a_retryable_refusal_keeps_the_provider_request_id() -> None:
    scripted = ScriptedTransport(
        httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"x-request-id": "rate-1"})
    )
    with pytest.raises(LlmTransportError) as raised:
        _provider(scripted).generate_structured(_request())
    assert getattr(raised.value, "request_id", None) == "rate-1"
