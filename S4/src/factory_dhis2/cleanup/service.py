"""项目数据清理（SRS 7.4 ``delete_project_data``、DX-DATA-02）。

只删**与项目关联的发现索引**。执行、验证记录、环境锁一律永久保留：它们支撑总体 SRS
FR-SEC-014 的追溯链（“任一 COMMIT 可追溯到环境、操作者、载荷哈希、执行记录、ImportReport”），
而迁移里已经装了 ``BEFORE DELETE`` 触发器把这三张表的删除挡在数据库层
（``migrations/versions/0001_dx_initial.py`` 的 ``dx.retention_guard``），所以这里连尝试都不做——
尝试只会得到一条被触发器拒绝的异常，把一次正常的项目删除变成失败。

Snapshot 索引也不删：它没有项目列，而是按 ``execution_id`` 挂在执行记录上，执行记录既然
永久保留，其证据（Snapshot 产物与索引行）也就必须留着，否则追溯链断在中间。

不写审计：附录 A 里没有 S4 的项目删除事件，项目删除本身由 S1 审计（S4 不替别人解释动作）。
"""

from __future__ import annotations

from factory_foundation.api import unit_of_work
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from factory_dhis2 import registries
from factory_dhis2.execution import steps
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.models.profile import DiscoveryIndexRow

# DX-DATA-02 的保留表清单只有一个出处（registries），迁移与清理共用同一份
RETAINED_TABLES = registries.RETAINED_TABLES


def delete_project_data(project_id: str, *, session: Session | None = None) -> None:
    """删除该项目的发现索引（事务内，SRS 7.4）。"""
    with unit_of_work(session) as sess:
        sess.execute(delete(DiscoveryIndexRow).where(DiscoveryIndexRow.project_id == project_id))
        sess.flush()


def retained_execution_ids(session: Session, project_id: str) -> list[str]:
    """该项目仍然保留的执行记录 ID（DX-DATA-02 的自检口径，也是测试的断言点）。"""
    return [
        str(row)
        for row in session.execute(
            select(ExecutionRow.id).where(ExecutionRow.project_id == project_id)
        ).scalars()
    ]


def retained_step_count(session: Session, execution_id: str) -> int:
    """某条保留执行的步骤行数：清理不能把步骤事实一起带走（DX-EXE-004）。"""
    return len(steps.steps_as_info(session, execution_id))


__all__ = ["RETAINED_TABLES", "delete_project_data", "retained_execution_ids", "retained_step_count"]
