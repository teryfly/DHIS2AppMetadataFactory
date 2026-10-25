"""Repository helpers — the only place besides migrations that touches ``cp`` SQL.

Deliberately thin: row locks, finders and list queries.  Business rules stay in
:mod:`factory_control.domain` and :mod:`factory_control.services`, so a rule can
be unit-tested without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select  # noqa: F401  (Select used by type checks in callers)
from sqlalchemy.orm import Session

from factory_control.domain.enums import GateStatus, QuestionStatus, RunStatus, StageStatus, StageType
from factory_control.errors import NotFoundControlError
from factory_control.foundation import idempotency
from factory_control.models import (
    Approval,
    ChangeRequest,
    DeploymentRun,
    EventStreamRecord,
    Gate,
    IdempotentResponse,
    Notification,
    NotificationDelivery,
    Project,
    ProjectMember,
    ProjectSetting,
    ProjectTargetEnvironment,
    Question,
    Stage,
    StageRun,
    SystemSetting,
    Workflow,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def get_project(session: Session, project_id: str, *, include_deleted: bool = False) -> Project:
    row = session.get(Project, project_id)
    if row is None or (row.deleted_at is not None and not include_deleted):
        raise NotFoundControlError(f"project {project_id} not found", details={"projectId": project_id})
    return row


def get_workflow_for_project(session: Session, project_id: str) -> Workflow:
    row = session.execute(
        select(Workflow).where(Workflow.project_id == project_id)
    ).scalars().first()
    if row is None:
        raise NotFoundControlError(
            f"no workflow for project {project_id}", details={"projectId": project_id}
        )
    return row


def get_workflow(session: Session, workflow_id: str) -> Workflow:
    row = session.get(Workflow, workflow_id)
    if row is None:
        raise NotFoundControlError(f"workflow {workflow_id} not found", details={"workflowId": workflow_id})
    return row


def lock_workflow(session: Session, workflow_id: str) -> Workflow:
    """Row lock for the workflow: serialises stage transitions and event numbering.

    Combined with the partial unique index on RUNNING stages this is how CP-WF-006
    holds across two worker processes.
    """
    row = session.execute(
        select(Workflow).where(Workflow.id == workflow_id).with_for_update()
    ).scalars().first()
    if row is None:
        raise NotFoundControlError(f"workflow {workflow_id} not found", details={"workflowId": workflow_id})
    return row


#: ``sortBy`` on ``GET /projects`` -> the column it orders by (S5IF-G04).
PROJECT_SORT_COLUMNS: dict[str, str] = {
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "name": "name",
    "milestoneStatus": "milestone_status",
}


def member_project_ids(session: Session, user_id: str) -> list[str]:
    """FR-SEC-004: the projects this account belongs to, which is the isolation boundary.

    Membership, not tenancy: a single-organization deployment still has to keep one project's
    listing from showing another's.
    """
    if not user_id:
        return []
    return [
        str(row)
        for row in session.execute(
            select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
        ).scalars()
    ]


def list_projects(
    session: Session,
    *,
    include_archived: bool = True,
    name_like: str | None = None,
    owner: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    sort_by: str = "createdAt",
    descending: bool = True,
    only_project_ids: list[str] | None = None,
) -> tuple[list[Project], int]:
    base = select(Project).where(Project.deleted_at.is_(None))
    if only_project_ids is not None:
        # Filtered inside the query: trimming a page afterwards would report a `total` that
        # counts projects the caller must not see, and repeat rows across pages.
        if not only_project_ids:
            return [], 0
        base = base.where(Project.id.in_(only_project_ids))
    if not include_archived:
        base = base.where(Project.archived.is_(False))
    if name_like:
        base = base.where(Project.name.ilike(f"%{name_like}%"))
    if owner:
        base = base.where(Project.owner_name == owner)
    total = int(session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0)
    column = PROJECT_SORT_COLUMNS.get(sort_by, Project.created_at.name)
    order = getattr(Project, column)
    direction = order.desc() if descending else order.asc()
    # A second key on the id: without it, rows sharing a timestamp come back in an
    # order the database chooses, and two pages can repeat or skip a project.
    tiebreak = Project.id.asc()
    rows = list(
        session.execute(
            base.order_by(direction, tiebreak)
            .limit(max(1, min(limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)))
            .offset(max(0, offset or 0))
        )
        .scalars()
        .all()
    )
    return rows, total


def stages_of(session: Session, workflow_id: str, *, run_id: str | None = None) -> list[Stage]:
    query = select(Stage).where(Stage.workflow_id == workflow_id)
    if run_id is not None:
        query = query.where(Stage.deployment_run_id == run_id)
    return list(session.execute(query.order_by(Stage.created_at)).scalars().all())


def get_stage(session: Session, stage_id: str) -> Stage:
    row = session.get(Stage, stage_id)
    if row is None:
        raise NotFoundControlError(f"stage {stage_id} not found", details={"stageId": stage_id})
    return row


def stage_by_type(
    session: Session, workflow_id: str, stage_type: StageType, *, run_id: str | None = None
) -> Stage | None:
    query = select(Stage).where(
        Stage.workflow_id == workflow_id, Stage.stage_type == stage_type.value
    )
    if run_id is None:
        query = query.where(Stage.deployment_run_id.is_(None))
    else:
        query = query.where(Stage.deployment_run_id == run_id)
    return session.execute(query).scalars().first()


def running_stage(session: Session, workflow_id: str) -> Stage | None:
    return session.execute(
        select(Stage).where(Stage.workflow_id == workflow_id, Stage.status == StageStatus.RUNNING.value)
    ).scalars().first()


def create_stage(
    session: Session,
    *,
    stage_id: str,
    workflow_id: str,
    stage_type: StageType,
    status: StageStatus,
    run_id: str | None = None,
    page_scope: Sequence[str] | None = None,
) -> Stage:
    row = Stage(
        id=stage_id,
        workflow_id=workflow_id,
        stage_type=stage_type.value,
        status=status.value,
        deployment_run_id=run_id,
        attempt=0,
        page_scope=list(page_scope or []),
    )
    session.add(row)
    session.flush()
    return row


def runs_of(session: Session, workflow_id: str, *, environment_key: str | None = None) -> list[DeploymentRun]:
    query = select(DeploymentRun).where(DeploymentRun.workflow_id == workflow_id)
    if environment_key:
        query = query.where(DeploymentRun.environment_key == environment_key)
    return list(session.execute(query.order_by(DeploymentRun.created_at)).scalars().all())


def get_run(session: Session, run_id: str) -> DeploymentRun:
    row = session.get(DeploymentRun, run_id)
    if row is None:
        raise NotFoundControlError(
            f"deployment run {run_id} not found", details={"deploymentRunId": run_id}
        )
    return row


def open_run_for_environment(session: Session, workflow_id: str, environment_key: str) -> DeploymentRun | None:
    open_states = [s.value for s in (RunStatus.CREATED, RunStatus.IN_PROGRESS, RunStatus.WAITING_APPROVAL, RunStatus.MANUAL_CHECK)]
    return session.execute(
        select(DeploymentRun)
        .where(
            DeploymentRun.workflow_id == workflow_id,
            DeploymentRun.environment_key == environment_key,
            DeploymentRun.status.in_(open_states),
        )
        .limit(1)
    ).scalars().first()


def gates_of(
    session: Session,
    workflow_id: str,
    *,
    status: GateStatus | None = None,
    gate_type: str | None = None,
) -> list[Gate]:
    query = select(Gate).where(Gate.workflow_id == workflow_id)
    if status is not None:
        query = query.where(Gate.status == status.value)
    if gate_type is not None:
        query = query.where(Gate.type == gate_type)
    return list(session.execute(query.order_by(Gate.created_at)).scalars().all())


#: ``sortBy`` on ``GET /gates`` -> the column it orders by (S5IF-FLD03 / G04).
GATE_SORT_COLUMNS: dict[str, str] = {
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "type": "type",
    "status": "status",
}

#: ``sortBy`` on ``GET /deployment-runs`` (S5IF-FLD04 【裁决】: default is updatedAt DESC).
RUN_SORT_COLUMNS: dict[str, str] = {
    "updatedAt": "updated_at",
    "startedAt": "started_at",
    "completedAt": "completed_at",
    "status": "status",
    "blueprintVersion": "blueprint_version",
}


def list_gates(
    session: Session,
    *,
    status: str | None = None,
    project_id: str | None = None,
    gate_type: str | None = None,
    sort_by: str = "createdAt",
    descending: bool = True,
    limit: int | None = None,
    offset: int | None = None,
) -> tuple[list[Gate], int]:
    """Every Gate, across projects — the Dashboard's "all awaiting approval" list.

    Gate rows are S1's own, so this is a plain query and not a cross-system call
    (S5IF-FLD03 【裁决】).
    """
    base = select(Gate)
    if status:
        base = base.where(Gate.status == status)
    if gate_type:
        base = base.where(Gate.type == gate_type)
    if project_id:
        base = base.join(Workflow, Workflow.id == Gate.workflow_id).where(Workflow.project_id == project_id)
    total = int(session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0)
    order = getattr(Gate, GATE_SORT_COLUMNS.get(sort_by, Gate.created_at.name))
    direction = order.desc() if descending else order.asc()
    rows = list(
        session.execute(
            base.order_by(direction, Gate.id.asc())
            .limit(max(1, min(limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)))
            .offset(max(0, offset or 0))
        )
        .scalars()
        .all()
    )
    return rows, total


def approval_facts(session: Session, gate_ids: Sequence[str]) -> dict[str, tuple[int, set[str]]]:
    """Approval count and dispositioned warning ids per Gate, in one query.

    UI-AC-04 greys the approve button out on an *unresolved* WARNING, so the Dashboard
    list needs both numbers; reading them Gate by Gate would be a query per row.
    """
    if not gate_ids:
        return {}
    rows = session.execute(
        select(Approval.gate_id, Approval.decision, Approval.warning_dispositions).where(
            Approval.gate_id.in_(list(gate_ids))
        )
    ).all()
    out: dict[str, tuple[int, set[str]]] = {str(gate_id): (0, set()) for gate_id in gate_ids}
    for gate_id, _decision, dispositions in rows:
        count, resolved = out.get(str(gate_id), (0, set()))
        for item in dispositions or []:
            issue_id = item.get("issueId") or item.get("issue_id")
            if issue_id:
                resolved.add(str(issue_id))
        out[str(gate_id)] = (count + 1, resolved)
    return out


def high_open_question_counts(session: Session, stage_ids: Sequence[str]) -> dict[str, int]:
    """Unanswered HIGH Questions per stage, grouped in one query (CP-Q-006 / UI-Q-002)."""
    if not stage_ids:
        return {}
    rows = session.execute(
        select(Question.stage_id, func.count())
        .where(
            Question.stage_id.in_(list(stage_ids)),
            Question.status == QuestionStatus.OPEN.value,
            Question.severity == "HIGH",
        )
        .group_by(Question.stage_id)
    ).all()
    return {str(row[0]): int(row[1] or 0) for row in rows if row[0]}


def list_runs(
    session: Session,
    *,
    status: str | None = None,
    environment_key: str | None = None,
    outdated: bool | None = None,
    sort_by: str = "updatedAt",
    descending: bool = True,
    limit: int | None = None,
    offset: int | None = None,
    only_project_ids: list[str] | None = None,
) -> tuple[list[DeploymentRun], int]:
    """Deployment Runs across projects (S5IF-FLD04), newest change first by default."""
    base = select(DeploymentRun)
    if only_project_ids is not None:
        # A Run reaches its project only through its workflow, and the restriction has to be in the
        # query: trimming a page afterwards would leave `total` counting runs in projects the
        # caller may not see (FR-SEC-004).
        if not only_project_ids:
            return [], 0
        base = base.join(Workflow, Workflow.id == DeploymentRun.workflow_id).where(
            Workflow.project_id.in_(only_project_ids)
        )
    if status:
        base = base.where(DeploymentRun.status == status)
    if environment_key:
        base = base.where(DeploymentRun.environment_key == environment_key)
    if outdated is not None:
        base = base.where(DeploymentRun.outdated.is_(outdated))
    total = int(session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0)
    order = getattr(DeploymentRun, RUN_SORT_COLUMNS.get(sort_by, DeploymentRun.updated_at.name))
    direction = order.desc() if descending else order.asc()
    rows = list(
        session.execute(
            base.order_by(direction, DeploymentRun.id.asc())
            .limit(max(1, min(limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)))
            .offset(max(0, offset or 0))
        )
        .scalars()
        .all()
    )
    return rows, total


def get_gate(session: Session, gate_id: str) -> Gate:
    row = session.get(Gate, gate_id)
    if row is None:
        raise NotFoundControlError(f"gate {gate_id} not found", details={"gateId": gate_id})
    return row


def waiting_gate_for_stage(session: Session, stage_id: str) -> Gate | None:
    return session.execute(
        select(Gate)
        .where(Gate.stage_id == stage_id, Gate.status == GateStatus.WAITING.value)
        .order_by(Gate.created_at.desc())
        .limit(1)
    ).scalars().first()


def approvals_of(session: Session, gate_id: str) -> list[Approval]:
    return list(
        session.execute(
            select(Approval).where(Approval.gate_id == gate_id).order_by(Approval.created_at)
        )
        .scalars()
        .all()
    )


def questions_of(
    session: Session, workflow_id: str, *, stage_id: str | None = None, status: str | None = None
) -> list[Question]:
    query = select(Question).where(Question.workflow_id == workflow_id)
    if stage_id:
        query = query.where(Question.stage_id == stage_id)
    if status:
        query = query.where(Question.status == status)
    return list(session.execute(query.order_by(Question.created_at)).scalars().all())


def questions_across(
    session: Session,
    *,
    status: str | None = None,
    project_id: str | None = None,
    workflow_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Question], int]:
    """One page of questions across projects, and how many match (S5IF-FLD08's list sibling).

    The approval inbox needs the unanswered blocking questions a person can act on; until this
    existed the only way to ask was one request per workflow, which turned the mobile 待办 list into
    a client-side scan and quietly dropped the fourth category the SRS asks for.
    """
    conditions = []
    if status:
        conditions.append(Question.status == status)
    if project_id:
        conditions.append(Question.project_id == project_id)
    if workflow_id:
        conditions.append(Question.workflow_id == workflow_id)

    total = session.execute(
        select(func.count()).select_from(Question).where(*conditions)
    ).scalar_one()
    rows = session.execute(
        select(Question)
        .where(*conditions)
        .order_by(Question.created_at.desc(), Question.id)
        .limit(limit)
        .offset(offset)
    ).scalars().all()
    return list(rows), int(total)


def get_question(session: Session, question_id: str) -> Question:
    row = session.get(Question, question_id)
    if row is None:
        raise NotFoundControlError(f"question {question_id} not found", details={"questionId": question_id})
    return row


def open_high_questions(session: Session, stage_id: str) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(Question)
            .where(
                Question.stage_id == stage_id,
                Question.status == "OPEN",
                Question.severity == "HIGH",
            )
        ).scalar()
        or 0
    )


def target_environments(session: Session, project_id: str) -> list[ProjectTargetEnvironment]:
    return list(
        session.execute(
            select(ProjectTargetEnvironment)
            .where(ProjectTargetEnvironment.project_id == project_id)
            .order_by(ProjectTargetEnvironment.promotion_order, ProjectTargetEnvironment.environment_key)
        )
        .scalars()
        .all()
    )


def design_reference_environment(session: Session, project_id: str) -> ProjectTargetEnvironment | None:
    return session.execute(
        select(ProjectTargetEnvironment).where(
            ProjectTargetEnvironment.project_id == project_id,
            ProjectTargetEnvironment.is_design_reference.is_(True),
        )
    ).scalars().first()


def project_settings(session: Session, project_id: str) -> ProjectSetting:
    row = session.get(ProjectSetting, project_id)
    if row is None:
        row = ProjectSetting(project_id=project_id)
        session.add(row)
        session.flush()
    return row


def change_requests(
    session: Session, project_id: str, *, status: str | None = None
) -> list[ChangeRequest]:
    query = select(ChangeRequest).where(ChangeRequest.project_id == project_id)
    if status:
        query = query.where(ChangeRequest.status == status)
    return list(session.execute(query.order_by(ChangeRequest.created_at)).scalars().all())


def get_change_request(session: Session, cr_id: str) -> ChangeRequest:
    row = session.get(ChangeRequest, cr_id)
    if row is None:
        raise NotFoundControlError(
            f"change request {cr_id} not found", details={"changeRequestId": cr_id}
        )
    return row


def in_progress_change_request(session: Session, project_id: str) -> ChangeRequest | None:
    return session.execute(
        select(ChangeRequest)
        .where(ChangeRequest.project_id == project_id, ChangeRequest.status == "IN_PROGRESS")
        .limit(1)
    ).scalars().first()


def stage_runs(session: Session, stage_id: str) -> list[StageRun]:
    return list(
        session.execute(
            select(StageRun).where(StageRun.stage_id == stage_id).order_by(StageRun.attempt)
        )
        .scalars()
        .all()
    )


def latest_stage_run(session: Session, stage_id: str) -> StageRun | None:
    return session.execute(
        select(StageRun).where(StageRun.stage_id == stage_id).order_by(StageRun.attempt.desc()).limit(1)
    ).scalars().first()


def notifications_of(
    session: Session, *, project_id: str | None, unread_only: bool = False, limit: int | None = None, offset: int | None = None
) -> tuple[list[Notification], int]:
    query = select(Notification)
    total_query = select(func.count()).select_from(Notification)
    if project_id is not None:
        query = query.where(Notification.project_id == project_id)
        total_query = total_query.where(Notification.project_id == project_id)
    if unread_only:
        query = query.where(Notification.read.is_(False))
        total_query = total_query.where(Notification.read.is_(False))
    total = int(session.execute(total_query).scalar() or 0)
    rows = list(
        session.execute(
            query.order_by(Notification.created_at.desc())
            .limit(max(1, min(limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)))
            .offset(max(0, offset or 0))
        )
        .scalars()
        .all()
    )
    return rows, total


def get_notification(session: Session, notification_id: str) -> Notification:
    row = session.get(Notification, notification_id)
    if row is None:
        raise NotFoundControlError(
            f"notification {notification_id} not found", details={"notificationId": notification_id}
        )
    return row


def pending_deliveries(session: Session, *, now: datetime, limit: int = 50) -> list[NotificationDelivery]:
    return list(
        session.execute(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.status.in_(["PENDING", "FAILED"]),
                NotificationDelivery.next_attempt_at.is_(None)
                | (NotificationDelivery.next_attempt_at <= now),
            )
            .order_by(NotificationDelivery.created_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def system_setting(session: Session, key: str) -> SystemSetting | None:
    return session.get(SystemSetting, key)


def events_after(session: Session, workflow_id: str, after: int, *, limit: int = 500) -> list[EventStreamRecord]:
    return list(
        session.execute(
            select(EventStreamRecord)
            .where(
                EventStreamRecord.workflow_id == workflow_id,
                EventStreamRecord.sequence > after,
            )
            .order_by(EventStreamRecord.sequence)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def idempotent_response(
    session: Session, scope: str, key: str
) -> IdempotentResponse | None:
    return session.execute(
        select(IdempotentResponse).where(
            IdempotentResponse.scope == scope, IdempotentResponse.idempotency_key == key
        )
    ).scalars().first()


def save_idempotent_response(
    session: Session,
    *,
    row_id: str,
    scope: str,
    key: str,
    status_code: int,
    content_type: str,
    body: str,
    expires_at: datetime | None,
) -> IdempotentResponse:
    row = IdempotentResponse(
        id=row_id,
        scope=scope,
        idempotency_key=key,
        status_code=status_code,
        content_type=content_type,
        body=body,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


def foundation_record(scope: str, key: str) -> Any:
    """Read the S0 idempotency row behind an endpoint scope."""
    return idempotency.get(scope, key)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "approvals_of",
    "change_requests",
    "create_stage",
    "design_reference_environment",
    "events_after",
    "foundation_record",
    "get_change_request",
    "get_gate",
    "high_open_question_counts",
    "get_notification",
    "get_project",
    "get_question",
    "get_run",
    "get_stage",
    "get_workflow",
    "get_workflow_for_project",
    "gates_of",
    "approval_facts",
    "list_gates",
    "list_runs",
    "idempotent_response",
    "in_progress_change_request",
    "latest_stage_run",
    "list_projects",
    "member_project_ids",
    "notifications_of",
    "open_high_questions",
    "open_run_for_environment",
    "pending_deliveries",
    "project_settings",
    "questions_of",
    "running_stage",
    "runs_of",
    "save_idempotent_response",
    "stage_by_type",
    "stage_runs",
    "stages_of",
    "system_setting",
    "target_environments",
    "waiting_gate_for_stage",
]
