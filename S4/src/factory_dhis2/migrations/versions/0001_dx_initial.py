"""dx initial schema

Revision ID: 0001_dx_initial
Revises:
Create Date: 2026-09-28

从 ORM 元数据建全部 ``dx`` 表，模型与迁移不可能漂移；随后装两条守卫：

1. **执行、验证记录、环境锁永久保留**（DX-DATA-02）：它们支撑总体 SRS FR-SEC-014 的
   “任一 COMMIT 可追溯到环境、操作者、载荷哈希、执行记录、ImportReport”，项目数据清理
   只删项目关联的发现索引，绝不删这三张表。
2. **环境锁的持有一致性**：有持有者就必须有获取时间，已释放就必须没有持有者——避免
   “一行持锁但无人持有”的僵尸锁，那会让一个环境永久无法提交。
"""

from __future__ import annotations

from alembic import op

revision = "0001_dx_initial"
down_revision = None
branch_labels = None
depends_on = None


_RETENTION_GUARD_FN = """
CREATE OR REPLACE FUNCTION dx.retention_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        RAISE EXCEPTION 'dx.% rows are retained forever (DX-DATA-02)', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$;
"""

_LOCK_CONSISTENCY_FN = """
CREATE OR REPLACE FUNCTION dx.environment_lock_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.holder_execution_id IS NOT NULL AND NEW.acquired_at IS NULL THEN
        RAISE EXCEPTION 'environment lock % held by % without acquired_at',
            NEW.environment_key, NEW.holder_execution_id;
    END IF;
    IF NEW.holder_execution_id IS NULL AND NEW.released_at IS NULL AND TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'environment lock % released must carry released_at', NEW.environment_key;
    END IF;
    RETURN NEW;
END;
$$;
"""

def _retained_tables() -> tuple[str, ...]:
    """DX-DATA-02 的保留表清单来自 ``registries``，迁移不另写一份（两处会漂移）。"""
    from factory_dhis2.registries import RETAINED_TABLES

    return RETAINED_TABLES


def upgrade() -> None:
    from factory_foundation.api import DxBase

    import factory_dhis2.models  # noqa: F401

    DxBase.metadata.create_all(bind=op.get_bind(), checkfirst=True)

    op.execute(_RETENTION_GUARD_FN)
    for table in _retained_tables():
        op.execute(
            f"CREATE TRIGGER dx_{table}_retention_guard BEFORE DELETE ON dx.{table} "
            "FOR EACH ROW EXECUTE FUNCTION dx.retention_guard()"
        )

    op.execute(_LOCK_CONSISTENCY_FN)
    op.execute(
        "CREATE TRIGGER dx_environment_lock_guard BEFORE INSERT OR UPDATE ON dx.environment_lock "
        "FOR EACH ROW EXECUTE FUNCTION dx.environment_lock_guard()"
    )


def downgrade() -> None:
    from factory_foundation.api import DxBase

    import factory_dhis2.models  # noqa: F401

    for table in _retained_tables():
        op.execute(f"DROP TRIGGER IF EXISTS dx_{table}_retention_guard ON dx.{table}")
    op.execute("DROP FUNCTION IF EXISTS dx.retention_guard()")
    op.execute("DROP TRIGGER IF EXISTS dx_environment_lock_guard ON dx.environment_lock")
    op.execute("DROP FUNCTION IF EXISTS dx.environment_lock_guard()")
    DxBase.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
