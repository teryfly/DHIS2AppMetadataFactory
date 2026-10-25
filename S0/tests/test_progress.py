"""Progress & cancellation tests (FD-PRG)."""

from __future__ import annotations

import pytest

from factory_foundation.api import CancellationToken, ManualCancellationToken, ProgressReporter
from factory_foundation.common.errors import CancelledError
from factory_foundation.testing import RecordingProgressReporter, TriggerableCancellationToken


def _run_long_task(reporter: ProgressReporter, token: CancellationToken, steps: int) -> list[str]:
    done: list[str] = []
    for i in range(steps):
        token.raise_if_cancelled()  # safe point
        reporter.report(step=f"step-{i}", completed_fraction=(i + 1) / steps)
        done.append(f"step-{i}")
    return done


def test_recording_progress_reporter():
    rep = RecordingProgressReporter()
    done = _run_long_task(rep, ManualCancellationToken(), 3)
    assert done == ["step-0", "step-1", "step-2"]
    assert rep.steps == ["step-0", "step-1", "step-2"]
    assert rep.events[-1].completed_fraction == 1.0


def test_progress_data_and_message():
    rep = RecordingProgressReporter()
    rep.report(step="s", message="m", data={"k": 1})
    assert rep.events[0].data == {"k": 1}
    assert rep.events[0].message == "m"


def test_cancellation_token_raises_at_safe_point():
    token = TriggerableCancellationToken()
    token.cancel("user requested")
    rep = RecordingProgressReporter()
    with pytest.raises(CancelledError) as ei:
        _run_long_task(rep, token, 5)
    assert ei.value.error_code == "CANCELLED"
    assert rep.steps == []  # cancelled before first report


def test_cancellation_partway():
    # a token that becomes cancelled after the first safe point
    class Flips:
        def __init__(self):
            self.calls = 0

        @property
        def cancelled(self):
            return self.calls >= 2

        def raise_if_cancelled(self):
            self.calls += 1
            if self.calls >= 2:
                raise CancelledError("cancel")

    rep = RecordingProgressReporter()
    with pytest.raises(CancelledError):
        _run_long_task(rep, Flips(), 5)
    assert rep.steps == ["step-0"]  # one step completed before cancellation
