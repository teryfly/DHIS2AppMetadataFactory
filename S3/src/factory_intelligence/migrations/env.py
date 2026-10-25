"""Alembic environment for the ``ai`` schema (S3).

S0's runner supplies ``target_schema`` and ``version_table`` as Alembic options
(``factory_foundation/db/migrations.py:_build_alembic_config``), so this file needs no
``alembic.ini``: it reads both, creates the schema if it does not exist yet, and
restricts autogenerate/compare to ``ai`` tables so S3 can never emit a migration that
touches another subsystem's schema (00 doc 4.2, D-08).
"""

from __future__ import annotations

from typing import Any

from alembic import context
from factory_foundation.api import AiBase
from sqlalchemy import engine_from_config, pool

config = context.config

target_schema = config.get_main_option("target_schema") or "ai"
version_table = config.get_main_option("version_table") or "ai_alembic_version"

# Importing the models package registers every ai table on AiBase.metadata.
import factory_intelligence.models  # noqa: E402,F401

target_metadata = AiBase.metadata


def _include_object(
    obj: Any,
    name: str | None,
    type_: str | None,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Manage only this schema's tables (FD-DB-002)."""
    if type_ == "table":
        return (obj.schema or target_schema) == target_schema
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
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
