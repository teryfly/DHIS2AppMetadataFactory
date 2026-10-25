"""The per-call model budget as a deployment knob (AI-LLM-003).

120 s was a guess about how long a model may take, and it was wrong in the direction that breaks
the product: a bot that needs two minutes to answer a one-page requirement document produced
``model request timed out after 120.0s: ReadTimeout``, which an operator cannot tell apart from a
wrong address or a refused key. The ceiling is therefore something a deployment sets. What is worth
pinning is that the knob reaches the wire, that a value which cannot mean seconds refuses to start
the process, and that raising the *generation* budget does not also raise the health probe — the
probe is the control an operator presses while a model is suspected of being slow.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from factory_intelligence.bootstrap import TIMEOUT_ENV, timeout_from_environment
from factory_intelligence.config import IntelligenceSettings, configure, reset, settings
from factory_intelligence.enums import ProviderClass
from factory_intelligence.llm import openai_compat
from factory_intelligence.llm.errors import LlmTransportError
from factory_intelligence.llm.types import Capabilities, ProviderInfo, StructuredRequest


@pytest.fixture(autouse=True)
def _restore_settings() -> object:
    reset()
    yield
    reset()


class _RefusingTransport(httpx.BaseTransport):
    """Records the budget of each request, then fails the way an unreachable host does."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise httpx.ConnectError("no route to the model", request=request)

    def read_budget(self, index: int = 0) -> float:
        timeout = self.requests[index].extensions.get("timeout") or {}
        return float(timeout.get("read", 0.0))


def _provider(transport: httpx.BaseTransport) -> openai_compat.OpenAICompatProvider:
    endpoint = "https://slow.invalid/v1"
    return openai_compat.OpenAICompatProvider(
        ProviderInfo(
            provider="slow",
            provider_class=ProviderClass.PRIVATE_NETWORK,
            model="slow-model",
            capabilities=Capabilities(),
            endpoint=endpoint,
        ),
        endpoint=endpoint,
        transport=transport,
    )


def test_the_packaged_budget_is_above_the_old_wall() -> None:
    """An operator who sets nothing should still be able to use a two-minute bot.

    Pinned against 120 on purpose: a regression back to it silently un-fixes that.
    """
    assert IntelligenceSettings().model_request_timeout_seconds > 120.0


def test_no_environment_leaves_the_packaged_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TIMEOUT_ENV, raising=False)
    assert timeout_from_environment() is None
    assert settings().model_request_timeout_seconds == IntelligenceSettings().model_request_timeout_seconds


def test_the_environment_value_reaches_the_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """``initialize`` applies exactly this number, so the parsed value is the deployed budget."""
    monkeypatch.setenv(TIMEOUT_ENV, "420")
    configured = timeout_from_environment()
    assert configured is not None
    configure(model_request_timeout_seconds=configured)
    transport = _RefusingTransport()
    with pytest.raises(LlmTransportError):
        _provider(transport).generate_structured(
            StructuredRequest(messages=(), json_schema={"type": "object"}, schema_name="probe")
        )
    assert transport.read_budget() == pytest.approx(420.0)


@pytest.mark.parametrize(("value", "why"), [("fast", "not a number"), ("0", "under the band"), ("5000", "over the band")])
def test_a_value_that_cannot_mean_seconds_refuses_start_up(
    monkeypatch: pytest.MonkeyPatch, value: str, why: str
) -> None:
    """Silently ignoring it would leave the process on the default while the operator believes 5000."""
    del why
    monkeypatch.setenv(TIMEOUT_ENV, value)
    with pytest.raises(ValueError, match=TIMEOUT_ENV):
        timeout_from_environment()


def test_the_health_probe_keeps_its_own_short_budget() -> None:
    """The probe must not inherit a raised generation budget (S5IF-GOV20's whole point)."""
    configure(model_request_timeout_seconds=600.0)
    transport = _RefusingTransport()
    health = _provider(transport).health()
    assert health.healthy is False
    assert health.detail is not None
    assert transport.read_budget() == pytest.approx(5.0)


def test_the_adapter_still_reads_the_setting_it_claims_to() -> None:
    """A rename on either side would leave the knob inert and the tests still green."""
    source = Path(openai_compat.__file__).read_text(encoding="utf-8")
    assert "settings().model_request_timeout_seconds" in source
