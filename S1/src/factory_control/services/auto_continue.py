"""Automatic continuation between design stages (CP-WF-028 sibling).

The workbench already unlocks the next stage when one passes; whether that stage starts has
always been a human click.  This service is the scheduled side of two rules:

* the design-line stages in ``AUTO_STAGES`` (DISCOVERY, BASELINE_COMPILE) have no agent, no
  question and no submission — nobody is ever needed at them — so they always continue on
  their own, with no configuration and no click.
* an agent stage named in the opt-in setting ``stage_auto_continue`` is claimed on the next
  tick; the setting stays the only way to automate a stage a person might want to start.

Two boundaries are deliberate:

* only *running* the stage is automated.  An agent stage still opens its draft, submits to its
  approval Gate and waits; a Gate is never approved by this job.
* the decision is read from ``cp.system_settings`` every tick, so switching a stage back to
  manual takes effect without a restart.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from factory_control.domain.enums import AUTO_STAGES, DESIGN_STAGES, StageStatus
from factory_control.foundation import get_logger
from factory_control.models import Project, Stage, Workflow
from factory_control.runtime import Runtime

_logger = get_logger("factory_control.auto_continue")

_STAGE_ORDER = {stage.value: index for index, stage in enumerate(DESIGN_STAGES)}

#: Design-line stages that execution cannot wait on a person for (附录 A 触发说明).
ALWAYS_CONTINUE: frozenset[str] = frozenset(
    stage.value for stage in set(AUTO_STAGES) & set(DESIGN_STAGES)
)


def auto_continue_ready_stages(runtime: Runtime, *, operator: str = "auto") -> dict[str, Any]:
    """Start READY stages the system runs on its own, plus the configured agent stages.

    One stage per workflow per tick: the next one can only become READY after this one passes,
    so serialising here matches the design line's own rule and never races a human click.
    """
    if runtime.engine is None:
        return {"started": [], "failed": [], "reason": "NO_ENGINE"}

    selected: list[tuple[str, str]] = []
    with runtime.transaction(operator="system") as ctx:
        configured = set(ctx.settings.auto_continue_stages()) | ALWAYS_CONTINUE
        rows = ctx.session.execute(
            select(Stage, Workflow, Project)
            .join(Workflow, Workflow.id == Stage.workflow_id)
            .join(Project, Project.id == Workflow.project_id)
            .where(
                Stage.status == StageStatus.READY.value,
                Stage.deployment_run_id.is_(None),
                Workflow.paused.is_(False),
                Workflow.cancelled.is_(False),
                Project.archived.is_(False),
                Project.deleted_at.is_(None),
            )
        ).all()
        # A workflow only ever has one READY design stage, but sort so a malformed row set still
        # starts the earliest one; never start two stages of the same workflow in one tick.
        rows.sort(key=lambda row: (row[0].workflow_id, _STAGE_ORDER.get(row[0].stage_type, 999)))
        for stage, workflow, _project in rows:
            if stage.stage_type not in configured:
                continue
            if any(item[0] == workflow.id for item in selected):
                continue
            running = ctx.session.execute(
                select(Stage.id).where(
                    Stage.workflow_id == workflow.id,
                    Stage.status == StageStatus.RUNNING.value,
                )
            ).first()
            if running is not None:
                continue
            selected.append((workflow.id, stage.id))

    started: list[str] = []
    failed: list[dict[str, str]] = []
    for workflow_id, stage_id in selected:
        try:
            runtime.engine.start_stage(stage_id, operator=operator)
            started.append(stage_id)
        except Exception as exc:  # noqa: BLE001 - one refusal must not stop other projects
            _logger.warning("auto-continue could not start %s: %s", stage_id, exc)
            failed.append({"workflowId": workflow_id, "stageId": stage_id, "error": type(exc).__name__})
    return {"started": started, "failed": failed}


__all__ = ["auto_continue_ready_stages"]
