"""Temporal engine without a server (CP-WF-007 … CP-WF-011, D-10).

The engine's job is to hand work to Temporal with the right identity, timeouts and
retry classification, and to translate cancellation into signals.  All of that is
observable against a stubbed client, which keeps the durable-engine contract under
test even where no Temporal server is running.  ``test_live_server.py`` in this
directory adds the worker round-trip when a server is configured.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from factory_control.config import Settings
from factory_control.engine.temporal import (
    CANCEL_SIGNAL,
    SCHEDULE_WORKFLOW,
    STAGE_WORKFLOW,
    CpScheduledJobs,
    CpStageExecution,
    StageWorkflowInput,
    TemporalEngine,
    _application_error,
    _list_stage_handles,
    _retry_policy,
    _seconds,
    _timeout,
    build_worker,
    run_commit_activity,
    run_scheduled_job_activity,
    run_stage_activity,
    should_retry,
)
from factory_control.foundation import FactoryError
from factory_control.testing.harness import ControlHarness, seed_project

pytestmark = pytest.mark.temporal


class _StubClient:
    """Enough of ``temporalio.client.Client`` for the engine's calls."""

    def __init__(self, handles: list[Any] | None = None, *, list_error: bool = False) -> None:
        self.started: list[dict[str, Any]] = []
        self.handles = handles or []
        self.list_error = list_error

    async def start_workflow(self, workflow: Any, args: Any, *, id: str, task_queue: str) -> str:
        self.started.append({"workflow": workflow, "args": args, "id": id, "task_queue": task_queue})
        return id

    async def list_workflows(self, *, query: str = "") -> Any:
        if self.list_error:
            raise RuntimeError("query not supported")
        for handle in self.handles:
            yield handle

    async def get_workflow_handle(self, workflow_id: str) -> Any:
        return _StubHandle(workflow_id)


class _StubHandle:
    def __init__(self, workflow_id: str) -> None:
        self.id = workflow_id
        self.signals: list[str] = []
        self.cancelled = False
        self.fail = False

    async def signal(self, name: str) -> None:
        if self.fail:
            raise RuntimeError("workflow already closed")
        self.signals.append(name)

    async def cancel(self) -> None:
        self.cancelled = True


def _engine(settings: Settings | None = None, runtime: Any = None) -> TemporalEngine:
    engine = TemporalEngine(settings=settings or Settings(workflow_engine="temporal"), runtime=runtime)
    return engine


# --- retry classification (CP-WF-009) ---------------------------------------


def test_only_catalogued_retryable_errors_are_retried(harness: ControlHarness) -> None:
    """The decision comes from the registered catalog, not from a guess (CP-WF-009)."""
    _ = harness  # control_harness registers S1's error codes in core
    assert should_retry(FactoryError("x", code="LLM_TIMEOUT")) is True
    assert should_retry(FactoryError("x", code="INVALID_STATE")) is False
    assert should_retry(FactoryError("x", code="NEVER_REGISTERED")) is False
    assert should_retry(ValueError("not a factory error")) is False
    assert should_retry(asyncio.CancelledError()) is False


def test_application_error_carries_the_code_and_the_retry_decision() -> None:
    from temporalio.exceptions import ApplicationError

    retryable = FactoryError("provider hiccup", code="LLM_TIMEOUT")
    wrapped = _application_error(retryable)
    assert isinstance(wrapped, ApplicationError)
    assert wrapped.type == "LLM_TIMEOUT"
    assert wrapped.non_retryable is False

    final = _application_error(FactoryError("bad state", code="INVALID_STATE"))
    assert final.non_retryable is True
    assert _application_error(RuntimeError("boom")).non_retryable is True


def test_retry_policy_comes_from_the_settings() -> None:
    settings = Settings(
        activity_initial_interval_seconds=3.0,
        activity_backoff_coefficient=2.5,
        activity_max_attempts=7,
    )
    policy = _retry_policy(settings)
    assert policy.initial_interval == timedelta(seconds=3)
    assert policy.backoff_coefficient == 2.5
    assert policy.maximum_attempts == 7
    assert _seconds(1.5) == timedelta(seconds=1.5)
    assert _timeout(900) == timedelta(seconds=900)


def test_workflow_input_is_plain_json() -> None:
    payload = StageWorkflowInput(stage_id="sta-1", operator="ada", idempotency_key="k-1")
    assert (payload.stage_id, payload.operator, payload.idempotency_key) == ("sta-1", "ada", "k-1")
    assert payload.timeout_seconds == 900.0
    assert payload.retry_policy is None


# --- the engine's use of the client ----------------------------------------


def test_start_stage_launches_one_workflow_per_stage(harness: ControlHarness) -> None:
    seeded = seed_project(harness)
    engine = _engine(runtime=harness.runtime)
    client = _StubClient()
    engine._client = client  # noqa: SLF001 - the stub replaces the connection

    result = engine.start_stage(seeded["currentStageId"], operator="ada")

    assert result["accepted"] is True
    assert result["engine"] == "temporal"
    assert result["workflowId"] == f"stage-{seeded['currentStageId']}"
    assert len(client.started) == 1
    call = client.started[0]
    assert call["workflow"] == STAGE_WORKFLOW
    assert call["task_queue"] == "factory-main"
    payload = call["args"]
    assert isinstance(payload, StageWorkflowInput)
    assert payload.stage_id == seeded["currentStageId"]
    assert payload.operator == "ada"
    assert payload.idempotency_key.startswith(f"cp:{seeded['currentStageId']}:")
    assert payload.retry_policy.maximum_attempts == 5


def test_a_claim_is_taken_once_and_carries_its_key_to_the_workflow(harness: ControlHarness) -> None:
    """CP-WF-008 / S5IF-G06: the key belongs to the attempt the claim opened.

    The activity no longer derives the key: the claim recorded it, and the workflow
    carries that value.  A second ``start_stage`` on a stage already RUNNING is refused
    rather than queued behind the first — the durable engine retries the *activity*,
    which re-reads the same attempt, and a user who means a new attempt says rerun.
    """
    from factory_control import store as S
    from factory_control.domain.enums import StageStatus
    from factory_control.errors import InvalidStateError
    from factory_control.foundation import unit_of_work

    seeded = seed_project(harness)
    engine = _engine(runtime=harness.runtime)
    client = _StubClient()
    engine._client = client  # noqa: SLF001
    claim = engine.start_stage(seeded["currentStageId"], operator="ada")
    key = str(claim["idempotencyKey"])

    assert key.startswith(f"cp:{seeded['currentStageId']}:")
    assert client.started[0]["args"].idempotency_key == key

    engine._client = _StubClient()  # noqa: SLF001
    with pytest.raises(InvalidStateError):
        engine.start_stage(seeded["currentStageId"], operator="ada")

    with unit_of_work() as session:
        stage = S.get_stage(session, seeded["currentStageId"])
        stage.status = StageStatus.PASSED.value
        stage.attempt = 1
        session.flush()

    rerun = engine.start_stage(seeded["currentStageId"], operator="ada", rerun=True)
    assert rerun["idempotencyKey"] != key  # a new attempt earns a new key


def test_cancel_signals_then_cancels_every_stage_handle(harness: ControlHarness) -> None:
    seeded = seed_project(harness)
    engine = _engine(runtime=harness.runtime)
    handle = _StubHandle(f"stage-{seeded['currentStageId']}")
    client = _StubClient(handles=[handle])
    engine._client = client  # noqa: SLF001

    engine.request_cancel(seeded["currentStageId"])
    assert handle.signals == [CANCEL_SIGNAL]
    assert handle.cancelled is True


def test_cancel_of_a_closed_workflow_does_not_break_the_call(harness: ControlHarness) -> None:
    seeded = seed_project(harness)
    engine = _engine(runtime=harness.runtime)
    broken = _StubHandle("stage-broken")
    broken.fail = True
    engine._client = _StubClient(handles=[broken])  # noqa: SLF001
    engine.request_cancel(seeded["currentStageId"])  # must not raise
    assert broken.cancelled is False


def test_listing_handles_degrades_to_nothing_on_an_old_server() -> None:
    async def scenario() -> list[Any]:
        return await _list_stage_handles(_StubClient(list_error=True), "sta-1")

    assert asyncio.run(scenario()) == []


def test_status_and_shutdown_describe_the_durable_engine() -> None:
    settings = Settings(
        workflow_engine="temporal",
        temporal_target="temporal:7233",
        temporal_namespace="factory",
        temporal_task_queue="factory-main",
    )
    engine = _engine(settings)
    status = engine.status()
    assert (status.name, status.durable) == ("temporal", True)
    assert status.detail == {
        "target": "temporal:7233",
        "namespace": "factory",
        "taskQueue": "factory-main",
    }
    engine._client = _StubClient()  # noqa: SLF001
    engine.shutdown()
    assert engine._client is None  # noqa: SLF001


def test_connect_reuses_one_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import temporalio.client as tc

    created: list[str] = []

    class _Factory:
        @staticmethod
        async def connect(target: str, *, namespace: str) -> str:
            created.append(f"{target}/{namespace}")
            return f"client-{len(created)}"

    monkeypatch.setattr(tc, "Client", _Factory)
    engine = _engine(Settings(temporal_target="host:7233", temporal_namespace="ns"))

    async def scenario() -> tuple[Any, Any]:
        first = await engine._connect()  # noqa: SLF001
        second = await engine._connect()  # noqa: SLF001
        return first, second

    first, second = asyncio.run(scenario())
    assert first == second == "client-1"
    assert created == ["host:7233/ns"]


# --- activities and the worker definition -----------------------------------


def test_activity_runs_the_same_service_entry_point(harness: ControlHarness) -> None:
    """CP-WF-005: the activity delegates to the service, it never writes ``cp`` itself.

    The stage is claimed first, because that is what the REST call now does before the
    workflow is started (S5IF-G06) — the activity executes an already-RUNNING stage.
    """
    from factory_control import api_service
    from factory_control import store as S
    from factory_control.foundation import unit_of_work
    from factory_control.services.steps import claim_stage

    seeded = seed_project(harness)
    api_service.configure(runtime=harness.runtime)
    claim = claim_stage(harness.runtime, seeded["currentStageId"], operator="ada")
    try:
        outcome = asyncio.run(
            run_stage_activity(
                claim["stageId"], "ada", str(claim["idempotencyKey"]), claim["stepRunId"]
            )
        )
    finally:
        api_service.reset()

    assert outcome["workflowIdempotencyKey"] == claim["idempotencyKey"]
    with unit_of_work() as session:
        stage = S.get_stage(session, seeded["currentStageId"])
        assert stage.status in ("WAITING_APPROVAL", "READY", "PASSED")
        assert stage.attempt == 1


def test_commit_activity_forwards_the_idempotency_key(harness: ControlHarness) -> None:
    """CP-WF-008/018: COMMIT must reach the service with the caller's key."""
    import factory_control.services.runs as runs
    from factory_control import api_service

    seed_project(harness)
    api_service.configure(runtime=harness.runtime)
    seen: dict[str, Any] = {}

    async def _call() -> dict[str, Any]:
        return await run_commit_activity("run-1", "ada", "commit-key-1")

    def recorder(runtime: Any, run_id: str, **kwargs: Any) -> dict[str, Any]:
        seen.update({"runId": run_id, "runtime": runtime, **kwargs})
        return {"deploymentRunId": run_id, "status": "COMMITTED"}

    original = runs.commit_run
    try:
        runs.commit_run = recorder  # type: ignore[assignment]
        assert asyncio.run(_call())["status"] == "COMMITTED"
    finally:
        runs.commit_run = original  # type: ignore[assignment]
        api_service.reset()
    assert seen["runId"] == "run-1"
    assert seen["idempotency_key"] == "commit-key-1"
    assert seen["operator"] == "ada"


def test_the_recorded_step_run_holds_the_key_the_activity_will_replay(harness: ControlHarness) -> None:
    """CP-WF-008/013: a retry re-reads the attempt instead of re-deriving a key.

    The key the workflow carries and the key stored on the step-run row are the same
    value, which is what lets an activity retry continue the attempt the claim opened
    rather than silently starting a new one.
    """
    from factory_control.foundation import unit_of_work
    from factory_control.models import StageRun

    seeded = seed_project(harness)
    engine = _engine(runtime=harness.runtime)
    engine._client = _StubClient()  # noqa: SLF001
    claim = engine.start_stage(seeded["currentStageId"], operator="ada")

    with unit_of_work() as session:
        row = session.get(StageRun, claim["stepRunId"])
        assert row is not None, claim
        assert row.idempotency_key == claim["idempotencyKey"]
        assert row.stage_id == seeded["currentStageId"]


def test_worker_definition_is_registrable() -> None:
    """Temporal rejects local classes, so both workflows live at module scope."""
    stage = CpStageExecution.__temporal_workflow_definition
    scheduled = CpScheduledJobs.__temporal_workflow_definition
    assert stage.name == STAGE_WORKFLOW
    assert scheduled.name == SCHEDULE_WORKFLOW
    assert CANCEL_SIGNAL in stage.signals


def test_worker_registers_both_workflows_and_three_activities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import temporalio.worker as tw

    captured: dict[str, Any] = {}

    class _RecordingWorker:
        def __init__(self, client: Any, **kwargs: Any) -> None:
            captured["client"] = client
            captured.update(kwargs)

    monkeypatch.setattr(tw, "Worker", _RecordingWorker)
    build_worker("client", settings=Settings(workflow_engine="temporal"))
    assert captured["task_queue"] == "factory-main"
    assert captured["workflows"] == [CpStageExecution, CpScheduledJobs]
    assert captured["activities"] == [
        run_stage_activity,
        run_commit_activity,
        run_scheduled_job_activity,
    ]


def test_scheduled_job_activity_runs_exactly_one_job(harness: ControlHarness) -> None:
    from factory_control import api_service

    api_service.configure(runtime=harness.runtime)
    try:
        outcome = asyncio.run(run_scheduled_job_activity("link_audit"))
        missing = asyncio.run(run_scheduled_job_activity("no_such_job"))
    finally:
        api_service.reset()
    assert outcome["job"] == "link_audit"
    assert missing == {"job": "no_such_job", "error": "UNKNOWN_JOB"}


def test_scheduled_workflow_run_calls_the_job_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The schedule action workflow is a thin wrapper around one activity."""
    import temporalio.workflow as tw

    seen: dict[str, Any] = {}

    async def fake_execute_activity(activity: Any, **kwargs: Any) -> dict[str, Any]:
        seen["activity"] = activity
        seen.update(kwargs)
        return {"job": kwargs["args"][0], "ran": True}

    monkeypatch.setattr(tw, "execute_activity", fake_execute_activity)
    outcome = asyncio.run(CpScheduledJobs().run({"job": "purge_events"}))
    assert outcome == {"job": "purge_events", "ran": True}
    assert seen["activity"] is run_scheduled_job_activity


def test_stage_workflow_run_returns_the_activity_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    import temporalio.workflow as tw

    async def fake_execute_activity(activity: Any, **kwargs: Any) -> dict[str, Any]:
        return {"stageId": kwargs["args"][0], "activity": activity.__name__}

    monkeypatch.setattr(tw, "execute_activity", fake_execute_activity)
    payload = StageWorkflowInput(
        stage_id="sta-1", operator="ada", idempotency_key="k", step_run_id="srn-1"
    )
    outcome = asyncio.run(CpStageExecution().run(payload))
    assert outcome["activity"] == "run_stage_activity"
    assert outcome["stageId"] == "sta-1"


def test_stage_workflow_wraps_a_service_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import temporalio.workflow as tw

    async def boom(activity: Any, **kwargs: Any) -> dict[str, Any]:
        raise FactoryError("provider down", code="LLM_TIMEOUT")

    monkeypatch.setattr(tw, "execute_activity", boom)
    workflow = CpStageExecution()
    payload = StageWorkflowInput(stage_id="sta-1", operator="ada", idempotency_key="k")
    with pytest.raises(Exception, match="provider down") as raised:
        asyncio.run(workflow.run(payload))
    assert raised.value.type == "LLM_TIMEOUT"  # type: ignore[attr-defined]
    assert raised.value.non_retryable is False  # type: ignore[attr-defined]


def test_stage_workflow_reports_cancellation_instead_of_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import temporalio.workflow as tw

    async def cancel_during(activity: Any, **kwargs: Any) -> dict[str, Any]:
        workflow.cancel()
        raise asyncio.CancelledError()

    workflow = CpStageExecution()
    monkeypatch.setattr(tw, "execute_activity", cancel_during)
    payload = StageWorkflowInput(stage_id="sta-1", operator="ada", idempotency_key="k")
    assert asyncio.run(workflow.run(payload)) == {"stageId": "sta-1", "cancelled": True}
