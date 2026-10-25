"""Change Requests (CP-CR-001 … CP-CR-007).

A CR is the only sanctioned way to change mapping or registry content after the
architecture Gate (CP-WF-017 / CP-BR-09).  Impact analysis comes from S2, extended
with the environments this project has already deployed to (CP-CR-003).
"""

from __future__ import annotations

from typing import Any

from factory_control import store as S
from factory_control.domain.enums import (
    ChangeRequestStatus,
    RunStatus,
    StageType,
)
from factory_control.domain.machines import assert_cr_transition
from factory_control.errors import InvalidStateError, ValidationFailedError
from factory_control.foundation import (
    ArtifactCreate,
    ArtifactStatus,
    DataClassification,
    new_id,
    register_artifact,
    stage_content,
)
from factory_control.models import ChangeRequest
from factory_control.runtime import ControlContext, Runtime
from factory_control.services.project import ensure_writable
from factory_control.services.recording import record_change


def _view(row: ChangeRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "projectId": row.project_id,
        "reason": row.reason,
        "affectedBusinessKeys": list(row.affected_business_keys or []),
        "deletionCandidates": list(row.deletion_candidates or []),
        "requestedBy": row.requested_by,
        "status": row.status,
        "impactReportArtifactId": row.impact_report_artifact_id,
        "affectedStages": list(row.affected_stages or []),
        "approvedBy": row.approved_by,
        "rejectedReason": row.rejected_reason,
        "resultBlueprintVersion": row.result_blueprint_version,
        "createdAt": row.created_at,
    }


def create_change_request(
    runtime: Runtime,
    project_id: str,
    *,
    operator: str,
    reason: str,
    affected_business_keys: list[str],
    deletion_candidates: list[str] | None = None,
) -> dict[str, Any]:
    """CP-CR-001: a change to an approved or deployed project must be a CR."""
    if not reason.strip():
        raise InvalidStateError("a change request must state its reason")
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        ensure_writable(project)
        candidates = _declared_deletions(ctx, project, list(deletion_candidates or []))
        row = ChangeRequest(
            id=new_id("crq"),
            project_id=project.id,
            reason=reason,
            affected_business_keys=list(affected_business_keys),
            deletion_candidates=candidates,
            requested_by=operator,
            status=ChangeRequestStatus.DRAFT.value,
        )
        ctx.session.add(row)
        ctx.session.flush()
        record_change(
            ctx.bind(workflow_id=S.get_workflow_for_project(ctx.session, project.id).id),
            event_type="change_request.updated",
            audit_event_type="cp.change_request.created",
            object_type="ChangeRequest",
            object_id=row.id,
            payload={
                "reason": reason,
                "businessKeys": list(affected_business_keys),
                "deletionCandidates": candidates,
            },
        )
        return _view(row)


def _declared_deletions(
    ctx: ControlContext, project: Any, keys: list[str]
) -> list[str]:
    """CP-CR-005 with 总体 SRS 15.6: the request names its own retirements, and S1 checks the naming.

    S2's impact read cannot supply this list — its ``ImpactSet`` carries no deletion field, and the
    port's own note says the candidate list is S1's judgement.  So the CR body is the only place a
    directed deletion can be authorized, and it is checked where it is stated: naming an object that
    is not DEPRECATED would sit on an *approved* request until BP-CMP-026 quietly dropped it at
    compile time, leaving the operator to believe the object had been retired while it is still on
    the instance.  The compile still re-checks everything (references, associated data); what is
    refused here is a declaration that could never mean what it says.
    """
    if not keys:
        return []
    unique = list(dict.fromkeys(keys))
    version = project.current_blueprint_version
    if not version:
        raise ValidationFailedError(
            "a change request can only retire objects from a Blueprint version the project has",
            details={"rule": "CP-CR-005", "fields": [{"path": "deletionCandidates"}]},
        )
    dispositions = {
        str(entry.business_key): str(getattr(entry, "disposition", "") or "").upper()
        for entry in ctx.blueprint.get_registry_entries(project.id, version)
    }
    unretirable = [key for key in unique if dispositions.get(key) != "DEPRECATED"]
    if unretirable:
        raise ValidationFailedError(
            "only a DEPRECATED Registry entry may be named as a deletion candidate",
            details={
                "rule": "CP-CR-005",
                "blueprintVersion": version,
                "notDeprecated": unretirable,
                "fields": [{"path": "deletionCandidates", "message": "must name DEPRECATED entries"}],
            },
        )
    return unique


def analyze(runtime: Runtime, cr_id: str, *, operator: str) -> dict[str, Any]:
    """CP-CR-003: S2 impact, plus every environment this project deployed to."""
    with runtime.transaction(operator=operator) as ctx:
        row = S.get_change_request(ctx.session, cr_id)
        ensure_writable(S.get_project(ctx.session, row.project_id))
        assert_cr_transition(row.status, ChangeRequestStatus.ANALYZED, cr_id=row.id)
        analysis = ctx.blueprint.analyze_impact(row.project_id, list(row.affected_business_keys or []))
        # CP-CR-005: the retirements are what the request itself declared.  S2's ``ImpactSet`` has no
        # deletion field, so reading them back off the analysis answered [] on the real provider and
        # overwrote the operator's declaration at the moment of analysis — the only step that
        # silently emptied it, which is why no fake ever caught it (S1's own double made up a rule).
        declared = list(row.deletion_candidates or [])
        project_id = row.project_id
        workflow = S.get_workflow_for_project(ctx.session, row.project_id)
        deployed_environments = sorted(
            {
                run.environment_key
                for run in S.runs_of(ctx.session, workflow.id)
                if run.status in (RunStatus.COMMITTED.value, RunStatus.VERIFIED.value)
            }
        )
        payload = {
            "changeRequestId": row.id,
            "affectedStages": list(analysis.affected_stages),
            "affectedObjects": list(analysis.affected_object_keys),
            "affectedPages": list(analysis.affected_pages),
            "affectedArtifactTypes": list(analysis.affected_artifact_types),
            "deletionCandidates": declared,
            "deployedEnvironments": deployed_environments,
        }
    # Content staging is external I/O: it runs after the checking transaction closed.
    artifact_id = _stage_impact_report(project_id, cr_id, payload)

    with runtime.transaction(operator=operator) as ctx:
        row = S.get_change_request(ctx.session, cr_id)
        register_handle(ctx, artifact_id)
        row.affected_stages = list(analysis.affected_stages)
        row.deletion_candidates = declared
        row.impact_report_artifact_id = artifact_id
        row.status = ChangeRequestStatus.ANALYZED.value
        ctx.session.flush()
        record_change(
            ctx.bind(workflow_id=workflow.id),
            event_type="change_request.updated",
            audit_event_type="cp.change_request.analyzed",
            object_type="ChangeRequest",
            object_id=row.id,
            payload=payload,
        )
        return {**_view(row), "deployedEnvironments": deployed_environments, "impact": payload}


#: The staged handle awaiting registration, keyed by change request id.
_STAGED: dict[str, Any] = {}


def _stage_impact_report(project_id: str, cr_id: str, payload: dict[str, Any]) -> str:
    """Stage the report content outside any unit of work (FD-ART-005)."""
    from factory_control.foundation import file_json

    handle = stage_content(
        iter([file_json(payload)]),
        project_id=project_id,
        artifact_type="CHANGE_IMPACT_REPORT",
        media_type="application/json",
    )
    _STAGED[cr_id] = handle
    return cr_id


def register_handle(ctx: Any, cr_id: str) -> str:
    """Register the staged report inside the caller's transaction."""
    handle = _STAGED.pop(cr_id)
    row = S.get_change_request(ctx.session, cr_id)
    artifact = register_artifact(
        handle,
        ArtifactCreate(
            project_id=row.project_id,
            type="CHANGE_IMPACT_REPORT",
            media_type="application/json",
            data_classification=DataClassification.INTERNAL,
            creator_subsystem="S1",
            workflow_id=S.get_workflow_for_project(ctx.session, row.project_id).id,
            status=ArtifactStatus.APPROVED,
            build_record={"changeRequestId": row.id},
        ),
        session=ctx.session,
        clock=ctx.clock,
    )
    return artifact.id


def approve(runtime: Runtime, cr_id: str, *, operator: str) -> dict[str, Any]:
    """CP-CR-002: approval needs the solution-architect role from M4 (CP-IAM-002)."""
    with runtime.transaction(operator=operator) as ctx:
        row = S.get_change_request(ctx.session, cr_id)
        ensure_writable(S.get_project(ctx.session, row.project_id))
        if row.status != ChangeRequestStatus.ANALYZED.value:
            raise InvalidStateError(
                f"a {row.status} change request cannot be approved; run the impact analysis first",
                details={"changeRequestId": row.id},
            )
        _require_architect_for(ctx, row, operator=operator)
        assert_cr_transition(row.status, ChangeRequestStatus.APPROVED, cr_id=row.id)
        row.status = ChangeRequestStatus.APPROVED.value
        row.approved_by = operator
        ctx.session.flush()
        record_change(
            ctx,
            event_type="change_request.updated",
            audit_event_type="cp.change_request.approved",
            object_type="ChangeRequest",
            object_id=row.id,
            payload={"approvedBy": operator},
        )
        return _view(row)


def reject(runtime: Runtime, cr_id: str, *, operator: str, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise InvalidStateError("rejecting a change request requires a reason")
    with runtime.transaction(operator=operator) as ctx:
        row = S.get_change_request(ctx.session, cr_id)
        assert_cr_transition(row.status, ChangeRequestStatus.REJECTED, cr_id=row.id)
        row.status = ChangeRequestStatus.REJECTED.value
        row.rejected_reason = reason
        ctx.session.flush()
        record_change(
            ctx,
            event_type="change_request.updated",
            audit_event_type="cp.change_request.rejected",
            object_type="ChangeRequest",
            object_id=row.id,
            payload={"reason": reason},
        )
        return _view(row)


def start(runtime: Runtime, cr_id: str, *, operator: str) -> dict[str, Any]:
    """CP-CR-004/006: reopen the main line at the earliest affected stage."""
    with runtime.transaction(operator=operator) as ctx:
        row = S.get_change_request(ctx.session, cr_id)
        project = S.get_project(ctx.session, row.project_id)
        ensure_writable(project)
        if S.in_progress_change_request(ctx.session, row.project_id) is not None:
            raise InvalidStateError(
                "this project already has an IN_PROGRESS change request (CP-BR-12)",
                details={"projectId": row.project_id},
            )
        assert_cr_transition(row.status, ChangeRequestStatus.IN_PROGRESS, cr_id=row.id)

        workflow = S.get_workflow_for_project(ctx.session, project.id)
        affected = [s for s in (row.affected_stages or []) if s]
        earliest = _earliest_stage_type(affected)
        _invalidate_from(ctx, workflow_id=workflow.id, from_stage=earliest)
        row.status = ChangeRequestStatus.IN_PROGRESS.value
        ctx.session.flush()
        record_change(
            ctx.bind(workflow_id=workflow.id),
            event_type="change_request.updated",
            audit_event_type="cp.change_request.started",
            object_type="ChangeRequest",
            object_id=row.id,
            payload={"reopenedFrom": earliest.value if earliest else None},
        )
        from factory_control.services.workflow import refresh_derived_state

        refresh_derived_state(ctx, workflow)
        return _view(row)


def complete(runtime: Runtime, cr_id: str, *, operator: str) -> dict[str, Any]:
    """CP-CR-007: main line passed again and a Run on the new version is VERIFIED."""
    with runtime.transaction(operator=operator) as ctx:
        row = S.get_change_request(ctx.session, cr_id)
        workflow = S.get_workflow_for_project(ctx.session, row.project_id)
        project = S.get_project(ctx.session, row.project_id)
        version = project.current_blueprint_version
        verified = [
            run
            for run in S.runs_of(ctx.session, workflow.id)
            if run.status == RunStatus.VERIFIED.value and run.blueprint_version == version
        ]
        if not verified:
            raise InvalidStateError(
                "the change is not finished: no VERIFIED run exists on the new version",
                details={"changeRequestId": row.id, "blueprintVersion": version},
            )
        assert_cr_transition(row.status, ChangeRequestStatus.COMPLETED, cr_id=row.id)
        row.status = ChangeRequestStatus.COMPLETED.value
        row.result_blueprint_version = version
        ctx.session.flush()
        record_change(
            ctx.bind(workflow_id=workflow.id),
            event_type="change_request.updated",
            audit_event_type="cp.change_request.completed",
            object_type="ChangeRequest",
            object_id=row.id,
            payload={"resultBlueprintVersion": version},
        )
        return _view(row)


def cancel(runtime: Runtime, cr_id: str, *, operator: str) -> dict[str, Any]:
    with runtime.transaction(operator=operator) as ctx:
        row = S.get_change_request(ctx.session, cr_id)
        assert_cr_transition(row.status, ChangeRequestStatus.CANCELLED, cr_id=row.id)
        row.status = ChangeRequestStatus.CANCELLED.value
        ctx.session.flush()
        record_change(
            ctx,
            event_type="change_request.updated",
            audit_event_type="cp.change_request.cancelled",
            object_type="ChangeRequest",
            object_id=row.id,
            payload={},
        )
        return _view(row)


def list_change_requests(runtime: Runtime, project_id: str, *, operator: str) -> list[dict[str, Any]]:
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        return [_view(r) for r in S.change_requests(ctx.session, project_id)]


def get_change_request(runtime: Runtime, cr_id: str, *, operator: str) -> dict[str, Any]:
    with runtime.transaction(operator=operator) as ctx:
        return _view(S.get_change_request(ctx.session, cr_id))


def _earliest_stage_type(names: list[str]) -> StageType | None:
    from factory_control.domain.invalidation import DESIGN_ORDER

    candidates: list[StageType] = []
    for name in names:
        try:
            candidates.append(StageType(name))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda s: DESIGN_ORDER.index(s) if s in DESIGN_ORDER else 99)


def _invalidate_from(ctx: Any, *, workflow_id: str, from_stage: StageType | None) -> None:
    """Rule: invalidate the affected stages, leaving unaffected ones alone (CP-CR-004)."""
    from factory_control.domain.enums import GateStatus, StageStatus
    from factory_control.domain.invalidation import stages_from
    from factory_control.services.gates import invalidate_gate
    from factory_control.services.workflow import transition

    if from_stage is None:
        return
    affected = set(stages_from(from_stage))
    for stage in S.stages_of(ctx.session, workflow_id):
        if stage.deployment_run_id is not None:
            continue
        try:
            stage_type = StageType(stage.stage_type)
        except ValueError:
            continue
        if stage_type not in affected:
            continue
        # LOCKED carries nothing to undo, and SKIPPED has no edges into
        # INVALIDATED in appendix A (it is a structural outcome of the delivery
        # mode or the absence of a Fixture spec, not a decision being superseded).
        if stage.status in (
            StageStatus.LOCKED.value,
            StageStatus.INVALIDATED.value,
            StageStatus.SKIPPED.value,
        ):
            continue
        transition(
            ctx,
            stage=stage,
            target=StageStatus.INVALIDATED,
            event_type="stage.invalidated",
            payload={"reason": "CHANGE_REQUEST", "fromStage": from_stage.value},
        )
    for gate in S.gates_of(ctx.session, workflow_id, status=GateStatus.WAITING):
        invalidate_gate(ctx, gate, reason="CHANGE_REQUEST")


__all__ = [
    "analyze",
    "approve",
    "cancel",
    "complete",
    "create_change_request",
    "get_change_request",
    "list_change_requests",
    "reject",
    "start",
]


def _require_architect_for(ctx: ControlContext, cr: Any, *, operator: str) -> None:
    """CP-CR-002's role check, inert while ``FACTORY_AUTH_MODE=off`` (CP-IAM-001)."""
    from factory_control import iam
    from factory_control.config import get_settings
    from factory_control.identity import directory, roles

    if not get_settings().auth_is_enforced():
        return
    account = directory.get_by_subject_any_status(ctx.session, operator)
    if account is None:
        raise iam.PermissionDeniedError(
            f"{operator!r} is not an account, so it cannot approve a change request",
            details={"action": iam.CHANGE_REQUEST_APPROVE, "operator": operator},
        )
    held = directory.roles_of(ctx.session, account.id, cr.project_id)
    if roles.SOLUTION_ARCHITECT not in held and not iam.get_permission_policy().is_allowed(
        operator=operator, project_id=cr.project_id, action=iam.CHANGE_REQUEST_APPROVE
    ):
        from factory_control.errors import InvalidStateError

        raise InvalidStateError(
            "approving a change request needs the Solution Architect role (CP-CR-002)",
            details={
                "changeRequestId": cr.id,
                "cause": "APPROVER_ROLE_REQUIRED",
                "requiredRole": roles.SOLUTION_ARCHITECT,
                "heldRoles": held,
            },
        )
