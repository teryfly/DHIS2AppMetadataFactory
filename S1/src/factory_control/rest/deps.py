"""What every handler reaches for: the runtime, the caller, the authz hook.

The handlers stay FastAPI-style functions taking ``request: Request`` rather than
``Depends(...)`` objects: the runtime is process-wide state hung off
``app.state``, and the operator is negotiated by
:class:`~factory_control.rest.middleware.RequestContextMiddleware`, so both are
properties of the request rather than injectable services.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from factory_control import iam
from factory_control.rest.middleware import context_of
from factory_control.runtime import Runtime

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def operator_of(request: Request) -> str:
    return context_of(request).operator


def user_id_of(request: Request) -> str:
    """The account behind the caller, or "" while ``auth_mode`` is off and there is no account.

    Membership is keyed on this id, not on the operator name (FR-SEC-004), so anything that
    records "who owns this object" has to read it rather than derive it from ``operator_of``.
    """
    return str(context_of(request).user_id or "")


def language_of(request: Request) -> str:
    return context_of(request).language


def runtime_of(request: Request) -> Runtime:
    return request.app.state.runtime  # type: ignore[no-any-return]


def authorize(request: Request, action: str, project_id: str | None = None) -> None:
    """CP-IAM-001: one hook on every state-changing path (MVP: always allowed)."""
    iam.authorize(operator_of(request), project_id, action)


def _project_of_workflow(session: Session, workflow_id: str) -> str:
    from factory_control import store as S

    return str(S.get_workflow(session, workflow_id).project_id)


def project_of_stage(request: Request, stage_id: str) -> str:
    """The project a stage belongs to, so a stage action can be authorized *in* it."""
    from factory_control.errors import NotFoundControlError
    from factory_control.models import Stage

    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        stage = ctx.session.get(Stage, stage_id)
        if stage is None:
            raise NotFoundControlError(f"unknown stage {stage_id}", details={"stageId": stage_id})
        return _project_of_workflow(ctx.session, stage.workflow_id)


def project_of_run(request: Request, run_id: str) -> str:
    from factory_control import store as S

    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        return _project_of_workflow(ctx.session, S.get_run(ctx.session, run_id).workflow_id)


def project_of_gate(request: Request, gate_id: str) -> str:
    from factory_control.errors import NotFoundControlError
    from factory_control.models import Gate

    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        gate = ctx.session.get(Gate, gate_id)
        if gate is None:
            raise NotFoundControlError(f"unknown gate {gate_id}", details={"gateId": gate_id})
        return _project_of_workflow(ctx.session, gate.workflow_id)


def project_of_execution(request: Request, execution_id: str) -> str | None:
    """The project an execution belongs to, by the Run that recorded it — when there is one.

    S1 keeps no execution table: an execution id comes back from S4 and is stored on the Run that
    started it.  That covers every COMMIT, but not an execution S4 was asked about directly (a
    fixture import, a rejected deletion, a harness's synthetic id), and S4's own outcome carries
    only an ``environment_key`` (DX-BR-02), no project.  So this returns None rather than inventing
    a link, and :func:`authorize_execution` falls back to the unscoped check it always had — the
    gap is real and is listed in the remaining-work note, but answering an unknown execution with
    404 would be a worse bug than the one being fixed.
    """
    from sqlalchemy import select

    from factory_control.models import DeploymentRun

    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        run = ctx.session.scalar(
            select(DeploymentRun).where(DeploymentRun.execution_id == execution_id)
        )
        if run is None:
            return None
        return _project_of_workflow(ctx.session, run.workflow_id)


def _project_of_row(request: Request, model_name: str, row_id: str, label: str) -> str:
    """The project an S1 row names on itself — Change Request, Question, Notification.

    Those three carry ``project_id`` directly, so scoping the check is one read and no guess.  A
    missing row is reported as unknown rather than authorized with no project, because a check
    without a project silently asks only the caller's organisation-wide roles and would answer 403
    for a person whose role lives on the project (FR-SEC-004).
    """
    from factory_control import models
    from factory_control.errors import NotFoundControlError

    model = getattr(models, model_name)
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        row = ctx.session.get(model, row_id)
        if row is None:
            raise NotFoundControlError(f"unknown {label} {row_id}", details={f"{label}Id": row_id})
        return str(row.project_id)


def authorize_stage(request: Request, stage_id: str, action: str) -> None:
    """Authorize an action on a stage, scoped to the stage's project.

    ``iam.PROJECT_SCOPED`` actions are decided by membership *and* role, and a role held on one
    project is only visible when the check knows which project it is: authorizing with
    ``project_id=None`` asked for the caller's platform roles alone, so an account created by
    CP-PRJ-001 as the project's first member was refused its own ``patch.submit`` with
    ``{action: "patch.submit", projectId: null}``.
    """
    authorize(request, action, project_of_stage(request, stage_id))


def authorize_workflow(request: Request, workflow_id: str, action: str) -> None:
    with runtime_of(request).transaction(operator=operator_of(request)) as ctx:
        project_id = _project_of_workflow(ctx.session, workflow_id)
    authorize(request, action, project_id)


def authorize_run(request: Request, run_id: str, action: str) -> None:
    authorize(request, action, project_of_run(request, run_id))


def authorize_gate(request: Request, gate_id: str, action: str) -> None:
    authorize(request, action, project_of_gate(request, gate_id))


def authorize_change_request(request: Request, cr_id: str, action: str) -> None:
    authorize(request, action, _project_of_row(request, "ChangeRequest", cr_id, "changeRequest"))


def authorize_question(request: Request, question_id: str, action: str) -> None:
    authorize(request, action, _project_of_row(request, "Question", question_id, "question"))


def authorize_notification(request: Request, notification_id: str, action: str) -> None:
    authorize(
        request, action, _project_of_row(request, "Notification", notification_id, "notification")
    )


def authorize_gate_approval(request: Request, gate_id: str, gate_type: str) -> None:
    """Authorize a Gate decision, naming the Gate the way the role table can grant it.

    CP-GATE-012 forms DEPLOY_GATE's action with the environment class
    (``gate.approve.DEPLOY_GATE.TEST``) and a narrower grant never covers a coarser ask
    (``policy._prefix_granted``), so asking for the unqualified ``gate.approve.DEPLOY_GATE`` here
    demanded a name no role may hold: every real DEPLOY_GATE approval answered 403 while the
    same person's PACKAGE_GATE went through.  The class comes from S4's own stored protection —
    a database read, not a call to the target instance — and :func:`gate_project_id` inside the
    approval transaction re-reads it fresh afterwards, as CP-WF-027 requires.
    """
    from factory_control.domain.enums import GateType
    from factory_control.errors import NotFoundControlError
    from factory_control.models import Gate

    runtime = runtime_of(request)
    with runtime.transaction(operator=operator_of(request)) as ctx:
        gate = ctx.session.get(Gate, gate_id)
        if gate is None:
            raise NotFoundControlError(f"unknown gate {gate_id}", details={"gateId": gate_id})
        from factory_control import store as S

        project_id = str(S.get_workflow(ctx.session, gate.workflow_id).project_id)
        environment_key = gate.environment_key

    action = iam.gate_approve_action(gate_type)
    if gate_type == GateType.DEPLOY_GATE.value and environment_key:
        protection = runtime.execution.get_protection(environment_key)
        action = iam.gate_approve_action(gate_type, str(protection.environment_class).upper())
    authorize(request, action, project_id)


def authorize_execution(request: Request, execution_id: str, action: str) -> None:
    authorize(request, action, project_of_execution(request, execution_id))


def visible_project_ids(request: Request) -> list[str] | None:
    """FR-SEC-004: which projects a cross-project listing may show.  ``None`` means all of them.

    A listing has no project in its path, so there is nothing to name in the authorization question —
    that is why both cross-project reads sat on this exception list.  Answering it is still two
    different things: an organization-scope ``project.view`` is the administration surface and sees
    every project, while anyone else is confined to the projects they belong to, which is exactly the
    isolation FR-SEC-004 names.  A caller with neither is refused the way ``authorize`` would refuse
    them, so no one gets a quiet empty list instead of a 403 they are entitled to act on.
    """
    from factory_control import store as S

    operator = operator_of(request)
    policy = iam.get_permission_policy()
    if policy.is_allowed(operator=operator, project_id=None, action=iam.PROJECT_VIEW):
        return None
    user_id = user_id_of(request)
    if not user_id:
        authorize(request, iam.PROJECT_VIEW)
        return None
    with runtime_of(request).transaction(operator=operator) as ctx:
        projects = S.member_project_ids(ctx.session, user_id)
    if not projects:
        authorize(request, iam.PROJECT_VIEW)
    return projects


__all__ = [
    "authorize",
    "authorize_change_request",
    "authorize_execution",
    "authorize_gate",
    "authorize_gate_approval",
    "authorize_notification",
    "authorize_question",
    "authorize_run",
    "authorize_stage",
    "authorize_workflow",
    "language_of",
    "operator_of",
    "project_of_execution",
    "project_of_gate",
    "project_of_run",
    "project_of_stage",
    "runtime_of",
    "user_id_of",
    "visible_project_ids",
]
