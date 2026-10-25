"""The per-request dependency container.

One :class:`Runtime` is built at process start; services receive a
:class:`ControlContext` that already carries an open unit of work, the ports,
the resolved settings and the acting operator.  Keeping that in one object means
a service function never reaches for process globals, which is what makes the
transaction boundaries (CP-WF-005, CP-GATE-014) visible in the code.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from factory_control.config import Settings, get_settings
from factory_control.events.bus import EventBus
from factory_control.foundation import Clock, SystemClock, new_correlation_id, unit_of_work
from factory_control.ports.protocols import BlueprintPort, ExecutionPort, IntelligencePort
from factory_control.settings import ResolvedSettings, load_settings_snapshot

if TYPE_CHECKING:
    from factory_control.engine.base import WorkflowEngine


@dataclass
class ControlContext:
    """Everything a service needs inside one unit of work."""

    session: Session
    blueprint: BlueprintPort
    intelligence: IntelligencePort
    execution: ExecutionPort
    settings: ResolvedSettings
    operator: str
    correlation_id: str
    clock: Clock
    project_id: str | None = None
    workflow_id: str | None = None
    stage_id: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    def bind(self, **changes: Any) -> ControlContext:
        """Return a copy with a few identifiers filled in (for events and audit)."""
        merged: dict[str, Any] = {
            "session": self.session,
            "blueprint": self.blueprint,
            "intelligence": self.intelligence,
            "execution": self.execution,
            "settings": self.settings,
            "operator": self.operator,
            "correlation_id": self.correlation_id,
            "clock": self.clock,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "stage_id": self.stage_id,
        }
        merged.update({k: v for k, v in changes.items() if k in merged})
        return ControlContext(**merged, notes=dict(self.notes))


@dataclass
class Runtime:
    """Process-wide wiring: engine, ports, event bus, session factory."""

    session_factory: Callable[[], Session]
    blueprint: BlueprintPort
    intelligence: IntelligencePort
    execution: ExecutionPort
    bus: EventBus
    settings: Settings
    clock: Clock = field(default_factory=SystemClock)
    engine: WorkflowEngine | None = None
    #: how a claimed parse is handed to a background thread.  ``None`` means the process-wide
    #: pool; tests inject ``lambda job: job()`` so a parse completes before the assertion.
    parse_submit: Callable[[Callable[[], None]], Any] | None = None

    @contextmanager
    def transaction(
        self,
        *,
        operator: str,
        project_id: str | None = None,
        workflow_id: str | None = None,
        stage_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Iterator[ControlContext]:
        """Open one unit of work and hand the service a bound context.

        Nested calls reuse the same transaction (S0 FD-DB-003), so a service can
        call another service without creating a second one.
        """
        with unit_of_work() as session:
            context = ControlContext(
                session=session,
                blueprint=self.blueprint,
                intelligence=self.intelligence,
                execution=self.execution,
                settings=load_settings_snapshot(session),
                operator=operator,
                correlation_id=correlation_id or new_correlation_id(),
                clock=self.clock,
                project_id=project_id,
                workflow_id=workflow_id,
                stage_id=stage_id,
            )
            yield context

    def read_only(self, *, operator: str = "system") -> AbstractContextManager[ControlContext]:
        """A context for queries; still one session, but nothing is committed."""
        return self.transaction(operator=operator)


def build_runtime(
    *,
    settings: Settings | None = None,
    blueprint: BlueprintPort | None = None,
    intelligence: IntelligencePort | None = None,
    execution: ExecutionPort | None = None,
    bus: EventBus | None = None,
    clock: Clock | None = None,
) -> Runtime:
    """Assemble the runtime, resolving ports to real providers or fakes."""
    from factory_control.adapters.registry import default_ports

    resolved = default_ports(
        settings=settings or get_settings(),
        blueprint=blueprint,
        intelligence=intelligence,
        execution=execution,
    )
    from factory_control.foundation import new_session

    return Runtime(
        session_factory=new_session,
        blueprint=resolved["blueprint"],
        intelligence=resolved["intelligence"],
        execution=resolved["execution"],
        bus=bus or EventBus(),
        settings=settings or get_settings(),
        clock=clock or SystemClock(),
    )


__all__ = ["ControlContext", "Runtime", "build_runtime"]
