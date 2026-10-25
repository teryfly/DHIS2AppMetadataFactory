"""P0: the ``bp`` schema, the catalog registration and the identity guards."""

from __future__ import annotations

import pytest
from factory_foundation.api import (
    check_no_cross_schema_fk,
    current_revision,
    error_catalog,
    list_artifact_types,
    list_audit_event_types,
    unit_of_work,
)
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError

from factory_blueprint.bootstrap import MIGRATION_CONFIG, initialize
from factory_blueprint.registries import (
    BP_ARTIFACT_TYPES,
    BP_AUDIT_EVENT_TYPES,
    BP_ERROR_CODES,
    S0_PENDING_ERROR_CODES,
)
from factory_blueprint.testing.harness import BlueprintHarness


def _expected_head() -> str:
    """The tip of ``bp``'s revision chain: the one revision nothing else builds on.

    Derived from the scripts rather than written as a literal, so adding a migration
    cannot leave this test asserting that the schema stopped one revision early.
    """
    import importlib.util
    from pathlib import Path

    versions = Path(MIGRATION_CONFIG.script_location) / "versions"
    revisions: dict[str, str | None] = {}
    for path in sorted(versions.glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        revisions[str(module.revision)] = getattr(module, "down_revision", None)
    parents = {parent for parent in revisions.values() if parent}
    heads = [revision for revision in revisions if revision not in parents]
    assert len(heads) == 1, f"bp migrations must form one chain, found heads {heads}"
    return heads[0]


def test_bp_schema_is_migrated_to_head(harness: BlueprintHarness) -> None:
    assert current_revision(MIGRATION_CONFIG, url=harness.url) == _expected_head()


def test_no_cross_schema_foreign_keys(harness: BlueprintHarness) -> None:
    # FD-DB-006 / D-08: raises when any registered table points outside its schema
    assert check_no_cross_schema_fk() is None


def test_all_bp_tables_exist(harness: BlueprintHarness) -> None:
    with unit_of_work() as session:
        rows = session.execute(
            text(
                "select table_name from information_schema.tables "
                "where table_schema = 'bp' and table_name <> 'bp_alembic_version' "
                "order by table_name"
            )
        ).scalars().all()
    assert set(rows) == {
        "blueprint_version",
        "blueprint_draft",
        "blueprint_patch",
        "registry_entry",
        "environment_binding",
        "uid_allocation",
        "uid_pool",
        "decision",
        "business_key_index",
        "dependency_edge",
        "prepared_content",
        "build_record",
        "validation_run",
        "import_batch",
    }


def test_s2_registers_its_artifact_types(harness: BlueprintHarness) -> None:
    catalog = {type_name: set(owners) for type_name, owners, _size in list_artifact_types()}
    for type_name, owners in BP_ARTIFACT_TYPES.items():
        assert type_name in catalog, type_name
        assert catalog[type_name] >= set(owners)
    # DIFF_REPORT is shared with S4 (FD-ART-002)
    assert catalog["DIFF_REPORT"] == {"S2", "S4"}


def test_s2_registers_every_audit_event_type(harness: BlueprintHarness) -> None:
    registered = {event_type for event_type, _owner in list_audit_event_types()}
    for event_type in BP_AUDIT_EVENT_TYPES:
        assert event_type in registered, event_type


#: the code BP-HO-004 raises for a package read handed another type, and FD-COM-001a's to own
SHARED_CODE = "ARTIFACT_TYPE_MISMATCH"


def test_s2_raises_the_shared_code_without_owning_it(harness: BlueprintHarness) -> None:
    """BP-HO-004: S2 must not register this code; the catalog row is S0's to declare."""
    assert SHARED_CODE not in BP_ERROR_CODES, "S2 claims ownership of an S0 code"
    assert S0_PENDING_ERROR_CODES[SHARED_CODE][0] == "S0", "the gap filler does not restamp it"
    meta = error_catalog.get(SHARED_CODE)
    assert meta is not None, "raising an unregistered code leaves retryability undefined"
    assert meta.owner_subsystem == "S0" and meta.retryable is False


def test_initialize_never_restamps_a_code_s0_declared(harness: BlueprintHarness) -> None:
    """An S0 that has FD-COM-001a registers the code first; S2 fills a gap, not a row.

    The distinctive ``retryable`` is the probe: were S2 to register the code again, its own
    non-retryable value would replace what S0 said.
    """
    try:
        error_catalog.register(SHARED_CODE, owner_subsystem="S0", retryable=True)
        initialize()
        declared = error_catalog.get(SHARED_CODE)
        assert (declared.owner_subsystem, declared.retryable) == ("S0", True), (
            "S2 overwrote an owner and retryability it does not hold"
        )
    finally:
        error_catalog.register(SHARED_CODE, owner_subsystem="S0", retryable=False)


def _insert_uid(session, uid: str, *, key: str = "a.b", project: str = "p1") -> None:
    session.execute(
        text(
            "insert into bp.uid_allocation "
            "(uid, project_id, business_key, dhis2_type, source_environment, status, allocated_at) "
            "values (:uid, :project, :key, 'dataElement', 'dev', 'ACTIVE', now())"
        ),
        {"uid": uid, "key": key, "project": project},
    )


def test_uid_column_rejects_wrong_length(harness: BlueprintHarness) -> None:
    """BP-REG-007: a UID is exactly 11 characters."""
    with pytest.raises(DataError), unit_of_work() as session:
        _insert_uid(session, "way-too-long-uid")


def test_uid_ledger_row_cannot_be_deleted(harness: BlueprintHarness) -> None:
    """BP-REG-006 / BP-EXP-003: an allocated UID is retained forever to block reuse."""
    with pytest.raises(ProgrammingError), unit_of_work() as session:
        _insert_uid(session, "uiddelete01")
        session.flush()
        session.execute(text("delete from bp.uid_allocation where uid = 'uiddelete01'"))


def test_uid_ledger_identity_is_immutable_but_status_is_not(harness: BlueprintHarness) -> None:
    with pytest.raises(ProgrammingError), unit_of_work() as session:
        _insert_uid(session, "uidmove0001")
        session.flush()
        session.execute(
            text("update bp.uid_allocation set business_key = 'other' where uid = 'uidmove0001'")
        )

    with unit_of_work() as session:
        _insert_uid(session, "uidretire01")
        session.flush()
        session.execute(
            text("update bp.uid_allocation set status = 'RETIRED' where uid = 'uidretire01'")
        )
        status = session.execute(
            text("select status from bp.uid_allocation where uid = 'uidretire01'")
        ).scalar_one()
    assert status == "RETIRED"


def test_uid_cannot_be_allocated_twice(harness: BlueprintHarness) -> None:
    """BP-REG-005: one UID belongs to exactly one business concept in the Factory."""
    with pytest.raises(IntegrityError), unit_of_work() as session:
        _insert_uid(session, "twin0000001", key="a.one")
        _insert_uid(session, "twin0000001", key="a.two")


def test_only_one_current_version_per_project(harness: BlueprintHarness) -> None:
    def insert_version(session, version: str, status: str = "CURRENT") -> None:
        session.execute(
            text(
                "insert into bp.blueprint_version "
                "(id, project_id, version, schema_version, change_level, content_hash, "
                " artifact_id, status, source_artifact_ids, decision_ids, created_by) "
                "values (:id, :project, :version, 1, 'Cosmetic', 'sha256:' || lpad('a', 64, '0'), "
                " 'art-x', :status, '[]'::jsonb, '[]'::jsonb, 'tester')"
            ),
            {"id": f"bpv-{version}", "project": "p1", "version": version, "status": status},
        )

    with pytest.raises(IntegrityError), unit_of_work() as session:
        insert_version(session, "0.1.0")
        insert_version(session, "0.2.0")


def test_only_one_active_draft_per_project(harness: BlueprintHarness) -> None:
    def insert_draft(session, draft_id: str, status: str = "OPEN") -> None:
        session.execute(
            text(
                "insert into bp.blueprint_draft "
                "(id, project_id, base_version, stage_scope, stage, status, content, base_content, "
                "content_hash, created_by) "
                "values (:id, 'p1', '0.1.0', '[]'::jsonb, 'MAPPING', :status, '{}'::jsonb, "
                "'{}'::jsonb, 'sha256:' || lpad('b', 64, '0'), 'tester')"
            ),
            {"id": draft_id, "status": status},
        )

    with pytest.raises(IntegrityError), unit_of_work() as session:
        insert_draft(session, "dft-1")
        insert_draft(session, "dft-2")

    # a promoted or reset draft frees the slot
    with unit_of_work() as session:
        insert_draft(session, "dft-3", status="PROMOTED")
        insert_draft(session, "dft-4", status="OPEN")
