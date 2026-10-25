"""Two-phase import of an exported project (BP-EXP-002).

``prepare_import`` is external I/O: it reads the export artifact, checks that every
version's content still hashes to what the manifest recorded, and rewrites the
artifact IDs the export names through the caller's ``id_map`` — the map S0's unpack
returns, because this deployment holds its own IDs for the same bytes.  What it
learns is parked in ``bp.import_batch``.

``commit_import`` runs inside the caller's unit of work and writes only ``bp`` rows:
the version index, the UID ledger, the bindings and the build records; then it
rebuilds every derived index from the version contents.  Restoring through the same
index code a promotion uses is what keeps an import from a foreign S2 build
self-consistent.  Bindings come back marked pending, because the environments over
there are not the environments here.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from factory_foundation.api import (
    NotFoundError,
    content_hash,
    current_session,
    external_io,
    idempotency,
    new_id,
    structure_hash,
    unit_of_work,
)
from sqlalchemy import DateTime, select
from sqlalchemy.orm import Session

from factory_blueprint import artifacts
from factory_blueprint.audit_log import record
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.draft.service import apply_version_semantics
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.dto.portability import ImportPreview, ImportResult
from factory_blueprint.enums import BindingResolution, IssueLevel, VersionStatus
from factory_blueprint.errors import ImportConflict, OperationInProgress
from factory_blueprint.indexes import rebuild_indexes
from factory_blueprint.models.binding import EnvironmentBindingRow
from factory_blueprint.models.build import BuildRecordRow, ValidationRunRow
from factory_blueprint.models.portability import ImportBatchRow
from factory_blueprint.models.uid import UidAllocationRow, UidPoolRow
from factory_blueprint.models.version import BlueprintVersionRow
from factory_blueprint.portability.export import MANIFEST_NAME, VERSIONS_PREFIX
from factory_blueprint.registries import IdemScope

RULE_INTEGRITY = "BPV-IMP-001"
RULE_UID_TAKEN = "BPV-IMP-002"
RULE_UNMAPPED = "BPV-IMP-003"

PREPARED = "PREPARED"
COMMITTED = "COMMITTED"
#: never restored: this deployment stamps its own rows
_BOOKKEEPING = frozenset({"created_at", "updated_at"})


def _issue(rule: str, message: str, *, key: str, remediation: str | None = None) -> Issue:
    return make_issue(
        rule, IssueLevel.BLOCKING, message, business_key=key, remediation=remediation
    )


def _rewrite(value: Any, id_map: dict[str, str], unmapped: set[str]) -> Any:
    """Replace each ``art-`` ID this row names with this deployment's ID."""
    if isinstance(value, str):
        if not value.startswith("art-"):
            return value
        if value in id_map:
            return id_map[value]
        unmapped.add(value)
        return value
    if isinstance(value, list):
        return [_rewrite(item, id_map, unmapped) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, id_map, unmapped) for key, item in sorted(value.items())}
    return value


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _construct(model: Any, values: dict[str, Any], **overrides: Any) -> Any:
    """Build an ORM row from exported column values, typing timestamps correctly."""
    columns = {str(column.name): column for column in model.__mapper__.columns}
    kwargs: dict[str, Any] = {}
    for name, value in values.items():
        if name in _BOOKKEEPING or name not in columns:
            continue
        if isinstance(columns[name].type, DateTime) and isinstance(value, str):
            value = _moment(value)
        kwargs[name] = value
    kwargs.update(overrides)
    return model(**kwargs)


def _primary_key(model: Any) -> list[str]:
    return [str(column.name) for column in model.__mapper__.primary_key]


@external_io
def prepare_import(
    export_artifact_id: str,
    id_map: dict[str, str],
    *,
    project_id: str | None = None,
) -> ImportPreview:
    """Verify an export, rewrite its artifact IDs, and park it for the commit step."""
    blob, _classification = artifacts.read_bytes(export_artifact_id)
    files = artifacts.unpack_archive(blob)
    if MANIFEST_NAME not in files:
        raise ImportConflict(
            f"{export_artifact_id} is not a Blueprint export: {MANIFEST_NAME} is missing",
            details={"artifactId": export_artifact_id},
        )
    raw: dict[str, Any] = json.loads(files[MANIFEST_NAME])
    target = project_id or str(raw["projectId"])
    unmapped: set[str] = set()
    # the version records name the artifacts a version is read from, so they are
    # re-pointed just like the index rows (BP-EXP-002)
    manifest = {
        **raw,
        "versions": [_rewrite(entry, id_map, unmapped) for entry in raw.get("versions") or []],
    }
    rows = {
        name: [_rewrite(row, id_map, unmapped) for row in values]
        for name, values in sorted((manifest.get("rows") or {}).items())
    }
    versions = {str(entry["version"]): entry for entry in manifest.get("versions") or []}

    issues = _archive_problems(manifest, files, versions)
    if unmapped:
        issues.append(
            _issue(
                RULE_UNMAPPED,
                "the export names artifact IDs that id_map does not cover, so versions, "
                "build records and lineage would point at nothing",
                key=", ".join(sorted(unmapped)),
                remediation="unpack those artifacts through S0 first and pass their ID map",
            )
        )
    issues.extend(_uid_conflicts(_uids(rows.get("uidAllocation") or []), target))

    batch_id = new_id("imp")
    payload = {
        "manifest": {key: value for key, value in manifest.items() if key != "rows"},
        "rows": rows,
        "versions": {version: document for version, document in _documents(files, issues).items()},
    }
    with unit_of_work() as session:
        session.add(
            ImportBatchRow(
                id=batch_id,
                project_id=target,
                source_deployment=str(raw["projectId"]),
                export_manifest=payload["manifest"],
                id_map=dict(sorted(id_map.items())),
                payload=payload,
                status=PREPARED,
            )
        )
    return ImportPreview(
        batch_id=batch_id,
        project_id=target,
        source_project_id=str(raw["projectId"]),
        versions=sorted(payload["versions"]),
        unmapped_artifact_ids=sorted(unmapped),
        issues=sort_issues(issues),
        counts={name: len(values) for name, values in sorted(rows.items())},
    )


def _archive_problems(
    manifest: dict[str, Any], files: dict[str, bytes], records: dict[str, dict[str, Any]]
) -> list[Issue]:
    """Every way the archive can fail to be what its manifest says it is."""
    issues: list[Issue] = []
    for name, expected in sorted((manifest.get("fileHashes") or {}).items()):
        data = files.get(name)
        if data is None:
            issues.append(
                _issue(
                    RULE_INTEGRITY,
                    f"the export manifest lists {name!r}, which the archive does not contain",
                    key=name,
                )
            )
        elif content_hash(data) != expected:
            issues.append(
                _issue(
                    RULE_INTEGRITY,
                    f"{name!r} does not hash to the value the export manifest recorded",
                    key=name,
                    remediation="re-export the project; this archive is damaged",
                )
            )
    for version, entry in sorted(records.items()):
        data = files.get(f"{VERSIONS_PREFIX}{version}.json")
        if data is None:
            continue
        document = BlueprintDocument(json.loads(data))
        if document.content_hash != entry["contentHash"]:
            issues.append(
                _issue(
                    RULE_INTEGRITY,
                    f"version {version} hashes to {document.content_hash}, not to the "
                    f"{entry['contentHash']} the export recorded, so the immutable version "
                    "history would not survive the import",
                    key=version,
                )
            )
    return issues


def _documents(files: dict[str, bytes], issues: list[Issue]) -> dict[str, dict[str, Any]]:
    """The version contents, normalized the same way a promotion normalizes them."""
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(files):
        if not name.startswith(VERSIONS_PREFIX):
            continue
        version = name[len(VERSIONS_PREFIX) : -len(".json")]
        try:
            out[version] = BlueprintDocument(json.loads(files[name])).data
        except Exception as error:  # a damaged file is a BLOCKING issue, not a crash
            issues.append(
                _issue(
                    RULE_INTEGRITY,
                    f"{name!r} is not readable as a Blueprint document: {error}",
                    key=version,
                )
            )
    return out


def _uids(allocations: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row["uid"]) for row in allocations if row.get("uid")})


def _foreign_uids(
    uids: list[str], project_id: str, session: Session | None = None
) -> dict[str, str]:
    """The UIDs among ``uids`` that a different project already owns here."""
    if not uids:
        return {}

    def read(active: Session) -> dict[str, str]:
        return {
            row.uid: row.project_id
            for row in active.execute(
                select(UidAllocationRow).where(UidAllocationRow.uid.in_(uids))
            ).scalars()
            if row.project_id != project_id
        }

    if session is not None:
        return read(session)
    with unit_of_work() as own:
        return read(own)


def _uid_conflicts(uids: list[str], project_id: str) -> list[Issue]:
    """A UID this deployment already handed out can never be handed out again."""
    return [
        _issue(
            RULE_UID_TAKEN,
            f"UID {uid} is already allocated to project {owner!r} in this deployment; "
            "a UID is never reassigned (BP-REG-006)",
            key=uid,
        )
        for uid, owner in sorted(_foreign_uids(uids, project_id).items())
    ]


def commit_import(
    project_id: str,
    batch_id: str,
    *,
    idempotency_key: str,
    session: Session | None = None,
) -> ImportResult:
    """Restore a prepared batch inside the caller's transaction (BP-EXP-002)."""
    sess = session if session is not None else current_session()
    batch = sess.get(ImportBatchRow, batch_id)
    if batch is None:
        raise NotFoundError(
            f"unknown import batch {batch_id}", code="NOT_FOUND", details={"batchId": batch_id}
        )
    if batch.project_id != project_id:
        raise ImportConflict(
            f"import batch {batch_id} was prepared for project {batch.project_id!r}",
            details={"batchId": batch_id, "projectId": project_id},
        )
    payload: dict[str, Any] = batch.payload
    digest = structure_hash(
        {"batchId": batch_id, "projectId": project_id, "versions": sorted(payload["versions"])}
    )
    begun = idempotency.begin(IdemScope.IMPORT_COMMIT, idempotency_key, digest, session=sess)
    if begun.already_completed and begun.first_result is not None:
        # a retry of the same request gets the same answer; that is why this check
        # comes before the status guard below
        return ImportResult.model_validate(begun.first_result)
    if batch.status != PREPARED:
        raise OperationInProgress(
            f"import batch {batch_id} is already {batch.status}", details={"batchId": batch_id}
        )

    versions = _restore_versions(sess, project_id, payload)
    counts = _restore_rows(sess, project_id, payload)
    for version, document in sorted(payload["versions"].items()):
        rebuild_indexes(sess, project_id=project_id, blueprint_version=version, document=document)
    batch.status = COMMITTED
    sess.flush()
    pending = len(
        sess.execute(
            select(EnvironmentBindingRow).where(
                EnvironmentBindingRow.project_id == project_id,
                EnvironmentBindingRow.pending_reparse.is_(True),
            )
        ).scalars().all()
    )
    result = ImportResult(
        project_id=project_id,
        batch_id=batch_id,
        versions=versions,
        current_version=_current_version(sess, project_id),
        counts=counts,
        pending_reparse=pending,
    )
    record(
        "bp.project.imported",
        object_type="importBatch",
        object_id=batch_id,
        project_id=project_id,
        after={
            "sourceDeployment": batch.source_deployment,
            "versions": versions,
            "counts": counts,
            "pendingReparse": pending,
        },
        session=sess,
    )
    idempotency.complete(
        IdemScope.IMPORT_COMMIT, idempotency_key, result.model_dump(mode="json"), session=sess
    )
    return result


def _restore_versions(session: Session, project_id: str, payload: dict[str, Any]) -> list[str]:
    entries = sorted(
        payload["manifest"].get("versions") or [], key=lambda item: str(item["version"])
    )
    _check_target_is_compatible(
        session, project_id, entries, str(payload["manifest"].get("currentVersion") or "")
    )
    restored: list[str] = []
    for entry in entries:
        version = str(entry["version"])
        existing = session.execute(
            select(BlueprintVersionRow).where(
                BlueprintVersionRow.project_id == project_id,
                BlueprintVersionRow.version == version,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.content_hash != entry["contentHash"]:
                raise ImportConflict(
                    f"version {version} already exists in this deployment with different "
                    "content; a project's version history is immutable (BP-VER-001)",
                    details={"version": version, "projectId": project_id},
                )
            restored.append(version)
            continue
        row = BlueprintVersionRow(
            id=new_id("bpv"),
            project_id=project_id,
            version=version,
            parent_version=entry.get("parentVersion"),
            schema_version=int(entry["schemaVersion"]),
            change_level=str(entry["changeLevel"]),
            content_hash=str(entry["contentHash"]),
            artifact_id=str(entry["artifactId"]),
            status=str(entry.get("status") or VersionStatus.APPROVED.value),
            source_artifact_ids=list(entry.get("sourceArtifactIds") or []),
            decision_ids=list(entry.get("decisionIds") or []),
            promoted_from_stage=entry.get("promotedFromStage"),
            promoted_from_draft_id=entry.get("promotedFromDraftId"),
            created_by=str(entry.get("createdBy") or "import"),
        )
        session.add(row)
        session.flush()
        document = payload["versions"].get(version)
        if isinstance(document, dict):
            apply_version_semantics(row, document)
        restored.append(version)
    session.flush()
    return restored


def _check_target_is_compatible(
    session: Session, project_id: str, entries: list[dict[str, Any]], incoming_current: str
) -> None:
    """A project holds exactly one promotable version, so an import may not leave two."""
    existing = session.execute(
        select(BlueprintVersionRow).where(
            BlueprintVersionRow.project_id == project_id,
            BlueprintVersionRow.status == VersionStatus.CURRENT.value,
        )
    ).scalar_one_or_none()
    if existing is None or not incoming_current or existing.version == incoming_current:
        return
    if existing.version in {str(entry["version"]) for entry in entries}:
        return
    raise ImportConflict(
        f"project {project_id} is at version {existing.version}, which this export does not "
        "contain; importing would leave two promotable versions, and a draft could "
        "no longer name its base (BP-DRF-001)",
        details={
            "projectId": project_id,
            "existingVersion": existing.version,
            "exportCurrentVersion": incoming_current,
        },
    )


def _restore_rows(session: Session, project_id: str, payload: dict[str, Any]) -> dict[str, int]:
    allocations = payload["rows"].get("uidAllocation") or []
    foreign = _foreign_uids(_uids(allocations), project_id, session)
    if foreign:
        uid = sorted(foreign)[0]
        raise ImportConflict(
            f"UID {uid} is already allocated to project {foreign[uid]!r} in this deployment; "
            "the UID ledger is global and a UID is never reassigned (BP-REG-005)",
            details={"uid": uid, "owner": foreign[uid]},
        )
    counts = {
        "uidAllocation": _restore_uid_allocations(session, project_id, allocations),
        "uidPool": _restore_by_key(session, project_id, payload, "uidPool", UidPoolRow),
        "environmentBinding": _restore_bindings(session, project_id, payload),
        "buildRecord": _restore_by_key(session, project_id, payload, "buildRecord", BuildRecordRow),
        "validationRun": _restore_validation_runs(session, payload),
    }
    session.flush()
    return {key: value for key, value in sorted(counts.items()) if value}


def _restore_uid_allocations(
    session: Session, project_id: str, rows: list[dict[str, Any]]
) -> int:
    """Restore the ledger: a UID this project already holds comes back as it was."""
    touched = 0
    for values in rows:
        existing = session.get(UidAllocationRow, str(values["uid"]))
        if existing is None:
            session.add(_construct(UidAllocationRow, values, project_id=project_id))
        else:
            # deleting a project's data keeps its ledger rows as RETIRED; restoring the
            # project restores their status, while the identity stays untouched
            existing.status = str(values.get("status") or existing.status)
            existing.retired_at = _moment(values.get("retired_at"))
        touched += 1
    return touched


def _restore_by_key(
    session: Session, project_id: str, payload: dict[str, Any], name: str, model: Any
) -> int:
    key = _primary_key(model)
    added = 0
    for values in payload["rows"].get(name) or []:
        if session.get(model, tuple(values[column] for column in key)) is not None:
            continue
        session.add(_construct(model, values, project_id=project_id))
        added += 1
    return added


def _restore_validation_runs(session: Session, payload: dict[str, Any]) -> int:
    """A validation run belongs to a build, so only runs of restored builds land."""
    builds = {str(row["id"]) for row in payload["rows"].get("buildRecord") or []}
    added = 0
    for values in payload["rows"].get("validationRun") or []:
        if str(values.get("build_id")) not in builds:
            continue
        if session.get(ValidationRunRow, str(values["id"])) is not None:
            continue
        session.add(_construct(ValidationRunRow, values))
        added += 1
    return added


def _restore_bindings(session: Session, project_id: str, payload: dict[str, Any]) -> int:
    key = _primary_key(EnvironmentBindingRow)
    known = {
        tuple(getattr(row, column) for column in key): row
        for row in session.execute(
            select(EnvironmentBindingRow).where(EnvironmentBindingRow.project_id == project_id)
        ).scalars()
    }
    added = 0
    for values in payload["rows"].get("environmentBinding") or []:
        row = _construct(
            EnvironmentBindingRow,
            values,
            project_id=project_id,
            # nothing resolved over there is trustworthy until the objects have been
            # observed here (BP-EXP-002)
            resolution=str(values.get("resolution") or BindingResolution.AUTO.value),
            pending_reparse=True,
        )
        handle = known.get(tuple(getattr(row, column) for column in key))
        if handle is None:
            session.add(row)
        else:
            for column in (
                "uid",
                "observed_hash",
                "observed_last_updated",
                "discovery_result_id",
                "resolution",
                "resolved_by",
                "note",
                "pending_reparse",
            ):
                setattr(handle, column, getattr(row, column))
        added += 1
    return added


def _current_version(session: Session, project_id: str) -> str | None:
    row = session.execute(
        select(BlueprintVersionRow).where(
            BlueprintVersionRow.project_id == project_id,
            BlueprintVersionRow.status == VersionStatus.CURRENT.value,
        )
    ).scalar_one_or_none()
    return row.version if row is not None else None
