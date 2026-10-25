"""Process-wide dependency injection (mirrors ``factory_blueprint.deps``).

S3's services never reach for an optional collaborator directly; they ask here.
That is what makes the mock agent and the record-replay adapter first-class
(AI-MOCK-001, AI-LLM-006) rather than test-only monkeypatching, and what lets S1's
``factory_intelligence.testing`` fakes drive S3's own tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import RLock
from typing import Any

from factory_foundation.api import Clock, SystemClock

from factory_intelligence.errors import ProviderUnavailable
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.types import ProviderInfo
from factory_intelligence.s2.adapter import S2Adapter
from factory_intelligence.s2.protocol import BlueprintPort

_lock = RLock()

_blueprint: BlueprintPort | None = None
_fixed_provider: LLMProvider | None = None
_provider_factory: Callable[[ProviderInfo], LLMProvider] | None = None
_sleeper: Callable[[float], None] | None = None
_clock: Clock | None = None
_http_transport: Any = None


def set_blueprint(implementation: BlueprintPort | None) -> None:
    global _blueprint
    with _lock:
        _blueprint = implementation


def get_blueprint() -> BlueprintPort:
    """The installed S2 collaborator, defaulting to the real API adapter."""
    with _lock:
        if _blueprint is not None:
            return _blueprint
    return S2Adapter()


def set_llm_provider(provider: LLMProvider | None) -> None:
    """Install one provider for every model — the mock and replay path (AI-MOCK-001)."""
    global _fixed_provider
    with _lock:
        _fixed_provider = provider


def set_llm_provider_factory(factory: Callable[[ProviderInfo], LLMProvider] | None) -> None:
    """Install the per-model constructor the real path uses (AI-NFR-05)."""
    global _provider_factory
    with _lock:
        _provider_factory = factory


def get_llm_provider(info: ProviderInfo) -> LLMProvider:
    """Return the provider serving one registered model.

    A fixed provider wins outright: a deployment that installed one has said
    "every model call goes here", which is precisely how a test or a rehearsal runs
    without touching a network.
    """
    with _lock:
        if _fixed_provider is not None:
            return _fixed_provider
        factory = _provider_factory
    if factory is None:
        raise ProviderUnavailable(
            "no LLM provider factory is configured; start-up must call initialize(), "
            "which installs the OpenAI-compatible default (AI-LLM-003)",
            details={"model": info.model},
        )
    return factory(info)


def set_sleeper(sleeper: Callable[[float], None] | None) -> None:
    global _sleeper
    with _lock:
        _sleeper = sleeper


def get_sleeper() -> Callable[[float], None]:
    """Backoff sleeper.

    Injectable so AI-RT-021's exponential backoff is testable without a test suite
    that sleeps: a recorder asserts the *delays* while the run finishes instantly.
    """
    with _lock:
        if _sleeper is not None:
            return _sleeper
    return time.sleep


def set_clock(clock: Clock | None) -> None:
    global _clock
    with _lock:
        _clock = clock


def get_clock() -> Clock:
    with _lock:
        if _clock is not None:
            return _clock
    return SystemClock()


def set_http_transport(transport: Any) -> None:
    """An ``httpx`` transport for the OpenAI-compatible adapter (tests inject a mock)."""
    global _http_transport
    with _lock:
        _http_transport = transport


def get_http_transport() -> Any:
    with _lock:
        return _http_transport


def reset_providers() -> None:
    """Return to the unconfigured state; used between tests."""
    global _blueprint, _fixed_provider, _provider_factory, _sleeper, _clock, _http_transport
    with _lock:
        _blueprint = None
        _fixed_provider = None
        _provider_factory = None
        _sleeper = None
        _clock = None
        _http_transport = None


__all__ = [
    "set_blueprint",
    "get_blueprint",
    "set_llm_provider",
    "set_llm_provider_factory",
    "get_llm_provider",
    "set_sleeper",
    "get_sleeper",
    "set_clock",
    "get_clock",
    "set_http_transport",
    "get_http_transport",
    "reset_providers",
]
