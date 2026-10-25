"""Process assembly (CP-RT-001 … CP-RT-005, CP-API-008, CP-IAM-001).

These run against a scratch database, because booting is exactly what the other
tests bypass: migrations, provider detection, catalog registration and the two
process entry points.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from factory_control.config import Settings
from factory_control.db import CP_MIGRATION_CONFIG, MIGRATIONS_DIR
from factory_control.foundation import MigrationConfig
from factory_control.testing.harness import resolve_url

pytestmark = pytest.mark.api

BOOT_DATABASE = "factory_s1_boot"


def _with_database(url: str, database: str) -> str:
    """Swap the database name, keeping the rest of the URL byte-identical.

    ``make_url(...).set(database=...)`` percent-encodes a Unix-socket host, and the
    migration runner reads the URL through configparser, which rejects ``%``.
    """
    head, sep, tail = url.partition("?")
    prefix, _, _current = head.rpartition("/")
    return f"{prefix}/{database}{sep}{tail}"


@pytest.fixture(scope="module")
def boot_url() -> Iterator[str]:
    """A database that no test has ever migrated, so apply really applies."""
    base = resolve_url()
    url = _with_database(base, BOOT_DATABASE)
    maintenance = _with_database(base, "postgres")

    def _admin() -> Any:
        return sqlalchemy.create_engine(maintenance, isolation_level="AUTOCOMMIT")

    with _admin().connect() as conn:
        conn.execute(sqlalchemy.text(f'DROP DATABASE IF EXISTS "{BOOT_DATABASE}"'))
        conn.execute(sqlalchemy.text(f'CREATE DATABASE "{BOOT_DATABASE}"'))
    yield url
    with _admin().connect() as conn:
        conn.execute(
            sqlalchemy.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :db"),
            {"db": BOOT_DATABASE},
        )
        conn.execute(sqlalchemy.text(f'DROP DATABASE IF EXISTS "{BOOT_DATABASE}"'))


@pytest.fixture(autouse=True)
def _clean_wiring() -> Iterator[None]:
    from factory_control import api_service

    api_service.reset()
    yield
    api_service.reset()


# --- start-up order ---------------------------------------------------------


def test_bootstrap_applies_migrations_and_registers_s1(boot_url: str) -> None:
    from factory_control.bootstrap import bootstrap
    from factory_control.registrations import (
        S1_ARTIFACT_TYPES,
        S1_AUDIT_EVENT_TYPES,
        S1_ID_PREFIXES,
        s1_error_codes,
    )

    report = bootstrap(database_url=boot_url)

    assert report.migrations_mode == "apply"
    # the migration order is core first and cp last (00 doc 6.10)
    assert report.applied_schemas[0] == "core"
    assert report.applied_schemas[-1] == "cp"
    assert report.registered == {
        "artifactTypes": len(S1_ARTIFACT_TYPES),
        "auditEventTypes": len(S1_AUDIT_EVENT_TYPES),
        "errorCodes": len(s1_error_codes()),
        "idPrefixes": len(S1_ID_PREFIXES),
    }
    engine = sqlalchemy.create_engine(boot_url)
    with engine.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT to_regclass('cp.projects')")).scalar() is not None
        assert conn.execute(sqlalchemy.text("SELECT to_regclass('core.idempotency_record')")).scalar() is not None
        head = conn.execute(sqlalchemy.text("SELECT version_num FROM cp.cp_alembic_version")).scalar()
    engine.dispose()
    assert head  # S1 keeps its own version table, not core's (FD-DB-007)


def test_bootstrap_tolerates_absent_providers(boot_url: str) -> None:
    """A provider that is not installed is recorded, not fatal (CP-RT-004, CP-ASM-08).

    The assertion is about boot surviving the absence, not about which providers are absent:
    on this branch all three subsystems happen to be installed, but a machine without them
    (or a subsystem that has not landed yet) must boot the same way — and a run that pinned
    ``providers_missing`` non-empty would fail purely because a subsystem landed.
    """
    from factory_control.bootstrap import PROVIDER_PACKAGES, bootstrap

    report = bootstrap(database_url=boot_url)
    known = {label for _package, label in PROVIDER_PACKAGES}
    assert set(report.providers_missing) <= known
    assert set(report.providers_initialized) <= known
    assert not set(report.providers_missing) & set(report.providers_initialized)
    assert "cp" in report.applied_schemas


def test_migrations_check_and_off_modes(boot_url: str) -> None:
    from factory_control.bootstrap import MIGRATIONS_CHECK, MIGRATIONS_OFF, bootstrap

    checked = bootstrap(database_url=boot_url, migrations_mode=MIGRATIONS_CHECK)
    assert checked.applied_schemas == []
    assert "cp" in checked.skipped_schemas

    off = bootstrap(database_url=boot_url, migrations_mode=MIGRATIONS_OFF)
    assert off.applied_schemas == []
    assert {"core", "cp"} <= set(off.skipped_schemas)


def test_check_mode_reports_schema_drift(boot_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from factory_control import bootstrap as bs

    monkeypatch.setattr(bs, "current_revision", lambda cfg, url=None: "0000")
    with pytest.raises(RuntimeError, match="expected"):
        bs.bootstrap(database_url=boot_url, migrations_mode=bs.MIGRATIONS_CHECK)


def test_installed_provider_is_migrated_and_initialized(
    boot_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider exposing ``MIGRATION_CONFIG``/``initialize()`` joins the sequence."""
    from factory_control.bootstrap import bootstrap

    calls: list[str] = []
    module = types.ModuleType("factory_blueprint.api")
    module.MIGRATION_CONFIG = MigrationConfig(  # type: ignore[attr-defined]
        schema="bp", script_location=str(MIGRATIONS_DIR), version_table="bp_alembic_version"
    )
    module.initialize = lambda: calls.append("initialize")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "factory_blueprint", types.ModuleType("factory_blueprint"))
    monkeypatch.setitem(sys.modules, "factory_blueprint.api", module)

    report = bootstrap(database_url=boot_url, migrations_mode="off")
    assert "S2" not in report.providers_missing
    assert "S2" in report.providers_initialized
    assert "bp" in report.skipped_schemas
    assert calls == ["initialize"]


def test_initialize_is_idempotent(boot_url: str) -> None:
    from factory_control.bootstrap import bootstrap, initialize

    bootstrap(database_url=boot_url)
    first = initialize()
    second = initialize()
    assert first == second
    assert first["auditEventTypes"] >= 40 and first["idPrefixes"] >= 10


def test_foundation_contract_is_verified_at_boot(
    boot_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S0 is developed concurrently: a missing symbol must surface at boot."""
    import factory_control.foundation as foundation
    from factory_control.bootstrap import bootstrap

    bootstrap(database_url=boot_url)  # runs verify_foundation_contract() first

    monkeypatch.setattr(
        foundation,
        "FOUNDATION_CONTRACT",
        tuple(foundation.FOUNDATION_CONTRACT) + ("definitely_not_exported_by_s0",),
    )
    with pytest.raises(foundation.FoundationContractError, match="missing symbols"):
        foundation.verify_foundation_contract()


# --- the two processes ------------------------------------------------------


def test_prepare_process_installs_the_runtime_and_serves_health(boot_url: str) -> None:
    from factory_control import api_service
    from factory_control.api import prepare_process
    from factory_control.rest.app import create_app

    settings = Settings(
        database_url=boot_url, provider_mode="fakes", workflow_engine="inprocess", app_env="test"
    )
    runtime = prepare_process(settings)

    assert api_service.get_runtime() is runtime
    assert api_service.get_engine() is runtime.engine
    assert api_service.get_settings_obj() is settings
    assert api_service.get_startup_report().applied_schemas[-1] == "cp"
    assert type(runtime.engine).__name__ == "InProcessEngine"

    with TestClient(create_app(runtime)) as client:
        health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["engine"]["name"] == "inprocess"
    assert health["engine"]["durable"] is False
    assert set(health["providers"]) == {"S2", "S3", "S4"}
    assert set(health["providers"].values()) == {"fake"}  # provider_mode=fakes
    assert health["startup"]["registered"]["artifactTypes"] > 0


def test_prepare_process_refuses_a_broken_configuration(boot_url: str) -> None:
    from factory_control.api import prepare_process

    with pytest.raises(RuntimeError, match="invalid configuration"):
        prepare_process(Settings(database_url="", provider_mode="nonsense"))


def test_worker_main_runs_every_scheduled_job_once(
    boot_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from factory_control.api import worker_main
    from factory_control.config import reset_settings_cache

    monkeypatch.setenv("DATABASE_URL", boot_url)
    monkeypatch.setenv("FACTORY_PROVIDER_MODE", "fakes")
    monkeypatch.setenv("FACTORY_WORKFLOW_ENGINE", "inprocess")
    reset_settings_cache()
    try:
        assert worker_main(["--once"]) == 0
        printed = capsys.readouterr().out
    finally:
        reset_settings_cache()
    assert "housekeeping" in printed and "gate_reminders" in printed
    assert "error" not in printed  # every job ran, none blew up


def test_api_and_worker_entry_points_are_exposed() -> None:
    from factory_control.api import _parser, api_main, create_app, worker_main

    assert callable(api_main) and callable(worker_main) and callable(create_app)
    assert _parser("factory-api").parse_args(["--port", "9000"]).port == 9000
    assert _parser("factory-worker").parse_args([]).port == 0


def test_migration_config_is_exported_for_the_assembly_runner() -> None:
    from factory_control.api import MIGRATION_CONFIG
    from factory_control.bootstrap import s1_migration_config

    assert MIGRATION_CONFIG is CP_MIGRATION_CONFIG
    assert s1_migration_config().schema == "cp"
    assert MIGRATION_CONFIG.version_table == "cp_alembic_version"
    assert (MIGRATIONS_DIR / "env.py").is_file()
    assert (MIGRATIONS_DIR / "versions").is_dir()


def test_settings_defaults_seed_into_the_database(boot_url: str) -> None:
    """CP-SET-001: every documented key exists after boot, with no secret values."""
    from sqlalchemy import text

    from factory_control.bootstrap import bootstrap
    from factory_control.settings import SETTING_KEYS

    bootstrap(database_url=boot_url)
    engine = sqlalchemy.create_engine(boot_url)
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT key, value FROM cp.system_settings")).all())
    engine.dispose()
    assert set(SETTING_KEYS) <= set(rows)
    assert not any("password" in str(value).lower() for value in rows.values())


def test_boot_installs_the_mvp_permission_policy(boot_url: str) -> None:
    """CP-IAM-001: the hook exists and defaults to allow-everything."""
    from factory_control.bootstrap import bootstrap
    from factory_control.iam import AllowAllPolicy, authorize, get_permission_policy

    bootstrap(database_url=boot_url)
    assert isinstance(get_permission_policy(), AllowAllPolicy)
    authorize("ada", None, "project.view")


def test_object_store_and_secrets_are_configured_at_boot(boot_url: str) -> None:
    from factory_control.bootstrap import bootstrap
    from factory_control.foundation import get_object_store, resolve_secret, store_secret

    bootstrap(database_url=boot_url, object_store_url="memory://")

    store = get_object_store()
    store.put("cp/assembly-probe", b"hello", media_type="text/plain")
    assert store.open_read("cp/assembly-probe").read_all() == b"hello"

    ref = store_secret("cp/assembly-probe-secret", "hunter2")
    secret = resolve_secret(ref)
    assert secret.reveal() == "hunter2"
    assert "hunter2" not in str(secret)  # FD-SEC-002: the string form stays masked
