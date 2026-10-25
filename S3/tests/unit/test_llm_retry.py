"""The transport retry policy (AI-RT-021): exponential, bounded by count *and* by time.

Two ceilings exist because neither alone is enough. A retry count without a time budget lets six
300-second timeouts hold a worker for half an hour with no operator-visible reason; a time budget
without a count lets a provider that fails in 50 ms be retried hundreds of times. What is pinned
here is the shape of both, the ``Retry-After`` override, and the reset rule that keeps one slow
answer from spending the budget the next answer needs.

No test sleeps: the sleeper is the injected one, and the clock is a fake when elapsed time is part
of the assertion.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from factory_intelligence import deps
from factory_intelligence.config import IntelligenceSettings, configure, reset
from factory_intelligence.llm.errors import LlmError, LlmTransportError
from factory_intelligence.llm.retry import (
    WaitBudget,
    call_with_retries,
    max_attempts,
    wait_budget,
)


@pytest.fixture(autouse=True)
def _restore() -> object:
    reset()
    deps.set_sleeper(None)
    deps.set_clock(None)
    yield
    reset()
    deps.set_sleeper(None)
    deps.set_clock(None)


def _scripted(*outcomes: Any) -> Callable[[], Any]:
    """A call that serves the script in order, raising exception entries."""
    remaining = list(outcomes)

    def call() -> Any:
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return call


class _AdvancingClock:
    """Each read of ``now()`` steps the clock, so an attempt can look like a 300 s timeout."""

    def __init__(self, step_seconds: float) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=UTC)
        self._step = timedelta(seconds=step_seconds)

    def now(self) -> datetime:
        value = self._now
        self._now += self._step
        return value


def test_the_packaged_policy_is_five_retries_over_a_thirty_minute_budget() -> None:
    packaged = IntelligenceSettings()
    assert packaged.transport_retries == 5
    assert packaged.transport_wait_budget_seconds == 1800.0
    assert max_attempts() == 6, "the first call plus five retries"


def test_a_transport_failure_is_retried_with_exponential_backoff() -> None:
    delays: list[float] = []
    deps.set_sleeper(delays.append)
    call = _scripted(LlmTransportError("busy"), LlmTransportError("busy"), "answer")

    assert call_with_retries(call, budget=wait_budget()) == "answer"
    assert delays == [0.5, 1.0]


def test_the_retry_count_is_the_ceiling_when_failures_are_instant() -> None:
    delays: list[float] = []
    deps.set_sleeper(delays.append)
    call = _scripted(*[LlmTransportError("down")] * 10)

    with pytest.raises(LlmTransportError):
        call_with_retries(call, budget=wait_budget())
    assert delays == [0.5, 1.0, 2.0, 4.0, 8.0], "five retries, no sixth wait"


def test_a_refusal_is_not_retried() -> None:
    delays: list[float] = []
    deps.set_sleeper(delays.append)

    with pytest.raises(LlmError):
        call_with_retries(_scripted(LlmError("bad request", status_code=400)), budget=wait_budget())
    assert delays == []


def test_the_wait_budget_stops_the_retries_before_the_count_does() -> None:
    configure(transport_wait_budget_seconds=7.0)
    delays: list[float] = []
    deps.set_sleeper(delays.append)
    call = _scripted(*[LlmTransportError("down")] * 10)

    with pytest.raises(LlmTransportError):
        call_with_retries(call, budget=wait_budget())
    # 0.5 + 1.0 + 2.0 = 3.5 s slept; the next 4 s delay would not fit in the 7 s budget
    assert delays == [0.5, 1.0, 2.0]


def test_time_spent_in_a_timed_out_attempt_counts_against_the_budget() -> None:
    configure(transport_wait_budget_seconds=700.0)
    deps.set_clock(_AdvancingClock(step_seconds=300.0))
    delays: list[float] = []
    deps.set_sleeper(delays.append)
    call = _scripted(*[LlmTransportError("slow")] * 10)

    with pytest.raises(LlmTransportError):
        call_with_retries(call, budget=wait_budget())
    assert delays == [0.5, 1.0], "300 s + 300 s + 300 s already exceeds the 700 s budget"


def test_a_retry_after_hint_wins_but_must_fit_the_budget() -> None:
    delays: list[float] = []
    deps.set_sleeper(delays.append)
    call = _scripted(LlmTransportError("busy", retry_after_seconds=3.0), "answer")
    assert call_with_retries(call, budget=wait_budget()) == "answer"
    assert delays == [3.0]

    delays.clear()
    with pytest.raises(LlmTransportError):
        call_with_retries(
            _scripted(LlmTransportError("busy", retry_after_seconds=3600.0)),
            budget=wait_budget(),
        )
    assert delays == [], "a ten-minute hint is not honoured when it cannot fit"


def test_an_answer_resets_the_budget_for_the_next_one() -> None:
    configure(transport_wait_budget_seconds=4.0)
    delays: list[float] = []
    deps.set_sleeper(delays.append)
    budget = wait_budget()

    slow_but_answered = _scripted(
        LlmTransportError("busy"), LlmTransportError("busy"), LlmTransportError("busy"), "first"
    )
    assert call_with_retries(slow_but_answered, budget=budget) == "first"
    assert budget.remaining() == pytest.approx(4.0), "a response resets the spent time"

    # Without the reset the 4 s budget would already be 3.5 s spent and this call could
    # not afford its own retries.
    slow_again = _scripted(
        LlmTransportError("busy"), LlmTransportError("busy"), LlmTransportError("busy"), "second"
    )
    assert call_with_retries(slow_again, budget=budget) == "second"
    assert delays == [0.5, 1.0, 2.0, 0.5, 1.0, 2.0]


def test_a_wait_budget_never_reports_negative_time() -> None:
    budget = WaitBudget(1.0)
    budget.spend(5.0)
    assert budget.remaining() == 0.0
    budget.answered()
    assert budget.remaining() == 1.0
