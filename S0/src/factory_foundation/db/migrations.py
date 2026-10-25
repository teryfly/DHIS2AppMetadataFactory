"""Migration runner (FD-DB-007).

Each subsystem keeps Alembic migrations and an independent version table for its
schema.  The runner executes them in the fixed order
``core → dx → bp → ai → cp``.  Production runs forward-only; a downgrade is only
permitted when explicitly requested (development).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from factory_foundation.common.config import load_settings
from factory_foundation.common.errors import MigrationError
from factory_foundation.db.engine import get_database

# Canonical execution order (00 doc 6.10, FR-DATA-003).
SCHEMA_ORDER: list[str] = ["core", "dx", "bp", "ai", "cp"]

_CORE_SCRIPT_LOCATION = str(Path(__file__).resolve().parent.parent / "migrations")


@dataclass(frozen=True)
class MigrationConfig:
    schema: str
    script_location: str
    version_table: str = "alembic_version"

    def __post_init__(self) -> None:
        if self.schema not in SCHEMA_ORDER:
            raise MigrationError(f"unknown migration schema: {self.schema}")


CORE_MIGRATION_CONFIG = MigrationConfig(
    schema="core",
    script_location=_CORE_SCRIPT_LOCATION,
    version_table="core_alembic_version",
)


def _build_alembic_config(cfg: MigrationConfig, url: str) -> Config:
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", cfg.script_location)
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    alembic_cfg.set_main_option("target_schema", cfg.schema)
    alembic_cfg.set_main_option("version_table", cfg.version_table)
    return alembic_cfg


def run_migrations(
    configs: Sequence[MigrationConfig] | None = None,
    *,
    url: str | None = None,
    forward_only: bool = True,
    downgrade_to: str | None = None,
) -> None:
    """Run each schema's migrations, in canonical order.

    FD-DB-007 says production is forward-only, and this module's own rule is that a downgrade
    happens only when somebody asks for one.  Asking means naming a target: the previous shape
    had a ``forward_only`` flag that raised whenever it was cleared and did nothing otherwise,
    so the flag decided nothing -- a caller who trusted it got an error where the docstring
    promised a development downgrade.  Both halves of the real rule are enforced here: an
    explicit target, and a non-production environment.
    """
    if configs is None:
        configs = [CORE_MIGRATION_CONFIG]
    connection_url = url if url is not None else get_database().url

    ordered = sorted(configs, key=lambda c: SCHEMA_ORDER.index(c.schema))
    if downgrade_to is None:
        for cfg in ordered:
            alembic_cfg = _build_alembic_config(cfg, connection_url)
            try:
                command.upgrade(alembic_cfg, "head")
            except Exception as exc:  # noqa: BLE001
                raise MigrationError(f"migration failed for schema {cfg.schema}: {exc}") from exc
        return

    if forward_only:
        raise MigrationError(
            f"cannot downgrade to {downgrade_to!r} while forward_only is set: a forward-only "
            "run has no downgrade step (FD-DB-007)",
            details={"downgradeTo": downgrade_to, "forwardOnly": True},
        )
    settings = load_settings()
    if settings.is_production:
        raise MigrationError(
            f"refusing to downgrade to {downgrade_to!r} in production: production migrations "
            "are forward-only (FD-DB-007, FR-DATA-003)",
            details={"downgradeTo": downgrade_to, "appEnv": settings.app_env},
        )
    for cfg in reversed(ordered):
        alembic_cfg = _build_alembic_config(cfg, connection_url)
        try:
            command.downgrade(alembic_cfg, downgrade_to)
        except Exception as exc:  # noqa: BLE001
            raise MigrationError(f"downgrade failed for schema {cfg.schema}: {exc}") from exc


def current_revision(cfg: MigrationConfig, *, url: str | None = None) -> str | None:
    """Return the schema's current Alembic revision (for diagnostics)."""
    connection_url = url if url is not None else get_database().url
    engine = create_engine(connection_url)
    try:
        with engine.connect() as conn:
            mc = MigrationContext.configure(
                connection=conn,
                opts={
                    "version_table": cfg.version_table,
                    "version_table_schema": cfg.schema,
                },
            )
            return mc.get_current_revision()
    finally:
        engine.dispose()
