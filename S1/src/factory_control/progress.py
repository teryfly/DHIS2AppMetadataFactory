"""S1's implementation of the S0 progress protocol (CP-EVT-002).

S3 agent runs and S4 long tasks report progress through the callback S1 hands
them; each report becomes an ``agent.progress`` / ``execution.progress`` event
record, which is how a browser sees a multi-minute run move without any
subsystem-to-subsystem messaging (00 doc 6.3).

Two extras beyond the protocol:

* reports are coalesced — a step is written when the step name changes, the
  fraction moves by at least ``min_fraction_delta``, or ``min_interval_seconds``
  have passed.  A chatty provider cannot flood the stream.
* an optional ``on_progress`` hook lets the Temporal activity wrapper send a
  heartbeat for the same report.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from factory_control.events import event_log
from factory_control.foundation import CancellationToken, ManualCancellationToken

SessionFactory = Callable[[], Session]


#: The token of the stage executing on this thread/task, if any.  Provider calls read it
#: through :func:`current_cancellation_token` instead of every service signature carrying a
#: parameter that means the same thing everywhere.
_ACTIVE: ContextVar[ManualCancellationToken | None] = ContextVar(
    "factory_control_active_cancellation_token", default=None
)

#: Live cancellation tokens, by workflow.
#:
#: CP-WF-011 / FR-WF-004: cancelling a workflow has to reach the long task that is running
#: right now -- an S4 discovery, a compile, a server-side validation -- and not only the S3
#: agent runs that ``_cancel_long_tasks`` used to stop.  S0's ``ManualCancellationToken`` is
#: built for exactly this (FD-PRG-002) and the reporter already carries one; nothing created
#: it, so every provider call went out with ``cancel=None`` and a cancelled workflow simply
#: kept spending its worker until the step finished on its own.
#:
#: Process-local by construction, like the S3 call beside it: with several workers, the
#: cancellation lands on the instance that owns the stage through the engine's own cancel
#: path, and this table serves the one that is running it here.
_TOKENS: dict[str, list[ManualCancellationToken]] = {}


def register_cancellation_token(workflow_id: str) -> ManualCancellationToken:
    """Create and track a token for one stage execution of ``workflow_id``."""
    token = ManualCancellationToken()
    # a list keyed by identity, not a set: ManualCancellationToken is a dataclass and
    # therefore unhashable
    _TOKENS.setdefault(workflow_id, []).append(token)
    return token


def activate_cancellation_token(token: ManualCancellationToken) -> object:
    """Make ``token`` the one provider calls should honour; returns a reset handle."""
    return _ACTIVE.set(token)


def deactivate_cancellation_token(handle: object) -> None:
    _ACTIVE.reset(handle)  # type: ignore[arg-type]


def current_cancellation_token() -> ManualCancellationToken | None:
    """The token of the running stage, or None when nothing owns this call."""
    return _ACTIVE.get()


def release_cancellation_token(workflow_id: str, token: ManualCancellationToken) -> None:
    live = _TOKENS.get(workflow_id)
    if live is None:
        return
    remaining = [item for item in live if item is not token]
    if remaining:
        _TOKENS[workflow_id] = remaining
    else:
        _TOKENS.pop(workflow_id, None)


def cancel_workflow_tokens(workflow_id: str, *, reason: str) -> int:
    """Flip every live token of one workflow; returns how many were signalled."""
    live = list(_TOKENS.get(workflow_id, ()))
    for token in live:
        token.cancel(reason)
    return len(live)


def live_token_count(workflow_id: str | None = None) -> int:
    """Tokens currently registered -- for the test that a finished stage leaves none."""
    if workflow_id is not None:
        return len(_TOKENS.get(workflow_id, ()))
    return sum(len(tokens) for tokens in _TOKENS.values())


@dataclass
class EventStreamProgressReporter:
    workflow_id: str
    session_factory: SessionFactory
    kind: str = "agent"  # "agent" | "execution"
    stage_id: str | None = None
    run_id: str | None = None
    execution_id: str | None = None
    retention_days: int = 7
    min_interval_seconds: float = 0.5
    min_fraction_delta: float = 0.01
    on_progress: Callable[[dict[str, Any]], None] | None = None
    cancel_token: CancellationToken = field(default_factory=ManualCancellationToken)
    _last_step: str | None = field(default=None, init=False, repr=False)
    _last_fraction: float | None = field(default=None, init=False, repr=False)
    _last_write: float = field(default=0.0, init=False, repr=False)
    reports: int = field(default=0, init=False, repr=False)

    def report(
        self,
        *,
        step: str,
        completed_fraction: float | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """S0 ``ProgressReporter`` protocol entry point."""
        self.reports += 1
        payload = {
            "step": step,
            "completedFraction": completed_fraction,
            "message": message,
            "stageId": self.stage_id,
            "agentRunId": self.run_id,
            "executionId": self.execution_id,
            "data": dict(data or {}),
        }
        if self.on_progress is not None:
            self.on_progress(payload)

        if not self._should_write(step, completed_fraction):
            return
        self._write(payload)

    def _should_write(self, step: str, fraction: float | None) -> bool:
        now = time.monotonic()
        if self._last_step != step:
            return True
        if fraction is not None and self._last_fraction is not None:
            if abs(fraction - self._last_fraction) >= self.min_fraction_delta:
                return True
        elif fraction is not None and self._last_fraction is None:
            return True
        return (now - self._last_write) >= self.min_interval_seconds

    def _write(self, payload: dict[str, Any]) -> None:
        from factory_control.foundation import unit_of_work

        event_type = f"{self.kind}.progress"
        # A long task runs outside any unit of work, so this opens a short one.
        with unit_of_work() as session:
            event_log.append(
                session,
                workflow_id=self.workflow_id,
                event_type=event_type,
                payload=payload,
                retention_days=self.retention_days,
            )
        self._last_step = str(payload.get("step"))
        self._last_fraction = payload.get("completedFraction")
        self._last_write = time.monotonic()

    def completed(self, step: str, *, message: str | None = None) -> None:
        """Final report at fraction 1.0, always written."""
        self._last_step = None
        self._last_write = 0.0
        self.report(step=step, completed_fraction=1.0, message=message)


def stage_cancel() -> ManualCancellationToken | None:
    """The cancellation token of the stage running here, if any.

    Every provider call S1 makes on behalf of a stage takes this instead of ``None`` so a
    workflow cancel actually reaches it (CP-WF-011, FR-WF-004).  COMMIT is the deliberate
    exception: an irreversible step already in flight must run to its end and be reconciled,
    never abandoned mid-request -- see ``_r4_commit``.
    """
    return current_cancellation_token()


__all__ = ["EventStreamProgressReporter", "stage_cancel"]
