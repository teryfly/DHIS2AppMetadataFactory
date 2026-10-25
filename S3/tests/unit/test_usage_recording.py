"""``cost/recording.py`` — the ledger wrapper a parse puts around a provider (AI-NFR-03).

The integration tests prove the wrapper is *fitted* to a real parse; these prove what it books
when the parse is not there to look at: one row per call, a row for a refused call, the call's
own temperature rather than the settings default, and a sequence that advances because a long
document is extracted one chunk per call.

``record_call`` is captured rather than executed: writing the row is
``tests/integration/test_cost.py``'s subject, and what is under test here is the arguments this
wrapper hands it.
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_intelligence.cost.recording import RecordingProvider
from factory_intelligence.cost.usage import CallContext
from factory_intelligence.enums import ProviderClass
from factory_intelligence.llm.errors import LlmError, LlmTransportError
from factory_intelligence.llm.registry import RegisteredModel
from factory_intelligence.llm.types import (
    Capabilities,
    ChatMessage,
    HealthStatus,
    ModelResponse,
    ProviderInfo,
    StructuredRequest,
    Usage,
)

CALL = CallContext(
    project_id="prj-parse",
    workflow_id="parse:art-input",
    stage=None,
    agent_type="REQUIREMENTS",
    run_id=None,
    call_role="extraction",
)

ARTIFACTS = ({"ref": "artifact:art-input", "classification": "CONFIDENTIAL"},)

CAPABILITIES = Capabilities(structured_output=True, vision=False, max_context_tokens=100_000)


class _Stub:
    """A provider with a scripted reply per call, and nothing else."""

    def __init__(self, replies: list[ModelResponse | BaseException]) -> None:
        self._replies = replies
        self.asked: list[StructuredRequest] = []
        self.info = ProviderInfo(
            provider="stub",
            provider_class=ProviderClass.LOCAL_ONLY,
            model="stub-model",
            capabilities=CAPABILITIES,
        )

    def generate_structured(self, request: StructuredRequest) -> ModelResponse:
        self.asked.append(request)
        reply = self._replies[min(len(self.asked) - 1, len(self._replies) - 1)]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def count_tokens(self, text: str) -> int:
        return len(text)

    def health(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail=None)


def _model() -> RegisteredModel:
    return RegisteredModel(
        model="stub-model",
        provider="stub",
        provider_id="apvd-stub",
        provider_class=ProviderClass.LOCAL_ONLY,
        capabilities=CAPABILITIES,
        enabled=True,
        unit_price_input_per_1k=0.0,
        unit_price_output_per_1k=0.0,
        applicable_agents={"REQUIREMENTS": 1},
        endpoint=None,
        api_key_secret_ref=None,
        adapter="mock",
        model_id="amdl-stub",
    )


def _request(*, temperature: float = 0.2, max_output_tokens: int | None = 512) -> StructuredRequest:
    return StructuredRequest(
        messages=(ChatMessage(role="user", content="extract"),),
        json_schema={"type": "object"},
        schema_name="parse.extract",
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def _response() -> ModelResponse:
    return ModelResponse(
        payload={"items": []},
        raw_text="{}",
        usage=Usage(input_tokens=120, output_tokens=30),
        model="stub-model",
        request_id="req-1",
    )


@pytest.fixture
def booked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every ``record_call`` the wrapper made, as the keyword arguments it passed."""
    rows: list[dict[str, Any]] = []

    def capture(**kwargs: Any) -> None:
        rows.append(kwargs)

    monkeypatch.setattr("factory_intelligence.cost.recording.record_call", capture)
    return rows


def _wrapper(replies: list[ModelResponse | BaseException]) -> RecordingProvider:
    return RecordingProvider(
        _Stub(replies),
        model=_model(),
        context=CALL,
        context_artifacts=ARTIFACTS,
        operator="ops",
    )


def test_an_answered_call_is_booked_as_the_call_it_was(booked: list[dict[str, Any]]) -> None:
    provider = _wrapper([_response()])

    response = provider.generate_structured(_request())

    assert response.payload == {"items": []}
    assert len(booked) == 1
    row = booked[0]
    assert row["outcome"] == "OK" and row["error"] is None
    # the id is read off the answer, not passed again as an argument
    assert row["request_id"] is None and row["response"].request_id == "req-1"
    assert row["temperature"] == 0.2 and row["max_output_tokens"] == 512
    assert row["context_artifacts"] == ARTIFACTS
    assert row["operator"] == "ops"
    assert row["model"].ref == "stub/stub-model"
    assert row["context"].project_id == "prj-parse"
    assert row["context"].call_role == "extraction"
    assert row["context"].sequence == 1
    assert row["latency_ms"] >= 0


def test_each_chunk_of_one_parse_advances_the_sequence(booked: list[dict[str, Any]]) -> None:
    """A long document is extracted one call per chunk, and a rollup has to tell them apart."""
    provider = _wrapper([_response()])

    for _ in range(3):
        provider.generate_structured(_request())

    assert [entry["context"].sequence for entry in booked] == [1, 2, 3]
    assert [entry["context"].call_role for entry in booked] == ["extraction"] * 3


def test_a_refused_call_is_booked_and_then_raised(booked: list[dict[str, Any]]) -> None:
    """A request the vendor refused was still a request (AI-LLM-004 records 错误 as a field)."""
    refusal = LlmError("gateway refused", status_code=400, request_id="req-9")
    provider = _wrapper([refusal])

    with pytest.raises(LlmError) as raised:
        provider.generate_structured(_request())

    assert raised.value is refusal
    assert len(booked) == 1
    assert booked[0]["outcome"] == "ERROR"
    assert booked[0]["error"] == "gateway refused"
    assert booked[0]["request_id"] == "req-9", "a refusal still cites the vendor's log"
    assert booked[0]["response"] is None


def test_a_transport_refusal_is_booked_on_every_attempt(booked: list[dict[str, Any]]) -> None:
    """Each attempt is a request the provider processed, so each is a row (AI-COST-001)."""
    provider = _wrapper([LlmTransportError("timeout", status_code=504, request_id="late-1")])

    for _ in range(2):
        with pytest.raises(LlmTransportError):
            provider.generate_structured(_request())

    assert [entry["outcome"] for entry in booked] == ["ERROR", "ERROR"]
    assert [entry["context"].sequence for entry in booked] == [1, 2]
    assert [entry["request_id"] for entry in booked] == ["late-1", "late-1"]


def test_the_wrapper_declares_the_provider_it_wraps(booked: list[dict[str, Any]]) -> None:
    """``info`` is what AI-INP-007's capability check reads: forwarding it is the difference
    between checking the model and checking the wrapper."""
    del booked
    inner = _Stub([_response()])
    provider = RecordingProvider(inner, model=_model(), context=CALL, context_artifacts=ARTIFACTS)

    assert provider.info is inner.info
    assert provider.count_tokens("four words here") == len("four words here")
    assert provider.health().healthy is True


def test_the_totals_it_reports_are_the_calls_it_booked(booked: list[dict[str, Any]]) -> None:
    """An evaluation states its own cost in its run row, and the figures must come from the
    same accounting that wrote the rows — not from a second count that could disagree."""
    provider = _wrapper([_response(), _response(), LlmError("no", status_code=400)])

    provider.generate_structured(_request())
    provider.generate_structured(_request())
    with pytest.raises(LlmError):
        provider.generate_structured(_request())

    assert provider.calls == 3, "a refused call was still a call it made"
    assert provider.usage == Usage(input_tokens=240, output_tokens=60), "only the answers"
    assert len(booked) == 3


def test_a_call_that_belongs_to_no_project_is_booked_as_such(booked: list[dict[str, Any]]) -> None:
    """The one row AI-COST-001 cannot bill to anybody: AI-NFR-03 still requires it."""
    context = CallContext(
        project_id=None,
        workflow_id="eval:aeval-1",
        stage=None,
        agent_type="REQUIREMENTS",
        run_id=None,
        call_role="evaluation",
    )
    provider = RecordingProvider(_Stub([_response()]), model=_model(), context=context)

    provider.generate_structured(_request())

    assert booked[0]["context"].project_id is None
    assert booked[0]["context"].call_role == "evaluation"
