"""Progress & cancellation protocols (FD-PRG).

Long tasks (S3 agents, S4 discovery/execution) are synchronous library calls run
inside S1's Temporal activities.  They report progress through a caller-supplied
:class:`ProgressReporter` (S1 implements it to write the event stream and send a
Temporal heartbeat) and observe cancellation via a shared
:class:`CancellationToken` at safe points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from factory_foundation.common.errors import CancelledError


@runtime_checkable
class ProgressReporter(Protocol):
    def report(
        self,
        *,
        step: str,
        completed_fraction: float | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Report the current step and optional completion fraction (0..1)."""
        ...


@runtime_checkable
class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...


@dataclass
class ManualCancellationToken:
    """Concrete token S1 flips when a workflow is cancelled (FD-PRG-002)."""

    _cancelled: bool = False
    reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self, reason: str | None = None) -> None:
        self._cancelled = True
        self.reason = reason

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise CancelledError("operation cancelled", details={"reason": self.reason})

    def check(self) -> None:
        """Alias for :meth:`raise_if_cancelled` for use at safe points."""
        self.raise_if_cancelled()


@dataclass
class ProgressEvent:
    step: str
    completed_fraction: float | None
    message: str | None
    data: dict[str, Any] = field(default_factory=dict)


class NullProgressReporter:
    """Default reporter that discards progress updates."""

    def report(
        self,
        *,
        step: str,
        completed_fraction: float | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        return None
