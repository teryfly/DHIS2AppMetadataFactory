"""Deployment Run lifecycle: R1…R6, PACKAGE_GATE, DEPLOY_GATE and COMMIT
(CP-WF-018 … CP-WF-022, CP-GATE-012/013/017/018).

COMMIT is the one place where every guard is re-evaluated on the server at request
time, and where S4 is called **only** after all of them hold (AC-GATE-02).  A
network failure after COMMIT is recovered by asking S4 about the idempotency key
before anything is re-submitted (CP-GATE-018, AC-EXE-05).
"""

from __future__ import annotations

import hashlib
from typing import Any

from factory_control import store as S
from factory_control.domain import verdicts as V
from factory_control.domain.enums import (
    CapabilityFreshness,
    ExecutionStatus,
    GateStatus,
    GateType,
    RunStatus,
    StageStatus,
    StageType,
)
from factory_control.domain.structure import run_line_plan
from factory_control.errors import (
    DriftDetectedError,
    GateNotApprovedError,
    InvalidStateError,
    NotFoundControlError,
)
from factory_control.foundation import find_artifact, get_artifact, new_id
from factory_control.models import DeploymentRun, Gate, Project, Stage, Workflow
from factory_control.ports.dto import BoundRef, Issue
from factory_control.progress import EventStreamProgressReporter, stage_cancel
from factory_control.runtime import ControlContext, Runtime
from factory_control.services.gates import (
    create_gate,
    environment_fingerprint,
    invalidate_gate,
    require_approved_deploy_gate,
)
from factory_control.services.project import ensure_writable
from factory_control.services.recording import record_change, write_audit
from factory_control.services.workflow import (
    _stage_run,
    record_step_result,
    refresh_derived_state,
    run_view,
    transition,
)


def _required_approvals(ctx: Any, environment_key: str) -> int:
    """How many signatures this environment demands before COMMIT (CP-GATE-012).

    The number is configured on the environment (S4's protection settings) and was already
    reported to both consoles as ``requiredApprovals``; what it did not do until now was decide
    anything, because the DEPLOY_GATE was created with a literal 1. An environment whose
    protection cannot be read falls back to 1 rather than losing the run, and says nothing about
    it -- the gate row keeps the number it used, so the answer is auditable either way.
    """
    try:
        protection = ctx.execution.get_protection(environment_key)
    except Exception:  # noqa: BLE001 - a missing protection row must not break the pipeline
        return 1
    raw = getattr(protection, "required_approvals", None)
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return value if value > 0 else 1


def build_context(idempotency_key: str, *, operator: str, now: Any) -> Any:
    """The deterministic build inputs a retry must resend (FR-CMP-001, BP-ASM-01).

    The seed comes from the idempotency key, so the same logical build always gets the
    same bytes; S2 is never allowed to invent either value on S1's behalf.
    """
    from factory_control.ports.dto import BuildContext

    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
    return BuildContext(
        built_at=now,
        seed=int.from_bytes(digest[:8], "big") % 2_147_483_647,
        requester=operator or "system",
        correlation_id=idempotency_key[:128],
    )


def _clock_now(runtime: Any) -> Any:
    return runtime.clock.now()


def _require_supported_environment(ctx: ControlContext, environment_key: str) -> None:
    """AC-CAP-02 / FR-CAP-001: an environment S4 cannot serve gets no Deployment Run.

    "the instance reports v2.39, or a version no shipped Profile covers" is decided by S4's
    connection test — S1 has no version knowledge of its own and must not gain any
    (P-10 Capability over Assumption).  What S1 must not do is ignore that answer: a Run is
    a promise to compile against and commit into this environment, and every later step
    (BIND_COMPILE, VALIDATE, COMMIT) would then be executed against an instance the Factory
    has never demonstrated it can talk to.
    """
    check = ctx.execution.get_last_connection_test(environment_key)
    if check is None:
        raise InvalidStateError(
            f"environment {environment_key} has never passed a connection test; run one "
            "before deploying to it (AC-CAP-02)",
            details={"environmentKey": environment_key, "reason": "NO_CONNECTION_TEST"},
        )
    if not check.ok:
        failed = sorted(
            {
                str(item.get("name"))
                for item in check.checks
                if isinstance(item, dict) and item.get("state") == "FAILED"
            }
        )
        raise InvalidStateError(
            f"environment {environment_key} does not answer its connection test; the "
            "version it runs is not one this Factory can deploy to (AC-CAP-02, FR-CAP-001)",
            details={
                "environmentKey": environment_key,
                "reason": "CONNECTION_TEST_FAILED",
                "failedChecks": failed,
                "version": check.version or check.dhis2_version,
                "detail": check.message,
            },
        )


def create_run(
    runtime: Runtime, workflow_id: str, *, environment_key: str, operator: str
) -> dict[str, Any]:
    """CP-WF-018: design line complete, no open run here, bound to the current version."""
    with runtime.transaction(operator=operator, workflow_id=workflow_id) as ctx:
        workflow = S.lock_workflow(ctx.session, workflow_id)
        project = ctx.session.get(Project, workflow.project_id)
        if project is None:
            raise NotFoundControlError(f"unknown project {workflow.project_id}")
        ensure_writable(project)
        if workflow.cancelled:
            raise InvalidStateError("the workflow is cancelled", details={"workflowId": workflow.id})

        design = [s for s in S.stages_of(ctx.session, workflow.id) if s.deployment_run_id is None]
        not_done = [
            s.stage_type
            for s in design
            if s.stage_type != StageType.HANDOFF.value
            and s.status not in (StageStatus.PASSED.value, StageStatus.SKIPPED.value)
        ]
        if not_done:
            raise InvalidStateError(
                "the design line is not complete; these stages are still open",
                details={"workflowId": workflow.id, "incompleteStages": not_done},
            )
        if S.open_run_for_environment(ctx.session, workflow.id, environment_key) is not None:
            raise InvalidStateError(
                f"environment {environment_key} already has an in-progress run (CP-BR-12)",
                details={"environmentKey": environment_key},
            )
        if ctx.execution.get_environment(environment_key) is None:
            raise InvalidStateError(
                f"unknown environmentKey {environment_key}: S4 does not have it",
                details={"environmentKey": environment_key},
            )
        registered = {t.environment_key for t in S.target_environments(ctx.session, project.id)}
        if environment_key not in registered:
            raise InvalidStateError(
                f"environment {environment_key} is not registered on this project",
                details={"environmentKey": environment_key, "registered": sorted(registered)},
            )
        _require_supported_environment(ctx, environment_key)

        protection = ctx.execution.get_protection(environment_key)
        semantics = ctx.blueprint.get_project_semantics(project.id)
        version = project.current_blueprint_version
        if not version:
            raise InvalidStateError(
                "the project has no Blueprint version to deploy", details={"projectId": project.id}
            )

        run = DeploymentRun(
            id=new_id("run"),
            workflow_id=workflow.id,
            environment_key=environment_key,
            blueprint_version=str(version),
            status=RunStatus.CREATED.value,
            outdated=False,
            started_at=ctx.clock.now(),
        )
        ctx.session.add(run)
        ctx.session.flush()

        plan = run_line_plan(
            delivery_mode=semantics.delivery_mode,
            has_fixture_spec=semantics.has_fixture_spec,
            environment_class=protection.environment_class,
        )
        stage_ids: dict[str, str] = {}
        for entry in plan:
            stage_id = new_id("sta")
            ctx.session.add(
                Stage(
                    id=stage_id,
                    workflow_id=workflow.id,
                    deployment_run_id=run.id,
                    stage_type=entry.stage_type.value,
                    status=entry.status.value,
                    attempt=0,
                )
            )
            stage_ids[entry.stage_type.value] = stage_id
        ctx.session.flush()

        if run.status == RunStatus.CREATED.value:
            run.status = RunStatus.IN_PROGRESS.value
        record_change(
            ctx,
            event_type="deployment_run.created",
            audit_event_type="cp.deployment_run.created",
            object_type="DeploymentRun",
            object_id=run.id,
            payload={
                "environmentKey": environment_key,
                "blueprintVersion": run.blueprint_version,
                "stages": sorted(stage_ids),
                "skipped": [entry.stage_type.value for entry in plan if entry.reason],
            },
        )
        refresh_derived_state(ctx, workflow)
        return {"run": run_view(run), "stages": sorted(stage_ids)}


def cancel_run(runtime: Runtime, run_id: str, *, operator: str) -> dict[str, Any]:
    """CP-WF-011 / CP-WF-015 rule 2: cancel only before COMMIT is under way."""
    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.lock_workflow(ctx.session, run.workflow_id)
        commit_stage = S.stage_by_type(
            ctx.session, workflow.id, StageType.COMMIT, run_id=run.id
        )
        if commit_stage is not None and commit_stage.status == StageStatus.RUNNING.value:
            raise InvalidStateError(
                "COMMIT is executing and cannot be cancelled; it will complete first",
                details={"stageId": commit_stage.id, "deploymentRunId": run.id},
            )
        run.status = RunStatus.CANCELLED.value
        run.completed_at = ctx.clock.now()
        for stage in S.stages_of(ctx.session, workflow.id, run_id=run.id):
            if stage.status in (
                StageStatus.RUNNING.value,
                StageStatus.WAITING_INPUT.value,
                StageStatus.WAITING_APPROVAL.value,
            ):
                transition(
                    ctx,
                    stage=stage,
                    target=StageStatus.INVALIDATED,
                    event_type="stage.invalidated",
                    payload={"reason": "RUN_CANCELLED", "deploymentRunId": run.id},
                )
        for gate in S.gates_of(ctx.session, workflow.id, status=GateStatus.WAITING):
            if gate.deployment_run_id == run.id:
                invalidate_gate(ctx, gate, reason="RUN_CANCELLED")
        ctx.session.flush()
        record_change(
            ctx,
            event_type="deployment_run.cancelled",
            audit_event_type="cp.deployment_run.cancelled",
            object_type="DeploymentRun",
            object_id=run.id,
            payload={"environmentKey": run.environment_key},
        )
        refresh_derived_state(ctx, workflow)
        return {"deploymentRunId": run.id, "status": run.status}


def execute_run_stage(
    runtime: Runtime, *, stage_id: str, operator: str, idempotency_key: str
) -> dict[str, Any]:
    """Run the R-step behind a stage (CP-WF-021 order, CP-WF-022 freshness)."""
    with runtime.transaction(operator=operator) as ctx:
        stage = S.get_stage(ctx.session, stage_id)
        run = ctx.session.get(DeploymentRun, stage.deployment_run_id or "")
        workflow = S.get_workflow(ctx.session, stage.workflow_id)
        project = ctx.session.get(Project, workflow.project_id)
        if run is None or project is None:
            raise NotFoundControlError(f"run or project missing for stage {stage_id}")
        environment_key = run.environment_key
        stage_type = StageType(stage.stage_type)

    _ensure_capability_fresh(runtime, environment_key=environment_key, stage_id=stage_id, operator=operator)

    reporter = EventStreamProgressReporter(
        workflow_id=workflow.id,
        session_factory=runtime.session_factory,
        kind="execution",
        stage_id=stage.id,
        execution_id=None,
        retention_days=_retention(runtime),
    )

    if stage_type is StageType.BIND_COMPILE:
        return _r1_bind(runtime, stage=stage, run=run, project=project, workflow=workflow,
                        environment_key=environment_key, idempotency_key=idempotency_key, reporter=reporter,
                        operator=operator)
    if stage_type is StageType.STATIC_VALIDATION:
        return _r2_static(runtime, stage=stage, run=run, project=project, workflow=workflow,
                          idempotency_key=idempotency_key, reporter=reporter, operator=operator)
    if stage_type is StageType.DHIS2_VALIDATE:
        return _r3_validate(runtime, stage=stage, run=run, project=project, workflow=workflow,
                            environment_key=environment_key, idempotency_key=idempotency_key, reporter=reporter,
                            operator=operator)
    if stage_type is StageType.POST_VERIFY:
        return _r5_post_verify(runtime, stage=stage, run=run, project=project, workflow=workflow,
                               environment_key=environment_key, idempotency_key=idempotency_key, reporter=reporter,
                               operator=operator)
    if stage_type is StageType.FIXTURE_IMPORT:
        return _r6_fixtures(runtime, stage=stage, run=run, project=project, workflow=workflow,
                            environment_key=environment_key, idempotency_key=idempotency_key, reporter=reporter,
                            operator=operator)
    if stage_type is StageType.COMMIT:
        raise InvalidStateError(
            "COMMIT is a separately authorised action; call the commit endpoint",
            stage=stage_type.value,
            details={"stageId": stage.id, "deploymentRunId": run.id},
        )
    raise InvalidStateError(f"unknown run stage {stage_type.value}", details={"stageId": stage.id})


def _retention(runtime: Runtime) -> int:
    with runtime.transaction(operator="system") as ctx:
        return ctx.settings.event_retention_days()


def _change_stats(ctx: Any, project_id: str, version: str | None, environment_key: str) -> dict[str, int]:
    """Create / update / delete / out-of-managed-scope counts for one compiled payload."""
    counts = {"create": 0, "update": 0, "delete": 0, "externalReferences": 0}
    if not version:
        return counts
    label = {"CREATE": "create", "UPDATE": "update", "DEPRECATED": "delete"}
    for entry in ctx.blueprint.get_registry_entries(project_id, version, environment_key):
        disposition = str(getattr(entry, "disposition", "") or "").upper()
        if disposition in label:
            counts[label[disposition]] += 1
        if not bool(getattr(entry, "managed", False)):
            # An out-of-scope reference counts even when the object is also created or
            # updated: it is the number UI-DEP-002 warns the operator about.
            counts["externalReferences"] += 1
    return counts


def _record_capability_freshness(
    runtime: Runtime, *, stage_id: str, operator: str, freshness: Any
) -> None:
    """Keep the profile verdict on the step that asked for it (S5IF-FLD05)."""
    record_step_result(
        runtime,
        stage_id=stage_id,
        operator=operator,
        capabilityFreshness={
            "state": freshness.state,
            "artifactId": freshness.capability_profile_artifact_id,
            "version": freshness.version,
            "profileVersion": freshness.profile_version,
            "dhis2Version": freshness.dhis2_version,
            "probedAt": freshness.probed_at.isoformat() if freshness.probed_at else None,
            "stale": bool(freshness.stale),
        },
    )


def _ensure_capability_fresh(
    runtime: Runtime, *, environment_key: str, stage_id: str, operator: str
) -> None:
    """CP-WF-022: stale capability or a changed version sends the Run back to R1."""
    freshness = runtime.execution.check_capability_freshness(environment_key)
    _record_capability_freshness(runtime, stage_id=stage_id, operator=operator, freshness=freshness)
    if freshness.state in (CapabilityFreshness.EXPIRED.value, CapabilityFreshness.VERSION_CHANGED.value):
        with runtime.transaction(operator=operator) as ctx:
            stage = S.get_stage(ctx.session, stage_id)
            run = ctx.session.get(DeploymentRun, stage.deployment_run_id or "")
            if run is not None:
                bind = S.stage_by_type(ctx.session, run.workflow_id, StageType.BIND_COMPILE, run_id=run.id)
                if bind is not None and bind.id != stage.id:
                    transition(
                        ctx,
                        stage=bind,
                        target=StageStatus.READY,
                        event_type="stage.rerun",
                        payload={"reason": f"CAPABILITY_{freshness.state}", "fromStageId": stage.id},
                    )
                    run.status = RunStatus.IN_PROGRESS.value
                    ctx.session.flush()
        raise InvalidStateError(
            f"capability profile is {freshness.state}; the run returns to BIND_COMPILE",
            details={
                "environmentKey": environment_key,
                "freshness": freshness.state,
                "restartStage": StageType.BIND_COMPILE.value,
            },
        )


def _r1_bind(
    runtime: Runtime,
    *,
    stage: Stage,
    run: DeploymentRun,
    project: Project,
    workflow: Workflow,
    environment_key: str,
    idempotency_key: str,
    reporter: EventStreamProgressReporter,
    operator: str,
) -> dict[str, Any]:
    """R1: discover the target environment, then compile for it (CP-WF-028)."""
    with runtime.transaction(operator="system") as ctx:
        refs = ctx.blueprint.registry_object_refs(project.id, run.blueprint_version, environment_key)
        # CP-CR-002: the request this Run implements is the project's IN_PROGRESS one — `start` is
        # what moves APPROVED there, and CP-BR-12 keeps at most one.  Testing the fetched row for
        # APPROVED could therefore never match, so no compile was ever told which request authorized
        # it, and CP-CR-005's deletion candidates never left S1: BP-CMP-026 admits a candidate only
        # to a compile that carries both the request id and its list.  An empty candidate list is
        # harmless — S2 still requires each entry to be DEPRECATED, unreferenced and data-free.
        change_request = S.in_progress_change_request(ctx.session, project.id)
        change_request_id = change_request.id if change_request is not None else None
        deletion_candidates = [
            {"businessKey": str(key)} for key in (change_request.deletion_candidates or [])
        ] if change_request is not None else []

    capability_id = runtime.execution.probe_capabilities(environment_key, idempotency_key=f"{idempotency_key}:cap")
    discovery = runtime.execution.discover(
        environment_key, project.id, "RUN_TARGET", refs,
        idempotency_key=f"{idempotency_key}:disc", progress=reporter, cancel=stage_cancel(),
    )
    build = runtime.blueprint.compile_for_environment(
        project.id, run.blueprint_version, environment_key,
        discovery.discovery_result_artifact_id, discovery.capability_profile_artifact_id or capability_id,
        change_request_id,
        build_context(f"{idempotency_key}:compile", operator=operator, now=_clock_now(runtime)),
        progress=reporter, cancel=stage_cancel(), idempotency_key=f"{idempotency_key}:compile",
        deletion_candidates=deletion_candidates,
    )

    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        run = reload_run(ctx, run)
        outputs = sorted({v for v in (build.artifact_ids or {}).values() if v})
        if any(i.is_blocking for i in build.issues):
            transition(
                ctx, stage=stage, target=StageStatus.FAILED, event_type="stage.failed",
                payload={"blocking": [i.model_dump() for i in build.issues if i.is_blocking]},
                failure_source_stage=_source_of(build.issues) or StageType.MAPPING.value,
            )
            run.status = RunStatus.FAILED.value
            ctx.session.flush()
            _stage_run(
                ctx, stage, agent_run_ids=[], idempotency_key=idempotency_key,
                result={"blockingIssueCount": sum(1 for i in build.issues if i.is_blocking)},
            )
            return {"status": StageStatus.FAILED.value, "blocking": sum(1 for i in build.issues if i.is_blocking)}
        _stage_run(ctx, stage, agent_run_ids=[], execution_ids=[], build_ids=[build.build_id or ""],
                   idempotency_key=idempotency_key, output_artifact_ids=outputs,
                   result={
                       "status": "BOUND",
                       "payloadHash": build.payload_hash,
                       # CP-UI-012: the console's change counts, taken once from the
                       # Registry dispositions this Run compiles.  Recorded here rather
                       # than recomputed on every paint, because the summary must stay
                       # free of round-trips (CP-NFR-03).
                       "changeStats": _change_stats(
                           ctx, project.id, run.blueprint_version, environment_key
                       ),
                   })
        run.validate_payload_hash = None
        passed = transition(
            ctx, stage=stage, target=StageStatus.PASSED, event_type="stage.passed",
            payload={"buildId": build.build_id, "environmentKey": environment_key},
            output_artifact_ids=outputs,
        )
        from factory_control.services.workflow import complete_stage_as_passed

        complete_stage_as_passed(ctx, stage=passed, output_artifact_ids=outputs, source="R1_OK")
        refresh_derived_state(ctx, workflow)
        return {"status": StageStatus.PASSED.value, "buildId": build.build_id, "artifacts": outputs}


def _announce_execution(
    ctx: ControlContext,
    *,
    stage: Stage | None,
    run: DeploymentRun,
    environment_key: str,
    outcome: Any,
    succeeded: bool,
) -> None:
    """C-02 / 19.2.3: S5's console follows an execution through execution.* events.

    21.4 requires the start and the completion of VALIDATE and of COMMIT to be audited.
    Both ends are appended once the call has returned, in start-then-end order, because S4's
    execution id — the thing an event has to name — only exists after S4 created it; S4's own
    ``dx.*`` records carry the instance side of the same execution (00 doc 4.1).
    """
    execution_id = getattr(outcome, "execution_id", None) or run.id
    common = {
        "executionId": execution_id,
        "environmentKey": environment_key,
        "runId": run.id,
        "stageType": stage.stage_type if stage is not None else None,
        "payloadHash": getattr(outcome, "payload_hash", None),
    }
    record_change(
        ctx,
        event_type="execution.started",
        audit_event_type="cp.execution.started",
        object_type="Execution",
        object_id=str(execution_id),
        # the commit path opens its transaction without a workflow bound (it is addressed by
        # run id), so the workflow has to be named here or the event has nothing to join onto
        workflow_id=run.workflow_id,
        payload=dict(common),
    )
    if succeeded:
        record_change(
            ctx,
            event_type="execution.completed",
            audit_event_type="cp.execution.completed",
            object_type="Execution",
            object_id=str(execution_id),
            workflow_id=run.workflow_id,
            payload={**common, "status": getattr(outcome, "status", None)},
        )
    else:
        record_change(
            ctx,
            event_type="execution.failed",
            audit_event_type="cp.execution.failed",
            object_type="Execution",
            object_id=str(execution_id),
            workflow_id=run.workflow_id,
            payload={
                **common,
                "issues": [issue.model_dump() for issue in getattr(outcome, "issues", [])][:20],
            },
        )


def _announce_run_failed(ctx: ControlContext, *, run: DeploymentRun, stage: Stage | None, reason: str) -> None:
    """CP-EVT-007: 19.2.3 names ``deployment_run.failed`` and S5's console lists on it.

    Setting ``RunStatus.FAILED`` without an event left the run failing silently to every
    subscriber: the REST read was correct, but nothing told the open console, and C-02 makes
    the event the notification channel.
    """
    if run.status != RunStatus.FAILED.value:
        return
    record_change(
        ctx,
        event_type="deployment_run.failed",
        audit_event_type="cp.deployment_run.failed",
        object_type="DeploymentRun",
        object_id=run.id,
        workflow_id=run.workflow_id,
        payload={
            "runId": run.id,
            "environmentKey": run.environment_key,
            "blueprintVersion": run.blueprint_version,
            "stageType": stage.stage_type if stage is not None else None,
            "reason": reason,
        },
    )


def _source_of(issues: list[Issue]) -> str | None:
    for issue in issues:
        if issue.source_stage:
            return issue.source_stage
    return None


def _r2_static(
    runtime: Runtime,
    *,
    stage: Stage,
    run: DeploymentRun,
    project: Project,
    workflow: Workflow,
    idempotency_key: str,
    reporter: EventStreamProgressReporter,
    operator: str,
) -> dict[str, Any]:
    """R2: static and cross-artifact validation, then PACKAGE_GATE (CP-GATE-001)."""
    build_id = _latest_build_id(runtime, stage)
    validation = runtime.blueprint.validate_build(build_id, progress=reporter, cancel=stage_cancel(),
                                                  idempotency_key=f"{idempotency_key}:validate")
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        run = reload_run(ctx, run)
        outputs = [a for a in [validation.validation_report_artifact_id, *(validation.artifact_ids or {}).values()] if a]
        # 19.2.3 / C-02: a validation run has an end, whether or not it found anything.
        # S5's Build and Deployments views refresh on this event rather than polling.
        record_change(
            ctx,
            event_type="validation.completed",
            audit_event_type="cp.validation.completed",
            object_type="Validation",
            object_id=validation.validation_report_artifact_id or build_id,
            payload={
                "buildId": build_id,
                "runId": run.id,
                "environmentKey": run.environment_key,
                "blocking": validation.blocking_count,
                "warnings": len([i for i in validation.issues if not i.is_blocking]),
                "reportArtifactId": validation.validation_report_artifact_id,
            },
        )
        if validation.blocking_count:
            transition(
                ctx, stage=stage, target=StageStatus.FAILED, event_type="stage.failed",
                payload={"blocking": validation.blocking_count, "issues": [i.model_dump() for i in validation.issues]},
                failure_source_stage=_source_of(validation.issues) or stage.stage_type,
            )
            run.status = RunStatus.FAILED.value
            ctx.session.flush()
            _announce_run_failed(ctx, run=run, stage=stage, reason="STATIC_VALIDATION_BLOCKING")
            _stage_run(
                ctx, stage, agent_run_ids=[], idempotency_key=idempotency_key,
                result={"blockingIssueCount": validation.blocking_count},
            )
            return {"status": StageStatus.FAILED.value, "blocking": validation.blocking_count}

        waiting = transition(
            ctx, stage=stage, target=StageStatus.WAITING_APPROVAL, event_type="stage.waiting_approval",
            payload={"buildId": build_id, "warnings": len(validation.issues)},
            output_artifact_ids=sorted(set(outputs)),
        )
        refs = _package_bound_refs(ctx, project, outputs)
        gate = create_gate(
            ctx,
            gate_type=GateType.PACKAGE_GATE,
            stage=waiting,
            bound_refs=refs,
            warnings=[i.model_dump() for i in validation.issues if not i.is_blocking],
            risk_summary_ref={"source": "S2", "validationReport": validation.validation_report_artifact_id},
            run=run,
            blueprint_ref={"version": run.blueprint_version, "buildId": build_id},
        )
        if run.status == RunStatus.IN_PROGRESS.value:
            run.status = RunStatus.WAITING_APPROVAL.value
        ctx.session.flush()
        return {"status": StageStatus.WAITING_APPROVAL.value, "gateId": gate.id, "warnings": len(gate.warnings or [])}


def _package_bound_refs(ctx: ControlContext, project: Project, artifact_ids: list[str]) -> list[BoundRef]:
    refs: list[BoundRef] = []
    for artifact_id in artifact_ids:
        artifact = find_artifact(artifact_id, session=ctx.session)
        if artifact is None:
            continue
        refs.append(BoundRef(refType="ARTIFACT", refId=artifact.id, contentHash=artifact.content_hash))
    return refs


def _latest_build_id(runtime: Runtime, stage: Stage) -> str:
    """The build R2 validates — which is R1's build, not one of this stage's own.

    STATIC_VALIDATION records no build, so looking only in its own stage runs found nothing
    and the answer fell back to an invented ``bld-<environment>``.  Real S2 rejected it
    (``unknown build bld-sbtest``); the fakes accepted any string, so the Run line passed in
    tests and died the first time it was run against the actual library (CP-WF-028).
    """
    with runtime.transaction(operator="system") as ctx:
        candidates = [stage]
        bind = S.stage_by_type(ctx.session, stage.workflow_id, StageType.BIND_COMPILE, run_id=stage.deployment_run_id)
        if bind is not None:
            candidates.append(bind)
        for row in candidates:
            for stage_run in reversed(list(S.stage_runs(ctx.session, row.id) or [])):
                ids = [build_id for build_id in (stage_run.build_ids or []) if build_id]
                if ids:
                    return ids[-1]
        run = ctx.session.get(DeploymentRun, stage.deployment_run_id)
        raise InvalidStateError(
            "no build was recorded for this Run, so there is nothing to validate",
            details={
                "stageId": stage.id,
                "deploymentRunId": stage.deployment_run_id,
                "environmentKey": run.environment_key if run else None,
            },
        )


def _r3_validate(
    runtime: Runtime,
    *,
    stage: Stage,
    run: DeploymentRun,
    project: Project,
    workflow: Workflow,
    environment_key: str,
    idempotency_key: str,
    reporter: EventStreamProgressReporter,
    operator: str,
) -> dict[str, Any]:
    """R3: execution plan, drift check, snapshot and DHIS2 VALIDATE → DEPLOY_GATE."""
    package_artifact_id, package_hash = _package_for_run(runtime, run)
    outcome = runtime.execution.prepare_and_validate(
        environment_key, project.id, package_artifact_id, package_hash,
        operator=_operator(runtime, run), idempotency_key=f"{idempotency_key}:validate",
        progress=reporter, cancel=stage_cancel(),
    )
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        run = reload_run(ctx, run)
        outputs = [a for a in [outcome.execution_plan_artifact_id, outcome.snapshot_artifact_id, outcome.import_report_artifact_id] if a]
        passed_validate = outcome.passed and not any(i.is_blocking for i in outcome.issues)
        _announce_execution(
            ctx,
            stage=stage,
            run=run,
            environment_key=environment_key,
            outcome=outcome,
            succeeded=passed_validate,
        )
        if not passed_validate:
            transition(
                ctx, stage=stage, target=StageStatus.FAILED, event_type="stage.failed",
                payload={"issues": [i.model_dump() for i in outcome.issues if i.is_blocking]},
                failure_source_stage=_source_of(outcome.issues) or StageType.MAPPING.value,
                output_artifact_ids=outputs,
            )
            _stage_run(
                ctx, stage, agent_run_ids=[], idempotency_key=idempotency_key,
                result={"blockingIssueCount": sum(1 for i in outcome.issues if i.is_blocking)},
            )
            return {"status": StageStatus.FAILED.value, "issues": len(outcome.issues)}

        run = reload_run(ctx, run)
        run.validate_payload_hash = outcome.payload_hash
        run.validate_operator = operator
        waiting = transition(
            ctx, stage=stage, target=StageStatus.WAITING_APPROVAL, event_type="stage.waiting_approval",
            payload={"payloadHash": outcome.payload_hash, "plan": outcome.execution_plan_artifact_id},
            output_artifact_ids=outputs,
        )
        refs = [BoundRef(refType="ARTIFACT", refId=package_artifact_id, contentHash=package_hash or "")]
        for artifact_id in outputs:
            artifact = find_artifact(artifact_id, session=ctx.session)
            if artifact is not None:
                refs.append(BoundRef(refType="ARTIFACT", refId=artifact.id, contentHash=artifact.content_hash))
        refs.append(
            BoundRef(
                refType="ENVIRONMENT",
                refId=environment_key,
                contentHash=environment_fingerprint(ctx, environment_key),
            )
        )

        gate = create_gate(
            ctx,
            gate_type=GateType.DEPLOY_GATE,
            stage=waiting,
            bound_refs=refs,
            warnings=[i.model_dump() for i in outcome.issues if not i.is_blocking],
            risk_summary_ref={"source": "S4", "executionPlan": outcome.execution_plan_artifact_id,
                              "snapshot": outcome.snapshot_artifact_id},
            confirmation_items=[
                {"id": str(c.get("id", "OPTION")), "label": str(c.get("label", "")), "required": True}
                for c in (outcome.requires_confirmation_items or [])
            ],
            run=run,
            # CP-GATE-012 read "所需审批数均为 1" while the environment already carried a
            # configured count, so PRODUCTION could reach COMMIT on one signature. The number the
            # operator sets on the environment is now the number the Gate enforces.
            required_approvals=_required_approvals(ctx, environment_key),
            blueprint_ref={"version": run.blueprint_version, "payloadHash": outcome.payload_hash},
        )
        if run.status == RunStatus.IN_PROGRESS.value:
            run.status = RunStatus.WAITING_APPROVAL.value
        ctx.session.flush()
        return {"status": StageStatus.WAITING_APPROVAL.value, "gateId": gate.id,
                "payloadHash": outcome.payload_hash, "confirmations": len(gate.confirmation_items or [])}


def reload_run(ctx: ControlContext, run: DeploymentRun) -> DeploymentRun:
    """Re-read the Run inside *this* transaction before writing to it.

    A Run carried over from a closed unit of work is detached, and a detached write
    would silently drop exactly the fields COMMIT re-checks — the validated payload
    hash among them (CP-GATE-017).
    """
    return S.get_run(ctx.session, run.id)


def _package_for_run(runtime: Runtime, run: DeploymentRun) -> tuple[str, str | None]:
    with runtime.transaction(operator="system") as ctx:
        bind = S.stage_by_type(ctx.session, run.workflow_id, StageType.BIND_COMPILE, run_id=run.id)
        if bind is None:
            raise InvalidStateError(
                "BIND_COMPILE has not produced a package for this run",
                details={"deploymentRunId": run.id},
            )
        ids = [i for i in (bind.output_artifact_ids or [])]
        package = next(
            (i for i in ids if _artifact_type(ctx, i) == "METADATA_PACKAGE"),
            ids[0] if ids else None,
        )
        if package is None:
            raise InvalidStateError("no package artifact to deploy", details={"deploymentRunId": run.id})
        artifact = find_artifact(package, session=ctx.session)
        return package, artifact.content_hash if artifact else None


def _artifact_type(ctx: ControlContext, artifact_id: str) -> str | None:
    artifact = get_artifact(artifact_id, session=ctx.session)
    return artifact.type if artifact else None


def _operator(runtime: Runtime, run: DeploymentRun) -> str:
    return run.commit_operator or run.validate_operator or "system"


def _probe_drift(runtime: Runtime, run_id: str, *, operator: str) -> Any:
    """Ask S4 about drift with no unit of work open, so the transaction can answer for it.

    ``check_drift`` reads the target instance and S4 marks it ``@external_io``; calling it from
    inside the pre-flight below raised ``TRANSACTION_MISUSE`` out of a 500 on the first real
    COMMIT (00 doc 6.2, CP-GATE-017).  The gate refusal is evaluated here too, in the same
    order, so a commit pressed too early still answers GATE_NOT_APPROVED rather than whatever
    the package lookup happens to complain about.
    """
    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        require_approved_deploy_gate(ctx, run)
        environment_key = run.environment_key
        # nested exactly as the pre-flight below nests it: the package lookup opens its own
        # read, and the Run must still be attached when it does.
        package_artifact_id, _package_hash = _package_for_run(runtime, run)
    return runtime.execution.check_drift(environment_key, package_artifact_id)


def commit_run(
    runtime: Runtime, run_id: str, *, operator: str, idempotency_key: str
) -> dict[str, Any]:
    """CP-GATE-017/018: full re-check, then S4 COMMIT, with key-based recovery."""
    drift = _probe_drift(runtime, run_id, operator=operator)
    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.lock_workflow(ctx.session, run.workflow_id)
        project = ctx.session.get(Project, workflow.project_id)
        if project is None:
            raise NotFoundControlError(f"unknown project {workflow.project_id}")
        ensure_writable(project)
        gate = require_approved_deploy_gate(ctx, run)

        # 1) three-way payload hash equality (CP-GATE-017, 总体 SRS FR-EXE-004)
        package_artifact_id, package_hash = _package_for_run(runtime, run)
        validated_hash = run.validate_payload_hash
        gate_hash = _gate_payload_hash(gate)
        if not (validated_hash and package_hash and gate_hash):
            raise GateNotApprovedError(
                "the deploy payload cannot be identified; run DHIS2_VALIDATE again",
                details={"deploymentRunId": run.id, "gateId": gate.id},
            )
        if not (validated_hash == gate_hash == package_hash):
            invalidate_gate(ctx, gate, reason="PAYLOAD_HASH_MISMATCH",
                            details={"validate": validated_hash, "gate": gate_hash, "current": package_hash})
            raise DriftDetectedError(
                "the payload to commit differs from what was validated and approved",
                details={
                    "deploymentRunId": run.id,
                    "validateHash": validated_hash,
                    "gateHash": gate_hash,
                    "currentHash": package_hash,
                },
            )

        # 2) protection attributes re-read at request time
        protection = ctx.execution.get_protection(run.environment_key)
        if protection.emergency_locked:
            raise V.environment_locked(protection, environment_key=run.environment_key)
        if not protection.within_commit_window:
            raise V.outside_commit_window(
                protection, now=ctx.clock.now(), environment_key=run.environment_key
            )
        if protection.environment_class.upper() == "PRODUCTION":
            _require_non_production_verified(ctx, run)

        # 3) drift check before committing — asked by _probe_drift with no session open
        if drift is None:
            raise InvalidStateError(
                "the drift check was not asked for this run",
                details={"deploymentRunId": run_id},
            )
        if drift.drifted:
            invalidate_gate(ctx, gate, reason="DRIFT_DETECTED", details={"objects": drift.objects})
            raise DriftDetectedError(
                "the target environment drifted after VALIDATE; re-run DHIS2_VALIDATE",
                details={"environmentKey": run.environment_key, "objects": drift.objects[:5]},
            )

        # 4) the COMMIT stage must be startable
        run = reload_run(ctx, run)
        stage = S.stage_by_type(ctx.session, workflow.id, StageType.COMMIT, run_id=run.id)
        if stage is not None and stage.status == StageStatus.MANUAL_CHECK.value:
            # A previous COMMIT could not be confirmed. Retrying must reconcile by
            # the same idempotency key instead of being refused (CP-GATE-018), and
            # MANUAL_CHECK has no LOCKED/READY edge back into the machine.
            recovery = ctx.execution.get_execution_by_key(idempotency_key)
            if recovery is not None and recovery.status == ExecutionStatus.COMPLETED.value:
                return _finalize_commit(
                    runtime, run_id=run_id, operator=operator, outcome=recovery,
                    idempotency_key=idempotency_key, recovered=True,
                )
            return {
                "deploymentRunId": run.id,
                "status": RunStatus.MANUAL_CHECK.value,
                "executionId": recovery.execution_id if recovery else run.execution_id,
                "stageId": stage.id,
                "needsManualCheck": True,
                "alreadyPending": True,
            }
        if stage is None or stage.status not in (StageStatus.READY.value, StageStatus.LOCKED.value):
            raise InvalidStateError(
                "the COMMIT step is not ready; PACKAGE_GATE or DEPLOY_GATE is still open",
                details={"deploymentRunId": run.id, "stageStatus": stage.status if stage else None},
            )
        run.commit_operator = operator
        transition(ctx, stage=stage, target=StageStatus.RUNNING, event_type="stage.started",
                   payload={"idempotencyKey": idempotency_key, "gateId": gate.id})
        run.status = RunStatus.IN_PROGRESS.value
        ctx.session.flush()

    # --- external I/O: no unit of work open ---------------------------------
    recovery = runtime.execution.get_execution_by_key(idempotency_key)
    if recovery is not None and recovery.status == ExecutionStatus.COMPLETED.value:
        return _finalize_commit(runtime, run_id=run_id, operator=operator, outcome=recovery,
                                idempotency_key=idempotency_key, recovered=True)

    if recovery is not None and recovery.status == ExecutionStatus.NEEDS_MANUAL_CHECK.value:
        return _mark_manual_check(runtime, run_id=run_id, operator=operator, outcome=recovery,
                                  idempotency_key=idempotency_key)

    reporter = EventStreamProgressReporter(
        workflow_id=run.workflow_id, session_factory=runtime.session_factory, kind="execution",
        execution_id=None, retention_days=_retention(runtime),
    )
    # COMMIT is the one provider call that is deliberately not cancellable (FR-WF-004,
    # CP-WF-011): an irreversible request already in flight must run to its end and be
    # reconciled against the instance, never abandoned because somebody cancelled the
    # workflow meanwhile -- the environment lock is held until the outcome is known.
    outcome = runtime.execution.commit(
        run.environment_key, _project_id_of(runtime, run), package_artifact_id,
        str(package_hash), operator=operator, idempotency_key=idempotency_key,
        progress=reporter, cancel=None,
    )
    if outcome.status == ExecutionStatus.COMPLETED.value:
        return _finalize_commit(runtime, run_id=run_id, operator=operator, outcome=outcome,
                                idempotency_key=idempotency_key, recovered=False)
    if outcome.status == ExecutionStatus.NEEDS_MANUAL_CHECK.value:
        return _mark_manual_check(runtime, run_id=run_id, operator=operator, outcome=outcome,
                                  idempotency_key=idempotency_key)
    return _fail_commit(runtime, run_id=run_id, operator=operator, outcome=outcome,
                        idempotency_key=idempotency_key)


def _project_id_of(runtime: Runtime, run: DeploymentRun) -> str:
    with runtime.transaction(operator="system") as ctx:
        workflow = S.get_workflow(ctx.session, run.workflow_id)
        return workflow.project_id


def _gate_payload_hash(gate: Gate) -> str | None:
    ref = dict(gate.blueprint_ref or {})
    return ref.get("payloadHash")


def _require_non_production_verified(ctx: ControlContext, run: DeploymentRun) -> None:
    for sibling in S.runs_of(ctx.session, run.workflow_id):
        if sibling.id == run.id or sibling.status != RunStatus.VERIFIED.value:
            continue
        if sibling.blueprint_version != run.blueprint_version:
            continue
        if ctx.execution.get_protection(sibling.environment_key).environment_class.upper() != "PRODUCTION":
            return
    raise V.prior_verification_missing(
        environment_key=run.environment_key, blueprint_version=run.blueprint_version
    )


def _finalize_commit(
    runtime: Runtime, *, run_id: str, operator: str, outcome: Any, idempotency_key: str, recovered: bool
) -> dict[str, Any]:
    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.get_workflow(ctx.session, run.workflow_id)
        run = reload_run(ctx, run)
        stage = S.stage_by_type(ctx.session, workflow.id, StageType.COMMIT, run_id=run.id)
        outputs = [a for a in [outcome.report_artifact_id, outcome.import_report_artifact_id] if a]
        run.execution_id = outcome.execution_id
        run.status = RunStatus.COMMITTED.value
        _announce_execution(
            ctx, stage=stage, run=run, environment_key=run.environment_key,
            outcome=outcome, succeeded=True,
        )
        if stage is not None and stage.status == StageStatus.RUNNING.value:
            passed = transition(
                ctx, stage=stage, target=StageStatus.PASSED, event_type="stage.passed",
                payload={"executionId": outcome.execution_id, "recovered": recovered},
                output_artifact_ids=outputs,
            )
            _stage_run(ctx, passed, agent_run_ids=[], execution_ids=[outcome.execution_id or ""],
                       build_ids=[], idempotency_key=idempotency_key, output_artifact_ids=outputs,
                       result={"status": outcome.status, "recovered": recovered})
            from factory_control.services.workflow import complete_stage_as_passed

            complete_stage_as_passed(ctx, stage=passed, output_artifact_ids=outputs, source="COMMIT")
        write_audit(
            ctx,
            event_type="cp.deployment_run.committed",
            object_type="DeploymentRun",
            object_id=run.id,
            after={"executionId": outcome.execution_id, "recovered": recovered, "artifacts": outputs},
        )
        from factory_control.services.notifications import notify_execution

        notify_execution(
            ctx, ok=True, execution_id=outcome.execution_id, environment_key=run.environment_key,
            detail=f"commit {outcome.execution_id} completed" + (" (recovered)" if recovered else ""),
        )
        refresh_derived_state(ctx, workflow)
        return {"deploymentRunId": run.id, "status": run.status, "executionId": outcome.execution_id,
                "stageId": stage.id if stage is not None else None,
                "recovered": recovered, "artifacts": outputs}


def _mark_manual_check(
    runtime: Runtime, *, run_id: str, operator: str, outcome: Any, idempotency_key: str
) -> dict[str, Any]:
    """CP-WF-008: an unconfirmable COMMIT must not be retried blindly."""
    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.get_workflow(ctx.session, run.workflow_id)
        run = reload_run(ctx, run)
        stage = S.stage_by_type(ctx.session, workflow.id, StageType.COMMIT, run_id=run.id)
        run.status = RunStatus.MANUAL_CHECK.value
        run.execution_id = outcome.execution_id
        if stage is not None:
            transition(
                ctx, stage=stage, target=StageStatus.MANUAL_CHECK, event_type="stage.manual_check",
                payload={"executionId": outcome.execution_id, "reason": "COMMIT_OUTCOME_UNCONFIRMED"},
            )
        from factory_control.services.notifications import notify_stage_event

        if stage is not None:
            notify_stage_event(ctx, stage=stage, kind="manual_check", detail="COMMIT outcome unconfirmed")
        refresh_derived_state(ctx, workflow)
        return {"deploymentRunId": run.id, "status": RunStatus.MANUAL_CHECK.value,
                "executionId": outcome.execution_id, "stageId": stage.id if stage is not None else None,
                "needsManualCheck": True}


def _fail_commit(
    runtime: Runtime, *, run_id: str, operator: str, outcome: Any, idempotency_key: str
) -> dict[str, Any]:
    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.get_workflow(ctx.session, run.workflow_id)
        run = reload_run(ctx, run)
        stage = S.stage_by_type(ctx.session, workflow.id, StageType.COMMIT, run_id=run.id)
        run.status = RunStatus.FAILED.value
        if stage is not None:
            transition(
                ctx, stage=stage, target=StageStatus.FAILED, event_type="stage.failed",
                payload={"executionId": outcome.execution_id, "issues": [i.model_dump() for i in outcome.issues]},
                failure_source_stage=_source_of(outcome.issues) or StageType.MAPPING.value,
            )
        _announce_execution(
            ctx, stage=stage, run=run, environment_key=run.environment_key,
            outcome=outcome, succeeded=False,
        )
        _announce_run_failed(ctx, run=run, stage=stage, reason="COMMIT_FAILED")
        from factory_control.services.notifications import notify_execution

        notify_execution(ctx, ok=False, execution_id=outcome.execution_id,
                         environment_key=run.environment_key, detail="commit failed")
        refresh_derived_state(ctx, workflow)
        return {"deploymentRunId": run.id, "status": run.status, "executionId": outcome.execution_id,
                "stageId": stage.id if stage is not None else None}


def _r5_post_verify(
    runtime: Runtime, *, stage: Stage, run: DeploymentRun, project: Project, workflow: Workflow,
    environment_key: str, idempotency_key: str, reporter: EventStreamProgressReporter,
    operator: str,
) -> dict[str, Any]:
    package_artifact_id, package_hash = _package_for_run(runtime, run)
    outcome = runtime.execution.post_verify(
        environment_key, project.id, package_artifact_id, run.execution_id,
        operator=_operator(runtime, run), idempotency_key=f"{idempotency_key}:verify",
        progress=reporter, cancel=stage_cancel(),
    )
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        run = reload_run(ctx, run)
        outputs = [outcome.report_artifact_id] if outcome.report_artifact_id else []
        if not outcome.consistent:
            transition(
                ctx, stage=stage, target=StageStatus.FAILED, event_type="stage.failed",
                payload={"issues": [i.model_dump() for i in outcome.issues]},
                failure_source_stage=_source_of(outcome.issues) or StageType.MAPPING.value,
                output_artifact_ids=outputs,
            )
            return {"status": StageStatus.FAILED.value}
        passed = transition(
            ctx, stage=stage, target=StageStatus.PASSED, event_type="stage.passed",
            payload={"consistent": True}, output_artifact_ids=outputs,
        )
        from factory_control.services.workflow import complete_stage_as_passed

        complete_stage_as_passed(ctx, stage=passed, output_artifact_ids=outputs, source="R5_OK")
        if _run_finished(ctx, run):
            _mark_run_verified(ctx, run, workflow)
        refresh_derived_state(ctx, workflow)
        return {"status": StageStatus.PASSED.value, "artifacts": outputs}


def _r6_fixtures(
    runtime: Runtime, *, stage: Stage, run: DeploymentRun, project: Project, workflow: Workflow,
    environment_key: str, idempotency_key: str, reporter: EventStreamProgressReporter,
    operator: str,
) -> dict[str, Any]:
    """R6: fixture VALIDATE then COMMIT, refused on PRODUCTION-class (CP-WF-002)."""
    protection = runtime.execution.get_protection(environment_key)
    if protection.environment_class.upper() == "PRODUCTION":
        raise InvalidStateError(
            "fixture import is forbidden on a PRODUCTION environment",
            details={"environmentKey": environment_key, "environmentClass": protection.environment_class},
        )
    fixture_artifact_id, _hash = _fixture_package_for_run(runtime, run)
    outcome = runtime.execution.import_fixtures(
        environment_key, project.id, fixture_artifact_id,
        operator=_operator(runtime, run), idempotency_key=f"{idempotency_key}:fixtures",
        progress=reporter, cancel=stage_cancel(),
    )
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        run = reload_run(ctx, run)
        outputs = [a for a in [outcome.report_artifact_id, outcome.import_report_artifact_id] if a]
        if outcome.status != ExecutionStatus.COMPLETED.value:
            transition(
                ctx, stage=stage, target=StageStatus.FAILED, event_type="stage.failed",
                payload={"executionId": outcome.execution_id},
                failure_source_stage=StageType.FIXTURE_IMPORT.value,
            )
            return {"status": StageStatus.FAILED.value}
        passed = transition(
            ctx, stage=stage, target=StageStatus.PASSED, event_type="stage.passed",
            payload={"executionId": outcome.execution_id}, output_artifact_ids=outputs,
        )
        from factory_control.services.workflow import complete_stage_as_passed

        complete_stage_as_passed(ctx, stage=passed, output_artifact_ids=outputs, source="R6_OK")
        if _run_finished(ctx, run):
            _mark_run_verified(ctx, run, workflow)
        refresh_derived_state(ctx, workflow)
        return {"status": StageStatus.PASSED.value, "executionId": outcome.execution_id}


def _fixture_package_for_run(runtime: Runtime, run: DeploymentRun) -> tuple[str, str | None]:
    with runtime.transaction(operator="system") as ctx:
        bind = S.stage_by_type(ctx.session, run.workflow_id, StageType.BIND_COMPILE, run_id=run.id)
        ids = list(bind.output_artifact_ids or []) if bind else []
        fixture = next((i for i in ids if _artifact_type(ctx, i) == "FIXTURE_PACKAGE"), None)
        if fixture is None:
            raise InvalidStateError(
                "this run has no FIXTURE_PACKAGE; FIXTURE_IMPORT should have been SKIPPED",
                details={"deploymentRunId": run.id},
            )
        artifact = find_artifact(fixture, session=ctx.session)
        return fixture, artifact.content_hash if artifact else None


def _run_finished(ctx: ControlContext, run: DeploymentRun) -> bool:
    stages = S.stages_of(ctx.session, run.workflow_id, run_id=run.id)
    return all(s.status in (StageStatus.PASSED.value, StageStatus.SKIPPED.value) for s in stages)


def _mark_run_verified(ctx: ControlContext, run: DeploymentRun, workflow: Workflow) -> None:
    if run.status in (RunStatus.VERIFIED.value,):
        return
    run.status = RunStatus.VERIFIED.value
    run.completed_at = ctx.clock.now()
    ctx.session.flush()
    record_change(
        ctx,
        event_type="deployment_run.completed",
        audit_event_type="cp.deployment_run.verified",
        object_type="DeploymentRun",
        object_id=run.id,
        payload={"environmentKey": run.environment_key, "blueprintVersion": run.blueprint_version},
    )


def _change_stats_view(recorded: dict[str, Any]) -> dict[str, int]:
    """The four counts CP-UI-012 asks for, zero while R1 has not run."""
    stored = dict(recorded.get("changeStats") or {})
    return {key: int(stored.get(key) or 0) for key in ("create", "update", "delete", "externalReferences")}


def _step_results(session: Any, stage_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The recorded step result per stage type, newest attempt wins — one query.

    Keyed by stage *type* because the console asks about roles ("what did R1 record"), and
    a rerun replaces the attempt rather than the stage.
    """
    from sqlalchemy import select

    from factory_control.models import Stage, StageRun

    if not stage_ids:
        return {}
    rows = session.execute(
        select(StageRun, Stage.stage_type)
        .join(Stage, Stage.id == StageRun.stage_id)
        .where(StageRun.stage_id.in_(stage_ids))
        .order_by(StageRun.attempt)
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for row, stage_type in rows:
        out[str(stage_type)] = dict(row.result or {})
    return out


def _latest_drift(results: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The most recently recorded drift verdict, without asking S4 again."""
    candidates = [dict(value["driftVerdict"]) for value in results.values() if value.get("driftVerdict")]
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("checkedAt") or ""))


def _passed(stage: Any) -> bool:
    return stage is not None and stage.status == StageStatus.PASSED.value


def _first_output(stage: Any) -> str | None:
    if stage is None:
        return None
    outputs = sorted(stage.output_artifact_ids or [])
    return outputs[0] if outputs else None


def _artifact_slot(stage: Any, artifact_type: str, session: Any) -> dict[str, Any] | None:
    """One artifact of a given type out of a stage's outputs, with what the console needs.

    `contentPurged` is the field that says whether the download is still there: FD-ART-010 keeps
    the row and its hash and flips that boolean, and reading a purged artifact raises.  The
    lifecycle `status` cannot say it — no `ArtifactStatus` member means "purged" — so it travels
    beside the flag rather than instead of it.
    """
    from factory_control.foundation import get_artifact

    artifact_id = _artifact_id_of(stage, artifact_type, session)
    if artifact_id is None:
        return None
    row = get_artifact(artifact_id, session=session)
    status = getattr(row, "status", None)
    return {
        "status": status.value if status is not None else None,
        "artifactId": artifact_id,
        "contentPurged": bool(getattr(row, "content_purged", False)),
    }


def _artifact_id_of(stage: Any, artifact_type: str, session: Any) -> str | None:
    from factory_control.foundation import get_artifact

    if stage is None:
        return None
    for artifact_id in stage.output_artifact_ids or []:
        row = get_artifact(artifact_id, session=session)
        if row is not None and row.type == artifact_type:
            return str(artifact_id)
    return None


def _package_of(ctx: Any, stage: Any) -> tuple[str | None, str | None]:
    """The METADATA_PACKAGE this Run compiled, and the hash it carries."""
    artifact_id = _artifact_id_of(stage, "METADATA_PACKAGE", ctx.session)
    if artifact_id is None:
        return None, None
    from factory_control.foundation import get_artifact

    row = get_artifact(artifact_id, session=ctx.session)
    return artifact_id, getattr(row, "content_hash", None)


def run_summary(runtime: Runtime, run_id: str, *, operator: str = "system") -> dict[str, Any]:
    """The deployment console in one request (S5IF-FLD05 【裁决】).

    No call in here may reach DHIS2.  Opening a panel is not a decision to talk to a
    target instance, and CP-NFR-03's P95 < 500 ms is measured on exactly this request.  So
    drift, capability freshness and the connection test are what S1 **recorded** the last
    time they ran, ``get_protection`` is the one live read the ruling allows (it queries
    S4's own database, and CP-WF-027 wants protection uncached for commit gating in any
    case), and the rest is S1's own Run, Gate and artifact index.  A re-check is a separate
    explicit action — ``POST /deployment-runs/{id}/drift-check``.
    """

    from factory_control.services.environments import last_connection_record
    from factory_control.services.gates import gate_view

    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.get_workflow(ctx.session, run.workflow_id)
        project = ctx.session.get(Project, workflow.project_id)
        stages = S.stages_of(ctx.session, run.workflow_id, run_id=run.id)
        gates = [g for g in S.gates_of(ctx.session, run.workflow_id) if g.deployment_run_id == run.id]
        by_type = {stage.stage_type: stage for stage in stages}
        results = _step_results(ctx.session, [stage.id for stage in stages])
        protection = ctx.execution.get_protection(run.environment_key)

        bind = by_type.get(StageType.BIND_COMPILE.value)
        freshness = dict(results.get(StageType.BIND_COMPILE.value, {}).get("capabilityFreshness") or {})
        drift = _latest_drift(results)
        package_artifact_id, package_hash = _package_of(ctx, bind)
        validate = by_type.get(StageType.DHIS2_VALIDATE.value)
        verify = by_type.get(StageType.POST_VERIFY.value)
        commit = by_type.get(StageType.COMMIT.value)
        deploy_gate = next((g for g in gates if g.type == GateType.DEPLOY_GATE.value), None)
        package_gate = next((g for g in gates if g.type == GateType.PACKAGE_GATE.value), None)
        warnings = [warning for gate in gates for warning in (gate.warnings or [])]

        return {
            "runId": run.id,
            "workflowId": run.workflow_id,
            "projectId": getattr(project, "id", None),
            "projectName": getattr(project, "name", None),
            "environmentKey": run.environment_key,
            "environmentClass": protection.environment_class,
            "blueprintVersion": run.blueprint_version,
            "outdated": run.outdated,
            "status": run.status,
            "dhis2Version": freshness.get("dhis2Version"),
            "profileVersion": freshness.get("profileVersion") or freshness.get("version"),
            "capabilityFreshness": {
                "state": freshness.get("state"),
                "probedAt": freshness.get("probedAt"),
                "artifactId": freshness.get("artifactId"),
            }
            if freshness
            else None,
            "connection": last_connection_record(runtime, run.environment_key),
            "drift": drift,
            "snapshot": _artifact_slot(validate, "SNAPSHOT", ctx.session),
            "validation": {
                "staticPassed": _passed(by_type.get(StageType.STATIC_VALIDATION.value)),
                "dhis2ValidatePassed": _passed(validate),
                "blockingCount": max(
                    (int(value.get("blockingIssueCount") or 0) for value in results.values()),
                    default=0,
                ),
                "warningCount": len(warnings),
                "reportArtifactId": _first_output(validate),
            },
            "approval": {
                "packageGate": package_gate.status if package_gate else None,
                "deployGate": deploy_gate.status if deploy_gate else None,
                "requiredApprovals": deploy_gate.required_approvals if deploy_gate else 1,
                "approvals": len(deploy_gate.approvals or []) if deploy_gate else 0,
            },
            "changeStats": _change_stats_view(results.get(StageType.BIND_COMPILE.value, {})),
            "diffReportArtifactId": _artifact_id_of(bind, "DIFF_REPORT", ctx.session),
            "maintenanceActions": [
                item
                for gate in gates
                for item in (gate.confirmation_items or [])
                if str(item.get("kind") or "") == "MAINTENANCE_ACTION"
            ],
            "payload": {"hash": package_hash or run.validate_payload_hash, "packageArtifactId": package_artifact_id},
            "commitWindow": {
                "withinWindow": protection.within_commit_window,
                "windows": list(protection.commit_windows or []),
                "timezone": protection.timezone,
                "emergencyLocked": protection.emergency_locked,
            },
            "verification": {
                "lastVerifiedAt": run.completed_at if run.status == RunStatus.VERIFIED.value else None,
                "postVerifyArtifactId": _first_output(verify),
                "consistent": results.get(StageType.POST_VERIFY.value, {}).get("consistent"),
            },
            "execution": {
                "executionId": run.execution_id,
                "status": commit.status if commit is not None else None,
                "reportArtifactId": _artifact_slot(commit, "EXECUTION_REPORT", ctx.session),
                "importReportArtifactId": _artifact_slot(commit, "DHIS2_IMPORT_REPORT", ctx.session),
            },
            "gates": [gate_view(g, S.approvals_of(ctx.session, g.id)) for g in gates],
            "stages": [
                {"id": s.id, "stageType": s.stage_type, "status": s.status, "attempt": s.attempt}
                for s in stages
            ],
            "evaluatedAt": ctx.clock.now().isoformat(),
        }


def check_run_drift(runtime: Runtime, run_id: str, *, operator: str = "system") -> dict[str, Any]:
    """Re-check drift on request, record the verdict, and void what it invalidates.

    CP-UI-013 / S5IF-FLD05: the console only ever shows the recorded verdict, so this
    action is the single way S4 is asked again — which keeps UI-DEP-002 honest about the
    cost and puts the wait where a user chose it (CP-NFR-03).  The answer is written onto
    the run's step record, pushed as an event, and a positive answer invalidates the
    DEPLOY_GATE, because the approved payload no longer describes the target (CP-GATE-005).
    """
    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.get_workflow(ctx.session, run.workflow_id)
        bind = S.stage_by_type(ctx.session, run.workflow_id, StageType.BIND_COMPILE, run_id=run.id)
        package_artifact_id, _hash = _package_of(ctx, bind)
        environment_key = run.environment_key
        stage_id = bind.id if bind is not None else ""
        project_id = workflow.project_id

    if not package_artifact_id:
        raise InvalidStateError(
            "the run has no compiled package to compare against yet",
            details={"deploymentRunId": run_id, "requires": StageType.BIND_COMPILE.value},
        )
    drift = runtime.execution.check_drift(environment_key, package_artifact_id)
    verdict: dict[str, Any] = {
        "runId": run_id,
        "drifted": bool(drift.drifted),
        "checkedAt": runtime.clock.now().isoformat(),
        "objects": list(drift.objects or []),
    }

    with runtime.transaction(
        operator=operator, project_id=project_id, workflow_id=workflow.id, stage_id=stage_id or None
    ) as ctx:
        step_run_id: str | None = None
        if stage_id:
            stage = ctx.session.get(Stage, stage_id)
            if stage is not None:
                step_run_id = _stage_run(
                    ctx,
                    stage,
                    agent_run_ids=[],
                    idempotency_key=f"drift:{run_id}",
                    result={"driftVerdict": verdict},
                ).id
        record_change(
            ctx,
            event_type="deployment_run.drift_checked",
            audit_event_type="cp.deployment_run.drift_checked",
            object_type="DeploymentRun",
            object_id=run_id,
            workflow_id=workflow.id,
            payload={
                "runId": run_id,
                "drifted": verdict["drifted"],
                "checkedAt": verdict["checkedAt"],
                "objects": verdict["objects"][:5],
            },
            after=verdict,
        )
        if verdict["drifted"]:
            gate = next(
                (
                    g
                    for g in S.gates_of(
                        ctx.session, workflow.id, gate_type=GateType.DEPLOY_GATE.value
                    )
                    if g.deployment_run_id == run_id
                ),
                None,
            )
            if gate is not None:
                invalidate_gate(
                    ctx,
                    gate,
                    reason="DRIFT_DETECTED",
                    details={"objects": verdict["objects"][:5]},
                )
    return {**verdict, "stageId": stage_id or None, "stepRunId": step_run_id or run_id}


def get_run_view(runtime: Runtime, run_id: str, *, operator: str = "system") -> dict[str, Any]:
    from factory_control.services.gates import gate_view

    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.get_workflow(ctx.session, run.workflow_id)
        stages = S.stages_of(ctx.session, run.workflow_id, run_id=run.id)
        gates = [g for g in S.gates_of(ctx.session, run.workflow_id) if g.deployment_run_id == run.id]
        project = ctx.session.get(Project, workflow.project_id)
        protection = ctx.execution.get_protection(run.environment_key)
        return {
            "run": _run_view(ctx, run, project=project, protection=protection),
            "stages": [
                {"id": s.id, "stageType": s.stage_type, "status": s.status, "attempt": s.attempt}
                for s in stages
            ],
            "gates": [gate_view(g, S.approvals_of(ctx.session, g.id)) for g in gates],
        }


def _run_view(ctx: Any, run: DeploymentRun, *, project: Any = None, protection: Any = None) -> dict[str, Any]:
    """S5IF-FLD06: the Run row alone cannot say the environment class or the VERIFIED time."""
    return run_view(
        run,
        project_id=getattr(project, "id", None),
        project_name=getattr(project, "name", None),
        environment_class=getattr(protection, "environment_class", None),
        last_verified_at=run.completed_at if run.status == RunStatus.VERIFIED.value else None,
    )


def list_runs(runtime: Runtime, workflow_id: str, *, operator: str = "system") -> list[dict[str, Any]]:
    with runtime.transaction(operator=operator) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        project = ctx.session.get(Project, workflow.project_id)
        classes: dict[str, Any] = {}
        runs = S.runs_of(ctx.session, workflow_id)
        for run in runs:
            if run.environment_key not in classes:
                classes[run.environment_key] = ctx.execution.get_protection(run.environment_key)
        return [_run_view(ctx, run, project=project, protection=classes.get(run.environment_key)) for run in runs]


def list_runs_across(
    runtime: Runtime,
    *,
    operator: str = "system",
    status: str | None = None,
    environment_key: str | None = None,
    outdated: bool | None = None,
    sort_by: str = "updatedAt",
    sort_dir: str = "DESC",
    limit: int = 50,
    offset: int = 0,
    only_project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Runs across every project, newest change first (S5IF-FLD04 【裁决】).

    ``projectName`` is S1's own; ``environmentClass`` is S4's and is never copied into
    ``cp`` (D-14, CP-API-007) — it is read per request through the 60-second display cache,
    which exists so a page of fifty rows is not fifty provider calls.
    """
    from sqlalchemy import select

    from factory_control.services.environments import environment_class

    with runtime.transaction(operator=operator) as ctx:
        rows, total = S.list_runs(
            ctx.session,
            status=status,
            environment_key=environment_key,
            outdated=outdated,
            sort_by=sort_by,
            descending=sort_dir != "ASC",
            limit=limit,
            offset=offset,
            only_project_ids=only_project_ids,
        )
        workflow_ids = list({run.workflow_id for run in rows})
        owner: dict[str, str] = {}
        if workflow_ids:
            for workflow_id, project_id in ctx.session.execute(
                select(Workflow.id, Workflow.project_id).where(Workflow.id.in_(workflow_ids))
            ).all():
                owner[str(workflow_id)] = str(project_id)
        project_ids = list({value for value in owner.values() if value})
        projects: dict[str, Project] = {}
        if project_ids:
            projects = {
                str(project.id): project
                for project in ctx.session.execute(
                    select(Project).where(Project.id.in_(project_ids))
                ).scalars()
            }
        items = [
            run_view(
                run,
                project_id=owner.get(run.workflow_id),
                project_name=getattr(projects.get(owner.get(run.workflow_id, "")), "name", None),
                environment_class=environment_class(runtime, run.environment_key),
                last_verified_at=run.completed_at if run.status == RunStatus.VERIFIED.value else None,
            )
            for run in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}


__all__ = [
    "cancel_run",
    "commit_run",
    "create_run",
    "execute_run_stage",
    "get_run_view",
    "list_runs",
    "list_runs_across",
]
