"""dx V1.1.0 补列：按环境的异步阈值、发现索引的实例版本、连接测试的实际权限

Revision ID: 0002_dx_v110_columns
Revises: 0001_dx_initial
Create Date: 2026-09-29

三条都是"DTO 里有、库里没有"的字段，不补就会静默失效——读出来永远是默认值，调用方看不出
任何异常：

* ``dx.environment.async_import_threshold``：DX-EXE-033 要求阈值"按环境可配置"，只有列被
  装配进 ``EnvironmentTarget`` 之后 ``plan`` 才可能读到它；
* ``dx.discovery_index.dhis2_version``：DX-DSC-010 要求发现索引列表给出"版本"，此前该栏
  恒为 ``None``；
* ``dx.connection_test.granted_authorities``：DX-CON-002 同时要求报告"实际具备"与"缺少"的
  权限，只存缺失项的话事后无法复核当时看到的账户事实，``get_last_connection_test``
  （DX-CON-005）也只能报空。

用 ``ADD COLUMN IF NOT EXISTS``：0001 是按 ORM 元数据 ``create_all`` 建的表，全新数据库
已经带着这些列，重复执行必须是无操作而不是失败。
"""

from __future__ import annotations

from alembic import op

revision = "0002_dx_v110_columns"
down_revision = "0001_dx_initial"
branch_labels = None
depends_on = None

#: (表, 列, 类型)
_ADDITIONS: tuple[tuple[str, str, str], ...] = (
    ("environment", "async_import_threshold", "INTEGER"),
    ("discovery_index", "dhis2_version", "VARCHAR(32)"),
    ("connection_test", "granted_authorities", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
)


def upgrade_statements() -> tuple[str, ...]:
    """升级语句本身：暴露出来是为了能重复执行验证幂等（见集成测试）。"""
    return tuple(
        f"ALTER TABLE dx.{table} ADD COLUMN IF NOT EXISTS {column} {sql_type}"
        for table, column, sql_type in _ADDITIONS
    )


def upgrade() -> None:
    for statement in upgrade_statements():
        op.execute(statement)


def downgrade() -> None:
    for table, column, _sql_type in reversed(_ADDITIONS):
        op.execute(f"ALTER TABLE dx.{table} DROP COLUMN IF EXISTS {column}")
