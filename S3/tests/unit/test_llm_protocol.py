"""AI-LLM-001 / 002, AI-NFR-05, AI-RT-021: the shape of the provider boundary.

Three things can only be proved about an interface by looking at the interface, and this
file does that so the rest of the provider suite can take them as given:

* **AI-LLM-001's four capabilities exist and nothing else does.**  The SRS asks for
  structured generation, vision, token counting and health *at least*; the protocol adds
  no member that could act on the world, which is AI-RT-032 enforced by shape rather
  than by a prompt — a model's only output channel is a structured answer.
* **AI-LLM-002's no-vendor-SDK rule is an import fact.**  A reviewer cannot check it by
  reading call sites, because every call site *looks* fine; the imports of the
  transport-neutral modules are parsed and asserted instead.
* **AI-LLM-002's "the adapter must declare its provider class and capabilities" is
  structural**: the declaration is a required field of ``ProviderInfo``, so a provider
  that cannot answer "which class are you" cannot be built at all, and a policy denial
  can never rest on an assumed class (AI-BR-03).

The retryable set is asserted here rather than at each call site because AI-RT-021's
backoff ledger needs one shared definition: 429/502/503/504 and a timeout are transport
events, a 400/401/404 is a final answer, and the two paths consume different budgets —
AI-AC-01 counts model calls exactly.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from factory_intelligence import llm as llm_package
from factory_intelligence.deps import get_llm_provider, reset_providers, set_llm_provider
from factory_intelligence.enums import ProviderClass, ProviderKind
from factory_intelligence.errors import ProviderUnavailable
from factory_intelligence.llm.errors import (
    RETRYABLE_STATUS,
    LlmError,
    LlmTransportError,
)
from factory_intelligence.llm.mock_agent import MockAgentProvider, mock_provider
from factory_intelligence.llm.openai_compat import OpenAICompatProvider
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.record_replay import RecordReplayProvider
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

#: the four members AI-LLM-001 asks for, and the whole public surface of the protocol
PROTOCOL_MEMBERS = frozenset({"info", "generate_structured", "count_tokens", "health"})

#: vendor SDKs nothing below the adapter layer may import (AI-LLM-002)
VENDOR_SDK_ROOTS = frozenset(
    {
        "anthropic",
        "azure",
        "boto3",
        "botocore",
        "cohere",
        "google",
        "huggingface_hub",
        "litellm",
        "mistralai",
        "ollama",
        "openai",
        "replicate",
        "vertexai",
    }
)

#: the modules a *caller* may import; only ``openai_compat`` may know a transport exists
TRANSPORT_NEUTRAL_MODULES = (
    "protocol.py",
    "types.py",
    "errors.py",
    "tokens.py",
    "mock_agent.py",
    "record_replay.py",
)


@pytest.fixture(autouse=True)
def _isolated_providers() -> Iterator[None]:
    """The provider slots are process globals; a leaked mock would decide the next test."""
    reset_providers()
    yield
    reset_providers()


def _info(
    *,
    provider: str = "vllm",
    provider_class: ProviderClass = ProviderClass.LOCAL_ONLY,
    model: str = "qwen-local",
    capabilities: Capabilities | None = None,
    endpoint: str | None = "http://gpu.internal:8000/v1",
    api_key_secret_ref: str | None = None,
    source: str = "LIVE",
) -> ProviderInfo:
    return ProviderInfo(
        provider=provider,
        provider_class=provider_class,
        model=model,
        capabilities=capabilities or Capabilities(structured_output=True, vision=True),
        source=source,
        endpoint=endpoint,
        api_key_secret_ref=api_key_secret_ref,
    )


def _request(text: str = "extract the fields") -> StructuredRequest:
    return StructuredRequest(
        messages=(ChatMessage(role="user", content=text),),
        json_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    )


def _imported_roots(filename: str) -> set[str]:
    path = Path(inspect.getfile(llm_package)).parent / filename
    roots: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


# --- AI-LLM-001: the four capabilities, and nothing else --------------------------


def test_the_protocol_exposes_exactly_the_four_declared_capabilities() -> None:
    """AI-LLM-001's list is "at least" these four; the set is closed on purpose so that a
    fifth member has to be argued for rather than added quietly."""
    members = {name for name in vars(LLMProvider) if not name.startswith("_")}
    extra = sorted(members - set(PROTOCOL_MEMBERS))
    missing = sorted(set(PROTOCOL_MEMBERS) - members)
    assert members == set(PROTOCOL_MEMBERS), f"the provider surface changed: extra={extra} missing={missing}"


@pytest.mark.parametrize(
    ("member", "parameters", "returns"),
    [
        ("generate_structured", ["request"], "ModelResponse"),
        ("count_tokens", ["text"], "int"),
        ("health", [], "HealthStatus"),
    ],
    ids=["structured", "tokens", "health"],
)
def test_each_capability_takes_only_its_input_and_answers_with_a_value(
    member: str, parameters: list[str], returns: str
) -> None:
    signature = inspect.signature(getattr(LLMProvider, member))
    named = [name for name in signature.parameters if name != "self"]
    assert named == parameters, f"{member} signature drifted: {named}"
    assert returns in str(signature.return_annotation), f"{member} must answer with a {returns}"


def test_no_member_of_the_protocol_is_a_void_action_on_the_world() -> None:
    """AI-RT-032: a member that returned nothing would be an instruction to *do*
    something rather than a request for an answer.  None of them may."""
    void = [
        name
        for name in ("generate_structured", "count_tokens", "health")
        if "None" in str(inspect.signature(getattr(LLMProvider, name)).return_annotation)
    ]
    assert not void, f"provider members with no answer to give: {void}"


def test_the_provider_declaration_is_a_property_not_a_call() -> None:
    """AI-LLM-002: "the adapter must declare the provider class and capabilities" is what
    :attr:`LLMProvider.info` is for; it is read, never asked, so a policy evaluation
    cannot be charged a network round trip."""
    assert isinstance(inspect.getattr_static(LLMProvider, "info"), property)


def test_vision_rides_on_the_request_rather_than_on_a_second_method() -> None:
    """AI-LLM-001's vision capability maps onto ``ChatMessage.images`` gated by
    ``Capabilities.vision``: a screenshot extraction is still a schema-constrained answer,
    and splitting it into a second method would lose the link between the picture and the
    fields drawn from it (AI-INP-005 provenance)."""
    assert ImageInput(media_type="image/png", data_base64="AA==").label == "image"
    plain = ChatMessage(role="user", content="text only")
    with_picture = ChatMessage(
        role="user", content="text only", images=(ImageInput("image/png", "AA=="),)
    )
    assert plain.has_images is False
    assert with_picture.has_images is True
    assert with_picture.images[0].media_type == "image/png"


# --- AI-LLM-002: no vendor SDK below the adapter ---------------------------------


@pytest.mark.parametrize("filename", TRANSPORT_NEUTRAL_MODULES)
def test_no_transport_neutral_module_imports_a_vendor_sdk(filename: str) -> None:
    offenders = sorted(_imported_roots(filename) & VENDOR_SDK_ROOTS)
    assert not offenders, f"llm/{filename} imports a vendor SDK: {offenders}"


@pytest.mark.parametrize("filename", TRANSPORT_NEUTRAL_MODULES)
def test_only_the_openai_adapter_is_allowed_to_know_about_http(filename: str) -> None:
    """AI-NFR-05: adding a provider means adding *one adapter file*, which is only true
    while the shared types and the mock and replay providers hold no transport import."""
    assert "httpx" not in _imported_roots(filename), f"llm/{filename} reaches for a transport"


def test_the_openai_adapter_is_the_one_module_that_holds_the_transport() -> None:
    """The negative half of the rule above: if ``httpx`` vanished from the adapter the
    import tests would pass while proving nothing."""
    assert "httpx" in _imported_roots("openai_compat.py")


# --- AI-LLM-002: the declaration is mandatory ------------------------------------


def test_a_provider_info_without_a_class_cannot_be_built() -> None:
    """AI-BR-03 / AI-LLM-002: no provider class means no policy evaluation, so the field
    is required rather than defaulted — an assumed ``LOCAL_ONLY`` would be a promise that
    nothing leaves the network, which S3 must not make on someone else's behalf."""
    with pytest.raises(TypeError):
        ProviderInfo(  # type: ignore[call-arg]
            provider="hosted", model="gpt-ish", capabilities=Capabilities()
        )


def test_capabilities_declare_exactly_the_three_abilities_ai_llm_002_names() -> None:
    names = {field.name for field in dataclasses.fields(Capabilities)}
    assert names == {"structured_output", "vision", "max_context_tokens"}


def test_capabilities_decline_everything_until_a_registration_says_otherwise() -> None:
    """The deny-by-default shape of AI-POL-007's capability check: an undeclared ability
    is unavailable, not merely unknown."""
    declared = Capabilities()
    assert declared.structured_output is False
    assert declared.vision is False
    assert declared.max_context_tokens == 8000


def test_the_source_of_a_call_is_recorded_as_live_by_default() -> None:
    """AI-MOCK-001 and AI-LLM-006 promise no cost and no network; the only way a reviewer
    can tell a served-from-script call from a paid one is the declared source."""
    assert _info().source == ProviderKind.LIVE.value
    assert {kind.value for kind in ProviderKind} == {"LIVE", "REPLAY", "MOCK"}


# --- the value objects ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        ImageInput("image/png", "AA=="),
        ChatMessage(role="user", content="x"),
        Capabilities(),
        ProviderInfo(
            provider="p",
            provider_class=ProviderClass.LOCAL_ONLY,
            model="m",
            capabilities=Capabilities(),
        ),
        StructuredRequest(messages=(), json_schema={}),
        AgentHint(agent_type="REQUIREMENTS", stage="REQUIREMENTS"),
        Usage(),
        ModelResponse(payload={"a": 1}),
        HealthStatus(healthy=True),
    ],
    ids=[
        "image",
        "message",
        "capabilities",
        "provider-info",
        "request",
        "agent-hint",
        "usage",
        "response",
        "health",
    ],
)
def test_every_provider_type_is_a_frozen_value(value: Any) -> None:
    """A request or a response mutated after the fact would make AI-AC-01's call ledger
    and AI-LLM-006's replay digest unprovable, so all of them are frozen."""
    first_field = dataclasses.fields(value)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, first_field, "attempted mutation")


def test_a_structured_request_defaults_to_the_deterministic_call() -> None:
    """AC-REQ-01: the same context must decide the same thing twice, so the default
    temperature is 0.0 and there is no hidden output cap."""
    request = _request()
    assert request.temperature == 0.0
    assert request.max_output_tokens is None
    assert request.schema_name == "response"
    assert request.agent_hint is None


def test_the_agent_hint_is_a_key_and_not_an_identity() -> None:
    """AI-MOCK-001 scripts by *agent type and stage*; a run id or an attempt counter in
    that key would make every script single-use."""
    hint = AgentHint(agent_type="MAPPING", stage="DATA_MAPPING", run_id="arun-7", attempt=3)
    assert hint.key == "MAPPING/DATA_MAPPING"
    assert hint.key == AgentHint(agent_type="MAPPING", stage="DATA_MAPPING").key
    assert "arun-7" not in hint.key


def test_usage_totals_the_two_directions_it_reports() -> None:
    """AI-LLM-004 / AI-COST-001: a call records input and output separately because the
    registry prices them differently; the total is derived, never supplied."""
    assert Usage(input_tokens=120, output_tokens=30).total_tokens == 150
    assert Usage().total_tokens == 0


def test_a_response_without_usage_still_reports_zero_usage() -> None:
    """AI-NFR-03 asks for a usage record on 100% of calls: the transport-neutral default
    is a real zero, not an absent row a rollup would have to guess about."""
    response = ModelResponse(payload={"answer": "ok"})
    assert response.usage == Usage(input_tokens=0, output_tokens=0)
    assert response.truncated is False
    assert response.raw_text == ""


def test_a_response_can_carry_an_answer_the_schema_refused() -> None:
    """AI-RT-011's boundary, stated by the type: the provider returns whatever decoded,
    so deciding that it fails the schema is the runtime's one corrective retry and not
    the transport's call to make."""
    response = ModelResponse(payload={"wrong": 1}, finish_reason="stop")
    assert response.payload == {"wrong": 1}


# --- AI-LLM-002's API-key rule, in the type that carries it ------------------------


def test_the_key_field_holds_a_reference_and_refuses_a_literal_value() -> None:
    """AI-LLM-005: a Secret reference such as ``env://NAME`` is all a registration may
    carry.  The guard sits on the record itself, because a literal key arriving here is a
    bug upstream and leaking it three calls later is the expensive version of it."""
    assert _info(api_key_secret_ref="env://MODEL_API_KEY").api_key_secret_ref == "env://MODEL_API_KEY"
    assert _info(api_key_secret_ref=None).api_key_secret_ref is None
    with pytest.raises(ValueError) as raised:
        _info(api_key_secret_ref="sk-live-9f2c0a")
    assert "secret reference" in str(raised.value).lower()


# --- AI-RT-021's classification --------------------------------------------------


def test_the_retryable_set_is_a_frozen_set_of_status_numbers() -> None:
    assert isinstance(RETRYABLE_STATUS, frozenset)
    assert all(isinstance(status, int) for status in RETRYABLE_STATUS)
    assert all(100 <= status < 600 for status in RETRYABLE_STATUS), RETRYABLE_STATUS


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_the_statuses_ai_rt_021_names_by_number_are_retryable(status: int) -> None:
    assert status in RETRYABLE_STATUS, f"{status} is named in AI-RT-021 and must retry"


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 405, 410, 413, 422, 451])
def test_the_statuses_that_would_retry_forever_are_final(status: int) -> None:
    """A malformed request or a refused credential does not become legal by waiting;
    retrying it would spend AI-AC-01's call budget on a certain failure."""
    assert status not in RETRYABLE_STATUS, f"{status} is a client error, not a transport event"


def test_a_transport_failure_and_a_final_one_are_siblings_not_parent_and_child() -> None:
    """The runtime catches the two apart: a transport error backs off and does **not**
    consume AI-RT-011's one corrective retry.  If one subclassed the other,
    ``except LlmError`` would swallow the retryable case and the retry ledger would read
    as a final failure."""
    assert not issubclass(LlmTransportError, LlmError)
    assert not issubclass(LlmError, LlmTransportError)
    assert LlmTransportError("x").retryable is True
    assert LlmError("x").retryable is False


# --- AI-NFR-05: the interface is the only thing an agent knows ---------------------


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("openai_compat", lambda: OpenAICompatProvider(_info(), endpoint="http://gpu.internal:8000/v1")),
        ("mock", lambda: mock_provider(scripts={"REQUIREMENTS/REQUIREMENTS": [{"answer": "ok"}]})),
        ("record_replay", lambda: RecordReplayProvider(_info(), path=Path("no-such-transcript.jsonl"))),
    ],
    ids=["openai", "mock", "replay"],
)
def test_each_shipped_provider_is_a_provider_by_inspection(
    name: str, build: Any
) -> None:
    instance = build()
    assert isinstance(instance, LLMProvider), f"{name} no longer satisfies the protocol"
    assert instance.info.provider_class is ProviderClass.LOCAL_ONLY


def test_a_new_adapter_needs_no_base_class_and_no_registration() -> None:
    """AI-NFR-05: "a new provider is one more ``LLMProvider`` adapter".  The protocol is
    runtime-checkable and structural, so a class that answers the four members *is* a
    provider without importing anything from S3's provider layer."""

    class ThirdPartyProvider:
        @property
        def info(self) -> ProviderInfo:
            return _info(provider="newcomer")

        def generate_structured(self, request: StructuredRequest) -> ModelResponse:
            return ModelResponse(payload={"answer": request.messages[0].content})

        def count_tokens(self, text: str) -> int:
            return len(text) // 4

        def health(self) -> HealthStatus:
            return HealthStatus(healthy=True)

    provider: Any = ThirdPartyProvider()
    assert isinstance(provider, LLMProvider)
    assert provider.generate_structured(_request("hello")).payload == {"answer": "hello"}

    class MissingHealth:
        """The same adapter, minus the health probe (AI-LLM-001's fourth capability)."""

        @property
        def info(self) -> ProviderInfo:
            return _info(provider="newcomer")

        def generate_structured(self, request: StructuredRequest) -> ModelResponse:
            return ModelResponse(payload={"answer": request.messages[0].content})

        def count_tokens(self, text: str) -> int:
            return len(text) // 4

    assert not isinstance(MissingHealth(), LLMProvider), (
        "a provider missing health must not pass the protocol check"
    )


def test_the_installed_provider_serves_whatever_model_the_registry_chose() -> None:
    """AI-MOCK-001 and AI-LLM-006 are first-class because they are installed through
    :func:`set_llm_provider`: a fixed provider answers for *every* registered model, which
    is how a rehearsal runs without touching a network."""
    installed = mock_provider(scripts={"response": [{"answer": "scripted"}]})
    set_llm_provider(installed)
    served = get_llm_provider(_info(provider="someone-else", model="other"))
    assert served is installed
    assert served.generate_structured(_request()).payload == {"answer": "scripted"}
    assert isinstance(installed, MockAgentProvider)


def test_an_unconfigured_provider_slot_refuses_instead_of_guessing_a_vendor() -> None:
    """AI-LLM-003's default adapter is installed at start-up; before that, reaching for a
    provider must fail rather than fall back to a public API nobody approved
    (AI-POL-002's deny by default)."""
    with pytest.raises(ProviderUnavailable) as raised:
        get_llm_provider(_info())
    assert raised.value.details["model"] == "qwen-local"
    assert "openai.com" not in str(raised.value)
