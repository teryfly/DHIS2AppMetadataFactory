"""Workflow and stage orchestration (CP-WF-001 … CP-WF-030).

Two structural choices come straight from the requirements:

* **every** stage transition goes through :func:`transition`, which locks the
  workflow row, validates appendix A, and writes the event and the audit record in
  the same unit of work (CP-WF-005, CP-WF-006, CP-BR-14);
* provider calls that do external I/O happen **between** transactions, never
  inside one (00 doc 6.2).  A stage execution therefore reads its inputs in one
  transaction, calls S2/S3/S4, and records the outcome in a second.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from factory_control import store as S
from factory_control.domain import actions as A
from factory_control.domain import feedback as FB
from factory_control.domain import invalidation as INV
from factory_control.domain import structure as ST
from factory_control.domain import workflow_derive as WD
from factory_control.domain.enums import (
    DESIGN_STAGES,
    RUN_STAGES,
    AgentRunStatus,
    ChangeLevel,
    ChangeRequestStatus,
    GateStatus,
    GateType,
    QuestionSeverity,
    QuestionStatus,
    RunStatus,
    StageStatus,
    StageType,
    WorkflowStatus,
)
from factory_control.errors import (
    InvalidStateError,
    NotFoundControlError,
    RequestInProgressError,
)
from factory_control.foundation import ArtifactQuery, new_id, query_artifacts
from factory_control.models import (
    DeploymentRun,
    Gate,
    Project,
    Stage,
    StageRun,
    Workflow,
)
from factory_control.runtime import ControlContext, Runtime
from factory_control.services.recording import record_change


def stage_view(stage: Stage, aggregate: dict[str, Any] | None = None) -> dict[str, Any]:
    """One stage row, plus the S5IF-FLD01 aggregates when the caller computed them.

    The aggregate comes from :func:`factory_control.services.stage_rollup.rollups`, which
    answers for the whole stage list at once: computing it per stage would mean a query
    per stage on the request that paints the navigation.
    """
    out: dict[str, Any] = {
        "id": stage.id,
        "workflowId": stage.workflow_id,
        "deploymentRunId": stage.deployment_run_id,
        "stageType": stage.stage_type,
        "status": stage.status,
        "attempt": stage.attempt,
        "inputBlueprintRef": stage.input_blueprint_ref,
        "outputArtifactIds": list(stage.output_artifact_ids or []),
        "failureSourceStage": stage.failure_source_stage,
        "feedback": stage.feedback,
        "startedAt": stage.started_at,
        "completedAt": stage.completed_at,
    }
    out.update(dict(aggregate or {}))
    return out


def active_draft(runtime: Runtime, project_id: str) -> dict[str, Any] | None:
    """The draft this project is working in, resolved by S1 (S5IF-BP05 【裁决】).

    S5 holds no business state and must not guess which draft is live (UI-BR-02), so the id
    travels with the workflow view, with the hash UI-WB-003 shows next to it.  This is a
    provider read, and therefore a caller already inside a unit of work must not reach it —
    the routes call it beside the transaction they open for the rest of the view.
    """
    from factory_control.services.blueprint_reads import _draft_row, resolve_active_draft

    resolved = resolve_active_draft(runtime, project_id)
    if resolved is None:
        return None
    row = _draft_row(runtime, str(resolved["draftId"]))
    if row is None:
        return resolved
    return {
        **resolved,
        "stage": row.stage or resolved["stage"],
        "stageScope": list(row.stage_scope) or list(resolved["stageScope"]),
        "baseVersion": row.base_version or resolved["baseVersion"],
        "contentHash": row.content_hash or resolved["contentHash"],
    }


def workflow_view(
    ctx: ControlContext,
    workflow: Workflow,
    project: Project,
    *,
    language: str = "en",
    active_draft_id: str | None = None,
) -> dict[str, Any]:
    from factory_control.services.stage_rollup import rollups

    stages = S.stages_of(ctx.session, workflow.id)
    runs = S.runs_of(ctx.session, workflow.id)
    aggregates = rollups(
        ctx.session, workflow.id, stages, language=language,
        active_draft_id=active_draft_id, current_stage_id=workflow.current_stage_id,
    )
    return {
        "id": workflow.id,
        "projectId": workflow.project_id,
        "status": workflow.status,
        "paused": workflow.paused,
        "cancelled": workflow.cancelled,
        "currentStageId": workflow.current_stage_id,
        "lastEventSeq": workflow.last_event_seq,
        "version": workflow.version,
        "milestoneStatus": project.milestone_status,
        "stages": [stage_view(s, aggregates.get(s.id)) for s in stages],
        "deploymentRuns": [
            run_view(
                r,
                project_id=project.id,
                project_name=project.name,
                last_verified_at=r.completed_at if r.status == RunStatus.VERIFIED.value else None,
            )
            for r in runs
        ],
    }


def run_view(
    run: DeploymentRun,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
    environment_class: str | None = None,
    last_verified_at: Any = None,
    post_verify_artifact_id: str | None = None,
) -> dict[str, Any]:
    """S5IF-FLD06: the run list needs the environment class and the VERIFIED time.

    Those two are not on the ``cp.deployment_runs`` row — the class is S4's to say
    and the timestamp is derived from S1's own status transition — so the caller
    supplies them and the projection stays a projection.
    """
    return {
        "id": run.id,
        "workflowId": run.workflow_id,
        "environmentKey": run.environment_key,
        "blueprintVersion": run.blueprint_version,
        "status": run.status,
        "outdated": run.outdated,
        "validatePayloadHash": run.validate_payload_hash,
        "validateOperator": run.validate_operator,
        "commitOperator": run.commit_operator,
        "executionId": run.execution_id,
        "startedAt": run.started_at,
        "completedAt": run.completed_at,
        "projectId": project_id,
        "projectName": project_name,
        "environmentClass": environment_class,
        "lastVerifiedAt": last_verified_at,
        "postVerifyArtifactId": post_verify_artifact_id,
    }


def refresh_derived_state(ctx: ControlContext, workflow: Workflow) -> WorkflowStatus:
    """Recompute workflow status and project milestone from the rows (CP-WF-023/024)."""
    project = ctx.session.get(Project, workflow.project_id)
    if project is None:
        raise NotFoundControlError(f"unknown project {workflow.project_id}")
    stages = S.stages_of(ctx.session, workflow.id)
    runs = S.runs_of(ctx.session, workflow.id)
    gates = S.gates_of(ctx.session, workflow.id)
    semantics = ctx.blueprint.get_project_semantics(project.id)

    facts = WD.WorkflowFacts(
        cancelled=workflow.cancelled,
        paused=workflow.paused,
        delivery_mode=semantics.delivery_mode or project.delivery_mode,
        stages=[
            WD.StageFact(
                stage_type=StageType(s.stage_type),
                status=StageStatus(s.status),
                in_run=s.deployment_run_id is not None,
            )
            for s in stages
        ],
        runs=[WD.RunFact(status=RunStatus(r.status), outdated=r.outdated) for r in runs],
        architecture_gate_approved=_gate_ever_approved(gates, GateType.ARCHITECTURE_GATE),
        design_gate_approved=_gate_ever_approved(gates, GateType.DESIGN_GATE),
        package_gate_approved=_gate_ever_approved(gates, GateType.PACKAGE_GATE),
        dhis2_validated=any(
            s.stage_type == StageType.DHIS2_VALIDATE.value and s.status == StageStatus.PASSED.value
            for s in stages
        ),
    )
    derived = WD.derive_workflow_status(facts)
    milestone = WD.derive_milestone_status(facts)
    previous_status = workflow.status
    if workflow.status != derived.value:
        workflow.status = derived.value
    if project.milestone_status != milestone.value:
        project.milestone_status = milestone.value
    if derived is WorkflowStatus.COMPLETED and previous_status != WorkflowStatus.COMPLETED.value:
        # 19.2.3 lists workflow.completed and C-02 makes it the event S5 redraws the
        # overview from.  Derivation happens in a dozen places, so it is announced on the
        # edge into COMPLETED only — a status that has not changed must not append again.
        record_change(
            ctx,
            event_type="workflow.completed",
            audit_event_type="cp.workflow.completed",
            object_type="Workflow",
            object_id=workflow.id,
            workflow_id=workflow.id,
            project_id=project.id,
            before={"status": previous_status},
            after={"status": derived.value, "milestone": milestone.value},
        )
    stage_ids: dict[StageType, str] = {}
    for stage in stages:
        if stage.deployment_run_id is None:
            stage_ids.setdefault(StageType(stage.stage_type), stage.id)
    workflow.current_stage_id = WD.current_stage_id(facts, stage_ids) or workflow.current_stage_id
    ctx.session.flush()
    return derived


def _gate_ever_approved(gates: list[Gate], gate_type: GateType) -> bool:
    """APPROVED now, or approved-then-invalidated: the decision did happen."""
    return any(
        g.type == gate_type.value and g.status in (GateStatus.APPROVED.value, GateStatus.INVALIDATED.value)
        and any(a.get("decision") == "APPROVED" for a in (g.approvals or []))
        for g in gates
    )


def reload_stage(ctx: ControlContext, stage: Stage) -> Stage:
    """Re-read the row inside *this* transaction.

    A stage handed over from another (already closed) unit of work is detached:
    mutating it would silently lose the write.  Every writer reloads first.
    """
    return S.get_stage(ctx.session, stage.id)


def transition(
    ctx: ControlContext,
    *,
    stage: Stage,
    target: StageStatus,
    event_type: str,
    payload: dict[str, Any] | None = None,
    feedback: dict[str, Any] | None = None,
    failure_source_stage: str | None = None,
    output_artifact_ids: list[str] | None = None,
) -> Stage:
    """The only legal way to move a stage (CP-BR-01, CP-WF-004/005)."""
    from factory_control.domain.stage_machine import assert_transition
    from factory_control.foundation import utcnow

    stage = reload_stage(ctx, stage)
    workflow = S.lock_workflow(ctx.session, stage.workflow_id)
    assert_transition(stage.status, target, stage_id=stage.id)
    previous = stage.status

    if target is StageStatus.RUNNING:
        # CP-WF-006: one RUNNING stage per workflow, enforced twice over — the
        # partial unique index plus this deterministic pre-check.
        clash = S.running_stage(ctx.session, workflow.id)
        if clash is not None and clash.id != stage.id:
            raise InvalidStateError(
                "another stage of this workflow is already RUNNING",
                stage=stage.stage_type,
                details={"runningStageId": clash.id, "attemptedStageId": stage.id},
            )
        stage.started_at = utcnow()
        stage.completed_at = None
        stage.attempt = int(stage.attempt or 0) + 1

    stage.status = target.value
    if feedback is not None:
        stage.feedback = feedback
    if failure_source_stage is not None:
        stage.failure_source_stage = failure_source_stage
    if output_artifact_ids is not None:
        stage.output_artifact_ids = list(output_artifact_ids)
    if target in (
        StageStatus.PASSED,
        StageStatus.FAILED,
        StageStatus.BLOCKED,
        StageStatus.INVALIDATED,
        StageStatus.MANUAL_CHECK,
    ):
        stage.completed_at = utcnow()

    ctx.session.flush()
    record_change(
        ctx.bind(workflow_id=workflow.id, stage_id=stage.id),
        event_type=event_type,
        audit_event_type=_audit_for_stage_event(event_type),
        object_type="Stage",
        object_id=stage.id,
        payload={"stageId": stage.id, "stageType": stage.stage_type, "from": previous, "to": target.value, **(payload or {})},
    )
    workflow.current_stage_id = stage.id
    ctx.session.flush()
    return stage


def _audit_for_stage_event(event_type: str) -> str:
    if event_type == "stage.started":
        return "cp.stage.started"
    if event_type == "stage.invalidated":
        return "cp.stage.invalidated"
    if event_type == "stage.rerun":
        return "cp.stage.rerun"
    return "cp.stage.completed"


def complete_stage_as_passed(
    ctx: ControlContext,
    *,
    stage: Stage,
    output_artifact_ids: list[str],
    source: str,
    detail: dict[str, Any] | None = None,
) -> Stage:
    """Close a stage as PASSED and unlock the next one — the single PASSED path.

    Tolerant of a caller that already moved the stage to PASSED, because appendix A
    has no PASSED -> PASSED edge; without this a stage would wedge the line.
    """
    stage = reload_stage(ctx, stage)
    if StageStatus(stage.status) is StageStatus.PASSED:
        passed = stage
    else:
        passed = transition(
            ctx,
            stage=stage,
            target=StageStatus.PASSED,
            event_type="stage.passed",
            payload={"source": source, **(detail or {})},
            output_artifact_ids=output_artifact_ids,
        )
    _unlock_next_stage(ctx, stage=passed)
    return passed


def _unlock_next_stage(ctx: ControlContext, *, stage: Stage) -> Stage | None:
    """LOCKED → READY for the following design stage; auto stages queue up too."""
    if stage.deployment_run_id is not None:
        return _unlock_next_run_stage(ctx, stage=stage)

    workflow = S.get_workflow(ctx.session, stage.workflow_id)
    design = [s for s in S.stages_of(ctx.session, workflow.id) if s.deployment_run_id is None]
    statuses = {StageType(s.stage_type): StageStatus(s.status) for s in design}
    nxt = ST.next_design_stage(statuses, StageType(stage.stage_type))
    if nxt is None:
        refresh_derived_state(ctx, workflow)
        return None
    row = S.stage_by_type(ctx.session, workflow.id, nxt)
    if row is None or row.status != StageStatus.LOCKED.value:
        refresh_derived_state(ctx, workflow)
        return None
    transition(
        ctx,
        stage=row,
        target=StageStatus.READY,
        event_type="stage.passed",
        payload={"unlockedBy": stage.id},
    )
    refresh_derived_state(ctx, workflow)
    return row


def _unlock_next_run_stage(ctx: ControlContext, *, stage: Stage) -> Stage | None:
    run_id = stage.deployment_run_id
    if run_id is None:
        return None
    rows = [s for s in S.stages_of(ctx.session, stage.workflow_id, run_id=run_id)]
    order = [stage.value for stage in RUN_STAGES]
    if stage.stage_type not in order:
        return None
    index = order.index(stage.stage_type)
    for candidate in order[index + 1 :]:
        row = next((s for s in rows if s.stage_type == candidate), None)
        if row is None:
            continue
        if row.status == StageStatus.LOCKED.value:
            transition(
                ctx,
                stage=row,
                target=StageStatus.READY,
                event_type="stage.passed",
                payload={"unlockedBy": stage.id},
            )
            return row
        if row.status == StageStatus.SKIPPED.value:
            continue
        return None
    run = ctx.session.get(DeploymentRun, run_id)
    if run is not None and run.status in (RunStatus.IN_PROGRESS.value, RunStatus.CREATED.value):
        run.status = RunStatus.VERIFIED.value
        from factory_control.foundation import utcnow

        run.completed_at = utcnow()
        ctx.session.flush()
        record_change(
            ctx,
            event_type="deployment_run.completed",
            audit_event_type="cp.deployment_run.completed",
            object_type="DeploymentRun",
            object_id=run.id,
            payload={"environmentKey": run.environment_key, "blueprintVersion": run.blueprint_version},
        )
    return None


def apply_agent_result(
    ctx: ControlContext,
    *,
    stage: Stage,
    result: Any,
    idempotency_key: str,
) -> dict[str, Any]:
    """CP-WF-028 completion criteria, plus CP-WF-030 for policy and budget."""
    stage = reload_stage(ctx, stage)
    status = AgentRunStatus(result.status)
    stage_run = _stage_run(
        ctx,
        stage,
        agent_run_ids=[result.run_id] if result.run_id else [],
        output_artifact_ids=[result.agent_output_artifact_id] if result.agent_output_artifact_id else [],
        idempotency_key=idempotency_key,
        result={"status": status.value, "summary": result.summary},
    )

    from factory_control.services.questions import record_questions

    record_questions(
        ctx,
        stage=stage,
        questions=list(result.questions),
        agent_run_id=result.run_id,
    )

    if status is AgentRunStatus.WAITING_INPUT:
        transition(
            ctx,
            stage=stage,
            target=StageStatus.WAITING_INPUT,
            event_type="stage.waiting_input",
            payload={"agentRunId": result.run_id, "questions": len(result.questions)},
        )
        return {"status": StageStatus.WAITING_INPUT.value, "agentRunId": result.run_id}

    if status is AgentRunStatus.COMPLETED:
        _record_output_artifacts(ctx, stage=stage, result=result)
        # A finished agent run is not an approval: submit_for_approval closes it
        # (CP-GATE-013a freezes the draft, then the Gate is created).
        return {
            "status": stage.status,
            "agentRunId": result.run_id,
            "stageRunId": stage_run.id if stage_run else None,
            "readyForApproval": True,
        }

    if status in (AgentRunStatus.POLICY_DENIED, AgentRunStatus.BUDGET_EXCEEDED):
        # CP-WF-030: blocked, with what was refused shown to the user.
        transition(
            ctx,
            stage=stage,
            target=StageStatus.BLOCKED,
            event_type="stage.blocked",
            payload={
                "reason": status.value,
                "rejectedArtifacts": list(result.rejected_artifacts),
                "budgetStatus": dict(result.budget_status),
                "denialReasons": list(result.denial_reasons),
            },
            feedback=FB.stage_feedback(
                status.value,
                "; ".join(str(reason) for reason in result.denial_reasons)
                or f"the run was refused: {status.value}",
                source_stage=stage.stage_type,
                created_at=ctx.clock.now(),
                extra={
                    "denialReasons": list(result.denial_reasons),
                    "rejectedArtifacts": list(result.rejected_artifacts),
                    "budgetStatus": dict(result.budget_status),
                },
            ),
        )
        from factory_control.services.notifications import notify_budget, notify_stage_event

        notify_stage_event(ctx, stage=stage, kind="blocked", detail=f"{stage.stage_type} {status.value}")
        if status is AgentRunStatus.BUDGET_EXCEEDED:
            notify_budget(ctx, exceeded=True, detail="agent run exceeded the project budget")
        return {"status": StageStatus.BLOCKED.value, "reason": status.value}

    transition(
        ctx,
        stage=stage,
        target=StageStatus.FAILED,
        event_type="stage.failed",
        payload={"agentRunId": result.run_id, "issues": [i.model_dump() for i in result.issues]},
        failure_source_stage=_source_stage_of(result.issues) or stage.stage_type,
        feedback=FB.stage_feedback(
            FB.EXECUTION_FAILED,
            result.summary or "the agent run failed",
            source_stage=_source_stage_of(result.issues) or stage.stage_type,
            issues=list(result.issues),
            created_at=ctx.clock.now(),
        ),
    )
    from factory_control.services.notifications import notify_stage_event

    notify_stage_event(ctx, stage=stage, kind="failed", detail=f"{stage.stage_type} failed")
    return {"status": StageStatus.FAILED.value}


def _source_stage_of(issues: list[Any]) -> str | None:
    for issue in issues:
        if getattr(issue, "source_stage", None):
            return str(issue.source_stage)
    return None


def _record_output_artifacts(ctx: ControlContext, *, stage: Stage, result: Any) -> None:
    if result.agent_output_artifact_id:
        ids = list(stage.output_artifact_ids or [])
        if result.agent_output_artifact_id not in ids:
            ids.append(result.agent_output_artifact_id)
        stage.output_artifact_ids = ids
        ctx.session.flush()


def record_step_result(runtime: Runtime, *, stage_id: str, operator: str, **fields: Any) -> None:
    """Merge facts into the step run S1 already opened for this attempt.

    Three readers need a record that has no column of its own: the console may not ask S4
    what the last capability probe or drift check said — both reach DHIS2 — so the answer
    is kept where it was obtained (S5IF-FLD05); and the stage navigation wants the
    blocking count of the last validation without re-running it (S5IF-FLD01).  These are
    S1's own execution records, which is what CP-API-007 lets it keep.
    """
    # A nested call reuses the caller's transaction (FD-DB-003), so this is safe whether
    # or not the step is mid-write.
    with runtime.transaction(operator=operator, stage_id=stage_id) as ctx:
        row = S.latest_stage_run(ctx.session, stage_id)
        if row is None:
            return
        row.result = {**dict(row.result or {}), **fields}
        ctx.session.flush()


def open_stage_run(ctx: ControlContext, stage: Stage, *, idempotency_key: str) -> StageRun:
    """The StageRun row for this attempt, created when the stage is claimed.

    S5IF-G06 makes the REST layer answer 202 with this row's id, so it has to exist
    before the request returns — the execution that fills it in may be asynchronous.
    A later call for the same attempt merges into the same row.
    """
    return _stage_run(ctx, stage, agent_run_ids=[], idempotency_key=idempotency_key)


def _stage_run(
    ctx: ControlContext,
    stage: Stage,
    *,
    agent_run_ids: list[str],
    execution_ids: list[str] | None = None,
    build_ids: list[str] | None = None,
    idempotency_key: str,
    result: dict[str, Any] | None = None,
    output_artifact_ids: list[str] | None = None,
) -> StageRun:
    attempt = int(stage.attempt or 0) or 1
    existing = ctx.session.execute(
        select(StageRun).where(StageRun.stage_id == stage.id, StageRun.attempt == attempt)
    ).scalars().first()
    if existing is not None:
        existing.agent_run_ids = list({*(existing.agent_run_ids or []), *agent_run_ids})
        existing.execution_ids = list({*(existing.execution_ids or []), *(execution_ids or [])})
        existing.build_ids = list({*(existing.build_ids or []), *(build_ids or [])})
        existing.output_artifact_ids = list(
            {*(existing.output_artifact_ids or []), *(output_artifact_ids or [])}
        )
        if result is not None:
            # Accumulate: a step records several facts at different moments (the profile
            # it checked, the count it hit, the outcome it produced), and the last writer
            # must not erase what the earlier ones said.
            existing.result = {**dict(existing.result or {}), **result}
            existing.completed_at = ctx.clock.now()
        ctx.session.flush()
        return existing
    row = StageRun(
        id=new_id("srn"),
        stage_id=stage.id,
        attempt=attempt,
        agent_run_ids=list(agent_run_ids),
        execution_ids=list(execution_ids or []),
        build_ids=list(build_ids or []),
        idempotency_key=idempotency_key,
        input_blueprint_ref=stage.input_blueprint_ref,
        output_artifact_ids=list(output_artifact_ids or []),
        result=result,
        completed_at=ctx.clock.now() if result is not None else None,
    )
    ctx.session.add(row)
    ctx.session.flush()
    return row


def propagate_design_reference_change(
    ctx: ControlContext,
    *,
    project_id: str,
    previous_reference: str | None,
    new_reference: str | None,
) -> list[str]:
    """CP-PRJ-003 / FR-ENV-007: moving the design reference invalidates what depended on it.

    The rule bites only once DISCOVERY has passed: until then the reference is a choice
    waiting to be exercised, and there is nothing derived from it to un-derive.  After that,
    the discovery result the mapping was designed against is gone — the existing-metadata
    index, the observed baselines behind every REUSE / REFERENCE / UPDATE entry and the
    default objects the compiler was told to reference all came from *that* instance — so
    DISCOVERY and every design stage after it go back to INVALIDATED with their Gates.

    Deployment Runs are left alone.  They are bound to a Blueprint *version*, and no new
    version was produced here; what they lose is the right to advance, which the invalidated
    design line takes care of on its own (7.4 derives the workflow state from the stages).

    Returns the ids of the stages it invalidated.
    """
    if previous_reference == new_reference or previous_reference is None:
        return []
    workflow = S.get_workflow_for_project(ctx.session, project_id)
    S.lock_workflow(ctx.session, workflow.id)
    stages = S.stages_of(ctx.session, workflow.id)
    design_index = {stage_type: index for index, stage_type in enumerate(DESIGN_STAGES)}
    discovery_index = design_index[StageType.DISCOVERY]

    if not any(
        stage.stage_type == StageType.DISCOVERY.value and stage.status == StageStatus.PASSED.value
        for stage in stages
    ):
        return []

    from factory_control.services.gates import invalidate_gate

    invalidated: list[str] = []
    for stage in stages:
        if stage.deployment_run_id is not None:
            continue
        index = design_index.get(StageType(stage.stage_type))
        if index is None or index < discovery_index:
            continue
        if stage.status in (StageStatus.INVALIDATED.value, StageStatus.SKIPPED.value):
            continue
        transition(
            ctx,
            stage=stage,
            target=StageStatus.INVALIDATED,
            event_type="stage.invalidated",
            payload={
                "reason": "DESIGN_REFERENCE_CHANGED",
                "previousDesignReference": previous_reference,
                "designReference": new_reference,
            },
        )
        invalidated.append(stage.id)

    for gate in S.gates_of(ctx.session, workflow.id):
        if gate.status != GateStatus.WAITING.value and gate.status != GateStatus.APPROVED.value:
            continue
        owner = next((s for s in stages if s.id == gate.stage_id), None)
        if owner is None or owner.id not in invalidated:
            continue
        invalidate_gate(ctx, gate, reason="DESIGN_REFERENCE_CHANGED")

    refresh_derived_state(ctx, workflow)
    return invalidated


def apply_propagation_from_promotion(
    ctx: ControlContext,
    *,
    project_id: str,
    change_level: ChangeLevel,
    affected_stage_names: list[str],
    protect_stage_ids: list[str] | None = None,
) -> INV.InvalidationPlan:
    """CP-WF-014/015: S2 said which stages are affected; apply it here.

    ``protect_stage_ids`` names the stages this promotion must not invalidate. The caller that
    approves a design Gate passes the stage whose approval caused the promotion, because
    ``workflow.current_stage_id`` no longer names it by then — see
    :attr:`factory_control.domain.invalidation.InvalidationInput.protect_stage_ids`.
    """
    workflow = S.get_workflow_for_project(ctx.session, project_id)
    S.lock_workflow(ctx.session, workflow.id)
    stages = S.stages_of(ctx.session, workflow.id)
    runs = S.runs_of(ctx.session, workflow.id)
    gates = S.gates_of(ctx.session, workflow.id)

    affected: list[StageType] = []
    for name in affected_stage_names:
        try:
            affected.append(StageType(name))
        except ValueError:
            continue

    input_state = INV.InvalidationInput(
        change_level=change_level,
        affected_stages=affected,
        current_stage_id=workflow.current_stage_id,
        protect_stage_ids=list(protect_stage_ids or []),
        stages=[
            INV.StageState(
                stage_id=s.id,
                stage_type=StageType(s.stage_type),
                status=StageStatus(s.status),
                deployment_run_id=s.deployment_run_id,
            )
            for s in stages
        ],
        runs=[
            INV.RunState(
                run_id=r.id,
                status=RunStatus(r.status),
                in_commit=any(
                    s.deployment_run_id == r.id
                    and s.stage_type == StageType.COMMIT.value
                    and s.status == StageStatus.RUNNING.value
                    for s in stages
                ),
            )
            for r in runs
        ],
        gates=[
            INV.GateState(
                gate_id=g.id,
                status=GateStatus(g.status),
                stage_type=_stage_type_of_gate(ctx, g),
                deployment_run_id=g.deployment_run_id,
            )
            for g in gates
        ],
    )
    plan = INV.plan(input_state)

    for stage_id in plan.invalidate_stage_ids:
        stage = ctx.session.get(Stage, stage_id)
        if stage is None or stage.status == StageStatus.INVALIDATED.value:
            continue
        transition(
            ctx,
            stage=stage,
            target=StageStatus.INVALIDATED,
            event_type="stage.invalidated",
            payload={"reason": plan.reason, "changeLevel": change_level.value},
        )

    from factory_control.services.gates import invalidate_gate

    for gate_id in plan.invalidate_gate_ids:
        gate = ctx.session.get(Gate, gate_id)
        if gate is not None:
            invalidate_gate(ctx, gate, reason="BLUEPRINT_SUPERSEDED")

    for run_id in plan.cancel_run_ids:
        run = ctx.session.get(DeploymentRun, run_id)
        if run is None:
            continue
        run.status = RunStatus.CANCELLED.value
        ctx.session.flush()
        record_change(
            ctx,
            event_type="deployment_run.cancelled",
            audit_event_type="cp.deployment_run.cancelled",
            object_type="DeploymentRun",
            object_id=run.id,
            payload={"reason": plan.reason},
        )

    for run_id in plan.mark_outdated_run_ids:
        run = ctx.session.get(DeploymentRun, run_id)
        if run is None or run.outdated:
            continue
        run.outdated = True
        ctx.session.flush()
        record_change(
            ctx,
            event_type="deployment_run.outdated",
            audit_event_type="cp.deployment_run.outdated",
            object_type="DeploymentRun",
            object_id=run.id,
            payload={"reason": plan.reason, "status": run.status},
        )

    refresh_derived_state(ctx, workflow)
    return plan


def _stage_type_of_gate(ctx: ControlContext, gate: Gate) -> StageType | None:
    if not gate.stage_id:
        return None
    stage = ctx.session.get(Stage, gate.stage_id)
    return StageType(stage.stage_type) if stage else None


def reject_stage(ctx: ControlContext, *, stage: Stage, feedback: dict[str, Any]) -> Stage:
    """CP-GATE-010: a rejection sends the stage back to READY with the reason."""
    stage = reload_stage(ctx, stage)
    return transition(
        ctx,
        stage=stage,
        target=StageStatus.READY,
        event_type="stage.waiting_approval",
        payload={"outcome": "REJECTED"},
        feedback=feedback,
    )


def supersede_gates_on_resubmit(ctx: ControlContext, stage: Stage) -> None:
    """CP-GATE-010 adjustment: a resubmission must bind different hashes."""
    for gate in S.gates_of(ctx.session, stage.workflow_id):
        if gate.stage_id == stage.id and gate.status == GateStatus.WAITING.value:
            from factory_control.services.gates import invalidate_gate

            invalidate_gate(ctx, gate, reason="RESUBMITTED")


def permission_evaluator(
    runtime: Runtime, operator: str, project_id: str | None
) -> Any | None:
    """The ``allowed-actions`` permission filter, or ``None`` when identity decides nothing.

    Returning ``None`` rather than an always-allow function keeps CP-IAM-001 honest: in the MVP
    the payload is exactly what the state allows, and a reader can tell that from the field being
    absent instead of from a policy that happens to agree with everything.
    """
    from factory_control import iam
    from factory_control.config import get_settings

    if not get_settings().auth_is_enforced():
        return None
    policy = iam.get_permission_policy()
    explain = getattr(policy, "actions_for", None)
    if explain is None:
        return None

    def _evaluate(names: tuple[str, ...]) -> dict[str, Any]:
        return dict(explain(operator, project_id, names))

    return _evaluate


def allowed_actions_for_workflow(runtime: Runtime, workflow_id: str, *, operator: str) -> dict[str, Any]:
    """CP-WF-025/026/027 — protection facts are read live from S4 here."""
    with runtime.transaction(operator=operator) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        project = ctx.session.get(Project, workflow.project_id)
        if project is None:
            raise NotFoundControlError(f"unknown project {workflow.project_id}")
        stages = S.stages_of(ctx.session, workflow.id)
        gates = S.gates_of(ctx.session, workflow.id)
        runs = S.runs_of(ctx.session, workflow.id)
        targets = S.target_environments(ctx.session, project.id)
        design_complete = all(
            s.status in (StageStatus.PASSED.value, StageStatus.SKIPPED.value)
            for s in stages
            if s.deployment_run_id is None and s.stage_type != StageType.HANDOFF.value
        )
        architecture_approved = any(
            g.type == GateType.ARCHITECTURE_GATE.value and g.status == GateStatus.APPROVED.value for g in gates
        )

        environments: list[A.EnvironmentFacts] = []
        for target in targets:
            protection = ctx.execution.get_protection(target.environment_key)
            # S4's status is what says an environment is out of service; the protection row does
            # not carry it, so the two reads are both needed for the action list to be honest.
            info = ctx.execution.get_environment(target.environment_key)
            environments.append(
                A.EnvironmentFacts(
                    environment_key=target.environment_key,
                    environment_class=protection.environment_class,
                    emergency_locked=protection.emergency_locked,
                    within_commit_window=protection.within_commit_window,
                    enabled=bool(info.enabled) if info is not None else True,
                )
            )

        verified_keys: set[str] = set()
        for run in runs:
            if run.status == RunStatus.VERIFIED.value:
                protection = ctx.execution.get_protection(run.environment_key)
                if protection.environment_class.upper() != "PRODUCTION":
                    verified_keys.add(f"{run.blueprint_version}:{A.PRODUCTION_CHECK}")

        stage_snapshots = []
        for stage in stages:
            open_gating = any(
                q.status == QuestionStatus.OPEN.value
                and (q.blocking or q.severity == QuestionSeverity.HIGH.value)
                for q in S.questions_of(ctx.session, workflow.id, stage_id=stage.id)
            )
            stage_snapshots.append(
                A.StageSnapshot(
                    stage_id=stage.id,
                    stage_type=StageType(stage.stage_type),
                    status=StageStatus(stage.status),
                    has_open_question=open_gating,
                )
            )

        approved_deploy_runs = {
            g.deployment_run_id
            for g in gates
            if g.type == GateType.DEPLOY_GATE.value and g.status == GateStatus.APPROVED.value
        }
        run_snapshots = [
            A.RunSnapshot(
                run_id=r.id,
                environment_key=r.environment_key,
                blueprint_version=r.blueprint_version,
                status=RunStatus(r.status),
                deploy_gate_approved=r.id in approved_deploy_runs,
            )
            for r in runs
        ]
        gate_snapshots = [
            A.GateSnapshot(
                gate_id=g.id,
                gate_type=g.type,
                status=GateStatus(g.status),
                stage_id=g.stage_id,
                deployment_run_id=g.deployment_run_id,
                environment_key=g.environment_key,
                open_warnings=len(g.warnings or []),
            )
            for g in gates
        ]

        cr_snapshots = [
            A.ChangeRequestSnapshot(cr_id=cr.id, status=ChangeRequestStatus(cr.status))
            for cr in S.change_requests(ctx.session, project.id)
        ]

        # CP-UI-021: the download button is honest only against S1's own artifact index;
        # the package content itself is never read here.
        frontend_present = bool(
            query_artifacts(
                ArtifactQuery(project_id=project.id, type="FRONTEND_PACKAGE", limit=1),
                session=ctx.session,
            ).items
        )
        # The same index answers CP-PRJ-007's other half: an archived project is deletable
        # only once an export of it exists, so the delete button cannot be lit from `archived` alone.
        export_present = bool(
            query_artifacts(
                ArtifactQuery(project_id=project.id, type="PROJECT_EXPORT", limit=1),
                session=ctx.session,
            ).items
        )

        computed: dict[str, Any] = A.compute_actions(
            A.ActionContext(
                archived=project.archived,
                cancelled=workflow.cancelled,
                paused=workflow.paused,
                delivery_mode=project.delivery_mode,
                design_line_complete=design_complete,
                architecture_gate_approved=architecture_approved,
                stages=stage_snapshots,
                gates=gate_snapshots,
                runs=run_snapshots,
                change_requests=cr_snapshots,
                environments=environments,
                verified_version_keys=verified_keys,
                evaluate=permission_evaluator(runtime, operator, project.id),
                frontend_package_available=frontend_present,
                project_export_available=export_present,
                m4_enabled=runtime.settings.m4_features_are_live(),
            )
        )
        computed["workflowId"] = workflow.id
        computed["evaluatedAt"] = ctx.clock.now().isoformat()
        return computed


def pause_workflow(runtime: Runtime, workflow_id: str, *, operator: str) -> dict[str, Any]:
    """CP-WF-011: no new stage starts; a RUNNING call finishes and records, but does not advance."""
    return _set_run_flag(runtime, workflow_id=workflow_id, operator=operator, flag="paused", value=True)


def resume_workflow(runtime: Runtime, workflow_id: str, *, operator: str) -> dict[str, Any]:
    return _set_run_flag(runtime, workflow_id=workflow_id, operator=operator, flag="paused", value=False)


def cancel_workflow(runtime: Runtime, workflow_id: str, *, operator: str) -> dict[str, Any]:
    """CP-WF-011 / CP-AC-10: cancel long tasks, except a COMMIT already under way."""
    with runtime.transaction(operator=operator) as ctx:
        workflow = S.lock_workflow(ctx.session, workflow_id)
        if workflow.cancelled:
            return {"workflowId": workflow.id, "cancelled": True}
        running = S.running_stage(ctx.session, workflow.id)
        if running is not None and running.stage_type == StageType.COMMIT.value:
            raise RequestInProgressError(
                "a COMMIT is in flight and cannot be cancelled; it will complete first",
                # SRS 8.3: ``scope`` says what is busy, and it is this Run's COMMIT step —
                # not an endpoint, because no idempotency key is involved here.
                details={
                    "scope": f"commit:{workflow.id}:{running.id}",
                    "stageId": running.id,
                    "retryable": True,
                },
            )
    _cancel_long_tasks(runtime, workflow_id=workflow_id)
    with runtime.transaction(operator=operator) as ctx:
        workflow = S.lock_workflow(ctx.session, workflow_id)
        workflow.cancelled = True
        workflow.paused = False
        for stage in S.stages_of(ctx.session, workflow.id):
            if stage.status in (StageStatus.RUNNING.value, StageStatus.WAITING_INPUT.value, StageStatus.WAITING_APPROVAL.value):
                transition(
                    ctx,
                    stage=stage,
                    target=StageStatus.INVALIDATED,
                    event_type="stage.invalidated",
                    payload={"reason": "WORKFLOW_CANCELLED"},
                )
        from factory_control.services.gates import invalidate_gate

        for gate in S.gates_of(ctx.session, workflow.id, status=GateStatus.WAITING):
            invalidate_gate(ctx, gate, reason="WORKFLOW_CANCELLED")
        refresh_derived_state(ctx, workflow)
        record_change(
            ctx,
            event_type="workflow.failed",
            audit_event_type="cp.workflow.cancelled",
            object_type="Workflow",
            object_id=workflow.id,
            payload={"cancelled": True},
        )
        return {"workflowId": workflow.id, "cancelled": True}


def allowed_actions_for_platform(runtime: Runtime, *, operator: str) -> dict[str, Any]:
    """FR-UI-009 for the Administration pages, which belong to no workflow.

    ``GET /workflows/{id}/allowed-actions`` is the source of truth everywhere else, but the
    Environments, Governance and Settings screens are reached before any project exists and
    have no workflow id to ask with.  Without an answer they had no choice but to decide
    locally who may create an environment or flip a production emergency lock -- which is the
    one thing FR-UI-009 and UI-BR-01 forbid, and the reason S5's environment controls were
    plain buttons.

    The environment facts are read live from S4 (CP-WF-027): the lock state in particular must
    be the one the execution layer is enforcing, not a cached copy.
    """
    from factory_control.iam import PROJECT_VIEW, authorize

    # the same read right the environment listing asks for: an availability answer discloses
    # no more than GET /environments already does.  Appendix B has no "platform.read", so
    # inventing one here would put an action name outside the vocabulary M4's roles key on.
    authorize(operator, None, PROJECT_VIEW)
    listed: list[tuple[str, bool]] = []
    with runtime.transaction(operator="system") as ctx:
        listed = [(str(info.environment_key), bool(info.enabled)) for info in ctx.execution.list_environments()]

    facts: list[A.EnvironmentFacts] = []
    for key, enabled in listed:
        protection = runtime.execution.get_protection(key)
        facts.append(
            A.EnvironmentFacts(
                environment_key=key,
                environment_class=protection.environment_class,
                emergency_locked=protection.emergency_locked,
                within_commit_window=protection.within_commit_window,
                # the status came from the same listing that decided which keys to ask about, so
                # an environment S4 switched off cannot be offered as if it were live
                enabled=enabled,
            )
        )
    actions = A.platform_actions(
        facts,
        evaluate=permission_evaluator(runtime, operator, None),
        m4_enabled=runtime.settings.m4_features_are_live(),
    )
    return {
        "allowed": [action.as_dict() for action in actions if action.available],
        "unavailable": [action.as_dict() for action in actions if not action.available],
        "scope": "platform",
        "evaluatedAt": runtime.clock.now().isoformat(),
    }


def _cancel_long_tasks(runtime: Runtime, *, workflow_id: str) -> None:
    """Tell everything still working for this workflow to stop (CP-WF-011, FR-WF-004).

    Two separate reaches, because the work sits in two places: an Agent run lives inside S3
    and is stopped through its own public function, while a compile, a discovery or a
    server-side validation runs here in the worker and is stopped by flipping the
    cancellation token that stage registered.  Before this, only the first half existed and
    every provider call had been handed ``cancel=None``, so a cancelled workflow kept
    running -- and kept holding the environment commit lock -- until the step ended by itself.
    """
    from factory_control.progress import cancel_workflow_tokens

    cancel_workflow_tokens(workflow_id, reason="WORKFLOW_CANCELLED")
    with runtime.transaction(operator="system") as ctx:
        run_ids = [
            run_id
            for row in ctx.session.execute(
                select(StageRun.agent_run_ids).where(StageRun.stage_id.in_(
                    select(Stage.id).where(Stage.workflow_id == workflow_id)
                ))
            ).all()
            for run_id in (row[0] or [])
        ]
    for run_id in run_ids:
        runtime.intelligence.cancel_agent_run(run_id)


def _set_run_flag(
    runtime: Runtime, *, workflow_id: str, operator: str, flag: str, value: bool
) -> dict[str, Any]:
    event_type = "workflow.paused" if value else "workflow.resumed"
    audit_type = "cp.workflow.paused" if value else "cp.workflow.resumed"
    with runtime.transaction(operator=operator) as ctx:
        workflow = S.lock_workflow(ctx.session, workflow_id)
        if workflow.cancelled:
            # domain/actions.py refuses pause and resume on a cancelled run for exactly this
            # reason; accepting it here would leave a finished workflow carrying a flag that
            # nothing can lift, and an action list that says one thing while the write says
            # another.
            raise InvalidStateError(
                f"workflow {workflow_id} is cancelled; it can neither be paused nor resumed",
                details={"reasonCode": A.WORKFLOW_CANCELLED, "cancelled": True},
            )
        setattr(workflow, flag, value)
        refresh_derived_state(ctx, workflow)
        record_change(
            ctx,
            event_type=event_type,
            audit_event_type=audit_type,
            object_type="Workflow",
            object_id=workflow.id,
            payload={flag: value},
        )
        return {"workflowId": workflow.id, flag: value, "status": workflow.status}


__all__ = [
    "allowed_actions_for_workflow",
    "apply_agent_result",
    "apply_propagation_from_promotion",
    "cancel_workflow",
    "complete_stage_as_passed",
    "pause_workflow",
    "reload_stage",
    "refresh_derived_state",
    "reject_stage",
    "resume_workflow",
    "run_view",
    "stage_view",
    "supersede_gates_on_resubmit",
    "transition",
    "workflow_view",
]
