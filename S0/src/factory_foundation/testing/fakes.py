"""Test doubles and recording helpers (FD-TST-001).

A recording :class:`ProgressReporter`, a manually triggerable
:class:`CancellationToken`, and thin re-exports of the in-memory object store /
fixed clock / memory secret backend so subsystems can build fakes without
pulling real dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_foundation.objectstore.memory import InMemoryObjectStore
from factory_foundation.progress.protocols import (
    CancellationToken,
    ManualCancellationToken,
    ProgressEvent,
)
from factory_foundation.secrets.memory import MemorySecretBackend

__all__ = [
    "InMemoryObjectStore",
    "MemorySecretBackend",
    "ManualCancellationToken",
    "RecordingProgressReporter",
    "TriggerableCancellationToken",
    "ProgressEvent",
    "CancellationToken",
]


@dataclass
class RecordingProgressReporter:
    """Captures every :meth:`report` call for assertions (FD-PRG-001)."""

    events: list[ProgressEvent] = field(default_factory=list)

    def report(
        self,
        *,
        step: str,
        completed_fraction: float | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            ProgressEvent(
                step=step,
                completed_fraction=completed_fraction,
                message=message,
                data=dict(data or {}),
            )
        )

    @property
    def steps(self) -> list[str]:
        return [e.step for e in self.events]


def TriggerableCancellationToken() -> ManualCancellationToken:  # noqa: N802 - factory alias
    """A token a test flips via ``.cancel()`` (FD-PRG-002)."""
    return ManualCancellationToken()
