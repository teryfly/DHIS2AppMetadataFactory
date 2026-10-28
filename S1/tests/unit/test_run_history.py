"""Per-attempt run history: every attempt keeps its own persisted log lines.

The route used to return only `stageRuns[]` without timestamps or progress, so the console could
only show the newest attempt.  Progress rows written before run ids were attached still belong to
an attempt by time, which is what this test pins.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from factory_control.rest.routes.workflow import _stage_run_history


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _statement):
        return _Scalars(self._rows)


def _run(run_id: str, attempt: int, created: datetime, result: dict | None = None):
    return SimpleNamespace(
        id=run_id,
        attempt=attempt,
        agent_run_ids=[f"arun-{attempt}"],
        execution_ids=[],
        build_ids=[],
        result=result,
        created_at=created,
        updated_at=created,
        completed_at=created + timedelta(minutes=1),
    )


def _event(seq: int, event_type: str, at: datetime, payload: dict):
    return SimpleNamespace(sequence=seq, event_type=event_type, occurred_at=at, payload=payload)


def test_each_attempt_keeps_its_own_progress_and_result() -> None:
    start = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)
    second = start + timedelta(minutes=10)
    runs = [
        _run("srn-1", 1, start, {"status": "WAITING_INPUT", "summary": "first"}),
        _run("srn-2", 2, second, {"status": "FAILED", "summary": "second"}),
    ]
    events = [
        _event(1, "agent.started", start + timedelta(seconds=5), {"runId": "arun-1"}),
        _event(2, "agent.progress", start + timedelta(seconds=10), {"stageId": "ST-1", "step": "model_call", "completedFraction": 0.3, "data": {"patchCount": 0, "questionCount": 0}}),
        _event(3, "agent.completed", start + timedelta(seconds=20), {"runId": "arun-1", "status": "WAITING_INPUT"}),
        _event(4, "stage.started", second - timedelta(seconds=1), {"stageId": "ST-1", "to": "RUNNING"}),
        _event(5, "agent.progress", second + timedelta(seconds=10), {"stageId": "ST-1", "step": "validating", "completedFraction": 0.55, "data": {"patchCount": 0, "questionCount": 0}}),
        _event(6, "stage.failed", second + timedelta(seconds=20), {"stageId": "ST-1", "to": "FAILED"}),
        _event(7, "agent.progress", second + timedelta(seconds=30), {"stageId": "OTHER", "step": "noise"}),
    ]

    history = _stage_run_history(_Session(events), "WF-1", "ST-1", runs)

    assert [entry["attempt"] for entry in history] == [1, 2]
    first_log = history[0]["log"]
    second_log = history[1]["log"]
    assert [entry["eventType"] for entry in first_log] == ["agent.started", "agent.progress", "agent.completed"]
    assert [entry["eventType"] for entry in second_log] == ["stage.started", "agent.progress", "stage.failed"]
    assert first_log[1]["fraction"] == 0.3 and first_log[1]["step"] == "model_call"
    assert second_log[1]["fraction"] == 0.55 and second_log[1]["step"] == "validating"
    assert history[0]["result"]["summary"] == "first"
    assert history[1]["result"]["summary"] == "second"
    assert all(entry["eventType"] != "agent.progress" or entry["step"] != "noise" for entry in first_log + second_log)
