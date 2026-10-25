"""Agent run status for the workbench (S5IF-INP06 / INP07).

S3's ``get_agent_run`` deliberately does not return questions — a Question belongs
to S1, and leaking its text into S3 would break D-14.  So the run view is assembled
here from three sources, none of which is a mirror of another subsystem's state:
S3's own result, S1's Question rows, and S1's event stream for progress.

One correction is required rather than optional: S3 reports a still-``RUNNING`` run
as ``WAITING_INPUT`` (``S3 runtime/get.py:28``).  S1 knows its stage's real status,
so it fixes the reported one instead of forwarding it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from factory_control import store as S
from factory_control.errors import NotFoundControlError
from factory_control.models import Stage, StageRun
from factory_control.runtime import ControlContext, Runtime
from factory_control.services import questions as Q
from factory_control.services.recording import record_change


def get_run(runtime: Runtime, workflow_id: str, run_id: str, *, operator: str) -> dict[str, Any]:
    with runtime.transaction(operator=operator) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        stage = _stage_for_run(ctx, workflow.id, run_id)
        result = runtime.intelligence.get_agent_run(run_id)
        if result is None:
            raise NotFoundControlError(
                f"unknown agent run {run_id}", details={"runId": run_id, "workflowId": workflow_id}
            )
        rows = S.questions_of(ctx.session, workflow.id, stage_id=stage.id if stage else None)
        progress = _latest_progress(ctx, workflow.id, run_id)
        status = _corrected_status(result.status, stage)
        return {
            "runId": run_id,
            "workflowId": workflow.id,
            "stageId": stage.id if stage else None,
            "stage": stage.stage_type if stage else None,
            "status": status,
            "summary": result.summary,
            "confidence": result.confidence,
            "patchIds": list(result.patch_ids),
            "agentOutputArtifactId": result.agent_output_artifact_id,
            "questions": [Q.question_view(q) for q in rows],
            "issues": [issue.model_dump(by_alias=True) for issue in result.issues],
            "budgetStatus": dict(result.budget_status),
            "denialReasons": list(result.denial_reasons),
            "rejectedArtifacts": [dict(r) for r in result.rejected_artifacts],
            "progress": progress,
        }


def cancel_run(runtime: Runtime, workflow_id: str, run_id: str, *, operator: str) -> dict[str, Any]:
    with runtime.transaction(operator=operator) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        stage = _stage_for_run(ctx, workflow.id, run_id)
    # External I/O, with no unit of work open.
    runtime.intelligence.cancel_agent_run(run_id)
    with runtime.transaction(operator=operator, project_id=workflow.project_id, workflow_id=workflow.id) as ctx:
        record_change(
            ctx.bind(stage_id=stage.id if stage else None),
            event_type="agent.completed",
            audit_event_type="cp.agent_run.cancelled",
            object_type="AgentRun",
            object_id=run_id,
            payload={"runId": run_id, "cancelled": True},
        )
    return {"runId": run_id, "status": "CANCELLED"}


def _stage_for_run(ctx: ControlContext, workflow_id: str, run_id: str) -> Stage | None:
    rows = ctx.session.execute(
        select(StageRun, Stage)
        .join(Stage, Stage.id == StageRun.stage_id)
        .where(Stage.workflow_id == workflow_id)
    ).all()
    for stage_run, stage in rows:
        if run_id in list(stage_run.agent_run_ids or []):
            return stage
    return None


def _corrected_status(reported: str, stage: Stage | None) -> str:
    """S3's WAITING_INPUT on a still-running stage is S1's RUNNING."""
    if stage is None or reported != "WAITING_INPUT":
        return reported
    from factory_control.domain.enums import StageStatus

    if stage.status == StageStatus.RUNNING.value:
        return "RUNNING"
    return reported


def _latest_progress(ctx: ControlContext, workflow_id: str, run_id: str) -> dict[str, Any] | None:
    from factory_control.models import EventStreamRecord

    rows = ctx.session.execute(
        select(EventStreamRecord)
        .where(
            EventStreamRecord.workflow_id == workflow_id,
            EventStreamRecord.event_type == "agent.progress",
        )
        .order_by(EventStreamRecord.sequence.desc())
        .limit(50)
    ).scalars()
    for row in rows:
        payload = dict(row.payload or {})
        if payload.get("agentRunId") not in (None, run_id):
            continue
        return {
            "step": payload.get("step"),
            "completedFraction": payload.get("completedFraction"),
            "message": payload.get("message"),
        }
    return None


__all__ = ["cancel_run", "get_run"]
