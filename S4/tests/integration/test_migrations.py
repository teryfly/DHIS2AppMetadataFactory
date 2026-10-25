"""``dx`` 迁移与两条守卫（DX-DATA-01、DX-DATA-02、DX-EXE-031）。

守卫是数据层的：一个“持锁但没有获取时间”的锁行会让某个环境永久无法提交，而一条被删掉的
执行记录会让某次 COMMIT 不再可追溯（FR-SEC-014）。这两件事都不能靠代码自觉。
"""

from __future__ import annotations

import pytest
from factory_foundation.api import DxBase, new_session, unit_of_work
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError

from factory_dhis2.models import (
    EnvironmentLockRow,
    ExecutionRow,
    ValidationRecordRow,
)

pytestmark = pytest.mark.integration


def _schemas() -> set[str]:
    with new_session().bind.connect() as conn:
        return set(conn.execute(text("select nspname from pg_namespace")).scalars())


def test_migration_creates_the_dx_schema_and_every_table(harness: object) -> None:
    assert "dx" in _schemas()
    names = {table.name for table in DxBase.metadata.sorted_tables}
    inspector = inspect(new_session().bind)
    found = {str(name) for name in inspector.get_table_names(schema="dx")}
    assert names <= found, names - found


def test_environment_lock_is_one_row_per_environment(harness: object) -> None:
    """跨项目互斥的结构保证：主键就是 environmentKey。"""
    inspector = inspect(new_session().bind)
    pk = inspector.get_pk_constraint("environment_lock", schema="dx")
    assert list(pk["constrained_columns"]) == ["environment_key"]


def test_environment_lock_has_no_expiry_column(harness: object) -> None:
    """DX-EXE-031：锁不因超时自动释放——所以表里就没有过期时间这一列。"""
    columns = {c["name"] for c in inspect(new_session().bind).get_columns("environment_lock", schema="dx")}
    assert "expires_at" not in columns
    assert "acquired_at" in columns and "released_at" in columns


def test_the_v110_columns_exist_and_the_schema_is_at_head(harness: object) -> None:
    """DX-DATA-01 + V1.1.0 补列：0002 必须真的被应用上。

    这三列都是"DTO 里有、库里没有"的那一类缺口：缺任何一个，对应需求会静默失效而不是
    报错——阈值读不到、发现列表的版本永远是空、连接测试的实际权限无处可存。
    """
    bind = new_session().bind
    inspector = inspect(bind)
    environment_columns = {c["name"] for c in inspector.get_columns("environment", schema="dx")}
    discovery_columns = {c["name"] for c in inspector.get_columns("discovery_index", schema="dx")}
    connection_columns = {c["name"] for c in inspector.get_columns("connection_test", schema="dx")}
    assert "async_import_threshold" in environment_columns, "DX-EXE-033 的按环境阈值"
    assert "dhis2_version" in discovery_columns, "DX-DSC-010 的版本栏"
    assert "granted_authorities" in connection_columns, "DX-CON-002/005 的实际权限"

    with bind.connect() as conn:
        stamped = list(
            conn.execute(text("select version_num from dx.dx_alembic_version")).scalars()
        )
    assert stamped == ["0002_dx_v110_columns"], f"dx 的迁移没停在 head：{stamped}"


def test_the_v110_migration_can_be_reapplied(harness: object) -> None:
    """0002 用的是 ``ADD COLUMN IF NOT EXISTS``：0001 按 ORM 元数据建表时列已经在了。

    全新数据库上 0001 就带着这些列，0002 必须是无操作而不是报错——否则每次从零建库都会
    在升级链上断掉。
    """
    from importlib import import_module

    migration = import_module("factory_dhis2.migrations.versions.0002_dx_v110_columns")

    with new_session().bind.connect() as conn:
        conn.execute(text("SELECT 1"))
        # 直接跑 upgrade() 里的语句：重复执行不报错才算幂等
        for statement in migration.upgrade_statements():
            conn.execute(text(statement))
        conn.commit()
    assert "async_import_threshold" in {
        column["name"] for column in inspect(new_session().bind).get_columns("environment", schema="dx")
    }


def test_execution_idempotency_key_is_unique(harness: object) -> None:
    """DX-EXE-036：按幂等键查询必须至多一行。"""
    uniques = inspect(new_session().bind).get_unique_constraints("execution", schema="dx")
    assert any(list(u["column_names"]) == ["idempotency_key"] for u in uniques)


def test_retention_guard_blocks_deleting_an_execution(harness: object) -> None:
    """DX-DATA-02：执行记录永久保留。"""
    execution_id = "dxexe0000000000000000000001"
    with unit_of_work() as session:
        session.add(
            ExecutionRow(
                id=execution_id,
                environment_key="dev",
                mode="COMMIT",
                status="COMPLETED",
            )
        )
    with pytest.raises(ProgrammingError) as excinfo, unit_of_work() as session:
        session.query(ExecutionRow).filter(ExecutionRow.id == execution_id).delete()
    assert "retained forever" in str(excinfo.value)


def test_retention_guard_blocks_deleting_a_validation_record(harness: object) -> None:
    from datetime import UTC, datetime

    with unit_of_work() as session:
        session.add(
            ValidationRecordRow(
                id="dxvld0000000000000000000001",
                environment_key="dev",
                payload_hash="sha256:" + "a" * 64,
                env_state_hash="sha256:" + "b" * 64,
                execution_id="dxexe0000000000000000000001",
                validated_at=datetime(2026, 1, 1, tzinfo=UTC),
                result="PASSED",
            )
        )
    with pytest.raises(ProgrammingError) as excinfo, unit_of_work() as session:
        session.query(ValidationRecordRow).delete(synchronize_session=False)
    assert "retained forever" in str(excinfo.value)


def test_lock_guard_rejects_a_holder_without_acquired_at(harness: object) -> None:
    with pytest.raises(ProgrammingError) as excinfo, unit_of_work() as session:
        session.add(
            EnvironmentLockRow(
                environment_key="dev",
                holder_execution_id="dxexe0000000000000000000001",
                acquired_at=None,
            )
        )
    assert "without acquired_at" in str(excinfo.value)


def test_delete_project_data_keeps_executions(harness: object) -> None:
    """DX-DATA-02：清理项目数据只删项目关联的发现索引，执行记录留下。

    必须走 :func:`factory_dhis2.api.delete_project_data` 本身 —— 手工删行测的是"我会删",
    而要保证的是"被调用的那个函数不会顺手把执行记录也带走"。
    """
    from datetime import UTC, datetime

    from factory_dhis2 import api
    from factory_dhis2.cleanup.service import retained_execution_ids, retained_step_count
    from factory_dhis2.models import DiscoveryIndexRow, ExecutionStepRow

    with unit_of_work() as session:
        session.add(
            DiscoveryIndexRow(
                id="dxdsc0000000000000000000001",
                environment_key="dev",
                project_id="prj1",
                artifact_id="art1",
                content_hash="sha256:" + "c" * 64,
                discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        session.add(
            DiscoveryIndexRow(
                id="dxdsc0000000000000000000002",
                environment_key="dev",
                project_id="prj2",
                artifact_id="art2",
                content_hash="sha256:" + "d" * 64,
                discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        session.add(
            ExecutionRow(
                id="dxexe0000000000000000000002",
                project_id="prj1",
                environment_key="dev",
                mode="COMMIT",
                status="COMPLETED",
            )
        )
        session.add(
            ExecutionStepRow(
                execution_id="dxexe0000000000000000000002",
                step="COMMIT",
                status="COMPLETED",
            )
        )

    api.delete_project_data("prj1")

    with unit_of_work() as session:
        assert (
            session.get(DiscoveryIndexRow, "dxdsc0000000000000000000001") is None
        ), "本项目的发现索引应被清理"
        assert session.get(DiscoveryIndexRow, "dxdsc0000000000000000000002") is not None, (
            "别的项目的索引不能被连带删掉"
        )
        assert session.get(ExecutionRow, "dxexe0000000000000000000002") is not None
        assert retained_execution_ids(session, "prj1") == ["dxexe0000000000000000000002"]
        assert retained_step_count(session, "dxexe0000000000000000000002") == 1, (
            "步骤事实跟着执行记录一起保留，否则追溯链断在中间"
        )


def test_the_retained_table_list_has_one_source(harness: object) -> None:
    """DX-DATA-02 / DX-DATA-01：清理逻辑与迁移守卫必须引用同一份保留表清单。"""
    from factory_dhis2.registries import RETAINED_TABLES

    with new_session().bind.connect() as conn:
        guarded = list(
            conn.execute(
                text(
                    "select tg.tgname from pg_trigger tg "
                    "join pg_class c on c.oid = tg.tgrelid "
                    "join pg_namespace n on n.oid = c.relnamespace "
                    "where n.nspname = 'dx' and tg.tgname like 'dx_%_retention_guard'"
                )
            ).scalars()
        )
    covered = {name.removeprefix("dx_").removesuffix("_retention_guard") for name in guarded}
    assert covered == set(RETAINED_TABLES), (covered, RETAINED_TABLES)


def test_no_cross_schema_foreign_key(harness: object) -> None:
    """00 文档 4.2：``dx`` 不声明指向其他 schema 的外键。"""
    from factory_foundation.api import find_cross_schema_fk

    assert not find_cross_schema_fk([DxBase.metadata])
