"""Progress vocabulary (AI-RT-022, overall SRS FR-UI-010).

S1 forwards :class:`ProgressReporter` events straight into its SSE stream, so the step
names here are what a user sees as an agent run's state.  They are a fixed vocabulary
rather than free text for two reasons: a UI that labels each step needs stable keys, and a
test that asserts "the run reported before it called the model" needs to match on something
that cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from factory_foundation.api import CancellationToken, ProgressReporter

STEP_STARTED = "started"
STEP_CONTEXT = "context_built"
STEP_POLICY = "policy_evaluated"
STEP_MODEL_CALL = "model_call"
STEP_VALIDATE = "validating"
STEP_SUBMIT = "submitting_patch"
STEP_QUESTIONS = "raising_questions"
STEP_COMPLETED = "completed"
STEP_CANCELLED = "cancelled"

#: The order the runtime reports in, used by tests to assert no step was skipped.
STEP_ORDER: tuple[str, ...] = (
    STEP_STARTED,
    STEP_CONTEXT,
    STEP_POLICY,
    STEP_MODEL_CALL,
    STEP_VALIDATE,
    STEP_SUBMIT,
    STEP_QUESTIONS,
    STEP_COMPLETED,
)


@dataclass
class Progress:
    """A reporter plus a cancellation token, with the counts AI-RT-022 asks for."""

    reporter: ProgressReporter | None
    cancel: CancellationToken | None
    patches: int = 0
    questions: int = 0
    model_calls: int = 0
    #: called with ``(step, fraction, payload)`` after every report, so a run that is still
    #: executing can answer AI-RT-019's 最近进度 from its own row
    sink: Callable[[str, float, dict[str, Any]], None] | None = None

    def report(self, step: str, fraction: float | None = None, **data: Any) -> None:
        if self.reporter is None:
            return
        payload = {"patchCount": self.patches, "questionCount": self.questions, **data}
        if self.sink is not None:
            # persisted whether or not a caller is listening: the query reads it later, on
            # another worker than the one that ran the activity
            self.sink(step, fraction if fraction is not None else 0.0, payload)
        if self.reporter is None:
            return
        self.reporter.report(step=step, completed_fraction=fraction, message=step, data=payload)

    def safe_point(self) -> None:
        """Check the cancellation token (AI-RT-020).

        The database flag is checked by the engine at the same points, because S1 always
        passes ``cancel=None`` and the only cancellation that reaches an activity running on
        another worker is ``ai.agent_run.cancel_requested`` (AI-AC-08).  Splitting the two
        this way keeps this module free of a database dependency.
        """
        if self.cancel is not None:
            self.cancel.raise_if_cancelled()


__all__ = [
    "Progress",
    "STEP_STARTED",
    "STEP_CONTEXT",
    "STEP_POLICY",
    "STEP_MODEL_CALL",
    "STEP_VALIDATE",
    "STEP_SUBMIT",
    "STEP_QUESTIONS",
    "STEP_COMPLETED",
    "STEP_CANCELLED",
    "STEP_ORDER",
]
