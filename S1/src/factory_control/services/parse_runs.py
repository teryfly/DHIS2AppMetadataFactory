"""Background business-input parsing (CP-API-012, CP-NFR-05).

A parse can take minutes — one model call alone is allowed 300 s and the transport policy
may retry it — so the REST call claims the work and answers 202 while a pool thread runs
S3.  ``cp.input_parse_runs`` is the durable state that makes the claim visible across a
reload; :func:`start_parse` writes it in the same transaction as the ``PARSING`` snapshot on
the stage run and the ``input.parse`` event.

The pool is deliberately in-process, like :class:`~factory_control.engine.inprocess.
InProcessEngine`: this deployment has one API process, and state is authoritative in ``cp``.
A restart cannot resume a thread, so :func:`reconcile_interrupted` closes leftover
``PARSING`` rows as ``PARSE_INTERRUPTED`` during start-up instead of leaving an input that
looks busy forever.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from factory_control import store as S
from factory_control.errors import (
    INTERNAL_ERROR,
    PARSE_INTERRUPTED,
    NotFoundControlError,
    RequestInProgressError,
)
from factory_control.events import event_log
from factory_control.foundation import CancelledError, get_artifact, new_id, unit_of_work
from factory_control.models import InputParseRun, Stage
from factory_control.progress import EventStreamProgressReporter
from factory_control.runtime import Runtime
from factory_control.services.recording import record_change

#: A parse occupies a thread while it waits on a model; two is enough for an operator's
#: simultaneous uploads and keeps a queue of slow parses from starving the API's own work.
_POOL_WORKERS = 2
_pool_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None


def _pool_instance() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=_POOL_WORKERS, thread_name_prefix="cp-parse")
        return _pool


def submit_parse(runtime: Runtime, run_id: str) -> None:
    """Hand the run to the configured submitter (tests inject an inline one)."""
    job = lambda: execute_parse(runtime, run_id)  # noqa: E731 - one deferred call
    if runtime.parse_submit is not None:
        runtime.parse_submit(job)
    else:
        _pool_instance().submit(job)


def shutdown_parse_pool() -> None:
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=False)


def _workspace(ctx: Any, input_artifact_id: str, stage_id: str | None) -> tuple[Stage, str]:
    from factory_control.services.inputs import _requirements_stage

    stage = S.get_stage(ctx.session, stage_id) if stage_id else _requirements_stage(ctx, input_artifact_id)
    workflow = S.get_workflow(ctx.session, stage.workflow_id)
    return stage, str(workflow.id)


def _active_run(session: Session, input_artifact_id: str) -> InputParseRun | None:
    return session.execute(
        select(InputParseRun).where(
            InputParseRun.input_artifact_id == input_artifact_id,
            InputParseRun.status == "PARSING",
        )
    ).scalars().first()


def start_parse(
    runtime: Runtime,
    project_id: str | None,
    input_artifact_id: str,
    *,
    operator: str,
    stage_id: str | None = None,
    options: dict[str, Any] | None = None,
    request_key: str = "",
) -> dict[str, Any]:
    """Claim a parse, record it as ``PARSING``, and submit it; answers with the run id.

    The artifact's project is checked here rather than trusted from the URL: a parse
    writes NRR entries into the project's history, so an artifact from another project is
    a 404 even when the caller may parse in this one.
    """
    artifact = get_artifact(input_artifact_id)
    artifact_project = str(getattr(artifact, "project_id", "") or "")
    if (
        artifact is None
        or getattr(artifact, "type", None) != "BUSINESS_INPUT"
        or (project_id is not None and artifact_project != project_id)
    ):
        raise NotFoundControlError(
            f"{input_artifact_id} is not a business input of {project_id or 'its project'}",
            details={"artifactId": input_artifact_id, "projectId": project_id},
        )
    project_id = project_id or artifact_project
    if not project_id:  # pragma: no cover - every registered artifact carries its project
        raise NotFoundControlError(
            f"{input_artifact_id} has no project",
            details={"artifactId": input_artifact_id},
        )

    key = f"parse:{input_artifact_id}" + (f":{request_key[:24]}" if request_key else "")
    run_id = new_id("ipr")
    try:
        with runtime.transaction(operator=operator, project_id=project_id) as ctx:
            stage, workflow_id = _workspace(ctx, input_artifact_id, stage_id)
            existing = _active_run(ctx.session, input_artifact_id)
            if existing is not None:
                raise RequestInProgressError(
                    "this input is already being parsed",
                    details={"parseRunId": existing.id, "artifactId": input_artifact_id},
                )
            row = InputParseRun(
                id=run_id,
                project_id=project_id,
                workflow_id=workflow_id,
                stage_id=stage.id,
                input_artifact_id=input_artifact_id,
                status="PARSING",
                idempotency_key=key,
                options=dict(options or {}),
                operator=operator,
            )
            ctx.session.add(row)
            _snapshot(
                ctx,
                stage=stage,
                input_artifact_id=input_artifact_id,
                run_id=run_id,
                key=key,
                status="PARSING",
            )
            record_change(
                ctx.bind(workflow_id=workflow_id, stage_id=stage.id),
                event_type="input.parse",
                audit_event_type="cp.input.parse_started",
                object_type="Artifact",
                object_id=input_artifact_id,
                payload={
                    "inputArtifactId": input_artifact_id,
                    "parseRunId": run_id,
                    "stageId": stage.id,
                    "status": "PARSING",
                },
            )
    except IntegrityError as exc:
        # The partial unique index lost the race the check above could not see.
        raise RequestInProgressError(
            "this input is already being parsed",
            details={"artifactId": input_artifact_id},
        ) from exc

    submit_parse(runtime, run_id)
    return {"runId": run_id, "stageId": stage.id, "workflowId": workflow_id}


def execute_parse(runtime: Runtime, run_id: str) -> None:
    """The pool's work: call S3 outside any transaction, then record the outcome."""
    with runtime.transaction(operator="system") as ctx:
        row = ctx.session.get(InputParseRun, run_id)
        if row is None or row.status != "PARSING":
            return
        project_id = row.project_id
        workflow_id = row.workflow_id
        stage_id = row.stage_id
        artifact_id = row.input_artifact_id
        operator = row.operator
        key = row.idempotency_key
        options = dict(row.options or {})
        row.started_at = ctx.clock.now()

    try:
        parsed = runtime.intelligence.parse_input(
            project_id,
            artifact_id,
            options=options,
            operator=operator,
            idempotency_key=key,
            progress=EventStreamProgressReporter(
                workflow_id=workflow_id,
                session_factory=runtime.session_factory,
                kind="agent",
                stage_id=stage_id,
            ),
            # No cancellation token is bound on a pool thread; a parse ends by finishing,
            # failing or being reconciled after a restart.
            cancel=None,
        )
    except CancelledError as exc:
        _finish_failed(runtime, run_id, code="CANCELLED", message=_message(exc))
        return
    except Exception as exc:  # noqa: BLE001 - recorded on the run, never lost in a thread
        _finish_failed(
            runtime,
            run_id,
            code=str(getattr(exc, "error_code", None) or INTERNAL_ERROR),
            message=_message(exc),
        )
        return
    _finish_ok(runtime, run_id, parsed)


def _finish_ok(runtime: Runtime, run_id: str, parsed: Any) -> None:
    from factory_control.services.inputs import _record_parse_outcome
    from factory_control.services.questions import record_questions

    with runtime.transaction(operator="system") as ctx:
        row = ctx.session.get(InputParseRun, run_id)
        if row is None:
            return
        if row.status != "PARSING":  # reconciled while the thread was finishing
            return
        stage = ctx.session.get(Stage, row.stage_id)
        if stage is None:
            _mark_failed_row(ctx, row, code=INTERNAL_ERROR, message="the stage no longer exists")
            return
        operator = row.operator
        ctx = ctx.bind(
            operator=operator,
            project_id=row.project_id,
            workflow_id=row.workflow_id,
            stage_id=row.stage_id,
        )
        ids = list(stage.output_artifact_ids or [])
        if parsed.nrr_artifact_id:
            ids.append(parsed.nrr_artifact_id)
        stage.output_artifact_ids = sorted(set(ids))
        created = record_questions(
            ctx,
            stage=stage,
            questions=list(parsed.questions),
            source_kind="AGENT",
            agent_run_id=None,
        )
        outcome = {
            "status": parsed.status,
            "nrrArtifactId": parsed.nrr_artifact_id,
            "parserVersion": parsed.parser_version,
            "questions": [q.id for q in created],
            "rejectedArtifacts": list(parsed.rejected_artifacts),
            "stageId": stage.id,
            "parseRunId": run_id,
        }
        outcome["stepRunId"] = _record_parse_outcome(
            ctx,
            stage=stage,
            input_artifact_id=row.input_artifact_id,
            parsed=parsed,
            outcome=outcome,
            key=row.idempotency_key,
            parse_run_id=run_id,
        )
        row.status = str(parsed.status)
        row.parse_id = parsed.parse_id
        row.parser_type = parsed.parser_type
        row.parser_version = parsed.parser_version
        row.used_model = bool(parsed.used_model)
        row.nrr_artifact_id = parsed.nrr_artifact_id
        row.error_code = parsed.error_code
        row.error_message = parsed.error_message
        row.step_run_id = outcome["stepRunId"]
        row.finished_at = ctx.clock.now()
        record_change(
            ctx,
            event_type="artifact.created",
            audit_event_type="cp.input.parsed",
            object_type="Artifact",
            object_id=row.input_artifact_id,
            payload={
                "inputArtifactId": row.input_artifact_id,
                "status": parsed.status,
                "nrrArtifactId": parsed.nrr_artifact_id,
            },
        )
        _announce(ctx, row, status=str(parsed.status))


def _finish_failed(runtime: Runtime, run_id: str, *, code: str, message: str) -> None:
    with runtime.transaction(operator="system") as ctx:
        row = ctx.session.get(InputParseRun, run_id)
        if row is None or row.status != "PARSING":
            return
        ctx = ctx.bind(
            operator=row.operator,
            project_id=row.project_id,
            workflow_id=row.workflow_id,
            stage_id=row.stage_id,
        )
        _mark_failed_row(ctx, row, code=code, message=message)
        record_change(
            ctx,
            event_type="artifact.created",
            audit_event_type="cp.input.parsed",
            object_type="Artifact",
            object_id=row.input_artifact_id,
            payload={
                "inputArtifactId": row.input_artifact_id,
                "status": "FAILED",
                "nrrArtifactId": None,
            },
        )
        _announce(ctx, row, status="FAILED")


def _mark_failed_row(ctx: Any, row: InputParseRun, *, code: str, message: str) -> None:
    row.status = "FAILED"
    row.error_code = code[:64]
    row.error_message = message[:2000]
    row.finished_at = ctx.clock.now()
    _snapshot(
        ctx,
        stage=ctx.session.get(Stage, row.stage_id),
        input_artifact_id=row.input_artifact_id,
        run_id=row.id,
        key=row.idempotency_key,
        status="FAILED",
        error_code=row.error_code,
        error_message=row.error_message,
    )


def _snapshot(
    ctx: Any,
    *,
    stage: Stage | None,
    input_artifact_id: str,
    run_id: str,
    key: str,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Keep ``stage_runs.result`` in step; the stage view and pre-upgrade reads use it."""
    if stage is None:
        return
    from factory_control.services.workflow import _stage_run

    result: dict[str, Any] = {
        "kind": "inputParse",
        "inputArtifactId": input_artifact_id,
        "status": status,
        "parseRunId": run_id,
    }
    if error_code:
        result["errorCode"] = error_code
    if error_message:
        result["errorMessage"] = error_message
    _stage_run(
        ctx,
        stage,
        agent_run_ids=[],
        idempotency_key=key,
        result=result,
    )


def _announce(ctx: Any, row: InputParseRun, *, status: str) -> None:
    event_log.append(
        ctx.session,
        workflow_id=row.workflow_id,
        event_type="input.parse",
        payload={
            "inputArtifactId": row.input_artifact_id,
            "parseRunId": row.id,
            "stageId": row.stage_id,
            "status": status,
        },
        retention_days=ctx.settings.event_retention_days(),
    )


def _message(exc: BaseException) -> str:
    message = getattr(exc, "message", None)
    return str(message if message is not None else exc)


def reconcile_interrupted(runtime: Runtime) -> int:
    """Close ``PARSING`` rows left by a previous process (single-instance assumption)."""
    with unit_of_work() as session:
        stale = list(
            session.execute(
                select(InputParseRun.id).where(InputParseRun.status == "PARSING")
            ).scalars()
        )
    for run_id in stale:
        _finish_failed(
            runtime,
            run_id,
            code=PARSE_INTERRUPTED,
            message="服务重启导致解析中断，请重新解析",
        )
    return len(stale)


__all__ = [
    "execute_parse",
    "reconcile_interrupted",
    "shutdown_parse_pool",
    "start_parse",
    "submit_parse",
]
