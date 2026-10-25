"""Build record construction and persistence (BP-CMP-003, BP-NFR-08)."""

from __future__ import annotations

from typing import Any

from factory_foundation.api import new_id, structure_hash
from sqlalchemy.orm import Session

from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.enums import BuildKind, BuildStatus
from factory_blueprint.models.build import BuildRecordRow


def build_record(
    inputs: CompileInputs,
    *,
    kind: BuildKind,
    output_artifact_ids: dict[str, str],
    output_hashes: dict[str, str],
    package_hash: str | None,
    status: BuildStatus = BuildStatus.COMPLETED,
    s4_probe: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The reproducibility record of one build.

    ``s4_probe`` stays in the database: it records what the environment looked
    like when the build ran, and must never reach an artifact, whose bytes depend
    only on the inputs (BP-CMP-001).
    """
    return {
        "kind": kind.value,
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "blueprintHash": inputs.blueprint_hash,
        "environmentKey": inputs.environment_key,
        # the three parameters that change an environment-bound product but cannot be
        # recomputed from the database: reproduce() has to replay them or it recompiles a
        # different package and reports a defect against its own record (FR-CMP-004)
        "compileParameters": {
            "designEnvironment": inputs.design_environment,
            "changeRequestId": inputs.change_request_id,
            "deletionCandidates": sorted(inputs.deletion_candidates),
        },
        "compatibility": inputs.compatibility(),
        "inputs": [ref.model_dump() for ref in inputs.input_refs()],
        "buildContext": inputs.build_context.as_record(),
        "outputArtifactIds": dict(sorted(output_artifact_ids.items())),
        "outputHashes": dict(sorted(output_hashes.items())),
        "packageHash": package_hash,
        "status": status.value,
        "error": error,
    }


def records_hash(record: dict[str, Any]) -> str:
    return structure_hash({key: value for key, value in record.items() if key != "error"})


def recipe_hash(
    kind: BuildKind, blueprint_version: str, environment_key: str | None, build_context: Any
) -> str:
    """The dedup key of a compilation: same recipe, same result (BP-CMP-004)."""
    return structure_hash(
        {
            "kind": kind.value,
            "blueprintVersion": blueprint_version,
            "environmentKey": environment_key,
            "buildContext": build_context.as_record()
            if hasattr(build_context, "as_record")
            else build_context,
        }
    )


def save(
    session: Session,
    *,
    inputs: CompileInputs,
    kind: BuildKind,
    record: dict[str, Any],
    output_artifact_ids: dict[str, str],
    output_hashes: dict[str, str],
    package_hash: str | None,
    status: BuildStatus,
    s4_probe: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> BuildRecordRow:
    compatibility = inputs.compatibility()
    row = BuildRecordRow(
        id=new_id("bld"),
        project_id=inputs.project_id,
        kind=kind.value,
        blueprint_version=inputs.blueprint_version,
        blueprint_hash=inputs.blueprint_hash,
        environment_key=inputs.environment_key,
        inputs=record["inputs"],
        build_context=record["buildContext"],
        compile_parameters=record["compileParameters"],
        blueprint_schema_version=int(compatibility["blueprintSchemaVersion"] or 0),
        compiler_version=str(compatibility["compilerVersion"] or ""),
        template_version=str(compatibility["templateVersion"] or ""),
        descriptor_version=str(compatibility["descriptorVersion"] or ""),
        profile_version=compatibility["profileVersion"],
        openapi_version=compatibility["openApiVersion"],
        package_template_version=compatibility["packageTemplateVersion"],
        output_artifact_ids=dict(sorted(output_artifact_ids.items())),
        output_hashes=dict(sorted(output_hashes.items())),
        package_hash=package_hash,
        records_hash=records_hash(record),
        recipe_hash=recipe_hash(
            kind, inputs.blueprint_version, inputs.environment_key, inputs.build_context
        ),
        status=status.value,
        s4_probe=s4_probe,
        error=error,
    )
    session.add(row)
    session.flush()
    return row


def find_by_artifact(session: Session, artifact_id: str) -> BuildRecordRow | None:
    """The build that produced a given artifact, for reproduction."""
    from sqlalchemy import select, text

    lookup = text(
        "EXISTS (SELECT 1 FROM jsonb_each_text(bp.build_record.output_artifact_ids) AS kv "
        "WHERE kv.value = :artifact_id)"
    ).bindparams(artifact_id=artifact_id)
    rows = session.execute(
        select(BuildRecordRow).where(lookup).order_by(BuildRecordRow.created_at.desc())
    ).scalars()
    return next(iter(rows), None)


def load(session: Session, build_id: str) -> BuildRecordRow | None:
    return session.get(BuildRecordRow, build_id)
