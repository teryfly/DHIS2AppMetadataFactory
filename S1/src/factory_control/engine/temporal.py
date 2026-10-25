"""Temporal engine (CP-WF-007/008/009, D-10).

One Temporal workflow per stage execution.  The workflow itself is deterministic —
it only calls activities and waits for signals — so all database work happens in
activities, where S0's unit of work applies (00 doc 6.2, CP-WF-005).

Retry policy implements CP-WF-009 literally: an activity error is retried only
when the foundation catalog marks its code ``retryable = true``; anything else
fails the activity immediately and the service records FAILED or BLOCKED.

Human waits (Gates, Questions) are Temporal signals, so a restart does not lose
them (CP-GATE-011); the authoritative state stays in ``cp`` regardless.
"""

from __future__ import annotations

import asyncio
from typing import Any

from temporalio import workflow as _workflow
from temporalio.common import RetryPolicy

from factory_control.engine.base import EngineStatus
from factory_control.foundation import FactoryError, error_catalog, get_logger

_logger = get_logger("factory_control.engine.temporal")

WORKFLOW_QUEUE = "factory-main"
STAGE_WORKFLOW = "CpStageExecution"
SCHEDULE_WORKFLOW = "CpScheduledJobs"
CANCEL_SIGNAL = "cancel_requested"


class ActivityOutcome(dict[str, Any]):
    """Activities return plain JSON so the Temporal history stays serialisable."""


def _retry_policy(settings: Any) -> Any:
    from temporalio import common

    return common.RetryPolicy(
        initial_interval=_seconds(settings.activity_initial_interval_seconds),
        backoff_coefficient=settings.activity_backoff_coefficient,
        maximum_attempts=settings.activity_max_attempts,
    )


def _seconds(value: float) -> Any:
    from datetime import timedelta

    return timedelta(seconds=float(value))


def should_retry(error: BaseException) -> bool:
    """CP-WF-009: only foundation-marked retryable errors are retried."""
    if isinstance(error, FactoryError):
        return bool(error.retryable) or error_catalog.is_retryable(error.error_code)
    return False


async def run_stage_activity(
    stage_id: str,
    operator: str,
    idempotency_key: str,
    step_run_id: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Execute one already-claimed stage (CP-WF-028).

    The RUNNING transition happened when the REST call claimed the stage, so an
    activity retry re-executes the work instead of refusing to start a stage that is
    already RUNNING — which is what makes a retried activity continue rather than redo
    (CP-WF-008).
    """
    from factory_control.api_service import get_runtime
    from factory_control.services.steps import run_claimed

    runtime = get_runtime()
    claim = {"stageId": stage_id, "idempotencyKey": idempotency_key, "stepRunId": step_run_id}
    result = await asyncio.to_thread(
        run_claimed, runtime, claim, operator=operator, language=language
    )
    return {**dict(result), "workflowIdempotencyKey": idempotency_key}


async def run_commit_activity(run_id: str, operator: str, idempotency_key: str) -> dict[str, Any]:
    """COMMIT runs as its own activity so it can be abandoned, not cancelled."""
    from factory_control.api_service import get_runtime
    from factory_control.services.runs import commit_run

    runtime = get_runtime()
    return dict(
        await asyncio.to_thread(
            commit_run, runtime, run_id, operator=operator, idempotency_key=idempotency_key
        )
    )


def _timeout(seconds: float) -> Any:
    from datetime import timedelta

    return timedelta(seconds=seconds)


async def run_scheduled_job_activity(job: str) -> dict[str, Any]:
    """Run one scheduled job (CP-SET-002); the schedule decides when, S1 decides what."""
    from factory_control import scheduled
    from factory_control.api_service import get_runtime

    runtime = get_runtime()
    results = await asyncio.to_thread(lambda: scheduled.run_all_once(runtime, jobs=[job]))
    outcome = results.get(job, {"job": job, "error": "UNKNOWN_JOB"})
    return dict(outcome)


class StageWorkflowInput:
    """Plain payload passed to the workflow; kept importable outside Temporal."""

    def __init__(
        self,
        *,
        stage_id: str,
        operator: str,
        idempotency_key: str,
        step_run_id: str | None = None,
        language: str | None = None,
        retry_policy: Any = None,
        timeout_seconds: float = 900.0,
    ) -> None:
        self.stage_id = stage_id
        self.operator = operator
        self.idempotency_key = idempotency_key
        self.step_run_id = step_run_id
        self.language = language
        self.retry_policy = retry_policy
        self.timeout_seconds = timeout_seconds


class TemporalEngine:
    """Starts one workflow per stage execution against a Temporal namespace."""

    name = "temporal"

    def __init__(self, *, settings: Any, runtime: Any = None) -> None:
        self._settings = settings
        self._runtime = runtime
        self._client: Any = None

    async def _connect(self) -> Any:
        from temporalio.client import Client

        if self._client is None:
            self._client = await Client.connect(
                self._settings.temporal_target, namespace=self._settings.temporal_namespace
            )
        return self._client

    def start_stage(
        self,
        stage_id: str,
        *,
        operator: str,
        rerun: bool = False,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Claim the stage here, then hand its execution to Temporal.

        The claim (RUNNING, and the step-run row the 202 answer names) happens in this
        process, so the caller gets an id it can watch (S5IF-G06) and an illegal start
        answers 409 rather than failing on a worker.  Everything after that — provider
        calls, outcome recording — is the activity's, so a worker restart replays one
        path through the state machine (CP-WF-005, CP-WF-012).
        """
        from factory_control.services.steps import claim_stage

        claim = claim_stage(self._runtime, stage_id, operator=operator, rerun=rerun)
        handle = asyncio.run(
            self._start(
                stage_id=claim["stageId"],
                operator=operator,
                idempotency_key=str(claim["idempotencyKey"]),
                step_run_id=claim["stepRunId"],
                language=language,
            )
        )
        return {**claim, "engine": self.name, "workflowId": handle}

    async def _start(
        self,
        *,
        stage_id: str,
        operator: str,
        idempotency_key: str,
        step_run_id: str | None = None,
        language: str | None = None,
    ) -> str:
        client = await self._connect()
        workflow_id = f"stage-{stage_id}"
        await client.start_workflow(
            STAGE_WORKFLOW,
            StageWorkflowInput(
                stage_id=stage_id,
                operator=operator,
                idempotency_key=idempotency_key,
                step_run_id=step_run_id,
                language=language,
                retry_policy=_retry_policy(self._settings),
            ),
            id=workflow_id,
            task_queue=self._settings.temporal_task_queue,
        )
        return workflow_id

    def request_cancel(self, workflow_id: str) -> None:
        asyncio.run(self._cancel(workflow_id))

    async def _cancel(self, workflow_id: str) -> None:
        client = await self._connect()
        for handle in await _list_stage_handles(client, workflow_id):
            try:
                await handle.signal(CANCEL_SIGNAL)
                await handle.cancel()
            except Exception as exc:  # noqa: BLE001 - best effort; cp stays authoritative
                _logger.warning("could not cancel %s: %s", handle.id, exc)

    def status(self) -> EngineStatus:
        return EngineStatus(
            name=self.name,
            durable=True,
            detail={
                "target": self._settings.temporal_target,
                "namespace": self._settings.temporal_namespace,
                "taskQueue": self._settings.temporal_task_queue,
            },
        )

    def shutdown(self) -> None:
        self._client = None


async def _list_stage_handles(client: Any, workflow_id: str) -> list[Any]:
    query = f"WorkflowId = 'stage-{workflow_id}' OR CustomKeyword = '{workflow_id}'"
    try:
        return [h async for h in client.list_workflows(query=query)]
    except Exception:  # noqa: BLE001 - older servers reject custom queries
        return []


@_workflow.defn(name=STAGE_WORKFLOW)
class CpStageExecution:
    """One workflow per stage execution: it only calls activities and waits."""

    _cancelled: bool

    @_workflow.signal(name=CANCEL_SIGNAL)
    def cancel(self) -> None:
        """A human asked for this run to stop; the activity decides what is safe."""
        self._cancelled = True

    @_workflow.run
    async def run(self, input: StageWorkflowInput) -> dict[str, Any]:
        self._cancelled = False
        try:
            return await _workflow.execute_activity(
                run_stage_activity,
                args=[
                    input.stage_id,
                    input.operator,
                    input.idempotency_key,
                    input.step_run_id,
                    input.language,
                ],
                start_to_close_timeout=_timeout(input.timeout_seconds),
                retry_policy=input.retry_policy,
            )
        except asyncio.CancelledError:
            # A cancel arrives as CancelledError, which is not an Exception: the run
            # is over, but the activity may already have committed its step (CP-WF-011).
            if self._cancelled:
                return {"stageId": input.stage_id, "cancelled": True}
            raise
        except Exception as exc:  # noqa: BLE001 - the workflow must classify, not swallow
            raise _application_error(exc) from exc


@_workflow.defn(name=SCHEDULE_WORKFLOW)
class CpScheduledJobs:
    """The single workflow the server-side schedules trigger (CP-SET-002)."""

    @_workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await _workflow.execute_activity(
            run_scheduled_job_activity,
            args=[payload["job"]],
            start_to_close_timeout=_timeout(300.0),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


def _application_error(error: BaseException) -> BaseException:
    """Wrap a FactoryError so Temporal honours CP-WF-009's retry decision."""
    from temporalio.exceptions import ApplicationError

    if isinstance(error, FactoryError):
        return ApplicationError(
            error.message, type=error.error_code, non_retryable=not should_retry(error)
        )
    return ApplicationError(str(error), non_retryable=True)


def build_worker(client: Any, *, settings: Any) -> Any:
    from temporalio.worker import Worker

    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[CpStageExecution, CpScheduledJobs],
        activities=[run_stage_activity, run_commit_activity, run_scheduled_job_activity],
    )


__all__ = [
    "CANCEL_SIGNAL",
    "SCHEDULE_WORKFLOW",
    "STAGE_WORKFLOW",
    "CpScheduledJobs",
    "CpStageExecution",
    "StageWorkflowInput",
    "TemporalEngine",
    "build_worker",
    "run_commit_activity",
    "run_scheduled_job_activity",
    "run_stage_activity",
    "should_retry",
]
