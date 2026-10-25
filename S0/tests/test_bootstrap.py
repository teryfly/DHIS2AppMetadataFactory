"""Bootstrap & migration runner tests (FD-DB-007, FD-ASM-01)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from factory_foundation.api import (
    BUILTIN_ERROR_CODES,
    CORE_MIGRATION_CONFIG,
    INITIAL_ARTIFACT_CATALOG,
    current_revision,
    initialize_foundation,
    list_artifact_types,
    list_audit_event_types,
    run_migrations,
)
from factory_foundation.db.engine import get_engine
from factory_foundation.db.migrations import SCHEMA_ORDER


@pytest.fixture
def db(foundation):
    return foundation


def test_migration_creates_core_tables(db):
    tables = set(inspect(get_engine()).get_table_names("core"))
    assert {
        "artifact",
        "artifact_type",
        "audit_event",
        "audit_chain_state",
        "idempotency_record",
    } <= tables


def test_current_revision_matches(db):
    assert current_revision(CORE_MIGRATION_CONFIG) == "0001_core_initial"


def test_initialize_foundation_loads_catalog(db):
    # the harness already ran initialize_foundation; verify contents
    types = {t for (t, _owners, _max) in list_artifact_types()}
    assert set(INITIAL_ARTIFACT_CATALOG) <= types
    # DIFF_REPORT merged owners S2 + S4
    diff_entry = next(e for e in list_artifact_types() if e[0] == "DIFF_REPORT")
    assert set(diff_entry[1]) == {"S2", "S4"}


def test_initialize_foundation_registers_builtin_audit_types(db):
    events = {t for (t, _o) in list_audit_event_types()}
    assert "core.artifact.created" in events
    assert "core.audit.chain_verified" in events


def test_initialize_foundation_is_idempotent(db):
    before = len(list_artifact_types())
    initialize_foundation()
    initialize_foundation()
    assert len(list_artifact_types()) == before


def _error_code_rows() -> dict[str, tuple[str, bool]]:
    with get_engine().connect() as conn:
        return {
            code: (owner, bool(retryable))
            for code, owner, retryable in conn.execute(
                text("SELECT code, owner_subsystem, retryable FROM core.error_code")
            )
        }


def test_initialize_foundation_persists_fd_com_001a_codes(db):
    """FD-COM-001a: S0's built-in codes land in ``core.error_code`` at start-up."""
    rows = _error_code_rows()
    missing = sorted(code for code in BUILTIN_ERROR_CODES if code not in rows)
    assert not missing, f"not persisted into core.error_code: {missing}"
    for code, retryable in BUILTIN_ERROR_CODES.items():
        assert rows[code] == ("S0", retryable), code


def test_initialize_foundation_retries_only_the_two_marked_codes(db):
    rows = _error_code_rows()
    retryable = sorted(code for code, (_o, r) in rows.items() if r and code in BUILTIN_ERROR_CODES)
    assert retryable == ["ARTIFACT_READBACK_MISMATCH", "CONCURRENCY_ERROR"]


def test_initialize_foundation_repairs_a_stale_row(db):
    """Re-running start-up overwrites a row another version wrote (e.g. S2's temporary
    ``ARTIFACT_TYPE_MISMATCH``), so the catalog converges on S0's ownership."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE core.error_code SET owner_subsystem = 'S2', retryable = FALSE "
                "WHERE code = 'ARTIFACT_TYPE_MISMATCH'"
            )
        )
    assert _error_code_rows()["ARTIFACT_TYPE_MISMATCH"] == ("S2", False)
    initialize_foundation()
    assert _error_code_rows()["ARTIFACT_TYPE_MISMATCH"] == ("S0", False)


def test_harness_stands_up_every_subsystem_schema(db):
    """FD-TST-001: the fixture establishes all schemas, not only ``core``."""
    schemas = set(inspect(get_engine()).get_schema_names())
    assert {"core", "cp", "bp", "ai", "dx"} <= schemas


def test_testing_package_exports_the_fixed_clock():
    """FD-TST-001: the fixed clock is part of the delivered test doubles."""
    from factory_foundation.testing import FixedClock

    assert FixedClock is not None


def test_schema_order_canonical():
    assert SCHEMA_ORDER == ["core", "dx", "bp", "ai", "cp"]


def test_clearing_forward_only_without_a_target_is_a_plain_forward_run(db):
    """FD-DB-007: the flag no longer raises on being cleared.

    It used to refuse ``forward_only=False`` unconditionally, so the flag decided nothing and a
    caller who read this module's docstring ("a downgrade is permitted when explicitly
    requested") had no way to ask for one.  Without a target a run is simply forward, and the
    schema is left at head.
    """
    run_migrations([CORE_MIGRATION_CONFIG], forward_only=False)
    assert current_revision(CORE_MIGRATION_CONFIG) == "0001_core_initial"
    # the tables are really there, not just a revision stamp
    assert "artifact" in inspect(get_engine()).get_table_names(schema="core")


def test_production_refuses_a_downgrade_even_with_a_target(db, monkeypatch):
    """FD-DB-007 / FR-DATA-003: production is forward-only, whatever the caller asks."""
    from factory_foundation.common.errors import MigrationError

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(MigrationError) as caught:
        run_migrations(
            [CORE_MIGRATION_CONFIG], forward_only=False, downgrade_to="0001_core_initial"
        )
    assert "production" in caught.value.message.lower()


def test_a_forward_only_run_never_downgrades(db, monkeypatch):
    from factory_foundation.common.errors import MigrationError

    monkeypatch.setenv("APP_ENV", "development")
    with pytest.raises(MigrationError) as caught:
        run_migrations([CORE_MIGRATION_CONFIG], forward_only=True, downgrade_to="base")
    assert "forward-only" in caught.value.message or "forward_only" in caught.value.message


def test_a_development_downgrade_to_base_actually_runs(db, monkeypatch):
    """FD-DB-007: outside production an explicit request is honoured, not refused.

    This is the half the old flag never implemented -- it raised on request and did nothing
    otherwise, so there was no path through the runner that could take a schema back down.
    """
    run_migrations([CORE_MIGRATION_CONFIG], forward_only=False, downgrade_to="base")
    assert current_revision(CORE_MIGRATION_CONFIG) is None
    # and it comes back up again on the next forward run
    run_migrations([CORE_MIGRATION_CONFIG])
    assert current_revision(CORE_MIGRATION_CONFIG) == "0001_core_initial"
