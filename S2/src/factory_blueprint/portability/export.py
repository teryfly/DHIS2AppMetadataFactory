"""Export one project's Blueprint to a portable file (BP-EXP-001).

The export carries the version *contents* — the same bytes that live in each
``BLUEPRINT`` artifact — plus the ``bp`` rows that cannot be derived from them: the
UID ledger, the environment bindings and the build records.  The derived indexes
(Registry, decisions, business-key and dependency indexes) are deliberately absent:
the importing deployment rebuilds them from the contents through the same code path
a promotion uses, which is also the proof that ``bp`` is only an index (BP-VER-002).
"""

from __future__ import annotations

import json
from typing import Any

from factory_foundation.api import NotFoundError, content_hash, external_io, file_json, unit_of_work
from sqlalchemy import select

from factory_blueprint import artifacts
from factory_blueprint.compilers import files as product_files
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.dto.portability import ExportResult
from factory_blueprint.models.binding import EnvironmentBindingRow
from factory_blueprint.models.build import BuildRecordRow, ValidationRunRow
from factory_blueprint.models.uid import UidAllocationRow, UidPoolRow
from factory_blueprint.models.version import BlueprintVersionRow
from factory_blueprint.version import numbering

EXPORT_TYPE = "BLUEPRINT_EXPORT"
EXPORT_SCHEMA_VERSION = 1
MANIFEST_NAME = "export_manifest.json"
VERSIONS_PREFIX = "versions/"
#: the row documents below are keyed by ``bp`` column name, verbatim
ROW_STYLE = "column"


class _VersionView:
    """A version row, plus the content that belongs to it."""

    def __init__(self, row: BlueprintVersionRow, document: dict[str, Any]) -> None:
        self.row = row
        self.document = document

    def manifest_record(self) -> dict[str, Any]:
        row, document = self.row, self.document
        return {
            "version": row.version,
            "parentVersion": row.parent_version,
            "schemaVersion": row.schema_version,
            "changeLevel": row.change_level,
            "contentHash": row.content_hash,
            "artifactId": row.artifact_id,
            "status": row.status,
            "sourceArtifactIds": sorted(str(item) for item in row.source_artifact_ids or []),
            "decisionIds": sorted(str(item) for item in row.decision_ids or []),
            "promotedFromStage": row.promoted_from_stage,
            "promotedFromDraftId": row.promoted_from_draft_id,
            "createdBy": row.created_by,
            "documentHash": BlueprintDocument(document).content_hash,
        }


def _row_document(row: Any) -> dict[str, Any]:
    columns = sorted(str(column.name) for column in row.__mapper__.columns)
    values = {name: getattr(row, name) for name in columns}
    document: dict[str, Any] = json.loads(file_json(values))
    return document


def _rows(session: Any, model: Any, project_id: str, order: tuple[str, ...]) -> list[dict[str, Any]]:
    statement = select(model).where(model.project_id == project_id)
    for column in order:
        statement = statement.order_by(getattr(model, column))
    return [_row_document(row) for row in session.execute(statement).scalars()]


#: table key → (model, order).  ``uidAllocation`` is the ledger that keeps a UID
#: from ever being reused (BP-REG-006), so it must travel with the project.
_ROW_TABLES: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
    ("uidAllocation", UidAllocationRow, ("uid",)),
    ("uidPool", UidPoolRow, ("uid",)),
    (
        "environmentBinding",
        EnvironmentBindingRow,
        ("blueprint_version", "environment_key", "business_key"),
    ),
    ("buildRecord", BuildRecordRow, ("id",)),
)


def _referenced_ids(rows: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        for value in row.values():
            found.update(_nested_ids(value))
    return found


def _nested_ids(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value.startswith("art-") else set()
    if isinstance(value, dict):
        found: set[str] = set()
        for item in value.values():
            found.update(_nested_ids(item))
        return found
    if isinstance(value, list):
        collected: set[str] = set()
        for item in value:
            collected.update(_nested_ids(item))
        return collected
    return set()


def _collect(project_id: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read phase of an export: index rows in one short transaction, content outside."""
    with unit_of_work() as session:
        version_rows = sorted(
            session.execute(
                select(BlueprintVersionRow).where(BlueprintVersionRow.project_id == project_id)
            ).scalars(),
            key=lambda row: numbering.Version.parse(row.version),
        )
        index_rows = {
            name: _rows(session, model, project_id, order) for name, model, order in _ROW_TABLES
        }
        validation_runs = [
            _row_document(row)
            for row in session.execute(
                select(ValidationRunRow)
                .join(BuildRecordRow, BuildRecordRow.id == ValidationRunRow.build_id)
                .where(BuildRecordRow.project_id == project_id)
                .order_by(ValidationRunRow.id)
            ).scalars()
        ]
        current = next((row.version for row in version_rows if row.status == "CURRENT"), None)

    if not version_rows:
        raise NotFoundError(
            f"project {project_id} has no Blueprint version to export",
            details={"projectId": project_id},
        )

    views = [_VersionView(row, artifacts.read_json(row.artifact_id)[0]) for row in version_rows]
    files: dict[str, bytes] = {
        f"{VERSIONS_PREFIX}{view.row.version}.json": product_files.json_bytes(view.document)
        for view in views
    }
    referenced: set[str] = set()
    for view in views:
        referenced.add(view.row.artifact_id)
        referenced.update(view.manifest_record()["sourceArtifactIds"])
    for rows in index_rows.values():
        referenced.update(_referenced_ids(rows))
    for run in validation_runs:
        if run.get("report_artifact_id"):
            referenced.add(str(run["report_artifact_id"]))

    manifest: dict[str, Any] = {
        "schemaVersion": EXPORT_SCHEMA_VERSION,
        "kind": EXPORT_TYPE,
        "projectId": project_id,
        "blueprintSchemaVersion": version_rows[0].schema_version,
        "currentVersion": current,
        "rowStyle": ROW_STYLE,
        "versions": [view.manifest_record() for view in views],
        "rows": {**index_rows, "validationRun": validation_runs},
        "referencedArtifactIds": sorted(referenced),
        # the importing side checks these before it restores anything (BP-EXP-002)
        "fileHashes": {
            name: content_hash(data) for name, data in sorted(files.items())
        },
    }
    files[MANIFEST_NAME] = product_files.json_bytes(manifest)
    return manifest, files


@external_io
def export_blueprint(project_id: str) -> ExportResult:
    """Export every version, the UID ledger, the bindings and the build records."""
    manifest, files = _collect(project_id)
    handle = artifacts.stage_files(files, project_id, EXPORT_TYPE)
    with unit_of_work() as session:
        info = artifacts.register(
            handle,
            project_id=project_id,
            artifact_type=EXPORT_TYPE,
            # the export lists every referenced artifact as a source, so S0 expects the
            # level of the most sensitive one among them (BP-CMP-007)
            data_classification=artifacts.classification_of_inputs(
                manifest["referencedArtifactIds"]
            ),
            source_artifact_ids=list(manifest["referencedArtifactIds"]),
            session=session,
        )
    return ExportResult(
        project_id=project_id,
        artifact_id=info.id,
        content_hash=info.content_hash,
        files={name: content_hash(data) for name, data in sorted(files.items())},
        versions=[record["version"] for record in manifest["versions"]],
        current_version=manifest["currentVersion"],
        referenced_artifact_ids=list(manifest["referencedArtifactIds"]),
    )
