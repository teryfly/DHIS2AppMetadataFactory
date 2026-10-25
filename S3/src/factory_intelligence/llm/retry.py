"""Transport retry policy (AI-RT-021): exponential backoff under a cumulative wait budget.

A timeout is auto-retryable, but a slow reasoning model makes every attempt expensive, so
the policy has two ceilings that work together: ``transport_retries`` bounds the number of
calls, and ``transport_wait_budget_seconds`` bounds the wall time spent waiting across them.
The second one is what keeps a queue of timed-out attempts from becoming a hung worker.

The wait budget is cumulative *per answer*, not per process or per input file: a successful
response resets it, so one unusually slow chunk cannot spend the time a later chunk needs.
A ``Retry-After`` hint still wins over the exponential curve, but a hint that would not fit
in the remaining budget ends the retries instead of sleeping through the ceiling.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from factory_intelligence.config import settings
from factory_intelligence.deps import get_clock, get_sleeper
from factory_intelligence.llm.errors import LlmTransportError

T = TypeVar("T")


class WaitBudget:
    """Time one answer may spend waiting for the provider, across retries."""

    def __init__(self, limit_seconds: float) -> None:
        self.limit_seconds = max(0.0, float(limit_seconds))
        self.spent_seconds = 0.0

    def spend(self, seconds: float) -> None:
        self.spent_seconds += max(0.0, float(seconds))

    def remaining(self) -> float:
        return max(0.0, self.limit_seconds - self.spent_seconds)

    def answered(self) -> None:
        """A response arrived: the next wait starts from a clean budget."""
        self.spent_seconds = 0.0


def wait_budget() -> WaitBudget:
    """A budget for one answer (one logical model call, or one document extraction)."""
    return WaitBudget(settings().transport_wait_budget_seconds)


def max_attempts() -> int:
    """Calls allowed for one answer: the first one plus ``transport_retries``."""
    return max(1, int(settings().transport_retries) + 1)


def backoff_seconds(attempt: int, error: LlmTransportError) -> float:
    """Exponential transport backoff, honouring a server's own ``Retry-After`` (AI-RT-021).

    ``Retry-After`` wins because a provider that says "three seconds" knows something the
    ceiling here does not; the exponential rule is the fallback for the failures that come
    back with no advice at all.
    """
    if error.retry_after_seconds is not None:
        return float(error.retry_after_seconds)
    configured = settings()
    base = float(configured.transport_backoff_base_seconds)
    ceiling = float(configured.transport_backoff_max_seconds)
    return min(base * float(2**attempt), ceiling)


def call_with_retries(
    call: Callable[[], T],
    *,
    budget: WaitBudget,
    before_attempt: Callable[[int], None] | None = None,
    on_retry: Callable[[int, float, LlmTransportError], None] | None = None,
) -> T:
    """Run ``call`` until it answers, a non-retryable failure raises, or a ceiling is hit.

    ``LlmError`` (a refusal, a bad request) is not retried: it propagates untouched.  So does
    the final ``LlmTransportError``, as-is, so the caller's audit row keeps the vendor's own
    message.  ``before_attempt`` runs before every call (cancellation, progress);
    ``on_retry`` receives the number of the upcoming attempt (1-based), the delay and the
    failure.
    """
    attempts = max_attempts()
    for attempt in range(attempts):
        if before_attempt is not None:
            before_attempt(attempt)
        started = get_clock().now()
        try:
            response = call()
        except LlmTransportError as exc:
            budget.spend((get_clock().now() - started).total_seconds())
            if attempt + 1 >= attempts or budget.remaining() <= 0:
                raise
            delay = backoff_seconds(attempt, exc)
            if delay > budget.remaining():
                raise
            if on_retry is not None:
                on_retry(attempt + 2, delay, exc)
            get_sleeper()(delay)
            budget.spend(delay)
            continue
        budget.answered()
        return response
    raise AssertionError("unreachable: the last attempt returns or raises")  # pragma: no cover


__all__ = [
    "WaitBudget",
    "backoff_seconds",
    "call_with_retries",
    "max_attempts",
    "wait_budget",
]
