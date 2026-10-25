"""AI-MOCK-001: scripted answers, per agent and stage, with no network and no cost.

M1's exit criterion is "run all stages and Gates with mock agents", which only means
something if the mock is the same kind of object a real model is: one ``LLMProvider``, asked
the same ``StructuredRequest``, answering with the same ``ModelResponse``.  Everything below
therefore drives the mock through the protocol rather than through its own conveniences, and
the call ledger it keeps is asserted exactly, because AI-AC-01's "只调用模型两次" is a count
of these records.

Three script shapes exist to hand the runtime its three failure paths, and each is asserted
in both directions — what it raises, and what it deliberately does **not** raise:

* a plain object — the answer;
* ``{"$raw": "..."}`` — text that is not an answer, which must come back as a returned
  response with an empty payload, not as an exception: the Schema-failure path belongs to
  AI-RT-011's corrective retry, and a mock that raised here would test a path the runtime
  never takes;
* ``{"$error": {...}}`` — a provider failure, split the same way AI-RT-021 splits the real
  one: ``transport``/``timeout`` retry, anything else is final.

One caveat the tests record rather than hide: an error script written as a bare string (
``{"$error": "transport"}``) is read as a kind by ``_raise_scripted`` and then crashed on,
because the transport branch reaches for ``error.get("status")`` on that string.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from factory_intelligence.deps import get_llm_provider, reset_providers, set_llm_provider
from factory_intelligence.enums import ProviderClass, ProviderKind
from factory_intelligence.llm.errors import LlmError, LlmTransportError
from factory_intelligence.llm.mock_agent import (
    MockAgentProvider,
    load_script_directory,
    load_scripts,
    mock_provider,
)
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.tokens import estimate_messages, estimate_text
from factory_intelligence.llm.types import (
    AgentHint,
    Capabilities,
    ChatMessage,
    ProviderInfo,
    StructuredRequest,
    Usage,
)


def _hint(agent: str = "REQUIREMENTS", stage: str = "REQUIREMENTS") -> AgentHint:
    return AgentHint(agent_type=agent, stage=stage, run_id="arun-1", attempt=1)


def _request(agent: str = "REQUIREMENTS", stage: str = "REQUIREMENTS", content: str = "the input") -> StructuredRequest:
    return StructuredRequest(
        messages=(
            ChatMessage(role="system", content="you consolidate requirements"),
            ChatMessage(role="user", content=content),
        ),
        json_schema={"type": "object", "required": ["answer"]},
        schema_name=f"{agent.lower()}.output",
        agent_hint=_hint(agent, stage),
    )


def _info_for(agent: str) -> ProviderInfo:
    return ProviderInfo(
        provider="mock",
        provider_class=ProviderClass.LOCAL_ONLY,
        model=agent.lower(),
        capabilities=Capabilities(structured_output=True),
    )


@pytest.fixture(autouse=True)
def _no_leaked_providers() -> Iterator[None]:
    reset_providers()
    yield
    reset_providers()


# --- the script is keyed by agent and stage ---------------------------------------


def test_a_script_is_served_to_the_agent_and_stage_it_was_written_for() -> None:
    """AI-MOCK-001's "按 Agent 类型与阶段": the key is ``<AgentType>/<stage>``, so two stages
    of one agent can be scripted differently without either reaching around the request for
    run context."""
    provider = mock_provider(
        scripts={
            "REQUIREMENTS/REQUIREMENTS": [{"answer": "requirements"}],
            "DOMAIN/DOMAIN": [{"answer": "domain"}],
        }
    )
    assert provider.generate_structured(_request("REQUIREMENTS", "REQUIREMENTS")).payload == {
        "answer": "requirements"
    }
    assert provider.generate_structured(_request("DOMAIN", "DOMAIN")).payload == {"answer": "domain"}
    assert provider.call_count == 2


def test_an_agent_level_script_covers_every_stage_that_agent_runs() -> None:
    """The documented fallback in ``_answer_for``: one key naming the agent answers for all
    of its stages, which is what makes a whole-run mock script short enough to read."""
    provider = mock_provider(scripts={"MAPPING": [{"answer": "any stage"}]})
    for stage in ("DATA_MAPPING", "METADATA_MAPPING", "API_MAPPING"):
        assert provider.generate_structured(_request("MAPPING", stage)).payload == {"answer": "any stage"}
    assert provider.calls_for("MAPPING/DATA_MAPPING")[0].key == "MAPPING/DATA_MAPPING"


def test_a_request_without_an_agent_hint_is_keyed_by_its_schema_name() -> None:
    """``_script_key`` falls back to ``schema_name``, so a bare call (an evaluation, a
    vision extraction) is still scriptable without inventing a stage for it."""
    provider = mock_provider(scripts={"requirements.output": [{"answer": "by schema"}]})
    request = StructuredRequest(
        messages=(ChatMessage(role="user", content="x"),),
        json_schema={"type": "object"},
        schema_name="requirements.output",
    )
    assert provider.generate_structured(request).payload == {"answer": "by schema"}


def test_a_stage_nobody_scripted_fails_loudly_and_invents_nothing() -> None:
    """The alternative — an empty answer for an unknown key — would let M1's exit criterion
    pass while every stage actually produced nothing."""
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "ok"}]})
    with pytest.raises(LlmError) as raised:
        provider.generate_structured(_request("REVIEW", "REVIEW"))
    error = raised.value
    assert error.retryable is False
    assert error.details["key"] == "REVIEW/REVIEW"
    assert error.details["known"] == ["REQUIREMENTS/REQUIREMENTS"]
    assert provider.call_count == 0, "a call the mock could not script is not a model call"


def test_a_fallback_answers_any_key_but_is_recorded_under_the_requested_one() -> None:
    """AI-MOCK-001's cost of a whole-run rehearsal: one default answer plus per-stage
    overrides.  The ledger must still show which stage asked."""
    provider = mock_provider(
        scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "specific"}]},
        fallback={"answer": "generic"},
    )
    assert provider.generate_structured(_request()).payload == {"answer": "specific"}
    assert provider.generate_structured(_request("DOMAIN", "DOMAIN")).payload == {"answer": "generic"}
    assert provider.calls_for("DOMAIN/DOMAIN")[0].payload == {"answer": "generic"}
    assert provider.call_count == 2


def test_a_fallback_is_stable_across_calls() -> None:
    """A fallback is not a sequence: a corrective retry (AI-RT-011) asks again and must get
    the same answer, or the run's behaviour would depend on how many times it had asked."""
    provider = mock_provider(fallback={"answer": "generic"})
    first = provider.generate_structured(_request())
    again = provider.generate_structured(_request())
    assert first.payload == again.payload == {"answer": "generic"}
    assert first.raw_text == again.raw_text


def test_an_empty_script_for_a_key_is_refused_rather_than_answered_empty() -> None:
    provider = MockAgentProvider(
        ProviderInfo(
            provider="mock",
            provider_class=ProviderClass.LOCAL_ONLY,
            model="mock-agent",
            capabilities=Capabilities(structured_output=True),
        ),
        scripts={"REQUIREMENTS/REQUIREMENTS": []},
    )
    with pytest.raises(LlmError) as raised:
        provider.generate_structured(_request())
    assert "empty" in str(raised.value)


# --- the sequence within one key --------------------------------------------------


def test_a_key_serves_its_entries_in_order_then_repeats_the_last_one() -> None:
    """This is how AI-AC-04 is scripted: a Question first, the corrected answer after, and
    any further ask pinned to the last entry rather than running off the end."""
    provider = mock_provider(
        scripts={
            "REQUIREMENTS/REQUIREMENTS": [
                {"questions": [{"localId": "Q1"}]},
                {"answer": "corrected"},
            ]
        }
    )
    served = [provider.generate_structured(_request()).payload for _ in range(4)]
    assert served[0] == {"questions": [{"localId": "Q1"}]}
    assert served[1] == {"answer": "corrected"}
    assert served[2] == served[3] == {"answer": "corrected"}
    assert provider.call_count == 4


def test_reset_rewinds_every_script_and_forgets_the_ledger() -> None:
    """``reset`` exists so one provider can drive two runs; a ledger that survived would make
    AI-AC-01's exact count a function of test order."""
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "one"}, {"answer": "two"}]})
    provider.generate_structured(_request())
    provider.reset()
    assert provider.calls == []
    assert provider.call_count == 0
    assert provider.generate_structured(_request()).payload == {"answer": "one"}


def test_add_script_extends_an_existing_key_and_creates_a_new_one() -> None:
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "one"}]})
    provider.add_script("REQUIREMENTS/REQUIREMENTS", {"answer": "two"})
    provider.add_script("DOMAIN/DOMAIN", {"answer": "three"})
    assert provider.generate_structured(_request()).payload == {"answer": "one"}
    assert provider.generate_structured(_request()).payload == {"answer": "two"}
    assert provider.generate_structured(_request("DOMAIN", "DOMAIN")).payload == {"answer": "three"}


def test_the_scripts_a_caller_handed_in_are_copied() -> None:
    """A script the mock shared with its author would let one test's mutation decide the next
    stage's answer."""
    answers: list[dict[str, Any]] = [{"answer": "original"}]
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": answers})
    provider.generate_structured(_request())
    answers[0]["answer"] = "mutated afterwards"
    assert provider.generate_structured(_request()).payload == {"answer": "original"}


# --- $raw: text that is not an answer --------------------------------------------


def test_a_raw_answer_is_returned_as_an_empty_payload_rather_than_raising() -> None:
    """AI-RT-011 / AI-AC-01: this is the Script-failure path.  The mock must hand back a
    response the ladder can reject, not an exception — a raised error here would test a path
    the runtime never walks and would consume the transport budget instead."""
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"$raw": "I would rather chat"}]})
    response = provider.generate_structured(_request())
    assert response.payload == {}
    assert response.raw_text == "I would rather chat"
    assert response.finish_reason == "stop"
    assert provider.call_count == 1, "the call was made and paid for; only the answer is unusable"


def test_a_raw_answer_beats_the_fields_written_beside_it() -> None:
    """The negative half: ``{"$raw": ..., "answer": ...}`` is a mistake in a script, and the
    ``$raw`` branch deciding it is what keeps such a script from silently disagreeing with
    itself."""
    provider = mock_provider(
        scripts={"REQUIREMENTS/REQUIREMENTS": [{"$raw": "not json", "answer": "ignored"}]}
    )
    assert provider.generate_structured(_request()).payload == {}


def test_a_raw_json_string_is_still_not_parsed_into_the_payload() -> None:
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"$raw": '{"answer": "x"}'}]})
    response = provider.generate_structured(_request())
    assert response.raw_text == '{"answer": "x"}'
    assert response.payload == {}


# --- $error: the two retry classes ----------------------------------------------


def test_a_scripted_transport_error_is_retryable_and_defaulted_to_a_gateway_status() -> None:
    """AI-RT-021's first class: the runtime backs off and does not spend AI-RT-011's
    corrective retry on it."""
    provider = mock_provider(
        scripts={"REQUIREMENTS/REQUIREMENTS": [{"$error": {"kind": "transport", "message": "busy"}}]}
    )
    with pytest.raises(LlmTransportError) as raised:
        provider.generate_structured(_request())
    assert raised.value.status_code == 503
    assert raised.value.message == "busy"
    assert raised.value.retryable is True


@pytest.mark.parametrize(
    ("kind", "status", "expected_status"),
    [("transport", None, 503), ("transport", 429, 429), ("timeout", None, None)],
    ids=["transport-default", "transport-with-status", "timeout"],
)
def test_a_scripted_failure_picks_the_class_the_runtime_branches_on(
    kind: str, status: int | None, expected_status: int | None
) -> None:
    error: dict[str, Any] = {"kind": kind}
    if status is not None:
        error["status"] = status
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"$error": error}]})
    with pytest.raises(LlmTransportError) as raised:
        provider.generate_structured(_request())
    assert raised.value.status_code == expected_status


@pytest.mark.parametrize(
    ("error", "expected_message", "expected_status"),
    [
        ({"kind": "final", "message": "policy refuses this context"}, "policy refuses this context", None),
        ({"kind": "refusal", "message": "cannot help with that"}, "cannot help with that", None),
        ({"kind": "final", "message": "bad schema", "status": 400}, "bad schema", 400),
        ("unknown kind", "scripted provider failure", None),
    ],
    ids=["final", "unknown-kind", "final-with-status", "bare-string"],
)
def test_a_scripted_final_failure_is_not_a_transport_error(
    error: Any, expected_message: str, expected_status: int | None
) -> None:
    """AI-RT-021's other half, and the reason a policy denial is not retried: a ``LlmError``
    ends the run and the usage row records it."""
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"$error": error}]})
    with pytest.raises(LlmError) as raised:
        provider.generate_structured(_request())
    assert not isinstance(raised.value, LlmTransportError)
    assert raised.value.message == expected_message
    assert raised.value.status_code == expected_status


def test_a_bare_string_error_names_the_transport_class() -> None:
    """``_raise_scripted`` reads a non-mapping error as its kind — ``str(error)`` — so the
    short form must still produce the documented exception."""
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"$error": "transport"}]})
    with pytest.raises(LlmTransportError):
        provider.generate_structured(_request())


def test_a_scripted_error_still_counts_as_a_model_call() -> None:
    """AI-AC-01 counts model calls, and a call that failed is a call: dropping it from the
    ledger would make "只调用模型两次" unfalsifiable."""
    provider = mock_provider(
        scripts={"REQUIREMENTS/REQUIREMENTS": [{"$error": {"kind": "transport"}}, {"answer": "ok"}]}
    )
    with pytest.raises(LlmTransportError):
        provider.generate_structured(_request())
    assert provider.call_count == 1
    assert provider.calls[0].payload == {}
    assert provider.generate_structured(_request()).payload == {"answer": "ok"}
    assert provider.call_count == 2


def test_the_error_marker_is_decided_before_the_raw_marker() -> None:
    """A script carrying both is a mistake; failing as an error is the safer reading, since
    ``$raw`` would quietly consume the run's one corrective retry."""
    provider = mock_provider(
        scripts={"REQUIREMENTS/REQUIREMENTS": [{"$error": {"kind": "final"}, "$raw": "text"}]}
    )
    with pytest.raises(LlmError):
        provider.generate_structured(_request())


# --- the mock as a provider -------------------------------------------------------


def test_the_mock_reports_the_usage_a_paid_call_would_have_cost() -> None:
    """AI-LLM-004 / AI-NFR-03's provider half: even a scripted answer arrives with token
    counts, a model name and a request id, so the usage layer is exercised by the same code
    path it will see in production."""
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "ok"}]})
    request = _request()
    response = provider.generate_structured(request)
    assert response.usage == Usage(
        input_tokens=estimate_messages(request.messages),
        output_tokens=estimate_text(json.dumps({"answer": "ok"}, ensure_ascii=False, sort_keys=True)),
    )
    assert response.usage.input_tokens > 0
    assert response.model == "mock-agent"
    assert response.request_id is not None
    assert response.truncated is False


def test_the_answer_is_serialised_the_way_the_transport_would_serialise_it() -> None:
    """``raw_text`` is what the evaluation diff and the cost estimate read, so the mock puts
    the same canonical JSON in it that an OpenAI-compatible answer would carry."""
    provider = mock_provider(
        scripts={"REQUIREMENTS/REQUIREMENTS": [{"z": 1, "a": {"nested": [1, 2]}}]}
    )
    response = provider.generate_structured(_request())
    assert response.raw_text == json.dumps({"a": {"nested": [1, 2]}, "z": 1}, ensure_ascii=False, sort_keys=True)


def test_two_calls_get_two_distinct_request_ids() -> None:
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "ok"}]})
    ids = [provider.generate_structured(_request()).request_id for _ in range(3)]
    assert all(request_id is not None for request_id in ids), ids
    assert len(set(ids)) == 3, f"AI-LLM-004 needs one id per call, got {ids}"


def test_the_mock_is_a_provider_and_its_health_probe_is_free() -> None:
    """AI-LLM-002 / AI-MOCK-001: the mock satisfies the same protocol, which is what lets
    ``runtime`` and the policy walk drive it unchanged; health is answered from the object,
    not from a probe."""
    provider = mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "ok"}]})
    assert isinstance(provider, LLMProvider)
    assert provider.health().healthy is True
    assert provider.count_tokens("eight chars") == estimate_text("eight chars")


def test_the_mock_declares_itself_as_mock_so_a_usage_row_cannot_impersonate_a_run() -> None:
    """AI-MOCK-001 promises "不产生成本"; that is only auditable because the declared source
    and provider class say a script answered."""
    provider = mock_provider(scripts={})
    assert provider.info.source == ProviderKind.MOCK.value
    assert provider.info.provider_class is ProviderClass.LOCAL_ONLY
    assert provider.info.api_key_secret_ref is None, "a mock resolves no credential"
    assert provider.info.capabilities == Capabilities(
        structured_output=True, vision=True, max_context_tokens=1_000_000
    )


def test_the_mock_can_be_given_the_identity_of_the_model_it_stands_in_for() -> None:
    """A rehearsal of a policy decision needs the *registered* class: a mock declaring
    LOCAL_ONLY could never reproduce a denial that only fires for an external provider."""
    registered = ProviderInfo(
        provider="acme",
        provider_class=ProviderClass.APPROVED_EXTERNAL,
        model="acme-large",
        capabilities=Capabilities(structured_output=True),
    )
    provider = mock_provider(registered, scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "ok"}]})
    assert provider.info is registered
    assert provider.info.provider_class is ProviderClass.APPROVED_EXTERNAL


def test_a_installed_mock_serves_the_provider_lookup_without_a_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI-MOCK-001's "不访问网络" proved structurally: with ``httpx.Client`` replaced by
    something that raises, a scripted multi-stage sequence still completes through the
    injection point the runtime actually uses."""

    def blocked(*args: Any, **kwargs: Any) -> httpx.Client:
        raise AssertionError("a mock run reached for a network client")

    monkeypatch.setattr(httpx, "Client", blocked)
    monkeypatch.setattr(httpx, "AsyncClient", blocked)
    provider = mock_provider(
        scripts={
            "REQUIREMENTS/REQUIREMENTS": [{"answer": "requirements"}],
            "DOMAIN/DOMAIN": [{"answer": "domain"}],
            "MAPPING/METADATA_MAPPING": [{"answer": "mapping"}],
        }
    )
    set_llm_provider(provider)
    for agent, stage in (("REQUIREMENTS", "REQUIREMENTS"), ("DOMAIN", "DOMAIN"), ("MAPPING", "METADATA_MAPPING")):
        assert get_llm_provider(_info_for(agent)).generate_structured(_request(agent, stage)).payload == {
            "answer": agent.lower()
        }
    assert provider.call_count == 3


# --- script files -----------------------------------------------------------------


def test_a_script_file_maps_keys_to_answers(tmp_path: Path) -> None:
    """AI-MOCK-001 says "从脚本文件": one JSON object of key to answer, either a single
    object or a sequence."""
    path = tmp_path / "m1.json"
    path.write_text(
        json.dumps(
            {
                "REQUIREMENTS/REQUIREMENTS": [{"answer": "one"}, {"answer": "two"}],
                "DOMAIN/DOMAIN": {"answer": "single"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    scripts = load_scripts(path)
    assert scripts["REQUIREMENTS/REQUIREMENTS"] == [{"answer": "one"}, {"answer": "two"}]
    assert scripts["DOMAIN/DOMAIN"] == [{"answer": "single"}]


def test_a_provider_can_be_built_straight_from_a_script_file(tmp_path: Path) -> None:
    path = tmp_path / "m1.json"
    path.write_text(json.dumps({"REVIEW/REVIEW": [{"answer": "from file"}]}), encoding="utf-8")
    provider = mock_provider(script_path=path)
    assert provider.generate_structured(_request("REVIEW", "REVIEW")).payload == {"answer": "from file"}


@pytest.mark.parametrize(
    "document",
    [
        ["a list, not a map of keys"],
        {"REVIEW/REVIEW": ["an answer that is a string"]},
        {"REVIEW/REVIEW": 3},
    ],
    ids=["root-is-a-list", "answer-is-a-string", "answer-is-a-number"],
)
def test_a_malformed_script_file_is_refused_when_loaded(
    document: Any, tmp_path: Path
) -> None:
    """A script file that does not describe answers must fail at load: a mock that accepted
    it would answer with nothing and the M1 rehearsal would pass on empty scripts."""
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        load_scripts(path)
    assert "must" in str(raised.value)


def test_a_script_directory_later_file_wins_per_key(tmp_path: Path) -> None:
    """``load_script_directory`` merges on purpose (a base pack plus a project's overrides),
    so the precedence is the thing worth pinning down."""
    (tmp_path / "00-base.json").write_text(
        json.dumps({"REVIEW/REVIEW": [{"answer": "base"}], "DOMAIN/DOMAIN": [{"answer": "base"}]}),
        encoding="utf-8",
    )
    (tmp_path / "10-override.json").write_text(
        json.dumps({"REVIEW/REVIEW": [{"answer": "override"}]}), encoding="utf-8"
    )
    merged = load_script_directory(tmp_path)
    assert merged["REVIEW/REVIEW"] == [{"answer": "override"}]
    assert merged["DOMAIN/DOMAIN"] == [{"answer": "base"}]
    provider = mock_provider(scripts=merged)
    assert provider.generate_structured(_request("REVIEW", "REVIEW")).payload == {"answer": "override"}


def test_an_empty_script_directory_yields_a_provider_that_refuses_every_call(tmp_path: Path) -> None:
    assert load_script_directory(tmp_path) == {}
    provider = mock_provider(scripts=load_script_directory(tmp_path))
    with pytest.raises(LlmError):
        provider.generate_structured(_request())
