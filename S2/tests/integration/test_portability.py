"""Export, import and project cleanup (BP-EXP-001…003, AC-DATA-01).

The import path is exercised the way a second deployment would be: the project is
exported, its ``bp`` data is dropped, and the export is restored under a *new* set of
artifact IDs — the IDs S0's unpack hands out over there.  Recompiling after that must
produce exactly the bytes the exporting deployment produced, which holds only if no
product ever named an input by artifact ID.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from factory_foundation.api import (
    ArtifactCreate,
    DataClassification,
    file_json,
    register_artifact,
    unit_of_work,
)
from sqlalchemy import select

from factory_blueprint import artifacts, deps
from factory_blueprint.build import service as builds
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.enums import BuildStatus, UidAllocationStatus, VersionStatus
from factory_blueprint.errors import ImportConflict, OperationInProgress
from factory_blueprint.indexes import rebuild_indexes
from factory_blueprint.models.portability import ImportBatchRow
from factory_blueprint.models.registry import RegistryEntryRow
from factory_blueprint.models.uid import UidAllocationRow
from factory_blueprint.models.version import BlueprintVersionRow
from factory_blueprint.portability import cleanup, export, import_
from factory_blueprint.s4.stub import StubDhis2
from factory_blueprint.testing.harness import BlueprintHarness
from tests.integration import test_compile as scenario

PROJECT = scenario.PROJECT


@pytest.fixture(autouse=True)
def _stub(harness: BlueprintHarness) -> Iterator[Any]:
    stub = StubDhis2()
    deps.set_dhis2(stub)
    yield stub
    deps.reset_providers()


def _copy_artifact(artifact_id: str) -> str:
    """Re-register one artifact's bytes under a new ID, as an unpack in S0 would."""
    info = artifacts.read_metadata(artifact_id)
    data, _classification = artifacts.read_bytes(artifact_id)
    handle = artifacts.stage_bytes(data, PROJECT, info.type, media_type=info.media_type)
    with unit_of_work() as session:
        copy = register_artifact(
            handle,
            ArtifactCreate(
                project_id=PROJECT,
                type=info.type,
                media_type=info.media_type,
                data_classification=info.data_classification,
                # the unpack keeps the original owner: S0 checks type ownership (FD-ART-002)
                creator_subsystem=info.creator_subsystem,
            ),
            session=session,
        )
    return copy.id


def _id_map_for(exported: Any) -> dict[str, str]:
    return {item: _copy_artifact(item) for item in exported.referenced_artifact_ids}


def _package_hashes(version: str, key: str) -> dict[str, str]:
    """Compile for TEST_ENV and return the package's per-file hashes."""
    result = builds.compile_for_environment(
        PROJECT,
        version,
        scenario.TEST_ENV,
        scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(),
        None,
        scenario.context(),
        idempotency_key=key,
        design_reference_environment=scenario.DESIGN_ENV,
    )
    assert result.status is BuildStatus.COMPLETED, result.binding_issues
    blob, _ = artifacts.read_bytes(result.artifact_ids["METADATA_PACKAGE"])
    return artifacts.archive_hashes(blob)


def _registry_keys(version: str, project_id: str = PROJECT) -> set[str]:
    with unit_of_work() as session:
        return {
            row.business_key
            for row in session.execute(
                select(RegistryEntryRow).where(
                    RegistryEntryRow.project_id == project_id,
                    RegistryEntryRow.blueprint_version == version,
                )
            ).scalars()
        }


def _ledger(project_id: str = PROJECT) -> list[UidAllocationRow]:
    with unit_of_work() as session:
        return list(
            session.execute(
                select(UidAllocationRow).where(UidAllocationRow.project_id == project_id)
            ).scalars()
        )


def _version_row(version: str) -> BlueprintVersionRow:
    with unit_of_work() as session:
        return session.execute(
            select(BlueprintVersionRow).where(
                BlueprintVersionRow.project_id == PROJECT,
                BlueprintVersionRow.version == version,
            )
        ).scalar_one()


def _allocate(uid: str, project_id: str) -> None:
    with unit_of_work() as session:
        session.add(
            UidAllocationRow(
                uid=uid,
                project_id=project_id,
                business_key="taken",
                dhis2_type="program",
                source_environment=scenario.DESIGN_ENV,
                status=UidAllocationStatus.ACTIVE.value,
                allocated_at=datetime.now(UTC),
            )
        )


def _export_carrying(uid: str) -> tuple[str, dict[str, str]]:
    """A well-formed export whose ledger names one UID that another project owns.

    Rewriting only the manifest is enough: its own bytes are not covered by
    ``fileHashes``, which exists to vouch for the version contents.
    """
    exported = export.export_blueprint(PROJECT)
    id_map = _id_map_for(exported)
    blob, _ = artifacts.read_bytes(exported.artifact_id)
    files = artifacts.unpack_archive(blob)
    manifest = json.loads(files[export.MANIFEST_NAME])
    for row in manifest["rows"]["uidAllocation"]:
        row["uid"] = uid
    files[export.MANIFEST_NAME] = file_json(manifest)
    handle = artifacts.stage_files(files, PROJECT, export.EXPORT_TYPE)
    with unit_of_work() as session:
        info = artifacts.register(
            handle,
            project_id=PROJECT,
            artifact_type=export.EXPORT_TYPE,
            data_classification=DataClassification.INTERNAL,
            session=session,
        )
    return info.id, id_map


def test_export_carries_every_version_and_the_uid_ledger() -> None:
    version = scenario.approved_version()
    result = export.export_blueprint(PROJECT)
    assert version in result.versions
    blob, _ = artifacts.read_bytes(result.artifact_id)
    files = artifacts.unpack_archive(blob)
    manifest = json.loads(files[export.MANIFEST_NAME])
    assert manifest["currentVersion"] == version
    for entry in manifest["versions"]:
        document = json.loads(files[f"{export.VERSIONS_PREFIX}{entry['version']}.json"])
        assert BlueprintDocument(document).content_hash == entry["contentHash"]
    assert manifest["rows"]["uidAllocation"], "the UID ledger must travel with the project"
    assert set(result.files) == set(files)


def test_prepare_import_rewrites_artifact_ids_and_parks_the_batch() -> None:
    scenario.approved_version()
    exported = export.export_blueprint(PROJECT)
    id_map = _id_map_for(exported)
    preview = import_.prepare_import(exported.artifact_id, id_map)
    assert preview.issues == [], [issue.message for issue in preview.issues]
    assert preview.committable
    assert preview.unmapped_artifact_ids == []
    assert preview.versions == exported.versions
    with unit_of_work() as session:
        batch = session.get(ImportBatchRow, preview.batch_id)
    assert batch is not None and batch.status == import_.PREPARED
    payload: dict[str, Any] = batch.payload
    restored = {entry["artifactId"] for entry in payload["manifest"]["versions"]}
    assert restored <= set(id_map.values()), "a version must point at this deployment's artifact"


def test_commit_import_restores_versions_and_rebuilds_the_indexes() -> None:
    version = scenario.approved_version()
    registry_before = _registry_keys(version)
    exported = export.export_blueprint(PROJECT)
    id_map = _id_map_for(exported)
    with unit_of_work() as session:
        cleanup.delete_project_data(PROJECT, session=session)
    assert _registry_keys(version) == set()

    preview = import_.prepare_import(exported.artifact_id, id_map)
    with unit_of_work() as session:
        result = import_.commit_import(
            PROJECT, preview.batch_id, idempotency_key="k-import", session=session
        )
    assert result.versions == exported.versions
    assert result.current_version == version
    assert _registry_keys(version) == registry_before, "the Registry index must come back"
    row = _version_row(version)
    assert row.artifact_id in id_map.values()
    assert row.status == VersionStatus.CURRENT.value
    assert result.counts.get("uidAllocation"), "the ledger is part of the restore"


def test_recompiling_after_import_gives_the_same_bytes() -> None:
    """AC-DATA-01 (S2 part): new artifact IDs, identical products."""
    version = scenario.approved_version()
    before = _package_hashes(version, "k-before-import")
    exported = export.export_blueprint(PROJECT)
    with unit_of_work() as session:
        cleanup.delete_project_data(PROJECT, session=session)
    preview = import_.prepare_import(exported.artifact_id, _id_map_for(exported))
    with unit_of_work() as session:
        import_.commit_import(PROJECT, preview.batch_id, idempotency_key="k-import", session=session)
    assert _package_hashes(version, "k-after-import") == before


def test_import_refuses_a_uid_another_project_holds() -> None:
    """BP-REG-005: one UID, one object, in one deployment — even across projects."""
    scenario.approved_version()
    uid = "stubOther01"
    _allocate(uid, "other")
    artifact, id_map = _export_carrying(uid)
    preview = import_.prepare_import(artifact, id_map, project_id="third")
    assert preview.blocking
    assert import_.RULE_UID_TAKEN in {issue.rule_id for issue in preview.issues}
    with pytest.raises(ImportConflict) as caught, unit_of_work() as session:
        import_.commit_import(
            "third", preview.batch_id, idempotency_key="k-clash", session=session
        )
    assert uid in str(caught.value)


def test_delete_project_data_retires_uids_and_keeps_the_ledger() -> None:
    """BP-EXP-003 with BP-AC-07: the data goes, the numbers never come back."""
    scenario.approved_version()
    uids = {row.uid for row in _ledger()}
    assert uids
    with unit_of_work() as session:
        result = cleanup.delete_project_data(PROJECT, session=session)
    assert result.retired_uids == len(uids)
    assert result.retained_uids == len(uids)
    assert "blueprint_version" in result.deleted
    rows = _ledger()
    assert {row.status for row in rows} == {UidAllocationStatus.RETIRED.value}
    assert {row.project_id for row in rows} == {PROJECT}
    with unit_of_work() as session:
        assert (
            session.execute(
                select(BlueprintVersionRow).where(BlueprintVersionRow.project_id == PROJECT)
            ).scalar_one_or_none()
            is None
        )


def test_a_batch_can_only_be_committed_once() -> None:
    scenario.approved_version()
    exported = export.export_blueprint(PROJECT)
    with unit_of_work() as session:
        cleanup.delete_project_data(PROJECT, session=session)
    preview = import_.prepare_import(exported.artifact_id, {})
    with unit_of_work() as session:
        import_.commit_import(PROJECT, preview.batch_id, idempotency_key="k-first", session=session)
    with pytest.raises(OperationInProgress), unit_of_work() as session:
        import_.commit_import(
            PROJECT, preview.batch_id, idempotency_key="k-second", session=session
        )


def test_repeated_commit_with_the_same_key_returns_the_first_result() -> None:
    """Every side-effecting S2 call takes an idempotency key, so a retry is safe."""
    version = scenario.approved_version()
    exported = export.export_blueprint(PROJECT)
    with unit_of_work() as session:
        cleanup.delete_project_data(PROJECT, session=session)
    preview = import_.prepare_import(exported.artifact_id, {})
    with unit_of_work() as session:
        first = import_.commit_import(
            PROJECT, preview.batch_id, idempotency_key="k-same", session=session
        )
    with unit_of_work() as session:
        again = import_.commit_import(
            PROJECT, preview.batch_id, idempotency_key="k-same", session=session
        )
    assert again == first
    assert again.versions == exported.versions
    assert again.current_version == version


def test_rebuilding_the_indexes_does_not_change_answers() -> None:
    """BP-VER-002: ``bp`` is derived data, so a rebuild is a repair, not a rewrite."""
    version = scenario.approved_version()
    before = _registry_keys(version)
    document, _ = artifacts.read_json(_version_row(version).artifact_id)
    with unit_of_work() as session:
        rebuild_indexes(session, project_id=PROJECT, blueprint_version=version, document=document)
    assert _registry_keys(version) == before


def test_prepare_import_reports_a_damaged_archive() -> None:
    scenario.approved_version()
    exported = export.export_blueprint(PROJECT)
    id_map = _id_map_for(exported)
    blob, _ = artifacts.read_bytes(exported.artifact_id)
    files = artifacts.unpack_archive(blob)
    name = next(key for key in sorted(files) if key != export.MANIFEST_NAME)
    tampered = dict(files)
    tampered[name] = b'{\n  "domain": {}\n}\n'
    handle = artifacts.stage_files(tampered, PROJECT, export.EXPORT_TYPE)
    with unit_of_work() as session:
        info = artifacts.register(
            handle,
            project_id=PROJECT,
            artifact_type=export.EXPORT_TYPE,
            data_classification=DataClassification.INTERNAL,
            session=session,
        )
    preview = import_.prepare_import(info.id, id_map)
    assert preview.blocking
    assert {issue.rule_id for issue in preview.issues} == {import_.RULE_INTEGRITY}
