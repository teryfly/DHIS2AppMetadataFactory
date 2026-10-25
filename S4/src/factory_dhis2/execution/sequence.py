"""执行器顺序守卫（DX-EXE-037、DX-BR-12）。

一次部署含多个执行器时顺序是固定的：**Metadata → DataStore → Tracker Data → App Upload**。
前一步失败时后续不许执行 —— 元数据没落地就跑 Tracker 会造出指向不存在 Program 的数据，
DataStore 没写完就上传应用会让应用读到旧配置。

调用方（S1 的 Run 编排）当然可以按这个顺序一步步调用，但 S4 是**唯一**能看见执行事实的
地方：把顺序变成 S4 自己检查的约束，而不是"希望调用方记得"，才是可交付的防线（总体 SRS
4.4：执行层只负责协议与执行，但协议就包含顺序）。

判定依据是同一载荷（同 ``payloadHash``）在**同一环境**上已持久化的执行记录：这里不访问
DHIS2，也不猜别的执行器是不是成功了 —— 没有记录就是"还没执行过"，不通过。
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import unit_of_work
from sqlalchemy import select

from factory_dhis2.enums import ExecutorType
from factory_dhis2.errors import ExecutionOrderError
from factory_dhis2.models.execution import ExecutionRow

#: 视为"这一步已经完成"的执行状态。NEEDS_MANUAL_CHECK 不算：结果没确认之前不得继续。
_SATISFIED_STATUSES: frozenset[str] = frozenset({"COMPLETED"})


def executor_of_mode(mode: str) -> str:
    """执行模式 → 它属于哪个执行器（COMMIT/VALIDATE 都是元数据那一步）。"""
    if mode in ("VALIDATE", "COMMIT", "POST_VERIFY", "DELETE", "MAINTENANCE"):
        return ExecutorType.METADATA.value
    if mode == "FIXTURE":
        return ExecutorType.TRACKER_DATA.value
    return ExecutorType.METADATA.value


def require_predecessors_applied(
    *,
    environment_key: str,
    payload_hash: str,
    plan_document: dict[str, Any],
    executor: str,
) -> None:
    """计划里排在这个执行器之前的每一步都必须已经成功执行。"""
    planned = [str(item) for item in (plan_document.get("executors") or [])]
    if len(planned) <= 1:
        return
    # 延迟导入：executors 包会回头导入执行模块，模块级导入会形成循环
    from factory_dhis2.executors import ordered_executors

    # 顺序只由 EXECUTOR_ORDER 决定（ordered_executors 是唯一出处），计划里的排列不参与
    ordered = [item.executor_type.value for item in ordered_executors(planned)]
    if executor not in ordered:
        return
    predecessors = ordered[: ordered.index(executor)]
    if not predecessors:
        return
    with unit_of_work() as session:
        rows = list(
            session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.environment_key == environment_key,
                    ExecutionRow.payload_hash == payload_hash,
                )
            ).scalars()
        )
    applied = {
        executor_of_mode(str(row.mode))
        for row in rows
        if row.status in _SATISFIED_STATUSES and row.applied
    }
    missing = [item for item in predecessors if item not in applied]
    if missing:
        raise ExecutionOrderError(
            f"执行器 {executor} 不能执行：同一载荷在 {environment_key!r} 上的前序步骤 "
            f"{', '.join(missing)} 还没有成功且已生效的执行记录（DX-EXE-037、DX-BR-12）",
            details={
                "environmentKey": environment_key,
                "payloadHash": payload_hash,
                "executor": executor,
                "pendingPredecessors": missing,
                "order": ordered,
                "applied": sorted(applied),
            },
        )


__all__ = ["executor_of_mode", "require_predecessors_applied"]
