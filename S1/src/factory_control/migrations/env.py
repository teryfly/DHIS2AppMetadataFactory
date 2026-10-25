"""Alembic environment for the ``cp`` schema (S1), mirroring S0's pattern (FD-DB-007).

The foundation runner passes ``sqlalchemy.url``, ``target_schema`` and
``version_table`` through Alembic ``Config`` main options; this module reads them
and creates the target schema before anything is written into it.  Only objects
belonging to ``cp`` are managed — S1 never touches another schema (D-08).
"""

from __future__ import annotations

from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

target_schema = config.get_main_option("target_schema") or "cp"
version_table = config.get_main_option("version_table") or "cp_alembic_version"

from factory_control import models as _models  # noqa: E402,F401 (registers all cp tables)
from factory_control.foundation import CpBase  # noqa: E402

target_metadata = CpBase.metadata


def _include_object(
    obj: Any, name: str, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if type_ == "table":
        return (obj.schema or target_schema) == target_schema
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        version_table=version_table,
        version_table_schema=target_schema,
        include_object=_include_object,  # type: ignore[arg-type]
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
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{target_schema}"')
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=version_table,
            version_table_schema=target_schema,
            include_object=_include_object,  # type: ignore[arg-type]
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
