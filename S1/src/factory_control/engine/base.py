"""Workflow engine abstraction (CP-RT-003, D-10).

Both engines expose the same interface, so the REST layer never knows which one is
running:

* ``inprocess`` — a thread-pool executor for development and evaluation.  It gives
  durability by relying on S1's own persisted state machine: a step is resumed
  from the last recorded status rather than from an event history.
* ``temporal`` — the production engine (CP-WF-007), where long-running and
  human-waiting flows live in a Temporal workflow and every provider call is an
  activity that retries only on ``retryable = true`` errors (CP-WF-009).

Choosing one is a configuration value (``FACTORY_WORKFLOW_ENGINE``), never a code
path in a service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from factory_control.errors import InvalidStateError


@dataclass(frozen=True)
class EngineStatus:
    name: str
    durable: bool
    detail: dict[str, Any]


@runtime_checkable
class WorkflowEngine(Protocol):
    """What S1 needs from an engine: start work, tell it to stop, ask how it is."""

    name: str

    def start_stage(
        self,
        stage_id: str,
        *,
        operator: str,
        rerun: bool = False,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Begin (or resume) the stage's execution and return its acceptance summary.

        ``language`` is the interface language of the person who clicked Run, carried to the
        agent so its prose matches the screen; an automated start leaves it unset and the
        project's own default language is used.

        The engine must run the full service entry point — validation, the RUNNING
        transition, the provider calls and the automatic approval submission — so
        that both engines behave identically (CP-RT-003).
        """
        ...

    def request_cancel(self, workflow_id: str) -> None:
        """Signal that long tasks of this workflow should stop at a safe point."""
        ...

    def status(self) -> EngineStatus: ...

    def shutdown(self) -> None: ...


def build_engine(kind: str, *, runtime: Any = None, settings: Any = None) -> WorkflowEngine:
    """Instantiate the configured engine."""
    if kind == "inprocess":
        from factory_control.engine.inprocess import InProcessEngine

        return InProcessEngine(runtime=runtime)
    if kind == "temporal":
        from factory_control.engine.temporal import TemporalEngine

        return TemporalEngine(settings=settings, runtime=runtime)
    raise InvalidStateError(
        f"unknown workflow engine {kind!r}; expected 'inprocess' or 'temporal'",
        details={"engine": kind},
    )


__all__ = ["EngineStatus", "WorkflowEngine", "build_engine"]
