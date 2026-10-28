"""``allowed-actions`` computation (CP-WF-025 / 026 / 027).

The response lists both what the user may do and what they may not, each with a
reason code, so "Explain Before Execute" (总体 SRS P-06) needs no client-side
guessing.  Environment protection facts are passed in by the service, read fresh
from S4 — never cached here (CP-WF-027).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_control import iam
from factory_control.domain.enums import (
    CHATABLE_STAGE_STATUSES,
    DIALOGUE_STAGE_TYPES,
    ChangeRequestStatus,
    GateStatus,
    RunStatus,
    StageStatus,
    StageType,
)
from factory_control.domain.machines import CR_TRANSITIONS

# --- Reason codes (CP-WF-026) ---------------------------------------------

GATE_NOT_APPROVED = "GATE_NOT_APPROVED"
ENVIRONMENT_LOCKED = "ENVIRONMENT_LOCKED"
#: An environment S4 has switched off. Not the same fact as a lock: a lock is a human hold on
#: commits that a person lifts, a disable takes the whole environment out of service.
ENVIRONMENT_DISABLED = "ENVIRONMENT_DISABLED"
OUTSIDE_COMMIT_WINDOW = "OUTSIDE_COMMIT_WINDOW"
PRIOR_VERIFICATION_MISSING = "PRIOR_VERIFICATION_MISSING"
PROJECT_ARCHIVED = "PROJECT_ARCHIVED"
WORKFLOW_PAUSED = "WORKFLOW_PAUSED"
WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
ALREADY_RUNNING_ELSEWHERE = "ALREADY_RUNNING_ELSEWHERE"
STAGE_NOT_READY = "STAGE_NOT_READY"
STAGE_NOT_WAITING = "STAGE_NOT_WAITING"
STAGE_NOT_IN_MANUAL_CHECK = "STAGE_NOT_IN_MANUAL_CHECK"
NO_OPEN_QUESTION = "NO_OPEN_QUESTION"
STAGE_NOT_PASSED = "STAGE_NOT_PASSED"
DESIGN_LINE_INCOMPLETE = "DESIGN_LINE_INCOMPLETE"
RUN_OPEN_FOR_ENVIRONMENT = "RUN_OPEN_FOR_ENVIRONMENT"
NO_OPEN_RUN = "NO_OPEN_RUN"
GATE_NOT_WAITING = "GATE_NOT_WAITING"
HIGH_QUESTION_OPEN = "HIGH_QUESTION_OPEN"
WARNING_UNRESOLVED = "WARNING_UNRESOLVED"
MAPPING_FROZEN_BY_GATE = "MAPPING_FROZEN_BY_GATE"
#: M4: the state permits it and the role table does not (CP-WF-026's permission reason).
PERMISSION_DENIED = "PERMISSION_DENIED"
MEMBERSHIP_REQUIRED = "MEMBERSHIP_REQUIRED"
APPROVER_ROLE_REQUIRED = "APPROVER_ROLE_REQUIRED"
SEPARATION_OF_DUTIES = "SEPARATION_OF_DUTIES"
#: A change request is offered only what its own state machine allows (CP-CR-002).
STATE_NOT_ALLOWED = "STATE_NOT_ALLOWED"
#: CP-UI-023 / CP-AC-32: the action is M4's and MVP refuses it — the endpoint answers
#: 409 ``FEATURE_NOT_ENABLED`` for the same reason the button is off.
FEATURE_NOT_ENABLED = "FEATURE_NOT_ENABLED"
#: CP-UI-021: the dev-package download exists as soon as HANDOFF has delivered one.
PACKAGE_NOT_GENERATED = "PACKAGE_NOT_GENERATED"
#: CP-PRJ-007: an archived project is deletable only once an export of it exists, so the
#: delete button has to say "export first" rather than light up and then meet a 409.
EXPORT_NOT_TAKEN = "EXPORT_NOT_TAKEN"

#: Actions that exist on a workflow / project.
PROJECT_ACTIONS: tuple[str, ...] = (
    "project.edit",
    "project.archive",
    "project.delete",
    "project.export",
    "project.upgrade_mode",
    "input.upload",
    "deployment.create",
    "change_request.create",
)

WORKFLOW_ACTIONS: tuple[str, ...] = (
    "workflow.pause",
    "workflow.resume",
    "workflow.cancel",
)

#: Lifecycle actions of a change request, offered per change request (CP-CR-002).
CHANGE_REQUEST_ACTIONS: tuple[str, ...] = (
    "change_request.impact",
    "change_request.approve",
    "change_request.reject",
    "change_request.start",
    "change_request.complete",
    "change_request.cancel",
)

#: Actions whose REST exit was missing until S5IF chapter 4-7.  They are listed
#: here so ``allowed-actions`` stays the single source of truth for whether a button
#: works (S5IF-G10 / CP-WF-025), including the environment actions S5 routes to S4.
FORWARDING_ACTIONS: tuple[str, ...] = (
    "input.parse",
    "input.confirm_column_mapping",
    "artifact.explain",
    "artifact.download",
    "agent.cancel",
    "registry_binding.set",
    "uid_pool.replenish",
    "governance.provider.register",
    "governance.provider.enable",
    "governance.model.register",
    "governance.model.enable",
    "governance.prompt.activate",
    "governance.regression.record",
    "governance.evaluation.run",
    "governance.policy.relax",
    "governance.budget.set",
    "artifact.purge",
    iam.FRONTEND_PACKAGE_DOWNLOAD,
)

#: Environment actions carry the environment key as ``targetId``.
ENVIRONMENT_ACTIONS: tuple[str, ...] = (
    "environment.create",
    "environment.edit",
    "environment.disable",
    "environment.test_connection",
    "environment.probe_capabilities",
    "environment.emergency_lock",
)

#: Actions that belong to the platform rather than to any project or workflow: the
#: Administration pages (SRS 18.1 Environments, Governance, Settings) have no workflow to ask,
#: so their availability has to come from an endpoint that does not need one.  FR-UI-009 makes
#: this the only acceptable source -- a console that decided for itself would be deciding who
#: may lock a production environment.
PLATFORM_ACTIONS: tuple[str, ...] = (
    "environment.create",
    # The SRS's own vocabulary (CP-UI-019) and `PUT /settings`'s `authorize` call both say
    # `settings.manage`; `settings.update` was an audit event's suffix that leaked into this list,
    # which left the console gating on a name the server never puts in the answer.  A spelling that
    # only one side of the wire uses is not a typo to average out — the endpoint is the authority.
    iam.SETTINGS_MANAGE,
    # ``POST /projects`` guards itself with exactly this name (routes/projects.py), and creating the
    # first project is the step the guided setup turns green on — so the console has to be able to
    # ask.  services/project.py already calls it a platform action; until this entry the guard and
    # the answer disagreed, and a console had no honest way to draw the button.
    iam.PROJECT_CREATE,
    "project.import",
    "governance.provider.register",
    "governance.provider.enable",
    "governance.model.register",
    "governance.model.enable",
    "governance.prompt.activate",
    "governance.regression.record",
    "governance.evaluation.run",
    "governance.policy.relax",
    "governance.budget.set",
    # S5IF-SEC01…03: the console can enter a credential without leaving the page it is on, so it
    # has to be able to ask whether that control exists at all (UI-BR-01 / FR-UI-009).
    iam.SECRET_MANAGE,
    # M4's Administration screens have no workflow either, and these are the actions their own
    # ``authorize`` calls check (identity/roles.py).  A page that cannot ask has no legitimate way
    # to know whether to draw the control, so the list that the guard already consults must answer.
    iam.USER_MANAGE,
    iam.MEMBER_MANAGE,
    iam.RESTRICTED_GRANT_MANAGE,
    iam.PRODUCTION_EXEMPTION_MANAGE,
)

#: Which platform entries are the caller's *rights* rather than a state the server can observe.
#: They are listed here so the platform page can be checked against the role table; the filtering
#: itself is `_apply_permissions`, shared with every other list.
POLICY_GATED_ACTIONS: frozenset[str] = frozenset(
    {
        iam.USER_MANAGE,
        iam.MEMBER_MANAGE,
        iam.RESTRICTED_GRANT_MANAGE,
        iam.SECRET_MANAGE,
        iam.PRODUCTION_EXEMPTION_MANAGE,
    }
)

#: The appendix-B actions whose endpoint writes stay gated until M4 (CP-UI-023).
#: Reading members, grants or exemptions stays available and decides nothing (CP-IAM-001).
M4_GATED_ACTIONS: frozenset[str] = frozenset(
    {iam.MEMBER_MANAGE, iam.RESTRICTED_GRANT_MANAGE, iam.PRODUCTION_EXEMPTION_MANAGE}
)

STAGE_ACTIONS: tuple[str, ...] = (
    "stage.run",
    "stage.rerun",
    "stage.chat",
    "stage.submit_for_approval",
    "stage.resolve_manual_check",
    "patch.submit",
    "question.answer",
    "gate.approve",
    "gate.reject",
    "execution.commit",
    "deployment.cancel",
    # Emitted per run by ``run_actions`` just below the tuple this list is read from. The console that
    # offers a re-check gates on this name, and an inventory that omits an action a builder does emit
    # reports that console as inventing it.
    "deployment_run.drift_check",
)


@dataclass(frozen=True)
class Action:
    name: str
    available: bool
    reason_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    target_id: str | None = None
    #: the permission the endpoint actually checks when it differs from ``name`` (a Gate's
    #: precise ``gate.approve.<GATE_TYPE>`` grant never covers the coarse ``gate.approve``)
    permission: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action": self.name, "available": self.available}
        if self.target_id:
            out["targetId"] = self.target_id
        if not self.available and self.reason_code:
            out["reasonCode"] = self.reason_code
        if self.details:
            out["details"] = dict(self.details)
        return out


@dataclass(frozen=True)
class StageSnapshot:
    stage_id: str
    stage_type: StageType
    status: StageStatus
    has_open_question: bool = False


@dataclass(frozen=True)
class ChangeRequestSnapshot:
    """One change request's own state, as far as availability needs it."""

    cr_id: str
    status: ChangeRequestStatus


@dataclass(frozen=True)
class GateSnapshot:
    gate_id: str
    gate_type: str
    status: GateStatus
    stage_id: str | None = None
    deployment_run_id: str | None = None
    environment_key: str | None = None
    open_warnings: int = 0


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    environment_key: str
    blueprint_version: str
    status: RunStatus
    deploy_gate_approved: bool = False


@dataclass(frozen=True)
class EnvironmentFacts:
    """Read live from S4 by the service (CP-WF-027)."""

    environment_key: str
    environment_class: str = "TEST"
    emergency_locked: bool = False
    within_commit_window: bool = True
    #: S4's ``EnvironmentStatus``: a DISABLED environment answers no operation about the
    #: instance, so the controls for those operations have to say so before the click.
    enabled: bool = True


@dataclass(frozen=True)
class ActionContext:
    archived: bool = False
    cancelled: bool = False
    paused: bool = False
    delivery_mode: str = "A"
    design_line_complete: bool = False
    architecture_gate_approved: bool = False
    stages: list[StageSnapshot] = field(default_factory=list)
    gates: list[GateSnapshot] = field(default_factory=list)
    runs: list[RunSnapshot] = field(default_factory=list)
    change_requests: list[ChangeRequestSnapshot] = field(default_factory=list)
    environments: list[EnvironmentFacts] = field(default_factory=list)
    verified_version_keys: set[str] = field(default_factory=set)
    open_run_exists: bool = False
    #: CP-UI-021: S1's own artifact index says a FRONTEND_PACKAGE exists for the project.
    frontend_package_available: bool = False
    #: CP-PRJ-007: the same index says a PROJECT_EXPORT exists, which is what makes an
    #: archived project deletable at all.
    project_export_available: bool = False
    #: CP-UI-023: M4's writes are delivered (explicit switch or enforced identity).
    m4_enabled: bool = False
    #: M4's permission filter: given the action names this payload is about to offer, return the
    #: ones this operator may not exercise, with the reason code (CP-UI-019, CP-WF-026).
    #: ``None`` is the MVP and means "state decides, identity does not" (CP-IAM-001).
    evaluate: Any = None

    def stage(self, stage_id: str) -> StageSnapshot | None:
        for s in self.stages:
            if s.stage_id == stage_id:
                return s
        return None


def _blocked(
    name: str,
    reason: str,
    *,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
    permission: str | None = None,
) -> Action:
    return Action(
        name=name,
        available=False,
        reason_code=reason,
        target_id=target_id,
        details=details or {},
        permission=permission,
    )


def _ok(
    name: str,
    *,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
    permission: str | None = None,
) -> Action:
    return Action(
        name=name,
        available=True,
        target_id=target_id,
        details=details or {},
        permission=permission,
    )


def workflow_actions(ctx: ActionContext) -> list[Action]:
    """pause / resume / cancel for the whole workflow."""
    if ctx.archived:
        return [_blocked(a, PROJECT_ARCHIVED) for a in WORKFLOW_ACTIONS]
    out: list[Action] = []
    for name in WORKFLOW_ACTIONS:
        if ctx.cancelled:
            out.append(_blocked(name, WORKFLOW_CANCELLED))
        elif name == "workflow.pause":
            out.append(_ok(name) if not ctx.paused else _blocked(name, WORKFLOW_PAUSED))
        elif name == "workflow.resume":
            out.append(_ok(name) if ctx.paused else _blocked(name, WORKFLOW_PAUSED))
        else:
            out.append(_ok(name))
    return out


#: Design stages whose BLOCKED state came from a content verdict (freeze, Gate rejection) and
#: can therefore be fixed by editing the draft and resubmitting.  A Deployment Run step is
#: blocked by conditions outside the draft, so it gets no resubmit button here.
_RESUBMITTABLE_BLOCKED_STAGES = frozenset(
    {
        StageType.REQUIREMENTS,
        StageType.DOMAIN,
        StageType.MAPPING,
        StageType.PAGE_DESIGN,
    }
)


def stage_actions(ctx: ActionContext) -> list[Action]:
    """Per-stage run / rerun / submit / resolve, plus patch and question answer."""
    running_elsewhere = any(s.status is StageStatus.RUNNING for s in ctx.stages)
    out: list[Action] = []
    for stage in ctx.stages:
        sid = stage.stage_id
        if ctx.archived or ctx.cancelled:
            reason = PROJECT_ARCHIVED if ctx.archived else WORKFLOW_CANCELLED
            for name in ("stage.run", "stage.rerun", "stage.submit_for_approval"):
                out.append(_blocked(name, reason, target_id=sid))
            continue
        if ctx.paused:
            # CP-WF-011: pausing stops new stages from starting, not the others.
            out.append(_blocked("stage.run", WORKFLOW_PAUSED, target_id=sid))
        elif stage.status is StageStatus.READY:
            if running_elsewhere:
                out.append(_blocked("stage.run", ALREADY_RUNNING_ELSEWHERE, target_id=sid))
            else:
                out.append(_ok("stage.run", target_id=sid))
        else:
            out.append(
                _blocked(
                    "stage.run",
                    STAGE_NOT_READY,
                    target_id=sid,
                    details={"status": stage.status.value},
                )
            )

        # In-stage dialogue (AI-CHAT-001).  The same two sets the forwarding service checks
        # decide this, so a control is never offered for a turn the service would refuse
        # (S5IF-G10: ``allowed-actions`` is the only source of button availability).
        if stage.stage_type.value not in DIALOGUE_STAGE_TYPES:
            out.append(
                _blocked(
                    "stage.chat",
                    STAGE_NOT_READY,
                    target_id=sid,
                    details={"stageType": stage.stage_type.value, "chatable": sorted(DIALOGUE_STAGE_TYPES)},
                )
            )
        elif stage.status.value not in CHATABLE_STAGE_STATUSES:
            out.append(
                _blocked("stage.chat", STAGE_NOT_READY, target_id=sid, details={"status": stage.status.value})
            )
        else:
            out.append(_ok("stage.chat", target_id=sid))

        if stage.status in (
            StageStatus.PASSED,
            StageStatus.FAILED,
            StageStatus.BLOCKED,
            StageStatus.INVALIDATED,
            StageStatus.SKIPPED,
            StageStatus.READY,
            StageStatus.WAITING_APPROVAL,
            StageStatus.WAITING_INPUT,
        ):
            out.append(_ok("stage.rerun", target_id=sid))
        else:
            out.append(_blocked("stage.rerun", STAGE_NOT_READY, target_id=sid, details={"status": stage.status.value}))

        if stage.status in (StageStatus.READY, StageStatus.RUNNING) or (
            stage.status is StageStatus.BLOCKED and stage.stage_type in _RESUBMITTABLE_BLOCKED_STAGES
        ):
            out.append(_ok("stage.submit_for_approval", target_id=sid))
        else:
            out.append(_blocked("stage.submit_for_approval", STAGE_NOT_READY, target_id=sid))

        if stage.status is StageStatus.MANUAL_CHECK:
            out.append(_ok("stage.resolve_manual_check", target_id=sid))
        else:
            out.append(_blocked("stage.resolve_manual_check", STAGE_NOT_IN_MANUAL_CHECK, target_id=sid))

        if (
            stage.status in (StageStatus.READY, StageStatus.RUNNING, StageStatus.WAITING_APPROVAL)
            or (stage.status is StageStatus.BLOCKED and stage.stage_type in _RESUBMITTABLE_BLOCKED_STAGES)
        ):
            patch_reason = _patch_blocked_reason(ctx, stage)
            if patch_reason:
                out.append(_blocked("patch.submit", patch_reason, target_id=sid))
            else:
                out.append(_ok("patch.submit", target_id=sid))

        if stage.has_open_question:
            out.append(_ok("question.answer", target_id=sid))
        else:
            out.append(_blocked("question.answer", NO_OPEN_QUESTION, target_id=sid))

    return out


def _patch_blocked_reason(ctx: ActionContext, stage: StageSnapshot) -> str | None:
    """CP-WF-017: after ARCHITECTURE_GATE, mapping/registry patches need a CR."""
    if stage.stage_type in (StageType.MAPPING, StageType.DISCOVERY) and ctx.architecture_gate_approved:
        return MAPPING_FROZEN_BY_GATE
    return None


def gate_actions(ctx: ActionContext) -> list[Action]:
    out: list[Action] = []
    environment_class = {env.environment_key: env.environment_class for env in ctx.environments}
    for gate in ctx.gates:
        # The endpoints authorize `gate.approve.<GATE_TYPE>` (and the environment class for a
        # DEPLOY_GATE); asking the role table for the coarse `gate.approve` denied every user
        # whose grant is precise, which is how every role is actually granted.
        permission = iam.gate_approve_action(
            gate.gate_type, environment_class.get(gate.environment_key or "")
        )
        if ctx.archived:
            out.append(
                _blocked("gate.approve", PROJECT_ARCHIVED, target_id=gate.gate_id, permission=permission)
            )
            continue
        if gate.status is not GateStatus.WAITING:
            for name in ("gate.approve", "gate.reject"):
                out.append(
                    _blocked(name, GATE_NOT_WAITING, target_id=gate.gate_id, permission=permission)
                )
            continue
        if gate.open_warnings > 0:
            out.append(
                _blocked(
                    "gate.approve",
                    WARNING_UNRESOLVED,
                    target_id=gate.gate_id,
                    details={"openWarnings": gate.open_warnings},
                    permission=permission,
                )
            )
        else:
            out.append(_ok("gate.approve", target_id=gate.gate_id, permission=permission))
        out.append(_ok("gate.reject", target_id=gate.gate_id, permission=permission))
    return out


def run_actions(ctx: ActionContext) -> list[Action]:
    """deployment.create per environment; execution.commit and deployment.cancel per Run."""
    protection = {e.environment_key: e for e in ctx.environments}
    out: list[Action] = []

    for env_key in protection:
        already_open = any(r.environment_key == env_key and _is_open(r.status) for r in ctx.runs)
        if ctx.archived:
            out.append(_blocked("deployment.create", PROJECT_ARCHIVED, target_id=env_key))
        elif not ctx.design_line_complete:
            out.append(_blocked("deployment.create", DESIGN_LINE_INCOMPLETE, target_id=env_key))
        elif already_open:
            out.append(_blocked("deployment.create", RUN_OPEN_FOR_ENVIRONMENT, target_id=env_key))
        else:
            out.append(_ok("deployment.create", target_id=env_key))

    for run in ctx.runs:
        facts = protection.get(run.environment_key)
        commit_blockers: list[tuple[str, dict[str, Any]]] = []
        if facts is not None:
            if facts.emergency_locked:
                commit_blockers.append((ENVIRONMENT_LOCKED, {}))
            if not facts.within_commit_window:
                commit_blockers.append((OUTSIDE_COMMIT_WINDOW, {}))
            if facts.environment_class == "PRODUCTION" and (
                f"{run.blueprint_version}:{PRODUCTION_CHECK}" not in ctx.verified_version_keys
            ):
                commit_blockers.append((PRIOR_VERIFICATION_MISSING, {"blueprintVersion": run.blueprint_version}))
        if not run.deploy_gate_approved:
            # Appended, not prepended: S5IF-AC06 wants the environment's own refusal
            # (lock, window, missing prior verification) to be the reason reported,
            # because that is the one the user can act on at the environment.
            commit_blockers.append((GATE_NOT_APPROVED, {"gateType": "DEPLOY_GATE"}))

        if ctx.archived or not _is_open(run.status):
            out.append(_blocked("execution.commit", STAGE_NOT_READY, target_id=run.run_id))
        elif commit_blockers:
            reason, details = commit_blockers[0]
            # AC-06: an environment-level refusal points at the environment, so S5 can
            # grey out the lock/window control rather than the Run.
            target = run.environment_key if reason in (ENVIRONMENT_LOCKED, OUTSIDE_COMMIT_WINDOW) else run.run_id
            out.append(_blocked("execution.commit", reason, target_id=target, details={**details, "runId": run.run_id}))
        else:
            out.append(_ok("execution.commit", target_id=run.run_id))

        if _is_open(run.status) and not ctx.archived:
            out.append(_ok("deployment.cancel", target_id=run.run_id))
            # S5IF-FLD05: a fresh drift check is an explicit action, never something a GET
            # does on the user's behalf — the console shows the last recorded verdict.
            out.append(_ok("deployment_run.drift_check", target_id=run.run_id))
        else:
            out.append(_blocked("deployment.cancel", NO_OPEN_RUN, target_id=run.run_id))
            out.append(_blocked("deployment_run.drift_check", NO_OPEN_RUN, target_id=run.run_id))

    return out


PRODUCTION_CHECK = "NON_PRODUCTION_VERIFIED"


def compute_actions(ctx: ActionContext) -> dict[str, list[dict[str, Any]]]:
    """Full payload for ``GET /workflows/{id}/allowed-actions``."""
    actions = (
        workflow_actions(ctx)
        + forwarding_actions(ctx)
        + environment_actions(ctx)
        + stage_actions(ctx)
        + gate_actions(ctx)
        + run_actions(ctx)
        + project_actions(ctx)
        + change_request_actions(ctx)
    )
    actions = _apply_permissions(ctx, actions)
    actions = _apply_feature_gate(actions, ctx)
    return {
        "allowed": [a.as_dict() for a in actions if a.available],
        "unavailable": [a.as_dict() for a in actions if not a.available],
    }


def _apply_feature_gate(actions: list[Action], ctx: ActionContext) -> list[Action]:
    """CP-UI-023 / CP-AC-32: M4's write actions are unavailable until M4 is delivered.

    Applied after the permission filter so the state and role answers are preserved
    for when the feature arrives; while it has not, one reason overrides them all —
    the client must be able to say "milestone M4" rather than guess a role it lacks.
    """
    if ctx.m4_enabled:
        return actions
    out: list[Action] = []
    for action in actions:
        if action.name in M4_GATED_ACTIONS:
            out.append(
                _blocked(
                    action.name,
                    FEATURE_NOT_ENABLED,
                    target_id=action.target_id,
                    details={"feature": action.name, "milestone": "M4"},
                )
            )
        else:
            out.append(action)
    return out


def _apply_permissions(ctx: ActionContext, actions: list[Action]) -> list[Action]:
    """Remove what this operator may not do, and say why (CP-UI-019, S5IF-G10).

    Applied to the finished list rather than inside each builder, because the builders answer the
    question "does the state allow this?" and a control that is both stateful and permissioned
    (``gate.approve.DEPLOY_GATE.PRODUCTION`` is the clearest) would otherwise have to consult the
    role table in eight places and could disagree with itself.
    """
    if ctx.evaluate is None:
        return actions
    # `stage.rerun` has no action of its own in Appendix B: the rerun route authorizes with
    # STAGE_RUN (routes/workflow.py:rerun_stage), so asking the role table for the literal
    # "stage.rerun" answered PERMISSION_DENIED for every role and greyed out a control the
    # endpoint would have accepted. Evaluate the permission the endpoint actually checks.
    permission_for = {"stage.rerun": iam.STAGE_RUN}

    def permission(action: Action) -> str:
        return action.permission or permission_for.get(action.name, action.name)

    asked = tuple(sorted({permission(action) for action in actions if action.available}))
    decisions = dict(ctx.evaluate(asked) or {})
    out: list[Action] = []
    for action in actions:
        decision = decisions.get(permission(action))
        if decision is None or action.available is False or getattr(decision, "allowed", True):
            out.append(action)
            continue
        out.append(
            _blocked(
                action.name,
                getattr(decision, "reason_code", "") or PERMISSION_DENIED,
                target_id=action.target_id,
                details=dict(getattr(decision, "details", {}) or {}),
                permission=action.permission,
            )
        )
    return out


def project_actions(ctx: ActionContext) -> list[Action]:
    out: list[Action] = []
    for name in ("project.edit", "project.upgrade_mode", "change_request.create", "input.upload"):
        out.append(_blocked(name, PROJECT_ARCHIVED) if ctx.archived else _ok(name))
    # M4's three project-scoped rights (CP-IAM-002).  They belong here rather than only in the
    # platform list because a role can be granted *for one project*: the platform answer, evaluated
    # with no project, would tell a project's own member manager that they may not manage it.
    # `_apply_permissions` then filters them with this project's id, exactly as the route will.
    for name in (iam.MEMBER_MANAGE, iam.RESTRICTED_GRANT_MANAGE, iam.PRODUCTION_EXEMPTION_MANAGE):
        out.append(_ok(name))
    # Export answers in both states on purpose: a project is exported before handover, and
    # CP-PRJ-007 requires a PROJECT_EXPORT to exist before an archived one can be deleted.
    out.append(_ok("project.export"))
    out.append(_ok("project.archive") if not ctx.archived else _blocked("project.archive", PROJECT_ARCHIVED))
    if not ctx.archived:
        out.append(_blocked("project.delete", STAGE_NOT_PASSED, details={"requires": "ARCHIVED"}))
    elif ctx.project_export_available:
        out.append(_ok("project.delete"))
    else:
        out.append(_blocked("project.delete", EXPORT_NOT_TAKEN, details={"requires": "PROJECT_EXPORT"}))
    return out


def change_request_actions(ctx: ActionContext) -> list[Action]:
    """Per change request: what CP-CR-002's transition table allows from its current state.

    The table is the only source. Hand-listing the buttons that look sensible here would put a
    second copy of the state machine in the availability layer, and the two would drift the first
    time a transition is added -- which is exactly what S5 must not end up deciding for itself
    (FR-UI-009).
    """
    if ctx.archived:
        return [_blocked(name, PROJECT_ARCHIVED) for name in CHANGE_REQUEST_ACTIONS]

    targets = {
        "change_request.impact": ChangeRequestStatus.ANALYZED,
        "change_request.approve": ChangeRequestStatus.APPROVED,
        "change_request.reject": ChangeRequestStatus.REJECTED,
        "change_request.start": ChangeRequestStatus.IN_PROGRESS,
        "change_request.complete": ChangeRequestStatus.COMPLETED,
        "change_request.cancel": ChangeRequestStatus.CANCELLED,
    }
    out: list[Action] = []
    for cr in ctx.change_requests:
        try:
            allowed = CR_TRANSITIONS[ChangeRequestStatus(cr.status)]
        except ValueError:
            allowed = frozenset()
        for name, target in targets.items():
            if target in allowed:
                out.append(_ok(name, target_id=cr.cr_id))
            else:
                out.append(
                    _blocked(
                        name,
                        STATE_NOT_ALLOWED,
                        target_id=cr.cr_id,
                        details={
                            "status": str(cr.status),
                            # the value, not str(enum): the reason a button is closed is read by
                            # a person in S5, and "ChangeRequestStatus.APPROVED" is not an answer
                            "wouldBecome": target.value,
                        },
                    )
                )
    return out


def forwarding_actions(ctx: ActionContext) -> list[Action]:
    """The actions behind the endpoints S5IF adds, with an honest reason when blocked.

    None of these carries a ``targetId``.  Their target is an artifact, an agent run
    or an input — an id the workflow-level ``allowed-actions`` call cannot know — and
    S5 keys the unavailable reason off ``targetId`` to grey out a specific control
    (S5IF-FLD12).  A wrong or borrowed id would attach the reason to the wrong
    button, so the id is left out rather than approximated.  Environment actions are
    the exception: their ``targetId`` is the environment key, which is knowable here.
    """
    if ctx.archived:
        return [_blocked(name, PROJECT_ARCHIVED) for name in FORWARDING_ACTIONS]

    running = any(s.status is StageStatus.RUNNING for s in ctx.stages)
    has_report = any(s.status in (StageStatus.FAILED, StageStatus.BLOCKED) for s in ctx.stages)
    has_open_run = any(_is_open(r.status) for r in ctx.runs)
    has_draft_target = bool(ctx.stages)

    availability = {
        "input.parse": (has_draft_target, DESIGN_LINE_INCOMPLETE),
        "input.confirm_column_mapping": (has_draft_target, DESIGN_LINE_INCOMPLETE),
        "artifact.explain": (has_report, STAGE_NOT_READY),
        # Reading a file back is never blocked by workflow state: UI-PRJ-004 downloads the
        # export *before* deleting an archived project, so an archived project must still
        # offer it.
        "artifact.download": (True, STAGE_NOT_READY),
        "agent.cancel": (running, NO_OPEN_RUN),
        "registry_binding.set": (has_open_run, NO_OPEN_RUN),
        "uid_pool.replenish": (True, STAGE_NOT_READY),
        "governance.provider.register": (True, PROJECT_ARCHIVED),
        "governance.provider.enable": (True, PROJECT_ARCHIVED),
        "governance.model.register": (True, PROJECT_ARCHIVED),
        "governance.model.enable": (True, PROJECT_ARCHIVED),
        "governance.prompt.activate": (True, PROJECT_ARCHIVED),
        # A regression record and an evaluation are what unblock an activation (S5IF-GOV09),
        # so they are offered whenever the project exists — S3 decides the rest.
        "governance.regression.record": (True, PROJECT_ARCHIVED),
        "governance.evaluation.run": (True, PROJECT_ARCHIVED),
        # Purging is a retention decision about a stored artifact, not a workflow step.
        "artifact.purge": (True, STAGE_NOT_READY),
        # PHI_PII and an over-matrix request are refused by S3 itself; the button exists
        # whenever the project does (S5IF-FLD12, 附录 B).
        "governance.policy.relax": (True, PROJECT_ARCHIVED),
        # The budget row is S3's and the enforcement is S3's; the button exists whenever the
        # project does (S5IF-GOV18, AI-COST-002).  From M4 who may press it is the role table's
        # answer, which is what the operator filter below adds.
        "governance.budget.set": (True, PROJECT_ARCHIVED),
        # CP-UI-021: there is nothing to download until HANDOFF has delivered a
        # FRONTEND_PACKAGE; S1's own artifact index is what says whether one exists.
        iam.FRONTEND_PACKAGE_DOWNLOAD: (ctx.frontend_package_available, PACKAGE_NOT_GENERATED),
    }
    return [
        _ok(name) if available else _blocked(name, reason)
        for name in FORWARDING_ACTIONS
        for available, reason in (availability[name],)
    ]


#: Operations S4 will not perform against a DISABLED environment.
#:
#: The connection test and the capability probe are in here because S4's own check runs first:
#: ``build_target`` refuses a disabled environment before it ever looks at ``allow_unprobed``
#: (``environment/loader.py``), so the exemption that list is documented for is about a missing
#: capability profile, not about being switched off.  ``environment.disable`` is here because
#: disabling twice is an error S4 raises (``rules.can_disable``).  Editing the profile and
#: locking or unlocking are deliberately *not* here — S4 allows both, and refusing them would
#: strand an operator who disabled an environment by mistake with no way to switch it back.
DISABLED_REFUSED_ACTIONS: frozenset[str] = frozenset(
    {"environment.disable", "environment.test_connection", "environment.probe_capabilities"}
)


def _environment_action(name: str, facts: EnvironmentFacts) -> Action:
    """One rule for an environment control, wherever it is drawn.

    The workflow view and the Administration pages used to build these entries separately, and
    only one of them carried ``details.locked`` — which is how a page came to render a control
    whose state the server had never told it.
    """
    target = facts.environment_key
    if name == "environment.emergency_lock":
        return _ok(name, target_id=target, details={"locked": facts.emergency_locked})
    if not facts.enabled and name in DISABLED_REFUSED_ACTIONS:
        return _blocked(name, ENVIRONMENT_DISABLED, target_id=target, details={"status": "DISABLED"})
    return _ok(name, target_id=target)


def environment_actions(ctx: ActionContext) -> list[Action]:
    """One entry per target environment; ``targetId`` is the environment key.

    S5 needs the unavailable reason attached to the right control: an environment
    action's target is a key, not a row id, which is why these are emitted here
    rather than folded into the Run actions (S5IF-FLD12).
    """
    out: list[Action] = []
    for facts in ctx.environments:
        for name in ENVIRONMENT_ACTIONS:
            if ctx.archived:
                out.append(_blocked(name, PROJECT_ARCHIVED, target_id=facts.environment_key))
            else:
                out.append(_environment_action(name, facts))
    return out


def platform_actions(
    environments: list[EnvironmentFacts],
    *,
    evaluate: Any = None,
    m4_enabled: bool = False,
) -> list[Action]:
    """Availability for the Administration pages, which have no workflow (FR-UI-009).

    Everything that is not gated by a piece of state these pages do not have is offered; the
    per-environment actions carry the environment key as ``targetId`` exactly as the workflow
    view does, so one rule governs a control wherever it is drawn -- both lists go through
    :func:`_environment_action`, which is where a disabled environment's write actions are
    withdrawn and where the lock is answered as ``details.locked``.  S4 is the one that knows an
    environment is disabled, so this list is only honest while it is read from there.

    M4's Administration rights arrive here too, because these pages have nothing else to ask:
    they go through the same `_apply_permissions` the workflow list uses, so the answer a control
    is drawn from and the answer its request will meet cannot diverge (CP-IAM-001, FR-UI-009).
    """
    out: list[Action] = [_ok(name) for name in PLATFORM_ACTIONS]
    for facts in environments:
        for name in ENVIRONMENT_ACTIONS:
            if name == "environment.create":
                continue  # not per-environment; already emitted once above
            out.append(_environment_action(name, facts))
    out = _apply_permissions(ActionContext(evaluate=evaluate), out)
    return _apply_feature_gate(out, ActionContext(m4_enabled=m4_enabled))


def _is_open(status: RunStatus) -> bool:
    return status in (
        RunStatus.CREATED,
        RunStatus.IN_PROGRESS,
        RunStatus.WAITING_APPROVAL,
        RunStatus.MANUAL_CHECK,
    )


__all__ = [
    "ALREADY_RUNNING_ELSEWHERE",
    "CHANGE_REQUEST_ACTIONS",
    "ChangeRequestSnapshot",
    "STATE_NOT_ALLOWED",
    "change_request_actions",
    "PLATFORM_ACTIONS",
    "M4_GATED_ACTIONS",
    "FEATURE_NOT_ENABLED",
    "PACKAGE_NOT_GENERATED",
    "EXPORT_NOT_TAKEN",
    "Action",
    "ActionContext",
    "DESIGN_LINE_INCOMPLETE",
    "DISABLED_REFUSED_ACTIONS",
    "ENVIRONMENT_DISABLED",
    "EnvironmentFacts",
    "platform_actions",
    "GateSnapshot",
    "GATE_NOT_APPROVED",
    "GATE_NOT_WAITING",
    "HIGH_QUESTION_OPEN",
    "MAPPING_FROZEN_BY_GATE",
    "NO_OPEN_RUN",
    "OUTSIDE_COMMIT_WINDOW",
    "PRIOR_VERIFICATION_MISSING",
    "PROJECT_ARCHIVED",
    "PRODUCTION_CHECK",
    "RunSnapshot",
    "STAGE_NOT_IN_MANUAL_CHECK",
    "STAGE_NOT_READY",
    "STAGE_NOT_WAITING",
    "StageSnapshot",
    "WARNING_UNRESOLVED",
    "WORKFLOW_CANCELLED",
    "WORKFLOW_PAUSED",
    "ENVIRONMENT_ACTIONS",
    "FORWARDING_ACTIONS",
    "compute_actions",
    "environment_actions",
    "forwarding_actions",
    "gate_actions",
    "project_actions",
    "run_actions",
    "stage_actions",
    "workflow_actions",
]
