"""Project management (CP-PRJ-001 … CP-PRJ-006).

The transaction shape is the requirement: S2 content is prepared *outside* the
unit of work (it is external I/O), then project properties, the workflow, the
stage skeleton and the S2 registration commit as one unit — any failure rolls
all of it back (CP-PRJ-001).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from factory_control import store as S
from factory_control.domain.enums import (
    DeliveryMode,
    GateStatus,
    MilestoneStatus,
    StageType,
    WorkflowStatus,
)
from factory_control.domain.structure import design_line_plan
from factory_control.errors import (
    InvalidStateError,
    NotFoundControlError,
    ProjectArchivedError,
    ValidationFailedError,
)
from factory_control.foundation import new_id
from factory_control.identity import roles
from factory_control.models import (
    Project,
    ProjectSetting,
    ProjectTargetEnvironment,
    Stage,
    Workflow,
)
from factory_control.runtime import ControlContext, Runtime
from factory_control.services.recording import record_change, write_audit


@dataclass(frozen=True)
class TargetEnvironment:
    environment_key: str
    promotion_order: int = 100
    is_design_reference: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "environmentKey": self.environment_key,
            "promotionOrder": self.promotion_order,
            "isDesignReference": self.is_design_reference,
        }


@dataclass(frozen=True)
class CreateProjectRequest:
    name: str
    owner_name: str
    description: str = ""
    delivery_mode: str = DeliveryMode.A.value
    project_type: str = "greenfield"
    version_constraints: str = ""
    default_language: str = "zh"
    translation_languages: list[str] = field(default_factory=list)
    code_prefix: str = ""
    targets: list[TargetEnvironment] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CreateProjectRequest:
        raw_targets = payload.get("targetEnvironments") or payload.get("targets") or []
        targets = [
            TargetEnvironment(
                environment_key=str(t["environmentKey"]),
                promotion_order=int(t.get("promotionOrder", 100)),
                is_design_reference=bool(t.get("isDesignReference", False)),
            )
            for t in raw_targets
        ]
        return cls(
            name=str(payload.get("name", "")).strip(),
            owner_name=str(payload.get("ownerName", "")).strip(),
            description=str(payload.get("description", "")),
            delivery_mode=str(payload.get("deliveryMode", "A")).upper(),
            project_type=str(payload.get("projectType", "greenfield")),
            version_constraints=str(payload.get("versionConstraints", "")),
            default_language=str(payload.get("defaultLanguage", "zh")),
            translation_languages=[str(x) for x in (payload.get("translationLanguages") or [])],
            code_prefix=str(payload.get("codePrefix", "")),
            targets=targets,
        )


def validate_request(request: CreateProjectRequest) -> None:
    """CP-PRJ-001: at least one target, exactly one design reference.

    Each complaint names the field it is about, because SRS 8.3 gives ``VALIDATION_ERROR``
    a ``fields[{path, message}]`` shape and S5 shows one message per input box (UI-REQ-002).
    """
    problems: list[dict[str, str]] = []
    if not request.name:
        problems.append({"path": "name", "message": "name is required"})
    if not request.owner_name:
        problems.append({"path": "ownerName", "message": "ownerName is required"})
    if request.delivery_mode not in (DeliveryMode.A.value, DeliveryMode.B.value):
        problems.append({"path": "deliveryMode", "message": "deliveryMode must be A or B"})
    if not request.targets:
        problems.append({"path": "targets", "message": "at least one target environment is required"})
    design_refs = [t for t in request.targets if t.is_design_reference]
    if request.targets and len(design_refs) != 1:
        problems.append(
            {
                "path": "targets",
                "message": "exactly one target environment must be the design reference",
            }
        )
    keys = [t.environment_key for t in request.targets]
    if len(keys) != len(set(keys)):
        problems.append({"path": "targets", "message": "target environments must be distinct"})
    if problems:
        raise ValidationFailedError(
            "; ".join(problem["message"] for problem in problems),
            details={"fields": problems},
        )


def ensure_writable(project: Project) -> None:
    """CP-PRJ-006: an archived project is read-only."""
    if project.archived:
        raise ProjectArchivedError(
            f"project {project.id} is archived", details={"projectId": project.id}
        )
    if project.deleted_at is not None:
        raise InvalidStateError(
            f"project {project.id} has been deleted",
            current_state="DELETED",
            required_states=["ACTIVE", "ARCHIVED"],
            target_id=project.id,
            details={"projectId": project.id},
        )


def _confirm_environments(ctx: ControlContext, targets: list[TargetEnvironment]) -> dict[str, str]:
    """CP-PRJ-002: S4 must know the key, and one non-PRODUCTION environment is required."""
    classes: dict[str, str] = {}
    for target in targets:
        if ctx.execution.get_environment(target.environment_key) is None:
            raise InvalidStateError(
                f"unknown environmentKey {target.environment_key!r}: S4 does not have it",
                details={"environmentKey": target.environment_key},
            )
        protection = ctx.execution.get_protection(target.environment_key)
        classes[target.environment_key] = protection.environment_class.upper()
    if not any(c != "PRODUCTION" for c in classes.values()):
        raise InvalidStateError(
            "a project needs at least one non-PRODUCTION target environment",
            details={"classes": classes},
        )
    return classes


def create_project(
    runtime: Runtime, request: CreateProjectRequest, *, operator: str, creator_user_id: str = ""
) -> dict[str, Any]:
    """CP-PRJ-001 end to end; returns the created project view with its stages.

    ``creator_user_id`` is the account that pressed the button, when there is one. Projects are
    isolated by membership rather than by role (FR-SEC-004), so without this row the person who
    just created a project could not open it — ``project.create`` is a platform action, and every
    action inside the project would then answer 403.
    """
    validate_request(request)

    project_id = new_id("prj")
    workflow_id = new_id("wf")

    # --- outside the transaction: prepare initial Blueprint content (S2) ------
    handle = runtime.blueprint.prepare_initial_blueprint(
        project_id,
        {
            "deliveryMode": request.delivery_mode,
            "projectType": request.project_type,
            "versionConstraints": request.version_constraints,
            "languages": [request.default_language, *request.translation_languages],
            "codePrefix": request.code_prefix,
        },
    )

    with runtime.transaction(operator=operator, project_id=project_id, workflow_id=workflow_id) as ctx:
        environment_classes = _confirm_environments(ctx, request.targets)

        project = Project(
            id=project_id,
            name=request.name,
            description=request.description,
            owner_name=request.owner_name,
            delivery_mode=request.delivery_mode,
            project_type=request.project_type,
            version_constraints=request.version_constraints,
            default_language=request.default_language,
            translation_languages=list(request.translation_languages),
            milestone_status=MilestoneStatus.DRAFT.value,
        )
        ctx.session.add(project)
        ctx.session.flush()

        workflow = Workflow(
            id=workflow_id,
            project_id=project_id,
            status=WorkflowStatus.ACTIVE.value,
            last_event_seq=0,
        )
        ctx.session.add(workflow)
        ctx.session.flush()

        plan = design_line_plan(
            delivery_mode=request.delivery_mode,
            has_fixture_spec=True,
        )
        stage_ids: dict[str, str] = {}
        for entry in plan:
            stage_id = new_id("sta")
            ctx.session.add(
                Stage(
                    id=stage_id,
                    workflow_id=workflow_id,
                    stage_type=entry.stage_type.value,
                    status=entry.status.value,
                    attempt=0,
                )
            )
            stage_ids[entry.stage_type.value] = stage_id
        ctx.session.flush()
        workflow.current_stage_id = stage_ids.get(StageType.REQUIREMENTS.value)

        settings_row = ProjectSetting(project_id=project_id)
        ctx.session.add(settings_row)

        for target in request.targets:
            ctx.session.add(
                ProjectTargetEnvironment(
                    id=new_id("tge"),
                    project_id=project_id,
                    environment_key=target.environment_key,
                    promotion_order=target.promotion_order,
                    is_design_reference=target.is_design_reference,
                )
            )

        # --- inside the transaction: the creator becomes the first member -----
        if creator_user_id:
            from factory_control.identity import service as identity

            # CP-PRJ-001's purpose is that the account which created a project can keep working
            # in it, and permissions come from a *role* on the membership row, not from the row's
            # existence: an unrole'd membership left the creator unable to parse the requirements
            # document they had just uploaded (`input.parse` is project-scoped), which is exactly
            # the "project.create is an action that builds something you cannot use" the clause
            # warns about.  The architect role is the one whose grant list covers the design line
            # without reaching deployment authority.
            identity.add_member(
                ctx.session,
                operator,
                project_id,
                creator_user_id,
                role=roles.SOLUTION_ARCHITECT,
            )

        # --- inside the transaction: register the Blueprint version ----------
        promotion = ctx.blueprint.register_initial_blueprint(project_id, handle, session=ctx.session)
        project.current_blueprint_version = promotion.version

        record_change(
            ctx,
            event_type="workflow.started",
            audit_event_type="cp.project.created",
            object_type="Project",
            object_id=project_id,
            payload={
                "projectId": project_id,
                "workflowId": workflow_id,
                "deliveryMode": request.delivery_mode,
                "blueprintVersion": promotion.version,
                "stages": sorted(stage_ids),
            },
            after={"name": project.name, "ownerName": project.owner_name},
        )
        return project_view(ctx, project, workflow, environment_classes=environment_classes)


def project_view(
    ctx: ControlContext,
    project: Project,
    workflow: Workflow | None,
    *,
    environment_classes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The REST projection of a project with its workflow shape (AC-WF-01)."""
    targets = S.target_environments(ctx.session, project.id)
    stages = [] if workflow is None else S.stages_of(ctx.session, workflow.id)
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "ownerName": project.owner_name,
        "deliveryMode": project.delivery_mode,
        "projectType": project.project_type,
        "versionConstraints": project.version_constraints,
        "defaultLanguage": project.default_language,
        "translationLanguages": list(project.translation_languages or []),
        "milestoneStatus": project.milestone_status,
        "archived": project.archived,
        "currentBlueprintVersion": project.current_blueprint_version,
        "workflowId": workflow.id if workflow else None,
        "workflowStatus": workflow.status if workflow else None,
        "currentStageId": workflow.current_stage_id if workflow else None,
        "createdAt": project.created_at,
        "updatedAt": project.updated_at,
        "targetEnvironments": [
            {
                "environmentKey": t.environment_key,
                "promotionOrder": t.promotion_order,
                "isDesignReference": t.is_design_reference,
                "environmentClass": (environment_classes or {}).get(t.environment_key),
            }
            for t in targets
        ],
        "stages": [
            {
                "id": s.id,
                "stageType": s.stage_type,
                "status": s.status,
                "attempt": s.attempt,
                "deploymentRunId": s.deployment_run_id,
            }
            for s in stages
        ],
    }


def get_project_view(runtime: Runtime, project_id: str, *, operator: str = "system") -> dict[str, Any]:
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        try:
            workflow = S.get_workflow_for_project(ctx.session, project_id)
        except NotFoundControlError:
            workflow = None
        return project_view(ctx, project, workflow)


def list_projects(
    runtime: Runtime,
    *,
    operator: str = "system",
    include_archived: bool = True,
    name_like: str | None = None,
    owner: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    sort_by: str = "createdAt",
    sort_dir: str = "DESC",
    only_project_ids: list[str] | None = None,
) -> dict[str, Any]:
    with runtime.transaction(operator=operator) as ctx:
        rows, total = S.list_projects(
            ctx.session,
            include_archived=include_archived,
            name_like=name_like,
            owner=owner,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            descending=sort_dir != "ASC",
            only_project_ids=only_project_ids,
        )
        # One query for the whole page's workflows.  Fetching them per project made the
        # dashboard cost N+1 reads, which CP-NFR-03 does not allow at 5,000 objects.
        workflows: dict[str, Workflow] = {}
        if rows:
            for workflow in ctx.session.execute(
                select(Workflow).where(Workflow.project_id.in_([p.id for p in rows]))
            ).scalars():
                workflows.setdefault(workflow.project_id, workflow)
        items = [project_view(ctx, project, workflows.get(project.id)) for project in rows]
        return {"items": items, "total": total, "limit": limit or 50, "offset": offset or 0}


def update_project(
    runtime: Runtime, project_id: str, *, operator: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """PATCH — management attributes only; Blueprint semantics are not editable here (D-15)."""
    editable = {"name", "description", "ownerName", "defaultLanguage", "translationLanguages", "projectType", "versionConstraints"}
    unknown = set(changes) - editable
    if unknown:
        raise InvalidStateError(
            f"fields cannot be changed on a project: {sorted(unknown)}",
            details={"fields": sorted(unknown)},
        )
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        ensure_writable(project)
        before = {"name": project.name, "description": project.description, "ownerName": project.owner_name}
        if "name" in changes:
            project.name = str(changes["name"]).strip() or project.name
        if "description" in changes:
            project.description = str(changes["description"])
        if "ownerName" in changes:
            project.owner_name = str(changes["ownerName"]).strip() or project.owner_name
        if "projectType" in changes:
            project.project_type = str(changes["projectType"])
        if "versionConstraints" in changes:
            project.version_constraints = str(changes["versionConstraints"])
        if "defaultLanguage" in changes:
            project.default_language = str(changes["defaultLanguage"])
        if "translationLanguages" in changes:
            project.translation_languages = [str(x) for x in (changes["translationLanguages"] or [])]
        ctx.session.flush()
        write_audit(
            ctx,
            event_type="cp.project.updated",
            object_type="Project",
            object_id=project.id,
            before=before,
            after={"name": project.name, "description": project.description, "ownerName": project.owner_name},
        )
        workflow = ctx.session.execute(
            select(Workflow).where(Workflow.project_id == project.id)
        ).scalars().first()
        return project_view(ctx, project, workflow)


def archive_project(runtime: Runtime, project_id: str, *, operator: str) -> dict[str, Any]:
    """CP-PRJ-006: no RUNNING stage, waiting Gates invalidated, workflow cancelled."""
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        if project.archived:
            return project_view(ctx, project, S.get_workflow_for_project(ctx.session, project_id))
        workflow = S.get_workflow_for_project(ctx.session, project_id)
        running = S.running_stage(ctx.session, workflow.id)
        if running is not None:
            raise InvalidStateError(
                "cannot archive while a stage is RUNNING; wait for it or cancel the workflow",
                stage=running.stage_type,
                details={"stageId": running.id, "status": running.status},
            )
        gates = S.gates_of(ctx.session, workflow.id, status=GateStatus.WAITING)
        for gate in gates:
            gate.status = GateStatus.INVALIDATED.value
            gate.invalidated_reason = "PROJECT_ARCHIVED"
        workflow.status = WorkflowStatus.CANCELLED.value
        workflow.cancelled = True
        project.archived = True
        from factory_control.foundation import utcnow

        project.archived_at = utcnow()
        ctx.session.flush()

        record_change(
            ctx,
            event_type="workflow.updated",
            audit_event_type="cp.project.archived",
            object_type="Project",
            object_id=project.id,
            workflow_id=workflow.id,
            payload={"archived": True, "invalidatedGates": [g.id for g in gates]},
        )
        return project_view(ctx, project, workflow)


def replace_target_environments(
    runtime: Runtime, project_id: str, *, operator: str, targets: list[TargetEnvironment]
) -> dict[str, Any]:
    """CP-PRJ-002/003/004 — the full list is replaced atomically.

    Changing the design reference invalidates DISCOVERY and everything after it;
    that propagation is :func:`factory_control.services.workflow.propagate_design_reference_change`
    because it needs the stage rows, not just this list, and it happens in this transaction
    so a settings change never lands with the design line still claiming to be valid.
    """
    validate_targets(targets)
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        ensure_writable(project)
        workflow = S.get_workflow_for_project(ctx.session, project_id)

        previous = {t.environment_key: t for t in S.target_environments(ctx.session, project_id)}
        incoming = {t.environment_key: t for t in targets}
        # snapshotted before anything below touches those rows: the loop that applies the
        # new list writes ``is_design_reference`` onto the very objects ``previous`` holds,
        # so reading it afterwards compares the list with itself and the change is never seen
        old_reference = next((k for k, v in previous.items() if v.is_design_reference), None)
        removed = set(previous) - set(incoming)
        for key in removed:
            if S.open_run_for_environment(ctx.session, workflow.id, key) is not None:
                raise InvalidStateError(
                    f"environment {key!r} still has an in-progress run",
                    details={"environmentKey": key},
                )
            ctx.session.delete(previous[key])

        _confirm_environments(ctx, targets)

        for key, target in incoming.items():
            existing = previous.get(key)
            if existing is None:
                ctx.session.add(
                    ProjectTargetEnvironment(
                        id=new_id("tge"),
                        project_id=project_id,
                        environment_key=target.environment_key,
                        promotion_order=target.promotion_order,
                        is_design_reference=target.is_design_reference,
                    )
                )
            else:
                existing.promotion_order = target.promotion_order
                existing.is_design_reference = target.is_design_reference

        new_reference = next((k for k, v in incoming.items() if v.is_design_reference), None)
        ctx.session.flush()

        from factory_control.services.workflow import propagate_design_reference_change

        invalidated = propagate_design_reference_change(
            ctx,
            project_id=project_id,
            previous_reference=old_reference,
            new_reference=new_reference,
        )
        record_change(
            ctx,
            event_type="workflow.updated",
            audit_event_type="cp.project.environment_updated",
            object_type="Project",
            object_id=project_id,
            workflow_id=workflow.id,
            payload={
                "environments": sorted(incoming),
                "removed": sorted(removed),
                "designReferenceChanged": old_reference != new_reference,
                "invalidatedStages": invalidated,
            },
        )
        return {
            "designReferenceChanged": old_reference != new_reference,
            "previousDesignReference": old_reference,
            "designReference": new_reference,
            "invalidatedStages": invalidated,
            "targetEnvironments": [t.as_dict() for t in targets],
        }


def validate_targets(targets: list[TargetEnvironment]) -> None:
    if not targets:
        raise InvalidStateError("at least one target environment is required")
    refs = [t for t in targets if t.is_design_reference]
    if len(refs) != 1:
        raise InvalidStateError("exactly one design reference environment is required")
    keys = [t.environment_key for t in targets]
    if len(keys) != len(set(keys)):
        raise InvalidStateError("target environments must be distinct")


__all__ = [
    "CreateProjectRequest",
    "TargetEnvironment",
    "archive_project",
    "create_project",
    "ensure_writable",
    "get_project_view",
    "list_projects",
    "project_view",
    "replace_target_environments",
    "update_project",
    "validate_request",
    "validate_targets",
]
