"""AI-LLM-005: the key is resolved through S0 and appears in exactly one place.

The rule is absolute in the SRS — "模型 API 密钥以 Secret 引用配置，只在 S3 内通过 S0 解析；密钥
不得出现在日志、审计、产物与异常中" — and an absolute negative is easy to test vacuously.  So
every test here first establishes that the key *is* real and *is* used: the outgoing request
must carry ``authorization: Bearer <key>``, resolved from a reference a test can see.  Only
against that positive control does "absent from every rendered form" mean anything.

What "rendered form" means here, because a value can hide in any of them:

* the exception's ``str``, ``repr``, ``message``, ``details`` and ``to_dict()`` — that last
  one is the payload S1 maps to a REST body and S3 hands to an audit record;
* the log lines a call produces, assembled the way S0's formatter assembles them (message,
  arguments, structured extras, exception text);
* the request body, the URL, the ``repr`` of the provider and of its ``ProviderInfo``, the
  ``StructuredRequest`` and the ``ModelResponse``;
* the transcript line a recording writes, which is the nearest thing to an artifact a unit
  test can produce out of a provider call.

The adapter stores a *reference*, not a value: that is asserted by walking the object's own
attributes after a call that used the key, which is the structural form of "resolved at
request time" (AI-LLM-003) instead of a promise about it.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest
from factory_foundation.api import (
    SecretUnavailableError,
    canonical_json,
    install_development_backends,
    resolve_secret,
)

from factory_intelligence.deps import (
    get_llm_provider,
    reset_providers,
    set_http_transport,
    set_llm_provider_factory,
)
from factory_intelligence.enums import ProviderClass
from factory_intelligence.llm import openai_compat
from factory_intelligence.llm.errors import LlmError, LlmSecretMissing, LlmTransportError
from factory_intelligence.llm.openai_compat import OpenAICompatProvider, provider_for
from factory_intelligence.llm.record_replay import RecordReplayProvider, request_digest
from factory_intelligence.llm.types import (
    Capabilities,
    ChatMessage,
    ProviderInfo,
    StructuredRequest,
)

SECRET_REF = "env://S3_TEST_MODEL_API_KEY"
API_KEY = "sk-live-0f9e8d7c6b5a4321fedcba987654321"
ROTATED_KEY = "sk-live-1a2b3c4d5e6f708192a3b4c5d6e7f809"
ANSWER = '{"answer": "the consolidated requirement"}'


class ScriptedTransport(httpx.BaseTransport):
    """The wire, seen from the provider side: everything it holds is rendered below."""

    def __init__(self, reply: httpx.Response | Exception) -> None:
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def body(self, index: int = 0) -> dict[str, Any]:
        document: Any = json.loads(self.requests[index].content)
        assert isinstance(document, dict)
        return document


def _reply() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen-local",
            "choices": [{"message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 12},
        },
        headers={"x-request-id": "req-1"},
    )


def _info(
    *,
    api_key_secret_ref: str | None = SECRET_REF,
    endpoint: str | None = "http://gpu.internal:8000/v1",
) -> ProviderInfo:
    return ProviderInfo(
        provider="vllm",
        provider_class=ProviderClass.LOCAL_ONLY,
        model="qwen-local",
        capabilities=Capabilities(structured_output=True, vision=True, max_context_tokens=32_000),
        endpoint=endpoint,
        api_key_secret_ref=api_key_secret_ref,
    )


def _request() -> StructuredRequest:
    return StructuredRequest(
        messages=(ChatMessage(role="user", content="consolidate these statements"),),
        json_schema={"type": "object", "required": ["answer"]},
        schema_name="requirements.output",
    )


def _provider(transport: httpx.BaseTransport, info: ProviderInfo | None = None) -> OpenAICompatProvider:
    """Wired the way :func:`provider_for` wires it: the adapter is handed the reference, and
    resolves it per request.  Passing the ``ProviderInfo`` alone would build an adapter with no
    credential at all, which is what the first test below would otherwise fail to notice."""
    registered = info or _info()
    return OpenAICompatProvider(
        registered,
        endpoint=registered.endpoint or "",
        api_key_secret_ref=registered.api_key_secret_ref,
        transport=transport,
    )


def _rendered_logs(caplog: pytest.LogCaptureFixture) -> str:
    """A log line as S0's formatter builds it: the message, its arguments, the structured
    extras and the exception text — every channel a value could travel on."""
    chunks: list[str] = []
    for record in caplog.records:
        chunks.append(record.getMessage())
        chunks.append(repr(record))
        if record.args:
            chunks.append(repr(record.args))
        extras = getattr(record, "extra_fields", None)
        if isinstance(extras, dict):
            chunks.append(json.dumps(extras, default=str, ensure_ascii=False))
        if record.exc_info:
            chunks.append("".join(traceback.format_exception(*record.exc_info)))
    return "\n".join(chunks)


def _rendered_error(error: BaseException) -> str:
    """Every way a failure can leave this layer: the message, the identifier attributes, the
    serialised form S1 turns into a REST body and S3 into an audit payload, and the traceback."""
    parts = [str(error), repr(error), "".join(traceback.format_exception(type(error), error, error.__traceback__))]
    if isinstance(error, LlmError | LlmTransportError | LlmSecretMissing):
        parts.append(error.message or "")
        parts.append(json.dumps(error.details, default=str))
        parts.append(canonical_json(error.to_dict()).decode("utf-8"))
    return " | ".join(parts)


@pytest.fixture(autouse=True)
def _s0_secret_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The real S0 resolution path: an ``env://`` reference the adapter has to resolve itself."""
    install_development_backends()
    monkeypatch.setenv("S3_TEST_MODEL_API_KEY", API_KEY)
    reset_providers()
    yield
    reset_providers()


# --- the positive control: the key is used, in exactly one place -------------------


def test_the_key_reaches_the_wire_only_as_the_authorization_header() -> None:
    """AI-LLM-003 configures the key as a Secret reference and AI-LLM-005 forbids it anywhere
    else.  This is the control that makes every absence below meaningful: the value is real,
    it is sent, and it is in one field of one place."""
    scripted = ScriptedTransport(_reply())
    _provider(scripted).generate_structured(_request())
    request = scripted.requests[0]
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in str(request.url)
    assert API_KEY not in request.content.decode("utf-8")
    echoed = [name for name, value in request.headers.items() if API_KEY in value and name != "authorization"]
    assert echoed == [], f"the credential was duplicated into {echoed}"


def test_the_value_is_resolved_lazily_and_only_through_s0(monkeypatch: pytest.MonkeyPatch) -> None:
    """AI-LLM-005's "只在 S3 内通过 S0 解析": the reference goes to :func:`resolve_secret` at
    request time, once per request, and building the object resolves nothing."""
    refs: list[str] = []

    def spy(ref: str) -> Any:
        refs.append(ref)
        return resolve_secret(ref)

    monkeypatch.setattr(openai_compat, "resolve_secret", spy)
    scripted = ScriptedTransport(_reply())
    provider = _provider(scripted)
    assert refs == [], "constructing a provider must not resolve anything"
    provider.generate_structured(_request())
    provider.generate_structured(_request())
    assert refs == [SECRET_REF, SECRET_REF]
    assert scripted.requests[1].headers["authorization"] == f"Bearer {API_KEY}"


def test_a_rotated_key_is_picked_up_by_the_next_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The consequence of resolving per request rather than caching: a rotation needs no
    restart, because no long-lived copy of the value exists anywhere in the process."""
    scripted = ScriptedTransport(_reply())
    provider = _provider(scripted)
    provider.generate_structured(_request())
    monkeypatch.setenv("S3_TEST_MODEL_API_KEY", ROTATED_KEY)
    provider.generate_structured(_request())
    assert scripted.requests[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert scripted.requests[1].headers["authorization"] == f"Bearer {ROTATED_KEY}"


def test_the_provider_holds_a_reference_and_never_the_value() -> None:
    """After a call that used the key, walking the adapter's own attributes finds the
    reference and not the secret — which is what makes a ``repr`` of the provider safe to put
    in a log line."""
    scripted = ScriptedTransport(_reply())
    provider = _provider(scripted)
    provider.generate_structured(_request())
    stored = json.dumps({name: repr(value) for name, value in vars(provider).items()}, default=str)
    assert API_KEY not in stored
    assert SECRET_REF in stored
    assert API_KEY not in repr(provider)
    assert SECRET_REF in repr(provider.info)


def test_the_adapter_source_reaches_for_no_environment_itself() -> None:
    """The structural half of the rule: the only credential machinery in the adapter is S0's
    ``resolve_secret``, and the value is read in one expression.  ``os.environ`` would be a
    second, unmasked path that S0's audit trail never sees."""
    source = Path(openai_compat.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
    assert "resolve_secret(" in source
    assert "reveal()" in source


def test_s0_masks_the_value_and_the_reference_is_what_repr_shows() -> None:
    """Why a reference is safe to hand around: S0's ``Secret`` string form is masked by
    construction (FD-SEC-002), so ``reveal()`` is the only way out — and the adapter uses it
    inside one f-string that becomes a header."""
    secret = resolve_secret(SECRET_REF)
    assert secret.reveal() == API_KEY
    assert API_KEY not in str(secret)
    assert API_KEY not in repr(secret)
    assert "***" in repr(secret)


# --- the negative: nothing rendered carries it ------------------------------------


def test_nothing_a_successful_call_produces_can_be_rendered_into_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The DTOs, the request, the response and the log lines of one successful call: every
    object this layer hands to another subsystem (AI-LLM-004's usage row, the artifact, the
    audit payload) is rendered here, and the key is in none of them."""
    scripted = ScriptedTransport(_reply())
    provider = _provider(scripted)
    with caplog.at_level("DEBUG"):
        response = provider.generate_structured(_request())
    rendered = "\n".join(
        [
            repr(response),
            str(response.payload),
            repr(response.raw_text),
            canonical_json(response.payload).decode("utf-8"),
            repr(response.usage),
            repr(_request()),
            repr(provider.info),
            repr(provider.info.capabilities),
            _rendered_logs(caplog),
            json.dumps(scripted.body(), ensure_ascii=False),
            str(scripted.requests[0].url),
        ]
    )
    assert API_KEY not in rendered
    assert caplog.records, "the sink captured nothing, so the absence above proved nothing"


def test_a_provider_error_carries_no_key_in_any_of_its_renderings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AI-LLM-005 names 异常 explicitly.  ``to_dict()`` is the form that reaches a REST body
    and an audit payload, so it is checked as well as the message, and the operator still gets
    the vendor's reason."""
    scripted = ScriptedTransport(
        httpx.Response(401, json={"error": {"message": "invalid api key provided"}})
    )
    provider = _provider(scripted)
    with caplog.at_level("DEBUG"), pytest.raises(LlmError) as raised:
        provider.generate_structured(_request())
    error = raised.value
    assert API_KEY not in _rendered_error(raised.value)
    assert API_KEY not in _rendered_logs(caplog)
    assert API_KEY not in json.dumps(scripted.body(), ensure_ascii=False)
    assert error.status_code == 401
    assert "invalid api key provided" in error.message, "a keyless error must still be actionable"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"error": {"message": "unknown model"}}),
        (401, {"error": {"message": "bad credentials"}}),
        (403, {"error": "forbidden"}),
        (404, {"detail": "no route"}),
        (422, {"error": {"message": "response_format rejected"}}),
        (429, {"error": {"message": "rate limited"}}),
        (502, {"error": {"message": "upstream refused"}}),
        (503, {"error": {"message": "no capacity"}}),
        (504, {"error": {"message": "upstream timed out"}}),
    ],
    ids=["400", "401", "403", "404", "422", "429", "502", "503", "504"],
)
def test_no_error_shape_echoes_the_credential(status: int, body: dict[str, Any]) -> None:
    """Every failure AI-LLM-004 may record — retryable or final — is rendered the same way by
    the caller, so both classes are checked, and neither may hold the key."""
    scripted = ScriptedTransport(httpx.Response(status, json=body, headers={"x-request-id": f"rid-{status}"}))
    with pytest.raises((LlmError, LlmTransportError)) as raised:
        _provider(scripted).generate_structured(_request())
    error = raised.value
    assert isinstance(error, (LlmError, LlmTransportError))
    assert API_KEY not in _rendered_error(error)
    assert API_KEY not in json.dumps(scripted.body(), ensure_ascii=False)
    assert error.status_code == status


def test_a_timeout_rendering_carries_no_key() -> None:
    """The most likely accidental leak: an exception raised from inside the transport layer,
    where the request object — headers and all — is attached to it."""
    request = httpx.Request("POST", "http://gpu.internal:8000/v1/chat/completions", headers={"authorization": f"Bearer {API_KEY}"})
    scripted = ScriptedTransport(httpx.ReadTimeout("read timed out", request=request))
    with pytest.raises(LlmTransportError) as raised:
        _provider(scripted).generate_structured(_request())
    assert API_KEY not in _rendered_error(raised.value)


def test_an_unresolvable_reference_fails_before_anything_leaves_the_process() -> None:
    """AI-LLM-003 and AI-LLM-005 together: with no key to send, the adapter must not send the
    project's context unauthenticated.  The reference is named — it is not a secret — and no
    request is made."""
    scripted = ScriptedTransport(_reply())
    provider = _provider(scripted, info=_info(api_key_secret_ref="env://S3_TEST_NO_SUCH_VARIABLE"))
    with pytest.raises(LlmSecretMissing) as raised:
        provider.generate_structured(_request())
    assert scripted.requests == [], "an unauthenticated request left with a project's context"
    assert raised.value.details == {"ref": "env://S3_TEST_NO_SUCH_VARIABLE"}
    assert raised.value.error_code == "SECRET_UNAVAILABLE"
    assert isinstance(raised.value.__cause__, SecretUnavailableError)
    assert API_KEY not in _rendered_error(raised.value)


def test_no_authorization_header_is_built_when_no_reference_is_registered() -> None:
    """The negative half of the header rule: a local model registered with no credential is
    sent no ``Authorization`` header at all, rather than an empty or a guessed one."""
    scripted = ScriptedTransport(httpx.Response(200, json={"data": []}))
    provider = _provider(scripted, info=_info(api_key_secret_ref=None))
    assert provider.health().healthy is True
    assert "authorization" not in scripted.requests[0].headers
    assert scripted.requests[0].headers["content-type"] == "application/json"


def test_the_default_factory_resolves_the_reference_it_was_registered_with() -> None:
    """AI-LLM-003: a registration carries endpoint + reference + class + capabilities, and
    the default factory is what turns that row into a provider.  The reference has to reach
    the wire through it, or every authenticated model in the registry is unreachable; and
    nothing on that path renders the value."""
    scripted = ScriptedTransport(_reply())
    set_http_transport(scripted)
    provider = provider_for(_info())
    provider.generate_structured(_request())
    assert scripted.requests[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in repr(provider.info)
    assert API_KEY not in repr(vars(provider))


def test_the_wiring_start_up_installs_carries_the_key_to_the_wire() -> None:
    """``bootstrap.initialize`` installs :func:`provider_for` as the factory and
    :func:`get_llm_provider` is the only route the runtime has to a provider: a reference
    dropped on that route turns into a 401 that reads like the vendor's fault."""
    scripted = ScriptedTransport(_reply())
    set_http_transport(scripted)
    set_llm_provider_factory(provider_for)
    served = get_llm_provider(_info())
    assert isinstance(served, OpenAICompatProvider)
    served.generate_structured(_request())
    assert scripted.requests[0].headers["authorization"] == f"Bearer {API_KEY}"


def test_a_recorded_transcript_holds_the_prompt_and_never_the_key(tmp_path: Path) -> None:
    """AI-LLM-006 writes the request into a file, and that file is what a repository keeps —
    the closest thing to an artifact a provider call produces, and the one place a credential
    would outlive the process (AI-LLM-005's 产物)."""
    transcript = tmp_path / "run.jsonl"
    scripted = ScriptedTransport(_reply())
    request = _request()
    recorder = RecordReplayProvider(_info(), path=transcript, mode="record", inner=_provider(scripted))
    recorder.generate_structured(request)
    recorded = transcript.read_text(encoding="utf-8")
    assert "consolidate these statements" in recorded, "a recording that lost the prompt is useless"
    assert request_digest(request) in recorded
    assert API_KEY not in recorded
    assert SECRET_REF not in recorded, "the replay key is a digest of the request, not of the credential"


def test_the_audit_shaped_record_of_a_call_carries_the_reference_only() -> None:
    """AI-POL-008 / AI-LLM-004: what a caller can write about a call is provider, class,
    model, endpoint, the reference, request id, usage and the answer — all of it assembled
    from what the adapter returned.  That the list contains no credential is not a filter
    applied at write time; it is because the provider never produced one to write."""
    scripted = ScriptedTransport(_reply())
    info = _info()
    response = _provider(scripted, info=info).generate_structured(_request())
    record: dict[str, Any] = {
        "provider": info.provider,
        "providerClass": info.provider_class.value,
        "model": response.model,
        "endpoint": info.endpoint,
        "apiKeySecretRef": info.api_key_secret_ref,
        "requestId": response.request_id,
        "usage": canonical_json(asdict(response.usage)).decode("utf-8"),
        "payload": response.payload,
        "rawText": response.raw_text,
        "sentBody": scripted.body(),
    }
    audit_shape = canonical_json(record).decode("utf-8")
    assert SECRET_REF in audit_shape
    assert API_KEY not in audit_shape, "the record the caller can build holds no credential to hide"


def test_a_vendor_that_echoes_the_request_back_does_not_put_the_key_in_the_exception() -> None:
    """The one rendered form the adapter does not author: the vendor's free text.  It must
    still be keyless before it reaches an operator, a log or an audit row."""
    echo = {"error": {"message": f"your header was: Bearer {API_KEY}"}}
    scripted = ScriptedTransport(httpx.Response(400, json=echo))
    with pytest.raises(LlmError) as raised:
        _provider(scripted).generate_structured(_request())
    assert API_KEY not in _rendered_error(raised.value)
