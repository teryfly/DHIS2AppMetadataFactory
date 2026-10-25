"""Gates, approvals and the COMMIT re-check (CP-GATE-001 … CP-GATE-019).

Three rules drive the structure of this module:

* an approval is bound to content hashes, so the hashes the approver saw are
  carried in the request and compared against live values (CP-GATE-009);
* approving a design Gate promotes the draft **inside the same unit of work**, so
  a promotion failure rolls the approval back and the Gate stays WAITING
  (CP-GATE-014, CP-AC-05);
* COMMIT re-runs the whole check server-side, including the three-way payload
  hash equality, and calls S4 only if everything holds (CP-GATE-017, AC-GATE-02).
"""

from __future__ import annotations

from typing import Any

from factory_control import store as S
from factory_control.domain import feedback as FB
from factory_control.domain import verdicts as V
from factory_control.domain.enums import (
    ChangeLevel,
    GateStatus,
    GateType,
    RefType,
    RunStatus,
    StageStatus,
    StageType,
    WarningDisposition,
)
from factory_control.domain.machines import assert_gate_transition, gate_can_act
from factory_control.errors import (
    ApprovalInvalidatedError,
    DriftDetectedError,
    GateNotApprovedError,
    InvalidStateError,
    NotFoundControlError,
)
from factory_control.foundation import FactoryError, new_id, normalize_hash
from factory_control.models import DeploymentRun, Gate, Stage
from factory_control.ports.dto import BoundRef
from factory_control.runtime import ControlContext, Runtime
from factory_control.services.recording import record_change, write_audit

#: Gates that close a design stage and therefore promote a Blueprint version.
DESIGN_GATE_VALUES: frozenset[str] = frozenset(g.value for g in GateType if g.is_design_gate)


def _warning_view(entry: dict[str, Any]) -> dict[str, Any]:
    """One WARNING as S5 renders it: one level name, not S2's ``level`` and S3's
    ``severity`` side by side (S5IF-FLD07)."""
    return {
        "issueId": entry.get("issueId") or entry.get("issue_id"),
        "ruleId": entry.get("ruleId") or entry.get("rule_id"),
        "level": entry.get("level") or entry.get("severity") or "WARNING",
        "message": entry.get("message") or entry.get("description") or "",
        "businessKey": entry.get("businessKey") or entry.get("business_key") or entry.get("objectKey"),
        "sourceStage": entry.get("sourceStage") or entry.get("source_stage"),
        "remediation": entry.get("remediation") or entry.get("suggestedAction"),
    }


def bound_ref_view(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "refType": r.get("refType") or r.get("ref_type"),
            "refId": r.get("refId") or r.get("ref_id"),
            "contentHash": r.get("contentHash") or r.get("content_hash"),
        }
        for r in refs
    ]


def gate_view(gate: Gate, approvals: list[Any] | None = None) -> dict[str, Any]:
    return {
        "id": gate.id,
        "type": gate.type,
        "workflowId": gate.workflow_id,
        "stageId": gate.stage_id,
        "deploymentRunId": gate.deployment_run_id,
        "environmentKey": gate.environment_key,
        "blueprintRef": gate.blueprint_ref,
        "boundObjects": bound_ref_view(list(gate.bound_refs or [])),
        "warnings": [_warning_view(w) for w in (gate.warnings or [])],
        "riskSummaryRef": gate.risk_summary_ref,
        "confirmationItems": list(gate.confirmation_items or []),
        "requiredApprovals": gate.required_approvals,
        "status": gate.status,
        "invalidatedReason": gate.invalidated_reason,
        "approvals": [
            {
                "id": a.id,
                "approverName": a.approver_name,
                "decision": a.decision,
                "comment": a.comment,
                "createdAt": a.created_at,
            }
            for a in (approvals or [])
        ],
        "createdAt": gate.created_at,
    }


def create_gate(
    ctx: ControlContext,
    *,
    gate_type: GateType,
    stage: Stage,
    bound_refs: list[BoundRef],
    warnings: list[dict[str, Any]] | None = None,
    risk_summary_ref: dict[str, Any] | None = None,
    confirmation_items: list[dict[str, Any]] | None = None,
    run: DeploymentRun | None = None,
    required_approvals: int = 1,
    blueprint_ref: dict[str, Any] | None = None,
) -> Gate:
    """CP-GATE-001/002/003/004 — one Gate per stage run, bound to frozen content."""
    existing = _waiting_gate(ctx, stage.id, gate_type.value, run.id if run else None)
    if existing is not None:
        invalidate_gate(ctx, existing, reason="RESUBMITTED", event=True)

    # One binding per object: the same artifact can reach a Gate through more than
    # one path (a report listed both directly and inside the build's artifact map).
    unique_refs = bound_ref_view(_dedupe([r.model_dump() for r in bound_refs]))

    gate = Gate(
        id=new_id("gat"),
        workflow_id=stage.workflow_id,
        stage_id=stage.id,
        deployment_run_id=run.id if run else None,
        type=gate_type.value,
        environment_key=run.environment_key if run else None,
        blueprint_ref=blueprint_ref or stage.input_blueprint_ref,
        bound_refs=unique_refs,
        warnings=list(warnings or []),
        risk_summary_ref=risk_summary_ref,
        confirmation_items=list(confirmation_items or []),
        required_approvals=required_approvals,
        status=GateStatus.WAITING.value,
    )
    ctx.session.add(gate)
    ctx.session.flush()

    record_change(
        ctx.bind(stage_id=stage.id),
        event_type="gate.created",
        audit_event_type="cp.gate.created",
        object_type="Gate",
        object_id=gate.id,
        payload={
            "gateId": gate.id,
            "gateType": gate.type,
            "stageId": stage.id,
            "boundObjects": len(gate.bound_refs or []),
            "warnings": len(gate.warnings or []),
        },
    )
    from factory_control.services.notifications import notify_gate_pending

    notify_gate_pending(ctx, gate)
    return gate


def _waiting_gate(ctx: ControlContext, stage_id: str, gate_type: str, run_id: str | None) -> Gate | None:
    from sqlalchemy import select

    query = select(Gate).where(
        Gate.stage_id == stage_id,
        Gate.type == gate_type,
        Gate.status == GateStatus.WAITING.value,
    )
    if run_id is None:
        query = query.where(Gate.deployment_run_id.is_(None))
    else:
        query = query.where(Gate.deployment_run_id == run_id)
    return ctx.session.execute(query).scalars().first()


def _dedupe(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for ref in refs:
        key = (str(ref.get("refType") or ref.get("ref_type")), str(ref.get("refId") or ref.get("ref_id")))
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def current_bound_hashes(ctx: ControlContext, gate: Gate) -> list[dict[str, Any]]:
    """Re-read every bound object's live hash (CP-GATE-005)."""
    out: list[dict[str, Any]] = []
    for ref in gate.bound_refs or []:
        ref_type = str(ref.get("refType") or ref.get("ref_type") or "")
        ref_id = str(ref.get("refId") or ref.get("ref_id") or "")
        recorded = str(ref.get("contentHash") or ref.get("content_hash") or "")
        out.append(
            {
                "refType": ref_type,
                "refId": ref_id,
                "recordedHash": recorded,
                "currentHash": _live_hash(ctx, ref_type, ref_id, gate),
            }
        )
    return out


def _live_hash(ctx: ControlContext, ref_type: str, ref_id: str, gate: Gate) -> str | None:
    from factory_control.foundation import find_artifact

    if ref_type == RefType.ARTIFACT.value:
        # A vanished artifact reads as "no current hash": the Gate is not treated as
        # changed, and the approval path reports the missing artifact with its own code.
        artifact = find_artifact(ref_id, session=ctx.session)
        return artifact.content_hash if artifact is not None else None
    if ref_type in (RefType.BLUEPRINT_DRAFT.value,):
        draft = ctx.blueprint.get_draft(ref_id)
        return draft.content_hash if draft is not None else None
    if ref_type == RefType.BLUEPRINT_VERSION.value:
        info = ctx.blueprint.get_current_version(ctx.project_id or "")
        return info.content_hash if info is not None else None
    if ref_type == RefType.ENVIRONMENT.value:
        return environment_fingerprint(ctx, ref_id)
    if ref_type == RefType.EXECUTION.value:
        execution = ctx.execution.get_execution(ref_id)
        return execution.payload_hash if execution is not None else None
    _ = gate
    return None


def environment_fingerprint(ctx: ControlContext, environment_key: str) -> str:
    """The one recipe for an environment's protective-state fingerprint.

    Binding an ENVIRONMENT object at DEPLOY_GATE creation and re-checking it at
    approval / COMMIT must use the identical key set, otherwise every approval
    would look stale the moment it is reviewed.
    """
    from factory_control.foundation import structure_hash

    protection = ctx.execution.get_protection(environment_key)
    return structure_hash(
        {
            "environmentKey": environment_key,
            "class": protection.environment_class,
            "locked": protection.emergency_locked,
            "windows": protection.commit_windows,
            "exportPolicy": protection.data_export_policy,
        }
    )


def has_binding_drift(ctx: ControlContext, gate: Gate) -> list[dict[str, Any]]:
    """Rows whose recorded hash no longer matches live content."""
    return [row for row in current_bound_hashes(ctx, gate) if _mismatch(row)]


def _mismatch(row: dict[str, Any]) -> bool:
    current = row.get("currentHash")
    recorded = row.get("recordedHash")
    if current is None or recorded is None:
        return False
    try:
        return normalize_hash(str(current)) != normalize_hash(str(recorded))
    except ValueError:
        return True


def invalidate_gate(
    ctx: ControlContext,
    gate: Gate,
    *,
    reason: str,
    event: bool = True,
    details: dict[str, Any] | None = None,
) -> Gate:
    """CP-GATE-005: bound content changed, so any pending approval is void."""
    if gate.status == GateStatus.INVALIDATED.value:
        return gate
    assert_gate_transition(gate.status, GateStatus.INVALIDATED, gate_id=gate.id)
    previous = gate.status
    gate.status = GateStatus.INVALIDATED.value
    gate.invalidated_reason = reason
    ctx.session.flush()
    if event:
        record_change(
            ctx,
            event_type="gate.invalidated",
            audit_event_type="cp.gate.invalidated",
            object_type="Gate",
            object_id=gate.id,
            workflow_id=gate.workflow_id,
            payload={"gateId": gate.id, "reason": reason, "details": details or {}},
            before={"status": previous},
            after={"status": GateStatus.INVALIDATED.value},
        )
        from factory_control.services.notifications import notify_gate_invalidated

        notify_gate_invalidated(ctx, gate)
    return gate


def seen_hashes_match(gate: Gate, seen: list[Any]) -> None:
    """CP-GATE-009 / CP-AC-04: reject an approval based on a stale page.

    S5IF-AC14 and UI-GATE-003 describe the payload as "the bound hashes the page
    showed", while SRS 2 describes the bound object as ``{refType, refId,
    contentHash}``.  Both spellings are accepted, and neither may raise: a raw
    ``item.get`` on a string turned a malformed client request into a 500.
    """
    pairs = [item for item in seen if isinstance(item, dict)]
    hashes = {str(item) for item in seen if isinstance(item, str)}
    seen_map = {str(item.get("refId")): str(item.get("contentHash", "")) for item in pairs if item.get("refId")}
    mismatches: list[dict[str, Any]] = []
    for ref in gate.bound_refs or []:
        ref_id = str(ref.get("refId"))
        ref_type = str(ref.get("refType") or ref.get("ref_type") or "")
        recorded = str(ref.get("contentHash", ""))
        if not pairs:
            # Hash-only form: the page is current only if this exact hash was shown.
            if recorded not in hashes:
                mismatches.append(_mismatch_entry(ref_type, ref_id, expected=recorded, actual=None))
            continue
        if ref_id not in seen_map:
            mismatches.append(_mismatch_entry(ref_type, ref_id, expected=recorded, actual=None))
        elif seen_map[ref_id] != recorded:
            mismatches.append(
                _mismatch_entry(ref_type, ref_id, expected=recorded, actual=seen_map[ref_id])
            )
    if mismatches:
        raise ApprovalInvalidatedError(
            "the content you are approving has changed; reload the Gate and approve again",
            details={"gateId": gate.id, "mismatches": mismatches},
        )


def _mismatch_entry(ref_type: str, ref_id: str, *, expected: str, actual: str | None) -> dict[str, Any]:
    """One row of ``APPROVAL_INVALIDATED.details.mismatches`` (SRS 8.3).

    ``expectedHash`` is always what the Gate is bound to and ``actualHash`` the other side
    of the comparison — what the page showed, or what the object hashes to now.  A null
    ``actualHash`` is the page never showed that object at all.
    """
    return {
        "refType": ref_type,
        "refId": ref_id,
        "expectedHash": expected,
        "actualHash": actual,
        "problem": "STALE" if actual else "NOT_PRESENTED",
    }


def _disposition_warnings(gate: Gate, dispositions: list[dict[str, Any]]) -> list[str]:
    """CP-GATE-008: every WARNING must be marked; MUST_FIX forces a rejection."""
    warnings = gate.warnings or []
    marked = {str(d.get("issueId")): str(d.get("disposition", "")) for d in dispositions}
    problems: list[str] = []
    for warning in warnings:
        issue_id = str(warning.get("issueId") or warning.get("issue_id") or "")
        disposition = marked.get(issue_id)
        if disposition is None:
            problems.append(f"WARNING {issue_id or '?'} is not dispositioned")
        elif disposition not in (WarningDisposition.ACCEPTED.value, WarningDisposition.MUST_FIX.value):
            problems.append(f"WARNING {issue_id} has an unknown disposition")
        elif disposition == WarningDisposition.MUST_FIX.value:
            problems.append(f"WARNING {issue_id} is marked MUST_FIX, so the Gate cannot be approved")
    for extra in set(marked) - {str(w.get("issueId") or w.get("issue_id")) for w in warnings}:
        problems.append(f"disposition refers to unknown warning {extra}")
    return problems


def _confirmations_required(gate: Gate) -> list[dict[str, Any]]:
    """CP-GATE-012 / CP-GATE-013: backup confirmation plus each non-default option."""
    items = list(gate.confirmation_items or [])
    if gate.type == GateType.DEPLOY_GATE.value:
        items.append(
            {
                "id": "OPERATIONAL_BACKUP_CONFIRMED",
                "label": "实例数据库备份已由运维确认 / instance database backup confirmed by operations",
                "required": True,
            }
        )
    return items


def _confirmations_missing(gate: Gate, confirmations: list[dict[str, Any]]) -> list[str]:
    given = {str(c.get("id")): bool(c.get("confirmed", False)) for c in confirmations}
    missing: list[str] = []
    for item in _confirmations_required(gate):
        item_id = str(item.get("id"))
        if item.get("required", True) and not given.get(item_id, False):
            missing.append(item_id)
    return missing


class _GateInvalidated(Exception):
    """Internal: the Gate must be invalidated, then a 409 reported.

    Carrying the intent out of the checking transaction is deliberate. An
    invalidation raised from inside the same unit of work rolls back with the
    error, leaving a Gate that S4 already considers stale in WAITING — exactly what
    CP-GATE-005 and CP-AC-08 forbid. So detection stays read-only here and
    :func:`_apply_invalidation` commits the state change on its own.
    """

    def __init__(self, error: FactoryError, reason: str, details: dict[str, Any]) -> None:
        super().__init__(error.error_code)
        self.error = error
        self.reason = reason
        self.details = details


def _apply_invalidation(runtime: Runtime, gate_id: str, carried: _GateInvalidated) -> None:
    """Commit a Gate invalidation in its own unit of work, then let the 409 surface."""
    with runtime.transaction(operator="system") as ctx:
        gate = ctx.session.get(Gate, gate_id)
        if gate is not None:
            invalidate_gate(ctx, gate, reason=carried.reason, details=carried.details)


def approve_gate(
    runtime: Runtime,
    gate_id: str,
    *,
    operator: str,
    seen_bound_hashes: list[dict[str, Any]],
    warning_dispositions: list[dict[str, Any]] | None = None,
    confirmations: list[dict[str, Any]] | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """CP-GATE-005 … CP-GATE-015."""
    drift = _deploy_drift_probe(runtime, gate_id, operator=operator)
    try:
        return _approve_gate_inner(runtime, gate_id, operator=operator,
                                   seen_bound_hashes=seen_bound_hashes,
                                   warning_dispositions=warning_dispositions,
                                   confirmations=confirmations, comment=comment,
                                   instance_drift=drift)
    except _GateInvalidated as carried:
        _apply_invalidation(runtime, gate_id, carried)
        raise carried.error from carried


def _deploy_drift_probe(runtime: Runtime, gate_id: str, *, operator: str) -> Any:
    """Ask S4 about drift *before* any unit of work is open, then return the answer.

    ``check_drift`` reads the target instance and S4 marks it ``@external_io``, so calling it
    from inside the approval transaction raised ``TRANSACTION_MISUSE`` out of a 500 the moment
    a real DEPLOY_GATE was approved.  A COMMIT already asks this question outside its
    transaction (CP-GATE-017); a Gate has to do the same (CP-WF-027).
    """
    with runtime.transaction(operator=operator) as ctx:
        gate = ctx.session.get(Gate, gate_id)
        if gate is None or gate.type != GateType.DEPLOY_GATE.value or not gate.environment_key:
            # A missing environment key stays `_check_deploy_gate`'s refusal to answer.
            return None
        environment_key, package_ref = gate.environment_key, _package_ref(gate)
    return runtime.execution.check_drift(environment_key, package_ref)


def _approve_gate_inner(
    runtime: Runtime,
    gate_id: str,
    *,
    operator: str,
    seen_bound_hashes: list[dict[str, Any]],
    warning_dispositions: list[dict[str, Any]] | None,
    confirmations: list[dict[str, Any]] | None,
    comment: str | None,
    instance_drift: Any = None,
) -> dict[str, Any]:
    with runtime.transaction(operator=operator) as ctx:
        gate = ctx.session.get(Gate, gate_id)
        if gate is None:
            raise NotFoundControlError(f"gate {gate_id} not found", details={"gateId": gate_id})

        from factory_control.services.project import ensure_writable

        project = S.get_project(ctx.session, gate_bound_project(ctx, gate))
        ensure_writable(project)

        if not gate_can_act(gate.status):
            raise InvalidStateError(
                f"gate is {gate.status} and no longer accepts a decision",
                details={"gateId": gate.id, "status": gate.status},
            )

        # Drifted bindings invalidate rather than reject (CP-GATE-005, AC-GATE-01).
        # `binding_drift`, not `drift`: the second one is a question to S4 about the target
        # instance, asked before this transaction opened, and the two are not interchangeable.
        binding_drift = has_binding_drift(ctx, gate)
        if binding_drift:
            raise _GateInvalidated(
                ApprovalInvalidatedError(
                    "bound content changed since this Gate was created",
                    details={
                        "gateId": gate.id,
                        "mismatches": [
                            _mismatch_entry(
                                str(row.get("refType") or ""),
                                str(row.get("refId") or ""),
                                expected=str(row.get("recordedHash") or ""),
                                actual=str(row.get("currentHash") or ""),
                            )
                            for row in binding_drift
                        ],
                    },
                ),
                reason="BOUND_CONTENT_CHANGED",
                details={"changed": binding_drift},
            )

        seen_hashes_match(gate, seen_bound_hashes)

        # M4's identity rules run before the checklist below: who may approve, and whether they
        # are the person who executed it, is more fundamental than a confirmation box — a
        # refusal that told an executor to tick a box first would send them down a path that
        # still ends in a refusal (CP-GATE-012, AC-GATE-04).
        _m4_approval_checks(ctx, gate, project=project, operator=operator)

        if gate.type == GateType.DEPLOY_GATE.value:
            _check_deploy_gate(ctx, gate, project=project, drift=instance_drift)

        stage = ctx.session.get(Stage, gate.stage_id) if gate.stage_id else None
        if gate.type in DESIGN_GATE_VALUES and stage is not None:
            from factory_control.services.questions import has_open_high_questions

            if has_open_high_questions(ctx, stage.id):
                # CP-Q-006
                raise InvalidStateError(
                    "a HIGH question is still open, so this design Gate cannot be approved",
                    details={
                        "stageId": stage.id,
                        "cause": "HIGH_QUESTION_OPEN",
                        "explanation": "a HIGH question is still open, so this design Gate cannot be approved",
                    },
                )

        problems = _disposition_warnings(gate, list(warning_dispositions or []))
        if problems:
            raise InvalidStateError(
                "; ".join(problems),
                details={"gateId": gate.id, "cause": "WARNING_UNRESOLVED", "problems": problems},
            )

        missing = _confirmations_missing(gate, list(confirmations or []))
        if missing:
            raise InvalidStateError(
                "approval requires these confirmations: " + ", ".join(missing),
                details={"gateId": gate.id, "missingConfirmations": missing},
            )

        from factory_control.models import Approval

        approval = Approval(
            id=new_id("apr"),
            gate_id=gate.id,
            approver_name=operator,
            approver_user_id=_authenticated_user_id(ctx),
            approver_roles=list(ctx.notes.get("approverRoles") or []),
            decision="APPROVED",
            comment=comment,
            warning_dispositions=list(warning_dispositions or []),
            confirmations=list(confirmations or []),
            seen_bound_hashes=list(seen_bound_hashes or []),
        )
        ctx.session.add(approval)
        approvals = list(gate.approvals or [])
        approvals.append(
            {
                "approverName": operator,
                "decision": "APPROVED",
                "approvalId": approval.id,
                "approverUserId": approval.approver_user_id,
            }
        )
        gate.approvals = approvals

        outcome: dict[str, Any] = {"gateId": gate.id, "status": GateStatus.WAITING.value}
        if len([a for a in approvals if a.get("decision") == "APPROVED"]) >= gate.required_approvals:
            outcome = _finalize_approval(ctx, gate, stage=stage, project=project, operator=operator)
        else:
            ctx.session.flush()
            write_audit(
                ctx,
                event_type="cp.gate.approved",
                object_type="Gate",
                object_id=gate.id,
                before={"status": gate.status},
                after={"status": gate.status, "approvals": len(approvals)},
            )
        return outcome


def _authenticated_user_id(ctx: ControlContext) -> str | None:
    """The account behind the name, when there is one (CP-IAM-001's M4 half).

    ``off`` mode has no accounts, and the approval row then records exactly what it recorded
    before: a name.  The NULL is the honest statement that the regime was the declared-name one.
    """
    return ctx.notes.get("operatorUserId") or None


def _m4_approval_checks(
    ctx: ControlContext, gate: Gate, *, project: Any, operator: str
) -> None:
    """Role check, multi-approver count and separation of duties (CP-GATE-007/012, AC-GATE-04).

    Everything here is inert while ``FACTORY_AUTH_MODE=off``: with no accounts there is no role
    to check, and an approval by two names that are one person cannot be detected either.  That
    is CP-IAM-001's bargain, stated in the one place that enforces it rather than scattered
    through the handlers.

    Four rules, all from CP-GATE-012 and AC-GATE-04:

    * the approver must hold the **precise** action, environment class included —
      ``gate.approve.DEPLOY_GATE.PRODUCTION`` is not ``gate.approve.DEPLOY_GATE``;
    * when S4's protection names an **operator role**, the approver must hold it.  The role comes
      from S4 read fresh, never cached (CP-WF-027);
    * one person may **not approve twice**.  CP-GATE-012's count is a count of *people*: without
      this, two clicks by the same name would satisfy a 2/2 production Gate;
    * the person who **executed** this Run may not approve its deployment Gate.  AC-GATE-04 is
      exactly that case, and it is the reason the executor's name is stored on the Run.
    """
    from factory_control import iam
    from factory_control.config import get_settings

    config = get_settings()
    if not config.auth_is_enforced():
        return

    from factory_control.identity import directory

    account = directory.get_by_subject_any_status(ctx.session, operator)
    if account is None:
        raise iam.PermissionDeniedError(
            f"{operator!r} is not an account in this Factory, so it cannot approve this Gate",
            details={"action": iam.gate_approve_action(gate.type), "operator": operator},
        )
    environment_class: str | None = None
    protection: Any = None
    if gate.type == GateType.DEPLOY_GATE.value and gate.environment_key:
        protection = ctx.execution.get_protection(gate.environment_key)
        environment_class = getattr(protection, "environment_class", None)
    precise_action = iam.gate_approve_action(gate.type, environment_class)
    held = directory.roles_of(ctx.session, account.id, gate_project_id(ctx, gate))
    ctx.notes["approverRoles"] = list(held)
    ctx.notes["operatorUserId"] = account.id
    # the coarse hook already ran in the handler; this is the environment-qualified one
    if not iam.get_permission_policy().is_allowed(
        operator=operator, project_id=gate_project_id(ctx, gate), action=precise_action
    ):
        raise iam.PermissionDeniedError(
            f"{operator!r} may not approve a {gate.type} for a {environment_class or 'unknown'} environment",
            details={"action": precise_action, "roles": held, "projectId": gate_project_id(ctx, gate)},
        )
    if protection is not None and getattr(protection, "operator_role", None):
        required_role = str(protection.operator_role)
        if required_role not in held:
            from factory_control.errors import InvalidStateError

            raise InvalidStateError(
                f"this environment's Gate may be approved by a {required_role} only",
                details={
                    "gateId": gate.id,
                    "cause": "APPROVER_ROLE_REQUIRED",
                    "requiredRole": required_role,
                    "heldRoles": held,
                },
            )
        # CP-GATE-012's 多人审批: the number is S4's statement about the environment, read now
        wanted = getattr(protection, "required_approvals", None)
        if wanted and int(wanted) > int(gate.required_approvals or 1):
            gate.required_approvals = int(wanted)

    from factory_control.errors import InvalidStateError

    prior = [entry for entry in list(gate.approvals or []) if entry.get("decision") == "APPROVED"]
    if any(
        (entry.get("approverUserId") or entry.get("approverName")) == (account.id or operator)
        for entry in prior
    ):
        raise InvalidStateError(
            "you have already approved this Gate; a required approval count is a count of people",
            details={
                "gateId": gate.id,
                "cause": "SEPARATION_OF_DUTIES",
                "reason": "ALREADY_APPROVED",
                "approvals": len(prior),
            },
        )
    if gate.type == GateType.DEPLOY_GATE.value and gate.deployment_run_id:
        from factory_control.models import DeploymentRun

        run = ctx.session.get(DeploymentRun, gate.deployment_run_id)
        # the executor of this Run is one of the people who may not sign its Gate; both names are
        # recorded on the Run precisely so the comparison is a lookup and not a reconstruction
        who_executed = {
            name
            for name in ((run.validate_operator, run.commit_operator) if run is not None else ())
            if name
        }
        if operator in who_executed:
            raise InvalidStateError(
                "the person who executed this Run may not approve its deployment Gate (AC-GATE-04)",
                details={
                    "gateId": gate.id,
                    "cause": "SEPARATION_OF_DUTIES",
                    "reason": "EXECUTOR_MAY_NOT_APPROVE",
                    "runId": gate.deployment_run_id,
                },
            )


def gate_project_id(ctx: ControlContext, gate: Gate) -> str:
    return gate_bound_project(ctx, gate)


def gate_bound_project(ctx: ControlContext, gate: Gate) -> str:
    from factory_control.models import Workflow

    workflow = ctx.session.get(Workflow, gate.workflow_id)
    if workflow is None:
        raise NotFoundControlError(f"unknown workflow {gate.workflow_id}")
    return workflow.project_id


def _finalize_approval(
    ctx: ControlContext,
    gate: Gate,
    *,
    stage: Stage | None,
    project: Any,
    operator: str,
) -> dict[str, Any]:
    """CP-GATE-014: promotion, PASSED, propagation, events and audit in one unit of work."""
    assert_gate_transition(gate.status, GateStatus.APPROVED, gate_id=gate.id)
    gate.status = GateStatus.APPROVED.value
    result: dict[str, Any] = {"gateId": gate.id, "status": GateStatus.APPROVED.value}

    if gate.type in DESIGN_GATE_VALUES and stage is not None:
        promoted = _promote_design_draft(ctx, gate=gate, project_id=project.id)
        result["blueprintVersion"] = promoted.version
        result["changeLevel"] = promoted.change_level
        result["affectedStages"] = list(promoted.affected_stages)
        project.current_blueprint_version = promoted.version

        from factory_control.services.workflow import complete_stage_as_passed

        complete_stage_as_passed(
            ctx,
            stage=stage,
            output_artifact_ids=[promoted.artifact_id] if promoted.artifact_id else [],
            source="GATE_APPROVED",
        )
        from factory_control.services.workflow import apply_propagation_from_promotion

        plan = apply_propagation_from_promotion(
            ctx,
            project_id=project.id,
            change_level=ChangeLevel(promoted.change_level),
            affected_stage_names=list(promoted.affected_stages),
            # CP-WF-015 rule 1 protects "非当前阶段", and ``complete_stage_as_passed`` just moved
            # ``workflow.current_stage_id`` on to the successor — so the stage whose Gate this
            # approval closed would otherwise be invalidated by its own promotion, because
            # BP-IMP-003 counts a section's own stage among "that stage plus every later stage".
            protect_stage_ids=[stage.id],
        )
        result["invalidation"] = plan.as_dict()
    elif gate.type == GateType.PACKAGE_GATE.value and stage is not None:
        # CP-GATE-015: approving PACKAGE_GATE closes R2 and makes R3 the next step.
        from factory_control.services.workflow import complete_stage_as_passed

        complete_stage_as_passed(
            ctx, stage=stage, output_artifact_ids=list(stage.output_artifact_ids or []),
            source="PACKAGE_GATE_APPROVED",
        )
        run = ctx.session.get(DeploymentRun, gate.deployment_run_id) if gate.deployment_run_id else None
        if run is not None and run.status == RunStatus.WAITING_APPROVAL.value:
            run.status = RunStatus.IN_PROGRESS.value
            ctx.session.flush()
        result["nextStageType"] = StageType.DHIS2_VALIDATE.value
    elif gate.type == GateType.DEPLOY_GATE.value and stage is not None:
        # CP-GATE-015: R3 closes and COMMIT becomes available, but is never run by itself.
        from factory_control.services.workflow import complete_stage_as_passed

        complete_stage_as_passed(
            ctx, stage=stage, output_artifact_ids=list(stage.output_artifact_ids or []),
            source="DEPLOY_GATE_APPROVED",
        )
        run = ctx.session.get(DeploymentRun, gate.deployment_run_id) if gate.deployment_run_id else None
        if run is not None and run.status == RunStatus.WAITING_APPROVAL.value:
            run.status = RunStatus.IN_PROGRESS.value
            ctx.session.flush()
        result["commitAvailable"] = True
    elif gate.type == GateType.HANDOFF_GATE.value:
        # Approving the delivery closes the stage: the package and the 17.3 check report are
        # already S2's artifacts, produced when the stage ran, so this promotes nothing — it says
        # a human reviewed the bytes bound to this Gate and agrees they can go.  Without the
        # transition the milestone would read HANDED_OFF while the stage that produced the
        # package still showed WAITING_APPROVAL, and the design line would never be closed.
        if stage is not None:
            from factory_control.services.workflow import complete_stage_as_passed

            complete_stage_as_passed(
                ctx,
                stage=stage,
                output_artifact_ids=list(stage.output_artifact_ids or []),
                source="HANDOFF_GATE_APPROVED",
            )
        project.milestone_status = "HANDED_OFF"
        ctx.session.flush()
        result["delivered"] = True

    ctx.session.flush()
    record_change(
        ctx.bind(workflow_id=gate.workflow_id),
        event_type="gate.approved",
        audit_event_type="cp.gate.approved",
        object_type="Gate",
        object_id=gate.id,
        payload={"gateId": gate.id, "approver": operator, "result": _compact(result)},
    )
    from factory_control.services.notifications import notify_gate_approved

    notify_gate_approved(ctx, gate)
    return result


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if isinstance(v, (str, bool, int, float, list))}


def _promote_design_draft(ctx: ControlContext, *, gate: Gate, project_id: str) -> Any:
    """S2 promote inside the caller's transaction (CP-ASM-01, CP-GATE-014)."""
    ref = dict(gate.blueprint_ref or {})
    draft_id = str(ref.get("draftId") or "")
    expected = str(ref.get("hash") or ref.get("contentHash") or "")
    if not draft_id or not expected:
        raise InvalidStateError(
            "this Gate does not bind a promotable draft",
            details={"gateId": gate.id, "blueprintRef": ref},
        )
    return ctx.blueprint.promote_draft(
        project_id,
        draft_id,
        expected,
        f"promote:{gate.id}",
        session=ctx.session,
    )


def _check_deploy_gate(ctx: ControlContext, gate: Gate, *, project: Any, drift: Any = None) -> None:
    """CP-GATE-012 / CP-GATE-013 / CP-WF-020, live from S4 (CP-WF-027)."""
    environment_key = gate.environment_key
    if not environment_key:
        raise InvalidStateError("DEPLOY_GATE has no environment key", details={"gateId": gate.id})
    protection = ctx.execution.get_protection(environment_key)
    if protection.emergency_locked:
        raise V.environment_locked(protection, environment_key=environment_key)
    if not protection.within_commit_window:
        raise V.outside_commit_window(
            protection, now=ctx.clock.now(), environment_key=environment_key
        )
    if drift is None:
        # Reaching this means the caller approved a DEPLOY_GATE without asking S4 first,
        # which is the bug this signature exists to make impossible to reintroduce.
        raise InvalidStateError(
            "the drift check must be asked outside the unit of work that approves this Gate",
            details={"gateId": gate.id, "environmentKey": environment_key},
        )
    if drift.drifted:
        raise _GateInvalidated(
            DriftDetectedError(
                "the target environment drifted since VALIDATE; the Gate is invalidated",
                details={"environmentKey": environment_key, "objects": drift.objects[:5]},
            ),
            reason="DRIFT_DETECTED",
            details={"objects": drift.objects},
        )
    if protection.environment_class.upper() == "PRODUCTION":
        _require_prior_verification(ctx, gate, project)


def _package_ref(gate: Gate) -> str:
    for ref in gate.bound_refs or []:
        if (ref.get("refType") or ref.get("ref_type")) == RefType.ARTIFACT.value:
            return str(ref.get("refId") or ref.get("ref_id") or "")
    return ""


def _require_prior_verification(ctx: ControlContext, gate: Gate, project: Any) -> None:
    """CP-WF-020 / CP-AC-07: same version must already be VERIFIED elsewhere."""
    run = ctx.session.get(DeploymentRun, gate.deployment_run_id) if gate.deployment_run_id else None
    version = run.blueprint_version if run is not None else project.current_blueprint_version
    siblings = ctx.session.query(DeploymentRun).filter(
        DeploymentRun.workflow_id == gate.workflow_id,
        DeploymentRun.status == RunStatus.VERIFIED.value,
    ).all()
    for sibling in siblings:
        if sibling.blueprint_version != version:
            continue
        protection = ctx.execution.get_protection(sibling.environment_key)
        if protection.environment_class.upper() != "PRODUCTION":
            return
    # CP-WF-020's exemption arrives with M4's permissions: an administrator may waive this
    # precondition for a stated reason and a window, and the waiver is reported rather than
    # silently obeyed -- the reviewer reading the Gate has to see that it was stepped around.
    from factory_control.identity import directory

    waiver = directory.active_exemption(
        ctx.session, project.id, "NON_PRODUCTION_VERIFIED", environment_key=str(gate.environment_key or "")
    )
    if waiver is not None:
        ctx.notes["productionExemption"] = {"id": waiver.id, "waiver": waiver.waiver, "reason": waiver.reason}
        return
    raise V.prior_verification_missing(
        environment_key=str(gate.environment_key or ""), blueprint_version=version
    )


def reject_gate(
    runtime: Runtime,
    gate_id: str,
    *,
    operator: str,
    reason: str,
    warning_dispositions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """CP-GATE-010: rejection needs a reason; the stage returns to READY."""
    if not reason or not reason.strip():
        raise InvalidStateError(
            "a rejection must state a reason", details={"gateId": gate_id, "cause": "COMMENT_REQUIRED"}
        )
    with runtime.transaction(operator=operator) as ctx:
        gate = ctx.session.get(Gate, gate_id)
        if gate is None:
            raise NotFoundControlError(f"gate {gate_id} not found", details={"gateId": gate_id})
        if not gate_can_act(gate.status):
            raise InvalidStateError(
                f"gate is {gate.status} and no longer accepts a decision", details={"gateId": gate.id}
            )
        seen: list[dict[str, Any]] = []
        assert_gate_transition(gate.status, GateStatus.REJECTED, gate_id=gate.id)

        from factory_control.models import Approval

        ctx.session.add(
            Approval(
                id=new_id("apr"),
                gate_id=gate.id,
                approver_name=operator,
                decision="REJECTED",
                comment=reason,
                warning_dispositions=list(warning_dispositions or []),
                confirmations=[],
                seen_bound_hashes=list(seen),
            )
        )
        approvals = list(gate.approvals or [])
        approvals.append({"approverName": operator, "decision": "REJECTED"})
        gate.approvals = approvals
        gate.status = GateStatus.REJECTED.value
        ctx.session.flush()

        stage = ctx.session.get(Stage, gate.stage_id) if gate.stage_id else None
        feedback: dict[str, Any] = FB.stage_feedback(
            FB.GATE_REJECTED,
            reason,
            gate_id=gate.id,
            created_at=ctx.clock.now(),
            extra={"rejectedBy": operator},
        )
        if stage is not None and stage.status == StageStatus.WAITING_APPROVAL.value:
            from factory_control.services.workflow import reject_stage

            reject_stage(ctx, stage=stage, feedback=feedback)

        record_change(
            ctx.bind(workflow_id=gate.workflow_id),
            event_type="gate.rejected",
            audit_event_type="cp.gate.rejected",
            object_type="Gate",
            object_id=gate.id,
            payload={"gateId": gate.id, "reason": reason, "stageId": gate.stage_id},
        )
        from factory_control.services.notifications import notify_gate_rejected

        notify_gate_rejected(ctx, gate, reason=reason)
        return {"gateId": gate.id, "status": GateStatus.REJECTED.value, "stageId": gate.stage_id}


def require_approved_deploy_gate(ctx: ControlContext, run: DeploymentRun) -> Gate:
    """CP-GATE-017: no COMMIT without an APPROVED, still-valid DEPLOY_GATE."""
    from sqlalchemy import select

    gate = ctx.session.execute(
        select(Gate)
        .where(
            Gate.deployment_run_id == run.id,
            Gate.type == GateType.DEPLOY_GATE.value,
        )
        .order_by(Gate.created_at.desc())
    ).scalars().first()
    if gate is None:
        raise GateNotApprovedError(
            "no DEPLOY_GATE exists for this run; run DHIS2_VALIDATE first",
            details={"deploymentRunId": run.id},
        )
    if gate.status != GateStatus.APPROVED.value:
        raise GateNotApprovedError(
            f"DEPLOY_GATE is {gate.status}, not APPROVED",
            details={"gateId": gate.id, "status": gate.status, "deploymentRunId": run.id},
        )
    return gate


def explanation(ctx: ControlContext, gate: Gate) -> dict[str, Any]:
    """CP-GATE-019: what was found, what will happen, why, and what can change."""
    decisions: list[dict[str, Any]] = []
    ref = dict(gate.blueprint_ref or {})
    decision_ids = [str(d) for d in (ref.get("decisionIds") or [])]
    if decision_ids and ctx.project_id:
        decisions = [d.model_dump(by_alias=True) for d in ctx.blueprint.get_decisions(ctx.project_id, decision_ids)]

    warnings = list(gate.warnings or [])
    open_questions: list[dict[str, Any]] = []
    if gate.stage_id:
        from factory_control.services.questions import open_questions as _open

        open_questions = [
            {"id": q.id, "text": q.text, "severity": q.severity} for q in _open(ctx, gate.stage_id)
        ]

    return {
        "gateId": gate.id,
        "gateType": gate.type,
        "found": {"boundObjects": bound_ref_view(list(gate.bound_refs or []))},
        "willDo": _will_do(gate),
        "why": {"decisions": decisions},
        "uncertain": {"warnings": warnings, "openQuestions": open_questions},
        "canChange": _can_change(gate),
        "afterApproval": _after_approval(gate),
    }


def _will_do(gate: Gate) -> dict[str, Any]:
    return {
        "gateType": gate.type,
        "environmentKey": gate.environment_key,
        "riskSummaryRef": gate.risk_summary_ref,
        "confirmationItems": list(gate.confirmation_items or []),
    }


def _can_change(gate: Gate) -> list[dict[str, Any]]:
    if gate.type in DESIGN_GATE_VALUES:
        return [
            {"action": "patch.submit", "target": "working draft", "invalidatesGate": True},
            {"action": "question.answer", "target": "open questions", "invalidatesGate": False},
            {"action": "stage.rerun", "target": "this stage", "invalidatesGate": True},
        ]
    if gate.type == GateType.PACKAGE_GATE.value:
        return [{"action": "stage.rerun", "target": "STATIC_VALIDATION", "invalidatesGate": True}]
    if gate.type == GateType.DEPLOY_GATE.value:
        return [
            {"action": "stage.rerun", "target": "DHIS2_VALIDATE", "invalidatesGate": True},
            {"action": "deployment-runs.cancel", "target": "this run", "invalidatesGate": False},
        ]
    return [{"action": "stage.rerun", "target": "HANDOFF", "invalidatesGate": True}]


def _after_approval(gate: Gate) -> dict[str, Any]:
    if gate.type in DESIGN_GATE_VALUES:
        return {
            "createsBlueprintVersion": True,
            "invalidatesStages": "per change level (CP-WF-015)",
            "next": "the following design stage becomes READY",
        }
    if gate.type == GateType.PACKAGE_GATE.value:
        return {"next": StageType.DHIS2_VALIDATE.value, "environmentKey": gate.environment_key}
    if gate.type == GateType.DEPLOY_GATE.value:
        return {
            "next": "COMMIT becomes available but is not started automatically (CP-GATE-015)",
            "environmentKey": gate.environment_key,
        }
    return {"next": "milestone HANDED_OFF", "environmentKey": gate.environment_key}


def gate_summaries(
    runtime: Runtime,
    *,
    operator: str,
    status: str | None = None,
    project_id: str | None = None,
    gate_type: str | None = None,
    mine: bool = False,
    sort_by: str = "createdAt",
    sort_dir: str = "DESC",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """The Dashboard's "all Gates awaiting approval" list (S5IF-FLD03 【裁决】).

    Gates are S1's own rows, so this is one query with three grouped companions — never a
    query per Gate: the first screen of UI-PRJ-001 has three seconds to paint (UI-NFR-01)
    and the projects it spans are unbounded.  MVP returns every Gate; the M4 role filter
    turns it into "awaiting *me*" (CP-IAM).
    """
    from sqlalchemy import select

    from factory_control.models import Project, Workflow

    with runtime.transaction(operator=operator) as ctx:
        rows, total = S.list_gates(
            ctx.session,
            status=status,
            project_id=project_id,
            gate_type=gate_type,
            sort_by=sort_by,
            descending=sort_dir != "ASC",
            limit=limit,
            offset=offset,
        )
        if mine:
            rows = [gate for gate in rows if _may_approve(ctx, gate, operator=operator)]
        facts = S.approval_facts(ctx.session, [gate.id for gate in rows])
        high = S.high_open_question_counts(
            ctx.session, [gate.stage_id for gate in rows if gate.stage_id]
        )
        workflow_ids = list({gate.workflow_id for gate in rows})
        owner: dict[str, str] = {}
        if workflow_ids:
            for workflow_id, project_id_of in ctx.session.execute(
                select(Workflow.id, Workflow.project_id).where(Workflow.id.in_(workflow_ids))
            ).all():
                owner[str(workflow_id)] = str(project_id_of)
        project_ids = list({value for value in owner.values() if value})
        names: dict[str, str] = {}
        if project_ids:
            for project_id_row, name in ctx.session.execute(
                select(Project.id, Project.name).where(Project.id.in_(project_ids))
            ).all():
                names[str(project_id_row)] = str(name)

        items: list[dict[str, Any]] = []
        for gate in rows:
            warnings = list(gate.warnings or [])
            _count, resolved = facts.get(gate.id, (0, set()))
            unresolved = sum(
                1
                for warning in warnings
                if str(warning.get("issueId") or warning.get("issue_id") or "") not in resolved
            )
            items.append(
                {
                    "id": gate.id,
                    "workflowId": gate.workflow_id,
                    "projectId": owner.get(gate.workflow_id),
                    "projectName": names.get(owner.get(gate.workflow_id, "")),
                    "stageId": gate.stage_id,
                    "deploymentRunId": gate.deployment_run_id,
                    "environmentKey": gate.environment_key,
                    "type": gate.type,
                    "status": gate.status,
                    "requiredApprovals": gate.required_approvals,
                    "approvalCount": _count,
                    "warningCount": len(warnings),
                    "unresolvedWarningCount": unresolved,
                    "highOpenQuestionCount": high.get(gate.stage_id or "", 0),
                    "createdAt": gate.created_at,
                }
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}


__all__ = [
    "DESIGN_GATE_VALUES",
    "approve_gate",
    "bound_ref_view",
    "create_gate",
    "current_bound_hashes",
    "environment_fingerprint",
    "explanation",
    "gate_bound_project",
    "gate_summaries",
    "gate_view",
    "has_binding_drift",
    "invalidate_gate",
    "reject_gate",
    "require_approved_deploy_gate",
    "seen_hashes_match",
]


def _may_approve(ctx: ControlContext, gate: Gate, *, operator: str) -> bool:
    """Whether this Gate is one *this* person is standing in a position to approve."""
    from factory_control import iam
    from factory_control.config import get_settings

    if not get_settings().auth_is_enforced():
        return True
    environment_class: str | None = None
    if gate.type == GateType.DEPLOY_GATE.value and gate.environment_key:
        environment_class = getattr(ctx.execution.get_protection(gate.environment_key), "environment_class", None)
    action = iam.gate_approve_action(gate.type, environment_class)
    return iam.get_permission_policy().is_allowed(
        operator=operator, project_id=gate_bound_project(ctx, gate), action=action
    )
