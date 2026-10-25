"""Alembic environment for the ``core`` schema (S0).

Configured so each schema keeps its own independent version table (FD-DB-007).
The runner (:mod:`factory_foundation.db.migrations`) passes ``sqlalchemy.url``,
``target_schema`` and ``version_table`` through Alembic ``Config`` attributes and
main options; this module reads them and ensures the target schema exists before
migrations run.
"""

from __future__ import annotations

from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

target_schema = config.get_main_option("target_schema") or "core"
version_table = config.get_main_option("version_table") or "alembic_version"

from factory_foundation.models.base import CoreBase  # noqa: E402

target_metadata = CoreBase.metadata


def _include_object(obj: Any, name: Any, type_: str, reflected: bool, compare_to: Any) -> bool:
    # Manage only objects belonging to the core schema.
    if type_ == "table":
        return (obj.schema or target_schema) == target_schema
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        version_table=version_table,
        version_table_schema=target_schema,
        include_object=_include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Ensure the schema exists before the version table is created in it.
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{target_schema}"')
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=version_table,
            version_table_schema=target_schema,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
