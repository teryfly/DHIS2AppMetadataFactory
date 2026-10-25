"""``dx`` schema 的 Alembic 环境（S4）。

S0 的运行器把 ``target_schema`` 与 ``version_table`` 作为 Alembic 选项传入
（``factory_foundation/db/migrations.py:_build_alembic_config``），所以本文件不需要
``alembic.ini``：它读取两者，必要时建 schema，并把 autogenerate/compare 限制在 ``dx``
的表上——S4 因此不可能发出一个动到别个子系统 schema 的迁移（00 文档 4.2、D-08）。
"""

from __future__ import annotations

from typing import Any

from alembic import context
from factory_foundation.api import DxBase
from sqlalchemy import engine_from_config, pool

config = context.config

target_schema = config.get_main_option("target_schema") or "dx"
version_table = config.get_main_option("version_table") or "dx_alembic_version"

# 导入 models 包会把每张表注册到 DxBase.metadata 上。
import factory_dhis2.models  # noqa: E402,F401

target_metadata = DxBase.metadata


def _include_object(
    obj: Any,
    name: str | None,
    type_: str | None,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """只管本 schema 的表（FD-DB-002）。"""
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
