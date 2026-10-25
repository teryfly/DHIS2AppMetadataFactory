"""AI-LLM-006 and AI-AC-10: a replay run answers from a transcript and performs no I/O.

AI-AC-10's promise — "录制回放模式运行端到端测试，不访问网络" — cannot be shown by reading the
replay provider's source and noticing there is no HTTP call in it, because the class is a
decorator: it holds an *inner* provider, and the failure mode that matters is a replay that
quietly falls through to it.  So these tests cut the network off structurally before
replaying: an injected transport that raises the moment it is asked to handle a request,
plus ``httpx.Client`` / ``httpx.AsyncClient`` replaced by something that raises on
construction.  A replay that reaches either does not fail quietly — it fails here.

The other half of the requirement is that the transcript is keyed by a digest of the
**request**.  A key that survived a prompt edit would keep a suite green while testing a
prompt nobody uses, which is the one way AI-AC-10 could be worse than no test at all, so the
sensitivity of the digest is asserted in both directions: everything that determines the
answer changes it, and the two things that do not (which run asked, which attempt this is)
must not.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest

from factory_intelligence.deps import reset_providers, set_http_transport
from factory_intelligence.enums import ProviderClass, ProviderKind
from factory_intelligence.llm.errors import LlmError, LlmTransportError
from factory_intelligence.llm.mock_agent import MockAgentProvider, mock_provider
from factory_intelligence.llm.openai_compat import OpenAICompatProvider
from factory_intelligence.llm.record_replay import RecordReplayProvider, request_digest
from factory_intelligence.llm.types import (
    AgentHint,
    Capabilities,
    ChatMessage,
    HealthStatus,
    ImageInput,
    ModelResponse,
    ProviderInfo,
    StructuredRequest,
    Usage,
)

PROMPT = "consolidate these statements into requirements"

#: the hint every default request carries; a module singleton because a frozen dataclass
#: built in an argument default would be re-made per call (and ruff is right to refuse it)
DEFAULT_HINT = AgentHint(agent_type="REQUIREMENTS", stage="REQUIREMENTS")


class DeadTransport(httpx.BaseTransport):
    """Any request that reaches this object is a network operation in a replay run."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.attempts.append(f"{request.method} {request.url}")
        raise AssertionError(f"a replay run performed network I/O: {request.method} {request.url}")


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> DeadTransport:
    """Cut both routes out: the injected transport, and any client the code might build."""
    dead = DeadTransport()
    set_http_transport(dead)

    def blocked(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("an httpx client was constructed during a replay run")

    monkeypatch.setattr(httpx, "Client", blocked)
    monkeypatch.setattr(httpx, "AsyncClient", blocked)
    monkeypatch.setattr(httpx, "HTTPTransport", blocked)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", blocked)
    return dead


def _info(source: str = ProviderKind.REPLAY.value) -> ProviderInfo:
    return ProviderInfo(
        provider="replay",
        provider_class=ProviderClass.LOCAL_ONLY,
        model="recorded-model",
        capabilities=Capabilities(structured_output=True, vision=True, max_context_tokens=32_000),
        source=source,
    )


def _request(
    content: str = PROMPT,
    *,
    schema_name: str = "requirements.output",
    schema: dict[str, Any] | None = None,
    temperature: float = 0.0,
    max_output_tokens: int | None = None,
    hint: AgentHint | None = DEFAULT_HINT,
    images: tuple[ImageInput, ...] = (),
) -> StructuredRequest:
    return StructuredRequest(
        messages=(
            ChatMessage(role="system", content="you extract requirements"),
            ChatMessage(role="user", content=content, images=images),
        ),
        json_schema=schema if schema is not None else {"type": "object", "required": ["answer"]},
        schema_name=schema_name,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        agent_hint=hint,
    )


def _entry(digest: str, answer: str = "recorded answer") -> dict[str, Any]:
    return {
        "digest": digest,
        "request": {"schemaName": "requirements.output"},
        "response": {
            "payload": {"answer": answer},
            "raw_text": json.dumps({"answer": answer}),
            "usage": {"input_tokens": 900, "output_tokens": 12},
            "model": "recorded-model",
            "finish_reason": "stop",
            "request_id": "req-1",
            "truncated": False,
        },
    }


def _write_transcript(path: Path, *entries: dict[str, Any]) -> Path:
    path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries), encoding="utf-8"
    )
    return path


def _live_provider(reply: httpx.Response) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        _info(source=ProviderKind.LIVE.value),
        endpoint="http://gpu.internal:8000/v1",
        transport=httpx.MockTransport(lambda request: reply),
    )


@pytest.fixture(autouse=True)
def _no_leaked_providers() -> Iterator[None]:
    reset_providers()
    yield
    reset_providers()


@pytest.fixture()
def transcript(tmp_path: Path) -> Path:
    return tmp_path / "run.jsonl"


# --- the apparatus, proved to bite ------------------------------------------------


def test_the_forbidden_network_actually_forbids_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every "no I/O happened" assertion above would pass for free if the guard were inert,
    so the guard is tested before it is trusted: the transport raises when asked, and once
    the network is forbidden even the adapter that legitimately needs it cannot run."""
    live_dead = DeadTransport()
    with (
        httpx.Client(base_url="http://gpu.internal:8000/v1", transport=live_dead) as client,
        pytest.raises(AssertionError, match="performed network I/O"),
    ):
        client.get("/models")
    assert live_dead.attempts == ["GET http://gpu.internal:8000/v1/models"]

    forbidden = _forbid_network(monkeypatch)
    with pytest.raises(AssertionError, match="httpx client was constructed"):
        httpx.Client(base_url="http://gpu.internal:8000/v1")
    adapter = OpenAICompatProvider(
        _info(source=ProviderKind.LIVE.value), endpoint="http://gpu.internal:8000/v1"
    )
    with pytest.raises(AssertionError, match="httpx client was constructed"):
        adapter.generate_structured(_request())
    assert forbidden.attempts == [], "the client died before any transport was reached"


# --- the digest: AI-LLM-006's replay key ------------------------------------------


def test_the_replay_key_is_a_digest_and_not_a_copy_of_the_prompt() -> None:
    """The key is a content hash (FD-COM-003), so a digest can be quoted in a log line
    without repeating the prompt that produced it."""
    digest = request_digest(_request())
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
    assert PROMPT not in digest
    assert digest == request_digest(_request()), "the same request must key the same entry"


@pytest.mark.parametrize(
    "changed",
    [
        lambda: _request(PROMPT + " and the constraints"),
        lambda: _request(hint=None),
        lambda: _request(schema={"type": "object"}),
        lambda: _request(schema_name="mapping.output"),
        lambda: _request(temperature=0.7),
        lambda: _request(max_output_tokens=2048),
        lambda: _request(hint=AgentHint(agent_type="MAPPING", stage="REQUIREMENTS")),
        lambda: _request(hint=AgentHint(agent_type="REQUIREMENTS", stage="DATA_MAPPING")),
        lambda: _request(images=(ImageInput("image/png", "AA=="),)),
        lambda: _request(images=(ImageInput("image/png", "BBBB"),)),
    ],
    ids=[
        "prompt-edited",
        "hint-removed",
        "schema-edited",
        "schema-renamed",
        "temperature-raised",
        "output-cap-set",
        "agent-type-changed",
        "stage-changed",
        "image-added",
        "image-replaced",
    ],
)
def test_everything_that_determines_the_answer_changes_the_key(changed: Any) -> None:
    """AI-LLM-006: a prompt edit must miss rather than replay a stale answer.  Each case
    varies exactly one field of the recorded request."""
    assert request_digest(changed()) != request_digest(_request())


@pytest.mark.parametrize(
    "unchanged",
    [
        lambda: _request(hint=AgentHint(agent_type="REQUIREMENTS", stage="REQUIREMENTS", run_id="arun-2")),
        lambda: _request(hint=AgentHint(agent_type="REQUIREMENTS", stage="REQUIREMENTS", attempt=2)),
    ],
    ids=["different-run", "different-attempt"],
)
def test_which_run_asked_and_which_attempt_this_is_do_not_change_the_key(unchanged: Any) -> None:
    """AI-RT-011's corrective retry and a resumed run (AI-RT-015) ask the same question
    again; with the run id in the key every recording would be single-use and AI-AC-10 could
    never re-run anything."""
    assert request_digest(unchanged()) == request_digest(_request())


def test_the_key_is_the_request_not_the_answer(transcript: Path) -> None:
    """The transcript is keyed by the request and written by the answer: recording the same
    call twice against a flapping model yields two entries under one digest."""
    inner = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "first"}, {"answer": "second"}]})
    provider = RecordReplayProvider(_info(), path=transcript, mode="record", inner=inner)
    provider.generate_structured(_request())
    provider.generate_structured(_request())
    digests = [json.loads(line)["digest"] for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert digests == [request_digest(_request())] * 2


# --- record mode ------------------------------------------------------------------


def test_record_mode_writes_the_request_and_the_answer_it_got(transcript: Path) -> None:
    """AI-LLM-006's recording half.  The request is stored in the open, which is why a
    transcript is sensitive material authored from synthetic input and never a production
    recording (``record_replay`` module docstring)."""
    inner = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "what the model said"}]})
    provider = RecordReplayProvider(_info(), path=transcript, mode="record", inner=inner)
    answered = provider.generate_structured(_request())

    lines = transcript.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry: dict[str, Any] = json.loads(lines[0])
    assert set(entry) == {"digest", "request", "response"}
    assert entry["digest"] == request_digest(_request())
    assert PROMPT in json.dumps(entry["request"])
    assert entry["response"]["payload"] == {"answer": "what the model said"}
    assert answered.payload == {"answer": "what the model said"}


def test_record_mode_passes_the_request_through_untouched(transcript: Path) -> None:
    """A recording adapter that edited the request would record an answer to a question
    nobody asked, and the transcript would stop matching the runtime's calls."""
    seen: list[StructuredRequest] = []

    class Watcher:
        @property
        def info(self) -> ProviderInfo:
            return _info(source=ProviderKind.LIVE.value)

        def generate_structured(self, inner_request: StructuredRequest) -> ModelResponse:
            seen.append(inner_request)
            return ModelResponse(payload={"answer": "ok"}, raw_text="{}", usage=Usage(3, 4))

        def count_tokens(self, text: str) -> int:
            return len(text)

        def health(self) -> HealthStatus:
            return HealthStatus(healthy=True)

    request = _request()
    provider = RecordReplayProvider(_info(), path=transcript, mode="record", inner=Watcher())
    response = provider.generate_structured(request)
    assert seen == [request]
    assert response.usage == Usage(input_tokens=3, output_tokens=4)
    assert provider.count_tokens("twelve chars") == 12, "record mode delegates the estimate"


def test_recording_appends_so_every_recorded_turn_stays_replayable(transcript: Path) -> None:
    """Two recorded turns, two lines, both replayable: a transcript that overwrote itself
    would answer only the last question asked."""
    inner = mock_provider(
        scripts={
            "REQUIREMENTS/REQUIREMENTS": [{"answer": "first"}, {"answer": "second"}],
        }
    )
    provider = RecordReplayProvider(_info(), path=transcript, mode="record", inner=inner)
    provider.generate_structured(_request("first question"))
    provider.generate_structured(_request("second question"))
    assert len(transcript.read_text(encoding="utf-8").splitlines()) == 2

    replay = RecordReplayProvider(_info(), path=transcript, mode="replay")
    assert replay.generate_structured(_request("first question")).payload == {"answer": "first"}
    assert replay.generate_structured(_request("second question")).payload == {"answer": "second"}


@pytest.mark.parametrize(
    ("mode", "inner", "expected_in_message"),
    [
        ("record", None, "inner provider"),
        ("nonsense", None, "mode"),
    ],
    ids=["record-without-inner", "unknown-mode"],
)
def test_a_transcript_that_cannot_be_produced_is_refused_at_construction(
    mode: str, inner: Any, expected_in_message: str, transcript: Path
) -> None:
    """Recording needs something to record from, and an unspelled mode is a typo that would
    otherwise silently replay nothing."""
    with pytest.raises(ValueError) as raised:
        RecordReplayProvider(_info(), path=transcript, mode=mode, inner=inner)  # type: ignore[arg-type]
    assert expected_in_message in str(raised.value)


def test_replay_needs_no_inner_provider(transcript: Path) -> None:
    """The other half of the constructor rule: replay is self-sufficient, which is exactly
    what makes AI-AC-10 hold when no live model exists at all."""
    _write_transcript(transcript, _entry(request_digest(_request())))
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay")
    assert provider.mode == "replay"
    assert provider.generate_structured(_request()).payload == {"answer": "recorded answer"}


# --- replay, and the no-network proof --------------------------------------------


def test_a_replayed_answer_is_the_recorded_one_field_for_field(
    transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI-AC-10's "结果与录制一致": the replayed response equals the recorded one, so an
    end-to-end test compares against what actually happened rather than a paraphrase.  The
    transcript is built through the real OpenAI-compatible adapter first, so what is being
    replayed is a real wire answer."""
    live = _live_provider(
        httpx.Response(
            200,
            json={
                "model": "recorded-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"answer": "recorded answer"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 12},
            },
            headers={"x-request-id": "req-1"},
        )
    )
    request = _request()
    recorded = RecordReplayProvider(_info(), path=transcript, mode="record", inner=live).generate_structured(
        request
    )

    dead = _forbid_network(monkeypatch)
    replay = RecordReplayProvider(_info(), path=transcript, mode="replay")
    assert replay.generate_structured(request) == recorded
    assert recorded.usage == Usage(input_tokens=900, output_tokens=12)
    assert replay.health().healthy is True, "a transcript on disk is a healthy provider"
    assert replay.count_tokens(PROMPT) > 0
    assert dead.attempts == []


def test_replay_never_falls_through_to_the_inner_provider(transcript: Path) -> None:
    """The failure mode a decorator class hides: a replay that quietly asked its live inner
    provider would pass while spending money and leaving the process."""
    _write_transcript(transcript, _entry(request_digest(_request())))
    inner: MockAgentProvider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "live"}]})
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay", inner=inner)
    assert provider.generate_structured(_request()).payload == {"answer": "recorded answer"}
    assert inner.call_count == 0, "replay asked the live provider it was supposed to shadow"


def test_a_replayed_run_touches_neither_a_transport_nor_a_client(
    transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI-AC-10 asserted structurally: with the network forbidden, a whole sequence — probe,
    generate, generate again — completes out of the transcript."""
    _write_transcript(
        transcript,
        _entry(request_digest(_request("first question")), "first answer"),
        _entry(request_digest(_request("second question")), "second answer"),
    )
    dead = _forbid_network(monkeypatch)
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay")

    assert provider.health().healthy is True
    assert provider.generate_structured(_request("first question")).payload == {"answer": "first answer"}
    assert provider.generate_structured(_request("second question")).payload == {"answer": "second answer"}
    assert dead.attempts == []


def test_a_changed_prompt_is_a_miss_and_not_a_silent_call_out(
    transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt moved and the transcript did not: this must be a loud failure.  Falling
    back to a live call would make the suite depend on a vendor (AI-LLM-006) and return an
    answer nobody recorded (AI-AC-10)."""
    _write_transcript(transcript, _entry(request_digest(_request())))
    dead = _forbid_network(monkeypatch)
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay")

    with pytest.raises(LlmError) as raised:
        provider.generate_structured(_request(PROMPT + " plus the constraints"))

    error = raised.value
    assert error.retryable is False, "a miss is final: re-recording is a human action"
    assert error.details["digest"] == request_digest(_request(PROMPT + " plus the constraints"))
    assert error.details["schemaName"] == "requirements.output"
    assert error.details["fixture"].endswith("run.jsonl")
    assert error.details["hint"] == "the prompt, the context or the schema changed; re-record"
    assert dead.attempts == []


def test_a_replayed_run_reaches_the_recorded_answer_again_on_the_corrective_retry(
    transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI-RT-011 asks the same question again after a schema failure; the attempt counter is
    not part of the key, so the second ask is served from the same entry with no I/O."""
    _write_transcript(transcript, _entry(request_digest(_request())))
    dead = _forbid_network(monkeypatch)
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay")
    first = _request(hint=AgentHint(agent_type="REQUIREMENTS", stage="REQUIREMENTS", attempt=1))
    again = _request(hint=AgentHint(agent_type="REQUIREMENTS", stage="REQUIREMENTS", attempt=2))
    assert provider.generate_structured(first).payload == provider.generate_structured(again).payload
    assert dead.attempts == []


def test_a_transcript_with_one_broken_line_still_answers_the_good_entry(
    transcript: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A hand-authored fixture is normal here and a stray line must not take the run down —
    but the skip has to be visible, or a transcript that had stopped matching would look
    exactly like a transcript that was working."""
    transcript.write_text(
        "{ this line is not json }\n\n" + json.dumps(_entry(request_digest(_request()))) + "\n",
        encoding="utf-8",
    )
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay")
    with caplog.at_level("WARNING"):
        response = provider.generate_structured(_request())
    assert response.payload == {"answer": "recorded answer"}
    assert "ignoring an unparseable line" in caplog.text
    assert provider.health().healthy is True


def test_a_missing_transcript_is_a_miss_rather_than_an_empty_answer(tmp_path: Path) -> None:
    """The wrong-but-plausible alternative is a provider that finds no file, finds no entry,
    and answers with nothing: a run would "pass" with no output at all."""
    absent = tmp_path / "never-recorded.jsonl"
    provider = RecordReplayProvider(_info(), path=absent, mode="replay")
    with pytest.raises(LlmError) as raised:
        provider.generate_structured(_request())
    assert absent.name in str(raised.value)
    health = provider.health()
    assert health.healthy is False
    assert health.detail is not None and str(absent) in health.detail


def test_an_empty_transcript_is_a_miss_on_a_provider_that_is_still_live(
    transcript: Path,
) -> None:
    """A zero-byte transcript is a recording that never happened; the failure is the miss,
    and the provider itself is still what a deployment configured."""
    transcript.write_text("", encoding="utf-8")
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay")
    with pytest.raises(LlmError):
        provider.generate_structured(_request())
    assert provider.health().healthy is True, "the file exists; it is the entry that is missing"


def test_an_entry_whose_response_is_not_an_object_is_treated_as_a_miss(transcript: Path) -> None:
    """Hand-edited transcripts happen.  Serving an empty payload from a malformed entry would
    look like a recorded answer and feed the runtime a fabricated turn."""
    _write_transcript(transcript, {"digest": request_digest(_request()), "response": "not an object"})
    with pytest.raises(LlmError):
        RecordReplayProvider(_info(), path=transcript, mode="replay").generate_structured(_request())


def test_a_recorded_answer_is_the_same_object_graph_the_caller_can_reproduce(
    transcript: Path,
) -> None:
    """The recorded payload must survive JSON round-tripping unchanged, or AI-AC-10's "结果
    与录制一致" would be about a lossy copy."""
    nested: dict[str, Any] = {
        "digest": request_digest(_request()),
        "request": {},
        "response": {
            "payload": {"decisions": [{"id": "D1", "confidence": 0.8, "tags": ["a", "b"]}], "n": 3},
            "raw_text": "{}",
            "usage": {"input_tokens": 2, "output_tokens": 1},
            "model": "recorded-model",
            "finish_reason": "stop",
            "request_id": None,
            "truncated": False,
        },
    }
    _write_transcript(transcript, nested)
    response = RecordReplayProvider(_info(), path=transcript, mode="replay").generate_structured(_request())
    assert response.payload == nested["response"]["payload"]


def test_a_failed_live_call_is_not_recorded(transcript: Path) -> None:
    """Recording an error would turn a transport failure into a permanent replayed answer;
    what the transcript holds are answers."""
    provider = RecordReplayProvider(
        _info(), path=transcript, mode="record", inner=_live_provider(httpx.Response(503, text="unavailable"))
    )
    with pytest.raises(LlmTransportError):
        provider.generate_structured(_request())
    assert not transcript.exists(), "a failed call leaves no transcript entry to replay"


def test_a_recorded_empty_answer_stays_empty_on_replay(transcript: Path) -> None:
    """AI-AC-10 reproduces what happened, including a call that answered nothing: the miss
    path must not be confused with the empty-answer path."""
    request = _request()
    recorded = RecordReplayProvider(
        _info(), path=transcript, mode="record", inner=_live_provider(httpx.Response(200, json={"choices": []}))
    ).generate_structured(request)
    replayed = RecordReplayProvider(_info(), path=transcript, mode="replay").generate_structured(request)
    assert recorded.payload == {}
    assert replayed == recorded


def test_the_provider_declares_itself_as_replay_so_usage_can_tell_it_apart(
    transcript: Path,
) -> None:
    """AI-LLM-004 records the provider of every call and AI-NFR-03 wants all of them; that
    is only meaningful if a replayed call is distinguishable from a paid one, which is what
    ``source`` and the absence of any credential reference say."""
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay")
    assert provider.info.source == ProviderKind.REPLAY.value
    assert provider.info.api_key_secret_ref is None, "a replay needs no credential at all"


def test_a_transcript_entry_is_served_stably_when_a_request_was_recorded_twice(
    transcript: Path,
) -> None:
    """Re-recording appends, so two entries can share one digest.  The answer served must be
    the same one every time (AC-REQ-01): a replay that alternated would make a run's result
    depend on how many times the fixture had already been used."""
    _write_transcript(
        transcript,
        _entry(request_digest(_request()), "first"),
        _entry(request_digest(_request()), "second"),
    )
    provider = RecordReplayProvider(_info(), path=transcript, mode="replay")
    served = [provider.generate_structured(_request()).payload["answer"] for _ in range(3)]
    assert served == [served[0]] * 3
