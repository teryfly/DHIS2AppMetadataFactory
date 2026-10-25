"""Database wiring for S1 (FD-DB-001 / FD-DB-007).

S1 owns schema ``cp`` and hands the foundation a :class:`MigrationConfig` for it;
the migration runner executes ``core → dx → bp → ai → cp`` in that fixed order
(CP-RT-004).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory_control.foundation import MigrationConfig, configure_engine

#: Alembic script directory holding S1's ``cp`` migrations (CP-DATA-01).
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: Exported for the assembly runner; each subsystem exports its own (00 doc 6.10).
CP_MIGRATION_CONFIG = MigrationConfig(
    schema="cp",
    script_location=str(MIGRATIONS_DIR),
    version_table="cp_alembic_version",
)


def configure_database(url: str, *, echo: bool = False) -> Any:
    """Point the shared foundation engine at ``url`` and create schema ``cp``."""
    from sqlalchemy import text

    db = configure_engine(url, echo=echo)
    with db.engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "cp"'))
    return db


def resolve_database_url(settings_database_url: str = "") -> str:
    """Prefer the explicit settings value, then the environment (FD-COM-008)."""
    import os

    url = settings_database_url or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not configured; S1 cannot start without a PostgreSQL connection"
        )
    return url


__all__ = [
    "CP_MIGRATION_CONFIG",
    "MIGRATIONS_DIR",
    "configure_database",
    "resolve_database_url",
]
