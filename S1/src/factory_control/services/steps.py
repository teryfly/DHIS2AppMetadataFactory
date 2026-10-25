"""Stage execution steps and approval submission (CP-WF-028, CP-GATE-013a, CP-WF-012/029).

Each function is one activity-sized unit: it reads what it needs in a
transaction, performs the provider calls with **no** transaction open, and writes
the outcome in a second transaction.  That is what makes activity retry safe
(CP-WF-008) and keeps S0's ``TRANSACTION_MISUSE`` guard satisfied.
"""

from __future__ import annotations

import hashlib
from typing import Any

from factory_control import store as S
from factory_control.domain import feedback as FB
from factory_control.domain.enums import (
    AUTO_STAGES,
    GateStatus,
    GateType,
    RunStatus,
    StageStatus,
    StageType,
)
from factory_control.errors import InvalidStateError, NotFoundControlError
from factory_control.foundation import has_active_uow, structure_hash
from factory_control.models import DeploymentRun, Project, Stage, Workflow
from factory_control.ports.dto import AgentRunRequest, BoundRef, Issue
from factory_control.progress import EventStreamProgressReporter, stage_cancel
from factory_control.runtime import ControlContext, Runtime
from factory_control.services.project import ensure_writable
from factory_control.services.recording import write_audit
from factory_control.services.workflow import (
    _stage_run,
    apply_agent_result,
    complete_stage_as_passed,
    open_stage_run,
    refresh_derived_state,
    reload_stage,
    supersede_gates_on_resubmit,
    transition,
)


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


def _load_stage_context(
    runtime: Runtime, stage_id: str, *, operator: str
) -> tuple[Stage, Workflow, Project, ControlContext]:
    """Stage + workflow + project, and the project's target environments."""
    with runtime.transaction(operator=operator) as ctx:
        stage = S.get_stage(ctx.session, stage_id)
        workflow = S.get_workflow(ctx.session, stage.workflow_id)
        project = ctx.session.get(Project, workflow.project_id)
        if project is None:
            raise NotFoundControlError(f"unknown project {workflow.project_id}")
        ensure_writable(project)
        if workflow.paused and stage.status != StageStatus.RUNNING.value:
            raise InvalidStateError(
                "the workflow is paused; no new stage may start (CP-WF-011)",
                stage=stage.stage_type,
                details={"workflowId": workflow.id, "paused": True},
            )
        return stage, workflow, project, ctx


def claim_stage(
    runtime: Runtime, stage_id: str, *, operator: str, rerun: bool = False
) -> dict[str, Any]:
    """Move a stage to RUNNING and open its step run — without executing anything.

    The REST layer answers 202 with the step run's id (S5IF-G06), so both have to
    exist before the request returns, whatever engine runs the work afterwards.
    Claiming here also means a stage that cannot start answers the caller with 409
    instead of failing inside a worker thread where nobody sees it.
    """
    stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)

    if rerun:
        _prepare_rerun(runtime, stage=stage, operator=operator)
        stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)

    allowed_start = {StageStatus.READY.value, StageStatus.FAILED.value, StageStatus.BLOCKED.value}
    if rerun:
        # Appendix A: PASSED -> RUNNING is the rerun edge; other states re-enter via READY.
        allowed_start |= {StageStatus.PASSED.value, StageStatus.INVALIDATED.value}
    if stage.status not in allowed_start:
        raise InvalidStateError(
            f"stage is {stage.status} and cannot be started",
            stage=stage.stage_type,
            current_state=stage.status,
            required_states=sorted(allowed_start),
            target_id=stage.id,
            details={"stageId": stage.id, "status": stage.status},
        )

    idempotency_key = _idempotency_key(stage, workflow)
    with runtime.transaction(
        operator=operator, workflow_id=workflow.id, stage_id=stage.id, project_id=project.id
    ) as ctx:
        running = transition(
            ctx,
            stage=stage,
            target=StageStatus.RUNNING,
            event_type="stage.started",
            payload={"idempotencyKey": idempotency_key},
        )
        running.failure_source_stage = None
        ctx.session.flush()
        step_run = open_stage_run(ctx, running, idempotency_key=idempotency_key)
    return {
        "stageId": running.id,
        "stepRunId": step_run.id,
        "workflowId": workflow.id,
        "idempotencyKey": idempotency_key,
        "accepted": True,
    }


def run_claimed(
    runtime: Runtime,
    claim: dict[str, Any],
    *,
    operator: str,
    language: str | None = None,
) -> dict[str, Any]:
    """Execute a stage already claimed by :func:`claim_stage` (CP-WF-028)."""
    stage_id = str(claim["stageId"])
    stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)
    # The operation context is what makes an artifact, a log line or an audit row say
    # *which* stage produced it (NFR-OBS-001).  REST binds it for the request, but a stage
    # runs on an engine worker thread long after that request answered, so nothing would be
    # bound here otherwise — and every artifact S4 writes during a Deployment Run would land
    # with a null workflowId, invisible to GET /workflows/{id}/artifacts (19.2, 23.1).
    from factory_control.foundation import OperatorType, bind_operation_context
    from factory_control.progress import (
        activate_cancellation_token,
        deactivate_cancellation_token,
        register_cancellation_token,
        release_cancellation_token,
    )

    # CP-WF-011 / FR-WF-004: cancelling a workflow must reach the long task that is running
    # right now, so the token every provider call below is handed is registered against the
    # workflow for the duration of this stage and released when it ends.
    token = register_cancellation_token(workflow.id)
    handle = activate_cancellation_token(token)
    try:
        with bind_operation_context(
            operator_name=operator,
            operator_type=OperatorType.USER,
            source_subsystem="S1",
            project_id=project.id,
            workflow_id=workflow.id,
            stage_id=stage.id,
            deployment_run_id=stage.deployment_run_id,
        ):
            result = _execute_or_record_failure(
                runtime,
                stage_id=stage_id,
                operator=operator,
                idempotency_key=str(claim.get("idempotencyKey") or ""),
                language=language,
            )
    finally:
        deactivate_cancellation_token(handle)
        release_cancellation_token(workflow.id, token)
    if result.get("readyForApproval") and stage_id_or_type(stage_id, runtime) in _AGENT_STAGES:
        # CP-WF-028: an agent stage that completed is submitted for approval.
        result.update(submit_for_approval(runtime, stage_id, operator=operator))
    result["status"] = _current_status(runtime, stage_id) or result.get("status")
    result.setdefault("stageId", stage_id)
    result.setdefault("stepRunId", claim.get("stepRunId"))
    return result


def start_stage(
    runtime: Runtime,
    stage_id: str,
    *,
    operator: str,
    rerun: bool = False,
    language: str | None = None,
) -> dict[str, Any]:
    """Claim and execute in one call — the synchronous entry point (CLI, no engine)."""
    claim = claim_stage(runtime, stage_id, operator=operator, rerun=rerun)
    return run_claimed(runtime, claim, operator=operator, language=language)


def _execute_or_record_failure(
    runtime: Runtime,
    *,
    stage_id: str,
    operator: str,
    idempotency_key: str,
    language: str | None = None,
) -> dict[str, Any]:
    """Execute a stage, but never leave it RUNNING when the call is refused.

    Appendix A allows RUNNING → FAILED and FAILED → READY: landing a refusal on
    FAILED is what keeps the reason visible and lets the user retry or cancel
    (CP-WF-011, CP-WF-016).  A stuck RUNNING stage would do neither.
    """
    from factory_control.foundation import FactoryError

    try:
        return execute_stage(
            runtime,
            stage_id,
            operator=operator,
            idempotency_key=idempotency_key,
            language=language,
        )
    except FactoryError as exc:
        from factory_control.services.notifications import notify_stage_event

        stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)
        with runtime.transaction(
            operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id
        ) as ctx:
            transition(
                ctx,
                stage=stage,
                target=StageStatus.FAILED,
                event_type="stage.failed",
                payload={"error": exc.error_code, "detail": exc.message, "refused": True},
            )
            notify_stage_event(ctx, stage=stage, kind="failed", detail=f"{exc.error_code}: {exc.message}")
        raise


def _current_status(runtime: Runtime, stage_id: str) -> str | None:
    """Read the committed status back, so a response never reports a stale value."""
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        row = session.get(Stage, stage_id)
        return row.status if row is not None else None


_AGENT_STAGES: frozenset[str] = frozenset(
    {
        StageType.REQUIREMENTS.value,
        StageType.DOMAIN.value,
        StageType.MAPPING.value,
        StageType.PAGE_DESIGN.value,
    }
)


def _idempotency_key(stage: Stage, workflow: Workflow, *, ahead: int = 0) -> str:
    """CP-WF-008: stable per (stage, attempt) so a retried activity continues.

    ``ahead`` names the attempt a caller is about to start rather than the one it
    has already recorded; the durable engine uses it so the key it hands to
    Temporal matches the attempt the activity will run.
    """
    attempt = int(stage.attempt or 0) + 1 + ahead
    digest = structure_hash({"stageId": stage.id, "attempt": attempt})
    return f"cp:{stage.id}:{digest.split(':', 1)[1][:24]}"


def _prepare_rerun(runtime: Runtime, *, stage: Stage, operator: str) -> None:
    """CP-WF-012: open questions are superseded and waiting Gates invalidated."""
    with runtime.transaction(operator=operator, stage_id=stage.id, workflow_id=stage.workflow_id) as ctx:
        from factory_control.services.questions import supersede_open_questions

        supersede_open_questions(ctx, stage.id, reason="STAGE_RERUN")
        supersede_gates_on_resubmit(ctx, stage)
        # A failure from the previous attempt is not this attempt's evidence; leaving it on the
        # stage made the workbench show "the agent run failed" while the new run was waiting for
        # input.  The new run writes its own feedback if it needs to.
        stage.feedback = None
        if StageStatus(stage.status) is not StageStatus.PASSED:
            transition(ctx, stage=stage, target=StageStatus.READY, event_type="stage.rerun", payload={"reason": "USER_RERUN"})


def execute_stage(
    runtime: Runtime,
    stage_id: str,
    *,
    operator: str,
    idempotency_key: str,
    language: str | None = None,
) -> dict[str, Any]:
    """Dispatch the stage to its providers (CP-WF-028 table)."""
    stage_type = StageType(stage_id_or_type(stage_id, runtime))
    if stage_type.value in _AGENT_STAGES:
        return _run_agent_stage(
            runtime,
            stage_id=stage_id,
            operator=operator,
            idempotency_key=idempotency_key,
            language=language,
        )
    if stage_type is StageType.DISCOVERY:
        return _run_discovery(runtime, stage_id=stage_id, operator=operator, idempotency_key=idempotency_key)
    if stage_type is StageType.BASELINE_COMPILE:
        return _run_baseline_compile(runtime, stage_id=stage_id, operator=operator, idempotency_key=idempotency_key)
    if stage_type is StageType.HANDOFF:
        return _run_handoff(runtime, stage_id=stage_id, operator=operator, idempotency_key=idempotency_key)
    from factory_control.services.runs import execute_run_stage

    return execute_run_stage(
        runtime, stage_id=stage_id, operator=operator, idempotency_key=idempotency_key
    )


def stage_id_or_type(stage_id: str, runtime: Runtime) -> str:
    """Resolve a stage id to its type; tests may pass the type directly."""
    with runtime.transaction(operator="system") as ctx:
        stage = ctx.session.get(Stage, stage_id)
        if stage is not None:
            return stage.stage_type
    return stage_id


def _run_agent_stage(
    runtime: Runtime,
    *,
    stage_id: str,
    operator: str,
    idempotency_key: str,
    language: str | None = None,
) -> dict[str, Any]:
    stage, workflow, project, ctx = _load_stage_context(runtime, stage_id, operator=operator)
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as inner:
        stage = reload_stage(inner, stage)
        draft_id = inner.blueprint.open_draft(project.id, stage.stage_type, session=inner.session)
        stage.input_blueprint_ref = {"kind": "DRAFT", "id": draft_id}
        inner.session.flush()
        target_keys = [t.environment_key for t in S.target_environments(inner.session, project.id)]
        design_ref = S.design_reference_environment(inner.session, project.id)
        capability_artifact, discovery_artifact = _design_reference_artifacts(inner, project)

    # --- external I/O, outside any transaction ------------------------------
    # The draft S2 just opened has no content until its base is materialised; the
    # agent is about to patch it, so this must run before the run starts.
    runtime.blueprint.prepare_draft_base(project.id, draft_id)
    if design_ref is not None:
        runtime.blueprint.replenish_uid_pool(project.id, design_ref.environment_key)
    policies = _export_policies(runtime.execution, target_keys)
    request = AgentRunRequest(
        project_id=project.id,
        workflow_id=workflow.id,
        stage_id=stage.id,
        stage=stage.stage_type,
        agent_type=_agent_type_for(stage.stage_type),
        draft_id=draft_id,
        scope=list(stage.page_scope or []),
        input_artifact_ids=list(stage.output_artifact_ids or []),
        design_reference_environment_key=design_ref.environment_key if design_ref else None,
        target_environment_policies=policies,
        capability_profile_artifact_id=capability_artifact,
        discovery_result_artifact_id=discovery_artifact,
        operator=operator,
        language=language or project.default_language,
        idempotency_key=idempotency_key,
    )
    reporter = EventStreamProgressReporter(
        workflow_id=workflow.id,
        session_factory=runtime.session_factory,
        kind="agent",
        stage_id=stage.id,
        retention_days=_event_retention(runtime),
    )
    result = runtime.intelligence.start_agent_run(request, progress=reporter, cancel=stage_cancel())

    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as inner:
        # 19.2.3 names agent.started and agent.completed, and 21.4 requires both ends of an
        # Agent run to be audited.  They are appended here, started first, because the run
        # only has an id once S3 has created it — S3's own ``ai.*`` records carry the
        # provider, model and policy verdict (00 doc 4.1), and this is the orchestration's
        # notice of the same run.
        from factory_control.services.recording import record_change

        def _announce(event_type: str, audit_type: str, payload: dict[str, Any]) -> None:
            record_change(
                inner,
                event_type=event_type,
                audit_event_type=audit_type,
                object_type="AgentRun",
                object_id=result.run_id or stage.id,
                payload={"runId": result.run_id, "agentType": request.agent_type, **payload},
            )

        _announce("agent.started", "cp.agent_run.started", {"stage": request.stage, "draftId": draft_id})
        _announce(
            "agent.completed",
            "cp.agent_run.completed",
            {
                "status": result.status,
                "patchIds": list(result.patch_ids),
                "questionCount": len(result.questions),
                "confidence": result.confidence,
            },
        )
        applied = apply_agent_result(inner, stage=stage, result=result, idempotency_key=idempotency_key)
        applied["stageId"] = stage.id
        return applied


def _agent_type_for(stage_type: str) -> str:
    return {
        StageType.REQUIREMENTS.value: "REQUIREMENTS",
        StageType.DOMAIN.value: "DOMAIN",
        StageType.MAPPING.value: "MAPPING",
        StageType.PAGE_DESIGN.value: "UI",
    }.get(stage_type, "REQUIREMENTS")


def _export_policies(execution: Any, environment_keys: list[str]) -> dict[str, Any]:
    """D-07: the effective policy is the strictest of org and every target env."""
    out: dict[str, Any] = {}
    for key in environment_keys:
        protection = execution.get_protection(key)
        out[key] = dict(protection.data_export_policy or {})
    return out


def _design_reference_artifacts(ctx: ControlContext, project: Project) -> tuple[str | None, str | None]:
    """The CAPABILITY_PROFILE and DISCOVERY_RESULT the design reference produced."""
    from factory_control.foundation import ArtifactQuery, query_artifacts

    def latest(kind: str) -> str | None:
        page = query_artifacts(
            ArtifactQuery(project_id=project.id, type=kind, limit=1, descending=True),
            session=ctx.session,
        )
        return page.items[0].id if page.items else None

    return latest("CAPABILITY_PROFILE"), latest("DISCOVERY_RESULT")


def _event_retention(runtime: Runtime) -> int:
    with runtime.transaction(operator="system") as ctx:
        return ctx.settings.event_retention_days()


def _design_reference_scope(ctx: Any, project: Project) -> dict[str, Any]:
    """What DISCOVERY asks S4 to look at on the design reference (CP-WF-028).

    The Registry cannot answer this.  DISCOVERY is stage 3 and the Registry entries are
    created by MAPPING, stage 4 — so handing S4 the project's Registry refs would ask it
    to inspect an object list that is always empty at this point, and S4 refuses a scope
    that selects nothing rather than silently pulling a whole instance (FR-DSC-002).  The
    types to inspect come from S2's own type descriptor catalog, which is exactly the set
    the mapping will later fall onto (FR-COV-001), and organisation units and the default
    CategoryCombo are in it, so FR-COV-007 and FR-COV-003 need no special case here.

    A Greenfield project reads only version, capabilities and the default objects
    (SRS 7.1), which is what S4's ``greenfield`` scope means.
    """
    if project.project_type.strip().lower() == "greenfield":
        return {"kind": "DESIGN_REFERENCE", "greenfield": True}
    return {
        "kind": "DESIGN_REFERENCE",
        "dhis2Types": sorted(
            {
                str(row.get("dhis2Type"))
                for row in ctx.blueprint.list_object_types()
                if row.get("dhis2Type")
            }
        ),
    }


def _run_discovery(
    runtime: Runtime, *, stage_id: str, operator: str, idempotency_key: str
) -> dict[str, Any]:
    """DISCOVERY on the design reference environment (CP-WF-028)."""
    stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id) as ctx:
        design_ref = S.design_reference_environment(ctx.session, project.id)
        explicit = ctx.blueprint.registry_object_refs(project.id, project.current_blueprint_version or "0.1.0", None)
        scope = _design_reference_scope(ctx, project)
    if design_ref is None:
        raise InvalidStateError(
            "the project has no design reference environment", details={"projectId": project.id}
        )

    environment_key = design_ref.environment_key
    capability_id = runtime.execution.probe_capabilities(environment_key, idempotency_key=f"{idempotency_key}:cap")
    outcome = runtime.execution.discover(
        environment_key,
        project.id,
        scope,
        explicit,
        idempotency_key=f"{idempotency_key}:disc",
        progress=EventStreamProgressReporter(
            workflow_id=workflow.id,
            session_factory=runtime.session_factory,
            kind="execution",
            stage_id=stage.id,
            retention_days=_event_retention(runtime),
        ),
        cancel=stage_cancel(),
    )

    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        stage = reload_stage(ctx, stage)
        outputs = [a for a in (outcome.discovery_result_artifact_id, outcome.capability_profile_artifact_id, capability_id) if a]
        stage.output_artifact_ids = list({*(stage.output_artifact_ids or []), *outputs})
        if outcome.blocking_issues:
            transition(
                ctx,
                stage=stage,
                target=StageStatus.FAILED,
                event_type="stage.failed",
                payload={"blocking": [i.model_dump() for i in outcome.blocking_issues]},
                failure_source_stage=StageType.DISCOVERY.value,
            )
            return {"status": StageStatus.FAILED.value, "blocking": len(outcome.blocking_issues)}
        passed = transition(
            ctx,
            stage=stage,
            target=StageStatus.PASSED,
            event_type="stage.passed",
            payload={"environmentKey": environment_key},
            output_artifact_ids=outputs,
        )
        complete_stage_as_passed(ctx, stage=passed, output_artifact_ids=outputs, source="DISCOVERY_OK")
        refresh_derived_state(ctx, workflow)
        return {"status": StageStatus.PASSED.value, "artifacts": outputs}


def _run_baseline_compile(
    runtime: Runtime, *, stage_id: str, operator: str, idempotency_key: str
) -> dict[str, Any]:
    """BASELINE_COMPILE: S2 compile then S2 static validation (CP-WF-028)."""
    stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)
    with runtime.transaction(operator=operator, project_id=project.id) as ctx:
        capability_id, _discovery = _design_reference_artifacts(ctx, project)
    version = project.current_blueprint_version or "0.1.0"
    build = runtime.blueprint.compile_baseline(
        project.id,
        version,
        capability_id,
        build_context(f"{idempotency_key}:compile", operator=operator, now=_clock_now(runtime)),
        progress=EventStreamProgressReporter(
            workflow_id=workflow.id, session_factory=runtime.session_factory, kind="execution", stage_id=stage.id
        ),
        cancel=stage_cancel(),
        idempotency_key=f"{idempotency_key}:compile",
    )
    validation = runtime.blueprint.validate_build(
        build.build_id or "",
        idempotency_key=f"{idempotency_key}:validate",
    )
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        outputs = sorted({*(build.artifact_ids or {}).values(), *([validation.validation_report_artifact_id] if validation.validation_report_artifact_id else [])})
        from factory_control.services.recording import record_change

        record_change(
            ctx,
            event_type="validation.completed",
            audit_event_type="cp.validation.completed",
            object_type="Validation",
            object_id=validation.validation_report_artifact_id or build.build_id or stage.id,
            payload={
                "buildId": build.build_id,
                "stageType": stage.stage_type,
                "blocking": validation.blocking_count,
                "warnings": len([i for i in validation.issues if not i.is_blocking]),
                "reportArtifactId": validation.validation_report_artifact_id,
            },
        )
        if validation.blocking_count > 0:
            transition(
                ctx,
                stage=stage,
                target=StageStatus.FAILED,
                event_type="stage.failed",
                payload={"blocking": validation.blocking_count},
                failure_source_stage=_blocking_source(validation.issues) or StageType.MAPPING.value,
            )
            return {"status": StageStatus.FAILED.value, "blocking": validation.blocking_count}
        passed = transition(
            ctx,
            stage=stage,
            target=StageStatus.PASSED,
            event_type="stage.passed",
            payload={"buildId": build.build_id, "artifacts": outputs},
            output_artifact_ids=outputs,
        )
        # CP-UI-020 reads the workflow's baseline build from S1's own StageRun row; a
        # build id that only lived in the event payload could not answer it, and the
        # version stamp is what lets ``blueprintVersion`` name an older baseline build.
        _stage_run(
            ctx,
            passed,
            agent_run_ids=[],
            execution_ids=[],
            build_ids=[build.build_id or ""],
            idempotency_key=idempotency_key,
            output_artifact_ids=outputs,
            result={"buildId": build.build_id, "blueprintVersion": version},
        )
        complete_stage_as_passed(ctx, stage=passed, output_artifact_ids=outputs, source="BASELINE_OK")
        refresh_derived_state(ctx, workflow)
        return {"status": StageStatus.PASSED.value, "buildId": build.build_id}


def _artifact_bound_refs(ctx: ControlContext, artifact_ids: list[str], output_hashes: dict[str, str]) -> list[BoundRef]:
    """Bind each delivery artifact at its stored hash.

    A Gate approval is only worth what it is bound to, so an empty hash here would be an
    approval of nothing.  ``output_hashes`` is keyed by S2's own role names and a provider that
    omits it is not unusual; the artifact index always has the real bytes hash, so that is the
    source and the provider's map is only a hint.
    """
    from factory_control.foundation import find_artifact

    by_id = {value.lower(): key for key, value in (output_hashes or {}).items()}
    refs: list[BoundRef] = []
    for artifact_id in artifact_ids:
        hash_value = ""
        hint = by_id.get(artifact_id.lower())
        if hint is not None:
            hash_value = str(output_hashes.get(hint) or "")
        if not hash_value:
            artifact = find_artifact(artifact_id, session=ctx.session)
            hash_value = artifact.content_hash if artifact is not None else ""
        if not hash_value:
            raise InvalidStateError(
                f"cannot bind the delivery Gate to {artifact_id}: no content hash for it",
                details={"artifactId": artifact_id, "reason": "UNHASHABLE_ARTIFACT"},
            )
        refs.append(BoundRef(refType="ARTIFACT", refId=artifact_id, contentHash=hash_value))
    return refs


def _blocking_source(issues: list[Issue]) -> str | None:
    for issue in issues:
        if issue.is_blocking and issue.source_stage:
            return issue.source_stage
    return None


def _delivery_evidence(ctx: ControlContext, workflow_id: str) -> tuple[list[DeploymentRun], list[str]]:
    """Runs that prove this version was actually delivered, and their verification reports.

    D-01 / 总体 SRS 17.3: HANDOFF requires at least one **non-production** Run at the current
    version in VERIFIED.  An OUTDATED run does not qualify — it verified a Blueprint version
    that is no longer the one being handed over, and saying otherwise would let a package be
    delivered on evidence of different bytes.  The environment class is read live from S4
    rather than from S1's display cache, because 00-doc 4.2 forbids a cached answer for a
    delivery decision.
    """
    runs = [
        run
        for run in S.runs_of(ctx.session, workflow_id)
        if run.status == RunStatus.VERIFIED.value and not run.outdated
    ]
    qualified: list[DeploymentRun] = []
    evidence: list[str] = []
    for run in runs:
        protection = ctx.execution.get_protection(run.environment_key)
        if str(protection.environment_class).upper() == "PRODUCTION":
            continue
        qualified.append(run)
        verify_stage = S.stage_by_type(
            ctx.session, run.workflow_id, StageType.POST_VERIFY, run_id=run.id
        )
        if verify_stage is None:
            continue
        evidence.extend(str(item) for item in (verify_stage.output_artifact_ids or []) if item)
    return qualified, evidence


def _run_handoff(
    runtime: Runtime, *, stage_id: str, operator: str, idempotency_key: str
) -> dict[str, Any]:
    """HANDOFF: S2 makes the delivery package, then HANDOFF_GATE decides it (17.3, D-01, CP-WF-028).

    S1 produces nothing of its own here.  The FRONTEND_PACKAGE and the 17.3 check report are
    S2's artifacts, and the Gate binds *their* hashes so an approval is bound to the bytes it
    reviewed rather than to a stage that has since moved on.
    """
    stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)

    with runtime.transaction(operator=operator, project_id=project.id) as ctx:
        # HANDOFF itself is a design-line row (no deployment_run_id), so it has to be excluded by
        # name — otherwise the stage it gates is permanently "unfinished" and can never run.
        design = [
            s
            for s in S.stages_of(ctx.session, workflow.id)
            if s.deployment_run_id is None and s.stage_type != StageType.HANDOFF.value
        ]
        unfinished = sorted(
            s.stage_type for s in design if s.status not in (StageStatus.PASSED.value, StageStatus.SKIPPED.value)
        )
        if unfinished:
            raise InvalidStateError(
                "the design line is not closed; there is nothing to hand off yet",
                stage=stage.stage_type,
                details={"stageId": stage.id, "unfinished": unfinished},
            )
        version = project.current_blueprint_version or ""
        if not version:
            raise InvalidStateError(
                "the project has no current Blueprint version to deliver",
                stage=stage.stage_type,
                details={"stageId": stage.id, "missing": "BLUEPRINT_VERSION"},
            )
        environment_keys = [t.environment_key for t in S.target_environments(ctx.session, project.id)]
        qualified, evidence_ids = _delivery_evidence(ctx, workflow.id)

    if not qualified:
        raise InvalidStateError(
            "a non-production Deployment Run must reach VERIFIED before this can be handed off (D-01)",
            stage=stage.stage_type,
            details={
                "stageId": stage.id,
                "reason": "NO_VERIFIED_RUN",
                "environmentKeys": environment_keys,
            },
        )

    package = runtime.blueprint.generate_frontend_package(
        project.id,
        version,
        environment_keys,
        {"artifactIds": evidence_ids},
        build_context(f"{idempotency_key}:handoff", operator=operator, now=_clock_now(runtime)),
        progress=EventStreamProgressReporter(
            workflow_id=workflow.id, session_factory=runtime.session_factory, kind="execution", stage_id=stage.id
        ),
        cancel=stage_cancel(),
        idempotency_key=f"{idempotency_key}:handoff",
    )

    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        artifact_ids = dict(package.artifact_ids or {})
        output_hashes = dict(package.output_hashes or {})
        outputs = sorted(artifact_ids.values())
        blocking = [issue for issue in (package.issues or []) if issue.is_blocking]
        if blocking:
            transition(
                ctx,
                stage=stage,
                target=StageStatus.FAILED,
                event_type="stage.failed",
                payload={"blocking": len(blocking), "buildId": package.build_id},
                failure_source_stage=_blocking_source(blocking) or StageType.MAPPING.value,
                output_artifact_ids=outputs,
            )
            return {"status": StageStatus.FAILED.value, "blocking": len(blocking)}

        waiting = transition(
            ctx,
            stage=stage,
            target=StageStatus.WAITING_APPROVAL,
            event_type="stage.waiting_approval",
            payload={"buildId": package.build_id, "artifacts": outputs, "evidence": evidence_ids},
            output_artifact_ids=outputs,
        )
        refs = _artifact_bound_refs(ctx, outputs, output_hashes)
        from factory_control.services.gates import create_gate

        gate = create_gate(
            ctx,
            gate_type=GateType.HANDOFF_GATE,
            stage=waiting,
            bound_refs=refs,
            warnings=[issue.model_dump() for issue in (package.issues or []) if not issue.is_blocking],
            blueprint_ref={"version": version, "kind": "VERSION"},
            required_approvals=1,
        )
        refresh_derived_state(ctx, workflow)
        return {
            "status": StageStatus.WAITING_APPROVAL.value,
            "gateId": gate.id,
            "gateType": gate.type,
            "buildId": package.build_id,
            "boundObjects": len(gate.bound_refs or []),
        }


def submit_for_approval(runtime: Runtime, stage_id: str, *, operator: str) -> dict[str, Any]:
    """CP-GATE-013a + CP-WF-029: freeze outside the transaction, then bind the Gate.

    Works both for a stage whose agent finished and for the manual-authoring path
    where the user submits a patch and asks for approval directly from READY.
    """
    stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)
    if stage.status not in (StageStatus.READY.value, StageStatus.RUNNING.value, StageStatus.WAITING_INPUT.value):
        raise InvalidStateError(
            f"a {stage.status} stage cannot be submitted for approval",
            stage=stage.stage_type,
            details={"stageId": stage.id},
        )
    gate_type = _gate_type_for(stage)
    if gate_type is None:
        raise InvalidStateError(
            "this stage has no approval Gate; it closes automatically",
            stage=stage.stage_type,
            details={"stageId": stage.id},
        )

    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        draft_id = ctx.blueprint.open_draft(project.id, stage.stage_type, session=ctx.session)
        target_keys = [t.environment_key for t in S.target_environments(ctx.session, project.id)]

    runtime.blueprint.prepare_draft_base(project.id, draft_id)
    freeze = runtime.blueprint.freeze_draft(draft_id, target_keys)

    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        if not freeze.promotable:
            # Appendix A reaches BLOCKED from RUNNING only. A hand-authored submission arrives from
            # READY, so going straight to BLOCKED raised 409 INVALID_STATE {"from":"READY","to":"BLOCKED"}
            # and the operator saw a state-machine complaint instead of the blocking issues that
            # actually prevented promotion — which is the one thing this branch exists to report.
            # Claim the stage first; BLOCKED -> READY is legal afterwards, so retry is unaffected.
            stage = reload_stage(ctx, stage)
            if stage.status == StageStatus.READY.value:
                stage = transition(
                    ctx,
                    stage=stage,
                    target=StageStatus.RUNNING,
                    event_type="stage.started",
                    payload={"reason": "FREEZE_BLOCKING", "claimedBy": operator},
                )
            transition(
                ctx,
                stage=stage,
                target=StageStatus.BLOCKED,
                event_type="stage.blocked",
                payload={"reason": "FREEZE_BLOCKING", "issues": [i.model_dump() for i in freeze.issues]},
                feedback=FB.stage_feedback(
                    FB.VALIDATION,
                    f"{len(freeze.issues)} blocking issue(s) prevent the draft from being promoted",
                    source_stage=stage.stage_type,
                    issues=list(freeze.issues),
                    created_at=_clock_now(runtime),
                    extra={"reason": "FREEZE_BLOCKING"},
                ),
            )
            return {"status": StageStatus.BLOCKED.value, "blocking": len(freeze.issues)}

        if stage.status != StageStatus.RUNNING.value:
            # Appendix A allows READY -> WAITING_APPROVAL directly (manual path).
            waiting = transition(
                ctx,
                stage=stage,
                target=StageStatus.WAITING_APPROVAL,
                event_type="stage.waiting_approval",
                payload={"draftHash": freeze.draft_hash},
            )
        else:
            waiting = transition(
                ctx,
                stage=stage,
                target=StageStatus.WAITING_APPROVAL,
                event_type="stage.waiting_approval",
                payload={"draftHash": freeze.draft_hash},
            )

        refs = [BoundRef(refType="BLUEPRINT_DRAFT", refId=draft_id, contentHash=freeze.draft_hash)]
        if gate_type is GateType.ARCHITECTURE_GATE:
            refs += _discovery_bound_refs(ctx, project)
        if gate_type is GateType.REQUIREMENT_GATE:
            # The NRR produced by parsing is a real artifact and belongs in the
            # binding; open questions are checked at approval time instead
            # (CP-Q-006), because a Question has no content hash of its own.
            refs += _nrr_bound_refs(ctx, waiting)

        from factory_control.services.gates import create_gate

        gate = create_gate(
            ctx,
            gate_type=gate_type,
            stage=waiting,
            bound_refs=refs,
            warnings=[i.model_dump() for i in freeze.issues if not i.is_blocking],
            blueprint_ref={"draftId": draft_id, "hash": freeze.draft_hash, "kind": "DRAFT"},
            required_approvals=1,
        )
        refresh_derived_state(ctx, workflow)
        return {
            "status": StageStatus.WAITING_APPROVAL.value,
            "gateId": gate.id,
            "gateType": gate.type,
            "boundObjects": len(gate.bound_refs or []),
        }


def _gate_type_for(stage: Stage) -> GateType | None:
    mapping = {
        StageType.REQUIREMENTS.value: GateType.REQUIREMENT_GATE,
        StageType.DOMAIN.value: GateType.DOMAIN_GATE,
        StageType.MAPPING.value: GateType.ARCHITECTURE_GATE,
        StageType.PAGE_DESIGN.value: GateType.DESIGN_GATE,
    }
    return mapping.get(stage.stage_type)


def _discovery_bound_refs(ctx: ControlContext, project: Project) -> list[BoundRef]:
    from factory_control.foundation import ArtifactQuery, query_artifacts

    page = query_artifacts(ArtifactQuery(project_id=project.id, type="DISCOVERY_RESULT", limit=1), session=ctx.session)
    if not page.items:
        return []
    artifact = page.items[0]
    return [BoundRef(refType="ARTIFACT", refId=artifact.id, contentHash=artifact.content_hash)]


def _nrr_bound_refs(ctx: ControlContext, stage: Stage) -> list[BoundRef]:
    """Bind the NRR artifact of the requirements stage, if one exists (CP-GATE-001)."""
    from factory_control.foundation import find_artifact

    refs: list[BoundRef] = []
    for artifact_id in stage.output_artifact_ids or []:
        artifact = find_artifact(artifact_id, session=ctx.session)
        if artifact is not None and artifact.type == "NRR":
            refs.append(BoundRef(refType="ARTIFACT", refId=artifact.id, contentHash=artifact.content_hash))
    return refs


def _manual_patch_key(stage: Stage, patch: dict[str, Any], draft_hash: str, client_key: str) -> str:
    """The idempotency key for one hand-authored patch.

    ``patch:{stage}:{attempt}`` pinned a whole stage attempt to the first body it ever saw: S2's
    own digest covers the operations (``draft/service.py`` ``submit_patch``) and S0 raises
    ``IdempotencyConflictError`` once one key arrives with two digests, so an operator
    hand-authoring a requirement list could write exactly one entry per attempt — every later
    edit read 422 IDEMPOTENCY_CONFLICT.

    What counts as *the same request again* is the caller's business, so a key the caller sent
    wins: the consoles mint a fresh ``Idempotency-Key`` per action and release it once the action
    succeeds (CP-API-003), which is the only thing that separates "the network dropped that, send
    it again" from "I deleted this entry and now I am adding it back". Content plus draft state
    cannot tell those apart — verified against a live draft, where a delete-then-re-add came back
    ``PROPOSED`` carrying the first submission's stored outcome and the entry stayed out of the
    draft. ``DELETE /environments/{key}/data`` reasons about the same trap (NFR-REL-003).

    With no header — curl, a test client, an older console — the fallback is the content plus the
    draft hash it was written against: multiple edits of one attempt stack, and a byte-identical
    resend against an unchanged draft still replays the first outcome instead of applying twice.
    """
    if client_key:
        return f"patch:{stage.id}:{client_key[:64]}"
    digest = structure_hash({"operations": patch.get("operations") or [], "draftHash": draft_hash})
    return f"patch:{stage.id}:{stage.attempt}:{digest[7:23]}"


def submit_manual_patch(
    runtime: Runtime, stage_id: str, *, operator: str, patch: dict[str, Any], client_key: str = ""
) -> dict[str, Any]:
    """CP-WF-029 with the CP-WF-017 guard; a draft change invalidates waiting Gates."""
    stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)
    if stage.status not in (StageStatus.READY.value, StageStatus.RUNNING.value, StageStatus.WAITING_APPROVAL.value):
        raise InvalidStateError(
            f"a {stage.status} stage does not accept patches",
            stage=stage.stage_type,
            current_state=stage.status,
            required_states=[
                StageStatus.READY.value,
                StageStatus.RUNNING.value,
                StageStatus.WAITING_APPROVAL.value,
            ],
            target_id=stage.id,
            details={"stageId": stage.id},
        )
    if _mapping_frozen(project, stage, runtime):
        raise InvalidStateError(
            "the mapping and registry are frozen after ARCHITECTURE_GATE; raise a Change Request instead",
            stage=stage.stage_type,
            details={"stageId": stage.id, "cause": "MAPPING_FROZEN_BY_GATE"},
        )

    # Opening and filling a draft is external I/O for S2, so it happens before the
    # unit of work that records the patch outcome.
    with runtime.transaction(operator=operator, project_id=project.id) as ctx:
        design = S.design_reference_environment(ctx.session, project.id)
    draft_id = runtime.blueprint.open_draft(project.id, stage.stage_type)
    runtime.blueprint.prepare_draft_base(project.id, draft_id)
    # Which draft state this submission was written against. It belongs in the idempotency key:
    # a retry is the same body on the same draft, and the same body on a draft that has since
    # moved is a different action — without it the second one replays the first and the draft
    # never changes, while the operator is told it was proposed.
    draft = runtime.blueprint.get_draft(draft_id)
    draft_hash = draft.content_hash if draft is not None else ""
    # A registry CREATE consumes a UID from the project's pool, and the pool is filled from
    # the target instance. An agent stage run tops it up itself (`_run_agent_stage`), but the
    # hand-authored path never did, so the first CREATE patch of a manual project was refused
    # with UID_POOL_EXHAUSTED against a pool that had never been touched at all.
    if design is not None:
        runtime.blueprint.replenish_uid_pool(project.id, design.environment_key)

    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        # Record which draft this stage is working in, exactly as `_run_agent_stage` does.
        # `GET /projects/{id}/blueprint/draft` (S5IF-BP05) resolves the live draft from S2's
        # project semantics and then from S1's own `inputBlueprintRef` column, because UI-BR-02
        # rules that S5 must not guess it. Real S2 answers the semantics read with no draft, so
        # for a hand-authored stage the column was the only source — and nothing wrote it. The
        # draft read 404'd with NOT_EXIST right after a patch that S2 had just accepted, which
        # also left the console's own manual-authoring screen without a draft to show.
        stage = reload_stage(ctx, stage)
        stage.input_blueprint_ref = {"kind": "DRAFT", "id": draft_id}
        ctx.session.flush()

        result = ctx.blueprint.submit_patch(
            project.id,
            draft_id,
            patch,
            stage=stage.stage_type,
            idempotency_key=_manual_patch_key(stage, patch, draft_hash, client_key),
            session=ctx.session,
        )
        from factory_control.services.questions import record_questions

        raised = record_questions(
            ctx,
            stage=stage,
            questions=list(result.questions),
            source_kind="PATCH_CONFLICT",
        ) if result.questions else []
        stage.input_blueprint_ref = {"kind": "DRAFT", "id": draft_id, "hash": result.draft_hash}
        if stage.status == StageStatus.WAITING_APPROVAL.value:
            from factory_control.services.gates import invalidate_gate

            for gate in S.gates_of(ctx.session, workflow.id, status=GateStatus.WAITING):
                if gate.stage_id == stage.id:
                    invalidate_gate(ctx, gate, reason="DRAFT_HASH_CHANGED")
        write_audit(
            ctx,
            event_type="cp.stage.patch_submitted",
            object_type="Stage",
            object_id=stage.id,
            after={"patchStatus": result.status, "draftHash": result.draft_hash},
        )
        ctx.session.flush()
        return {
            "stageId": stage.id,
            "patchStatus": result.status,
            "draftHash": result.draft_hash,
            # by_alias keeps the wire camelCase (S5IF-G03); an Issue dumped raw here
            # was one of the five places S1 leaked snake_case.
            "issues": [i.model_dump(by_alias=True) for i in result.issues],
            "questions": [row.id for row in raised],
            "gateInvalidated": stage.status == StageStatus.WAITING_APPROVAL.value,
            "assignedUids": dict(result.assigned_uids),
        }


def _mapping_frozen(project: Project, stage: Stage, runtime: Runtime) -> bool:
    """CP-WF-017: after the architecture Gate, mapping/registry patches need a CR."""
    if stage.stage_type not in (StageType.MAPPING.value, StageType.DISCOVERY.value):
        return False
    with runtime.transaction(operator="system") as ctx:
        gates = S.gates_of(ctx.session, stage.workflow_id, gate_type=GateType.ARCHITECTURE_GATE.value)
        return any(g.status == GateStatus.APPROVED.value for g in gates)


def resolve_manual_check(
    runtime: Runtime, stage_id: str, *, operator: str, outcome: str, note: str
) -> dict[str, Any]:
    """附录 A / CP-WF-019: MANUAL_CHECK leaves only via a user verdict."""
    if outcome not in ("PASSED", "FAILED"):
        raise InvalidStateError("outcome must be PASSED or FAILED", details={"outcome": outcome})
    stage, workflow, project, _ = _load_stage_context(runtime, stage_id, operator=operator)
    if stage.status != StageStatus.MANUAL_CHECK.value:
        raise InvalidStateError(
            f"stage is {stage.status}, not MANUAL_CHECK", stage=stage.stage_type, details={"stageId": stage.id}
        )
    execution_id = _execution_id_for_stage(runtime, stage)
    if execution_id:
        runtime.execution.resolve_manual_check(execution_id, outcome, note, operator)

    target = StageStatus.PASSED if outcome == "PASSED" else StageStatus.FAILED
    event = "stage.passed" if outcome == "PASSED" else "stage.failed"
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id, stage_id=stage.id) as ctx:
        resolved = transition(
            ctx,
            stage=stage,
            target=target,
            event_type=event,
            payload={"manualCheck": outcome, "note": note},
        )
        write_audit(
            ctx,
            event_type="cp.stage.manual_check_resolved",
            object_type="Stage",
            object_id=stage.id,
            after={"outcome": outcome, "note": note, "executionId": execution_id},
        )
        if outcome == "PASSED":
            complete_stage_as_passed(ctx, stage=resolved, output_artifact_ids=list(stage.output_artifact_ids or []), source="MANUAL_CHECK")
        refresh_derived_state(ctx, workflow)
        return {"stageId": stage.id, "status": resolved.status, "outcome": outcome}


def _execution_id_for_stage(runtime: Runtime, stage: Stage) -> str | None:
    with runtime.transaction(operator="system") as ctx:
        row = S.latest_stage_run(ctx.session, stage.id)
        ids = [i for i in (row.execution_ids or []) if i] if row is not None else []
        if ids:
            return ids[-1]
        run = ctx.session.get(DeploymentRun, stage.deployment_run_id) if stage.deployment_run_id else None
        return run.execution_id if run else None


def stage_is_auto(stage_type: str) -> bool:
    return stage_type in {s.value for s in AUTO_STAGES}


def has_no_open_transaction() -> bool:
    """Assertion used by tests to prove the external-I/O boundary."""
    return not has_active_uow()


__all__ = [
    "execute_stage",
    "has_no_open_transaction",
    "resolve_manual_check",
    "stage_is_auto",
    "start_stage",
    "submit_for_approval",
    "submit_manual_patch",
]
