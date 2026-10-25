"""Delivery-mode upgrade, project export / import and deletion
(CP-PRJ-005, CP-PRJ-007, CP-PRJ-008, CP-PRJ-009).

Deletion order matters: S1 marks and audits first, then asks S2/S3/S4 to clean
their own data, then removes its rows, and finally asks S0 to drop artifact
content.  Audit records are never deleted (S0 FD-AUD-009, CP-PRJ-007).
"""

from __future__ import annotations

from typing import Any

from factory_control import store as S
from factory_control.domain.enums import RunStatus, StageStatus, StageType
from factory_control.errors import InvalidStateError
from factory_control.foundation import (
    ArtifactCreate,
    ArtifactQuery,
    ArtifactStatus,
    DataClassification,
    new_id,
    pack,
    purge_content,
    query_artifacts,
    read_content_bytes,
    register_artifact,
    stage_content,
    unpack,
    utcnow,
)
from factory_control.models import Project
from factory_control.runtime import Runtime
from factory_control.services.project import ensure_writable
from factory_control.services.recording import record_change


def upgrade_delivery_mode(runtime: Runtime, project_id: str, *, operator: str) -> dict[str, Any]:
    """CP-PRJ-005 / CP-BR-11: mode A may become B; B never goes back to A."""
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        ensure_writable(project)
        if project.delivery_mode != "A":
            raise InvalidStateError(
                "delivery mode can only be upgraded from A to B",
                details={"projectId": project.id, "currentMode": project.delivery_mode},
            )
        semantics = ctx.blueprint.get_project_semantics(project.id)
        if semantics.delivery_mode == "B":
            raise InvalidStateError(
                "the Blueprint is already in delivery mode B", details={"projectId": project.id}
            )
        handle = None

    handle = runtime.blueprint.prepare_mode_upgrade(project_id)

    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        workflow = S.get_workflow_for_project(ctx.session, project.id)
        promotion = ctx.blueprint.commit_mode_upgrade(
            project.id, str(handle), f"mode:{project.id}", session=ctx.session
        )
        project.current_blueprint_version = promotion.version
        project.delivery_mode = "B"

        _unskip(ctx, workflow_id=workflow.id, stage_type=StageType.PAGE_DESIGN, target=StageStatus.READY)
        _unskip(ctx, workflow_id=workflow.id, stage_type=StageType.HANDOFF, target=StageStatus.LOCKED)
        ctx.session.flush()
        record_change(
            ctx.bind(workflow_id=workflow.id),
            event_type="workflow.updated",
            audit_event_type="cp.project.updated",
            object_type="Project",
            object_id=project.id,
            payload={"deliveryMode": "B", "blueprintVersion": promotion.version, "changeLevel": promotion.change_level},
        )
        return {
            "projectId": project.id,
            "deliveryMode": "B",
            "blueprintVersion": promotion.version,
            "pageDesignStatus": StageStatus.READY.value,
            "handoffStatus": StageStatus.LOCKED.value,
        }


def _unskip(ctx: Any, *, workflow_id: str, stage_type: StageType, target: StageStatus) -> None:
    from factory_control.services.workflow import transition

    stage = S.stage_by_type(ctx.session, workflow_id, stage_type)
    if stage is None or stage.status != StageStatus.SKIPPED.value:
        return
    transition(
        ctx,
        stage=stage,
        target=target,
        event_type="stage.passed" if target is StageStatus.LOCKED else "stage.passed",
        payload={"reason": "DELIVERY_MODE_UPGRADE"},
    )


def export_project(runtime: Runtime, project_id: str, *, operator: str) -> dict[str, Any]:
    """CP-PRJ-008: Blueprint versions + Registry + every referenced artifact + manifest."""
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        if project.deleted_at is not None:
            raise InvalidStateError("the project is deleted", details={"projectId": project.id})
        workflow = S.get_workflow_for_project(ctx.session, project.id)
        manifest = _management_manifest(ctx, project, workflow)

    # S2's export is external I/O and must run with no unit of work open (00 doc 6.2).
    bundle = runtime.blueprint.export_blueprint(project_id)

    artifact_ids = sorted(set(bundle.artifact_ids or []))
    artifact_ids += _referenced_capability_artifacts(project.id)
    packed = pack(artifact_ids, include_phi_content=False)

    handle = stage_content(
        iter([packed.archive]),
        project_id=project.id,
        artifact_type="PROJECT_EXPORT",
        media_type="application/zip",
    )
    with runtime.transaction(operator=operator, project_id=project.id, workflow_id=workflow.id) as ctx:
        project = S.get_project(ctx.session, project_id)
        artifact = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project.id,
                type="PROJECT_EXPORT",
                media_type="application/zip",
                data_classification=DataClassification.CONFIDENTIAL,
                creator_subsystem="S1",
                workflow_id=workflow.id,
                status=ArtifactStatus.APPROVED,
                build_record={
                    "artifactIds": artifact_ids,
                    "exportedAt": utcnow().isoformat(),
                    "archiveHash": packed.archive_hash,
                    "blueprintExportArtifactId": bundle.export_artifact_id,
                    # The management-property manifest travels with the archive
                    # (CP-PRJ-008); PHI_PII content itself is excluded by pack().
                    "manifest": manifest,
                },
            ),
            session=ctx.session,
            clock=ctx.clock,
        )
        record_change(
            ctx,
            event_type="artifact.created",
            audit_event_type="cp.project.exported",
            object_type="Project",
            object_id=project.id,
            payload={"artifactId": artifact.id, "artifactCount": len(artifact_ids)},
        )
        return {
            "projectId": project.id,
            "exportArtifactId": artifact.id,
            "contentHash": artifact.content_hash,
            "includedArtifacts": len(artifact_ids),
        }


def _management_manifest(ctx: Any, project: Project, workflow: Any) -> dict[str, Any]:
    targets = S.target_environments(ctx.session, project.id)
    settings_row = S.project_settings(ctx.session, project.id)
    return {
        "name": project.name,
        "description": project.description,
        "ownerName": project.owner_name,
        "deliveryMode": project.delivery_mode,
        "projectType": project.project_type,
        "versionConstraints": project.version_constraints,
        "defaultLanguage": project.default_language,
        "translationLanguages": list(project.translation_languages or []),
        "targetEnvironments": [
            {
                "environmentKey": t.environment_key,
                "promotionOrder": t.promotion_order,
                "isDesignReference": t.is_design_reference,
            }
            for t in targets
        ],
        "gateReminderHours": settings_row.gate_reminder_hours,
        # No credentials, environment profiles, audit records or PHI_PII content
        # leave the deployment (CP-PRJ-009 【新增】).
        "excluded": ["credentials", "environmentProfiles", "auditRecords", "PHI_PII_CONTENT"],
        "workflowId": workflow.id,
    }


def _referenced_capability_artifacts(project_id: str) -> list[str]:
    """CAPABILITY_PROFILE and DISCOVERY_RESULT keep recompiles hash-stable."""
    ids: list[str] = []
    for kind in ("CAPABILITY_PROFILE", "DISCOVERY_RESULT"):
        page = query_artifacts(ArtifactQuery(project_id=project_id, type=kind, limit=200))
        ids.extend(a.id for a in page.items)
    return ids


def import_project(runtime: Runtime, *, artifact_id: str, operator: str) -> dict[str, Any]:
    """CP-PRJ-009: unpack, verify, create a fresh project, import Blueprint."""
    from factory_control.foundation import get_artifact

    source = get_artifact(artifact_id)
    if source is None or source.type != "PROJECT_EXPORT":
        raise InvalidStateError(
            f"artifact {artifact_id} is not a PROJECT_EXPORT", details={"artifactId": artifact_id}
        )
    archive_bytes, _classification, _media = read_content_bytes(artifact_id)
    source_row = get_artifact(artifact_id)
    manifest = dict((source_row.build_record or {}).get("manifest") or {}) if source_row else {}

    project_id = new_id("prj")
    # S0 verifies every packed hash before registering anything (FD-ART-015).
    result = unpack(archive_bytes, project_id)
    # The Blueprint import is external I/O too: prepared here, committed below.
    import_handle = runtime.blueprint.prepare_import(
        {"exportArtifactId": artifact_id, "projectId": project_id},
        dict(result.id_map or {}),
    )

    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = Project(
            id=project_id,
            name=str(manifest.get("name", "imported")),
            description=str(manifest.get("description", "")),
            owner_name=str(manifest.get("ownerName", operator)),
            delivery_mode=str(manifest.get("deliveryMode", "A")),
            project_type=str(manifest.get("projectType", "greenfield")),
            version_constraints=str(manifest.get("versionConstraints", "")),
            default_language=str(manifest.get("defaultLanguage", "zh")),
            translation_languages=[str(x) for x in (manifest.get("translationLanguages") or [])],
            milestone_status="DRAFT",
        )
        ctx.session.add(project)
        ctx.session.flush()
        from factory_control.models import Workflow

        wf = Workflow(
            id=new_id("wf"),
            project_id=project.id,
            status="ACTIVE",
            last_event_seq=0,
        )
        ctx.session.add(wf)
        ctx.session.flush()
        _create_imported_stages(ctx, workflow_id=wf.id, delivery_mode=project.delivery_mode)

        imported = ctx.blueprint.commit_import(
            project.id, import_handle, f"import:{project.id}", session=ctx.session
        )
        project.current_blueprint_version = (
            imported.restored_versions[-1] if imported.restored_versions else None
        )
        record_change(
            ctx.bind(workflow_id=wf.id),
            event_type="workflow.started",
            audit_event_type="cp.project.imported",
            object_type="Project",
            object_id=project.id,
            payload={"sourceArtifactId": artifact_id, "restoredVersions": imported.restored_versions},
        )
        return {
            "projectId": project.id,
            "workflowId": wf.id,
            "currentBlueprintVersion": project.current_blueprint_version,
            "requiresEnvironmentRegistration": True,
            "designStagesRestartFromDraft": True,
        }


def _create_imported_stages(ctx: Any, *, workflow_id: str, delivery_mode: str) -> None:
    """Imported design stages become a draft to re-approve: first READY, rest LOCKED."""
    from factory_control.domain.structure import design_line_plan
    from factory_control.models import Stage

    plan = design_line_plan(delivery_mode=delivery_mode, has_fixture_spec=True)
    stage_id = None
    for entry in plan:
        current = StageStatus.READY if entry.stage_type is StageType.REQUIREMENTS else entry.status
        if entry.status is StageStatus.SKIPPED:
            current = StageStatus.SKIPPED
        row = Stage(
            id=new_id("sta"),
            workflow_id=workflow_id,
            stage_type=entry.stage_type.value,
            status=current.value,
            attempt=0,
        )
        ctx.session.add(row)
        if entry.stage_type is StageType.REQUIREMENTS:
            stage_id = row.id
    if stage_id is not None:
        from factory_control.models import Workflow

        wf = ctx.session.get(Workflow, workflow_id)
        if wf is not None:
            wf.current_stage_id = stage_id
    ctx.session.flush()


def delete_project(runtime: Runtime, project_id: str, *, operator: str) -> dict[str, Any]:
    """CP-PRJ-007 / CP-AC-09: archived only, export first, then cascade."""
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id, include_deleted=True)
        if not project.archived:
            raise InvalidStateError(
                "only an archived project can be deleted",
                current_state="ACTIVE",
                required_states=["ARCHIVED"],
                target_id=project.id,
                details={"projectId": project.id, "archived": project.archived},
            )
        if project.deleted_at is not None:
            return {"projectId": project.id, "deleted": True, "already": True}
        exports = query_artifacts(ArtifactQuery(project_id=project.id, type="PROJECT_EXPORT", limit=1))
        if not exports.items:
            raise InvalidStateError(
                "delete the project only after a PROJECT_EXPORT exists",
                current_state="ARCHIVED",
                target_id=project.id,
                details={"projectId": project.id, "requires": "PROJECT_EXPORT"},
            )
        workflow = S.get_workflow_for_project(ctx.session, project.id)
        project.deleted_at = utcnow()
        ctx.session.flush()
        record_change(
            ctx.bind(workflow_id=workflow.id),
            event_type="workflow.updated",
            audit_event_type="cp.project.deleted",
            object_type="Project",
            object_id=project.id,
            payload={"exportArtifactId": exports.items[0].id},
        )
        artifact_ids = _all_project_artifact_ids(ctx, project.id)

    # Provider cleanup runs in its own transaction (CP-ASM-07).
    for cleanup in (runtime.blueprint.delete_project_data, runtime.intelligence.delete_project_data):
        with runtime.transaction(operator=operator, project_id=project_id) as ctx:
            cleanup(project.id, session=ctx.session)
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        ctx.execution.delete_project_data(project.id, session=ctx.session)

    # Content removal is external I/O: after everything else, outside a unit of work.
    for artifact_id in artifact_ids:
        try:
            purge_content(artifact_id)
        except Exception:  # noqa: BLE001 - audit must survive a purge failure
            continue
    return {"projectId": project_id, "deleted": True, "purgedArtifacts": len(artifact_ids)}


def _all_project_artifact_ids(ctx: Any, project_id: str) -> list[str]:
    page = query_artifacts(ArtifactQuery(project_id=project_id, limit=500), session=ctx.session)
    return [a.id for a in page.items]


def purge_artifact(runtime: Runtime, artifact_id: str, *, operator: str) -> dict[str, Any]:
    """CP: PHI_PII / retention purge — content goes, metadata and audit stay."""
    from factory_control.foundation import get_artifact

    artifact = get_artifact(artifact_id)
    if artifact is None:
        raise InvalidStateError(f"unknown artifact {artifact_id}", details={"artifactId": artifact_id})
    # Deleting stored content is external I/O, so it happens with no unit of work
    # open; the audit of the purge is then written in its own transaction
    # (FD-ART-010, 00 doc 6.2).
    result = purge_content(artifact_id)
    with runtime.transaction(operator=operator, project_id=artifact.project_id) as ctx:
        workflow = S.get_workflow_for_project(ctx.session, artifact.project_id)
        record_change(
            ctx.bind(workflow_id=workflow.id),
            event_type="artifact.superseded",
            audit_event_type="cp.artifact.purged",
            object_type="Artifact",
            object_id=artifact_id,
            payload={"type": result.type, "classification": result.data_classification.value},
        )
        return {"artifactId": artifact_id, "contentPurged": result.content_purged}


def verified_runs_for_version(runtime: Runtime, workflow_id: str, version: str) -> list[str]:
    """Helper for the M2 production rule (CP-WF-020)."""
    with runtime.transaction(operator="system") as ctx:
        return [
            run.environment_key
            for run in S.runs_of(ctx.session, workflow_id)
            if run.status == RunStatus.VERIFIED.value and run.blueprint_version == version
        ]


__all__ = [
    "delete_project",
    "export_project",
    "import_project",
    "purge_artifact",
    "upgrade_delivery_mode",
    "verified_runs_for_version",
]
