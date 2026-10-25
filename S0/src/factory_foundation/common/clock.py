"""Clock interface (FD-COM-004).

Two implementations: :class:`SystemClock` and :class:`FixedClock`.  Code that
needs determinism (e.g. the Compiler path) must use the clock passed in by the
caller or the build-context time, never the system clock (00 doc 6.7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, Self, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time in UTC."""
        ...


class SystemClock:
    """Wall-clock reading, always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock for tests and reproducible builds.

    :func:`advance` lets a test move time forward explicitly.
    """

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        self._moment = moment.astimezone(UTC)

    def now(self) -> datetime:
        return self._moment

    def advance(self, seconds: float) -> Self:
        from datetime import timedelta

        self._moment = self._moment + timedelta(seconds=seconds)
        return self


def utcnow(clock: Clock | None = None) -> datetime:
    """Read time from the given clock, defaulting to the system clock."""
    return (clock or SystemClock()).now()
