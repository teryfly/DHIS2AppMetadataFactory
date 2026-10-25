"""Business input upload and parsing (SRS 6.1, CP-API-009).

The upload path is exactly the two-step artifact flow S0 prescribes: content is
streamed and hash-verified **outside** the transaction, then the metadata, the
audit record and the stage link commit together.

Type and size limits are enforced here, at the entry point — S0 keeps no limit for
user uploads (FD-ART-006 defers that to S1).
"""

from __future__ import annotations

import io
from typing import Any

from factory_control import store as S
from factory_control.domain.enums import StageType
from factory_control.errors import (
    NotFoundControlError,
    UploadRejectedError,
    ValidationFailedError,
)
from factory_control.foundation import (
    ArtifactCreate,
    ArtifactQuery,
    ArtifactStatus,
    DataClassification,
    query_artifacts,
    register_artifact,
    stage_content,
)
from factory_control.models import Stage
from factory_control.runtime import ControlContext, Runtime
from factory_control.security.content_scan import scan_input_content
from factory_control.services.recording import record_change


def record_input(
    runtime: Runtime,
    project_id: str,
    *,
    filename: str,
    media_type: str | None,
    data: bytes,
    operator: str,
    data_classification: DataClassification | None = None,
    phi_declared: bool = False,
) -> dict[str, Any]:
    """CP-API-009 + SRS 6.1 step 1: validate, stage, register, audit.

    The classification is the uploader's declaration (FR-REQ-001), not a default S1 can
    pick for them: a PHI/PII file recorded as ``CONFIDENTIAL`` passes every policy check
    that keys on ``PHI_PII``, so the file would reach a model context while the audit said
    it never could (AC-POL-02).  Refusing an undeclared upload is the only way the guard
    downstream means anything.
    """
    if data_classification is None:
        raise ValidationFailedError(
            "the upload needs a data classification",
            details={
                "fields": [
                    {
                        "path": "dataClassification",
                        "message": "FR-REQ-001: declared by the uploader; one of PUBLIC, INTERNAL, "
                        "CONFIDENTIAL, RESTRICTED, PHI_PII",
                    }
                ]
            },
        )
    if data_classification is DataClassification.PHI_PII and not phi_declared:
        raise ValidationFailedError(
            "PHI/PII input needs an explicit declaration",
            details={
                "fields": [
                    {
                        "path": "phiDeclared",
                        "message": "PHI_PII must be confirmed by the uploader: it never enters a "
                        "model context under any policy (FR-POL-003, AC-POL-02)",
                    }
                ]
            },
        )
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        policy = ctx.settings.upload_policy()
    if not data:
        raise UploadRejectedError(
            "the uploaded file is empty",
            details={
                "filename": filename,
                "violation": "SIZE_EXCEEDED",
                "mediaType": media_type,
                "sizeBytes": 0,
                "maxBytes": policy.max_bytes,
                "allowedTypes": list(policy.allowed_types),
            },
        )

    violation = policy.violation(media_type, len(data))
    if violation is not None:
        raise UploadRejectedError(
            f"upload rejected: {violation}",
            details={
                "filename": filename,
                "violation": violation,
                "mediaType": media_type,
                "sizeBytes": len(data),
                "maxBytes": policy.max_bytes,
                "allowedTypes": list(policy.allowed_types),
            },
        )

    # CP-API-009's second half: the type and size limits say what may arrive; the scan says what
    # the bytes actually are. Both answer 422 UPLOAD_REJECTED with the rule that fired in `violation`.
    content_violation = scan_input_content(filename, media_type, data)
    if content_violation is not None:
        raise UploadRejectedError(
            f"upload rejected: {content_violation}",
            details={
                "filename": filename,
                "violation": content_violation,
                "mediaType": media_type,
                "sizeBytes": len(data),
                "maxBytes": policy.max_bytes,
                "allowedTypes": list(policy.allowed_types),
            },
        )

    # External I/O: write and verify content before any transaction is open.
    handle = stage_content(
        io.BytesIO(data),
        project_id=project_id,
        artifact_type="BUSINESS_INPUT",
        media_type=media_type or "application/octet-stream",
    )

    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        workflow = S.get_workflow_for_project(ctx.session, project.id)
        artifact = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project.id,
                type="BUSINESS_INPUT",
                media_type=media_type or "application/octet-stream",
                data_classification=data_classification,
                creator_subsystem="S1",
                workflow_id=workflow.id,
                status=ArtifactStatus.APPROVED,
                build_record={"filename": filename, "sizeBytes": len(data)},
            ),
            session=ctx.session,
            clock=ctx.clock,
        )
        requirements = S.stage_by_type(ctx.session, workflow.id, StageType.REQUIREMENTS)
        if requirements is not None:
            ids = list(requirements.output_artifact_ids or [])
            if artifact.id not in ids:
                ids.append(artifact.id)
                requirements.output_artifact_ids = ids
        record_change(
            ctx.bind(workflow_id=workflow.id),
            event_type="artifact.created",
            audit_event_type="cp.input.uploaded",
            object_type="Artifact",
            object_id=artifact.id,
            payload={"artifactId": artifact.id, "type": "BUSINESS_INPUT", "filename": filename},
        )
        return {
            "artifactId": artifact.id,
            "filename": filename,
            "mediaType": artifact.media_type,
            "sizeBytes": artifact.size,
            "contentHash": artifact.content_hash,
            "dataClassification": artifact.data_classification.value,
            "stageId": requirements.id if requirements else None,
        }


def list_inputs(
    runtime: Runtime,
    project_id: str,
    *,
    operator: str = "system",
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        workflow = S.get_workflow_for_project(ctx.session, project_id)
        page = query_artifacts(
            ArtifactQuery(
                project_id=project_id,
                type="BUSINESS_INPUT",
                limit=min(limit or 50, 500),
                offset=offset or 0,
            ),
            session=ctx.session,
        )
        parsed = _parse_records(ctx, workflow_id=workflow.id)
        return {
            "items": [
                {
                    "artifactId": a.id,
                    "filename": (a.build_record or {}).get("filename"),
                    "mediaType": a.media_type,
                    "sizeBytes": a.size,
                    "contentHash": a.content_hash,
                    "dataClassification": a.data_classification.value,
                    "createdAt": a.created_at,
                    **(parsed.get(a.id) or {}),
                }
                for a in page.items
            ],
            "total": page.total,
            "limit": min(limit or 50, 500),
            "offset": offset or 0,
        }


def _parse_records(ctx: ControlContext, *, workflow_id: str) -> dict[str, dict[str, Any]]:
    """Parse status per input artifact: the latest run row, then the stage-run fallback.

    ``cp.input_parse_runs`` is authoritative because one requirements stage can parse
    several inputs and a stage row's single ``result`` cannot name them all.  The
    ``cp.stage_runs`` read stays for inputs parsed before this table existed, so an
    upgrade does not blank the list.
    """
    from sqlalchemy import select

    from factory_control.models import InputParseRun, Stage, StageRun

    out: dict[str, dict[str, Any]] = {}
    stage_ids = list(
        ctx.session.execute(select(Stage.id).where(Stage.workflow_id == workflow_id)).scalars()
    )
    if stage_ids:
        rows = ctx.session.execute(
            select(StageRun).where(StageRun.stage_id.in_(stage_ids))
        ).scalars()
        for row in rows:
            result = dict(row.result or {})
            if result.get("kind") != "inputParse":
                continue
            artifact_id = result.get("inputArtifactId")
            if not artifact_id:
                continue
            out[str(artifact_id)] = {
                "parseStatus": result.get("status", "PARSED"),
                "errorCode": result.get("errorCode"),
                "errorMessage": result.get("errorMessage"),
                "parserVersion": result.get("parserVersion"),
                "parserType": result.get("parserType"),
                "usedModel": result.get("usedModel"),
                "parseId": result.get("parseId") or row.id,
                "parseRunId": result.get("parseRunId"),
                "nrrArtifactId": result.get("nrrArtifactId"),
            }

    if ctx.project_id:
        runs = ctx.session.execute(
            select(InputParseRun)
            .where(InputParseRun.project_id == ctx.project_id)
            .order_by(InputParseRun.created_at.desc(), InputParseRun.id.desc())
        ).scalars()
        seen: set[str] = set()
        for run in runs:
            artifact_id = str(run.input_artifact_id)
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            # the latest run is newer than the stage snapshot it replaces, so it wins
            out[artifact_id] = {
                "parseStatus": run.status,
                "errorCode": run.error_code,
                "errorMessage": run.error_message,
                "parserVersion": run.parser_version,
                "parserType": run.parser_type,
                "usedModel": run.used_model,
                "parseId": run.parse_id,
                "parseRunId": run.id,
                "nrrArtifactId": run.nrr_artifact_id,
            }
    return out


def parse_input(
    runtime: Runtime,
    input_artifact_id: str,
    *,
    operator: str,
    stage_id: str | None = None,
    options: dict[str, Any] | None = None,
    request_key: str = "",
    project_id: str | None = None,
) -> dict[str, Any]:
    """SRS 6.1 step 2, asynchronous: claim the parse and return its run id (CP-API-012).

    The model call no longer happens inside this request; :mod:`factory_control.services.
    parse_runs` runs it on a pool thread and writes the terminal status on
    ``cp.input_parse_runs``.  The caller's answer is the acceptance the contract already
    declared (202) — the input list and SSE carry the progress.
    """
    from factory_control.services.parse_runs import start_parse

    return start_parse(
        runtime,
        project_id,
        input_artifact_id,
        operator=operator,
        stage_id=stage_id,
        options=options,
        request_key=request_key,
    )


def _record_parse_outcome(
    ctx: ControlContext,
    *,
    stage: Stage,
    input_artifact_id: str,
    parsed: Any,
    outcome: dict[str, Any],
    key: str,
    parse_run_id: str | None = None,
) -> str:
    """The step run this parse wrote its answer on (CP-API-012)."""
    from factory_control.services.workflow import _stage_run

    row = _stage_run(
        ctx,
        stage,
        agent_run_ids=[],
        idempotency_key=key,
        output_artifact_ids=[parsed.nrr_artifact_id] if parsed.nrr_artifact_id else None,
        result={
            "kind": "inputParse",
            "inputArtifactId": input_artifact_id,
            "status": parsed.status,
            "errorCode": parsed.error_code,
            "errorMessage": parsed.error_message,
            "parserVersion": parsed.parser_version,
            "parserType": parsed.parser_type,
            "usedModel": parsed.used_model,
            "parseId": parsed.parse_id or outcome.get("stageId"),
            "nrrArtifactId": parsed.nrr_artifact_id,
            "parseRunId": parse_run_id,
        },
    )
    return row.id


def column_mapping(
    runtime: Runtime, project_id: str, artifact_id: str, *, operator: str
) -> dict[str, Any]:
    """S5IF-INP03 — the mapping review currently reachable through Questions.

    S3's structured ``TablePlan`` stays inside the NRR document (S5IF-UP07); what S1
    can forward today is each review Question's own ``columnMapping`` proposal.
    """
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        workflow = S.get_workflow_for_project(ctx.session, project_id)
        rows = S.questions_of(ctx.session, workflow.id, stage_id=None)
        proposals: list[dict[str, Any]] = []
        for row in rows:
            for suggestion in list(row.suggestions or []):
                mapping = suggestion.get("columnMapping") or suggestion.get("column_mapping")
                if not mapping:
                    continue
                proposals.append(
                    {
                        "questionId": row.id,
                        "sheet": suggestion.get("sheet") or (mapping.get("sheet") if isinstance(mapping, dict) else None),
                        "question": suggestion.get("question") or row.text,
                        "headers": list(suggestion.get("headers") or []),
                        "affectedKeys": list(suggestion.get("affectedKeys") or row.affects or []),
                        "columnMapping": dict(mapping),
                        "suggestions": [dict(suggestion)],
                    }
                )
        return {
            "artifactId": artifact_id,
            "workflowId": workflow.id,
            "proposals": proposals,
            "tablePlans": None,
        }


def _requirements_stage(ctx: ControlContext, input_artifact_id: str) -> Stage:
    """Find the REQUIREMENTS stage that owns this input artifact."""
    from sqlalchemy import select

    rows = ctx.session.execute(select(Stage).where(Stage.stage_type == "REQUIREMENTS")).scalars().all()
    for stage in rows:
        if input_artifact_id in list(stage.output_artifact_ids or []):
            return stage
    if rows:
        return rows[0]
    raise NotFoundControlError(
        f"no REQUIREMENTS stage is linked to input {input_artifact_id}",
        details={"inputArtifactId": input_artifact_id},
    )


__all__ = ["list_inputs", "parse_input", "record_input"]
