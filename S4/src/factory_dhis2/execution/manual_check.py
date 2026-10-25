"""人工核实结论与释放环境锁（DX-EXE-031、AC-EXE-05、DX-AC-04）。

一次结果不明的提交（``NEEDS_MANUAL_CHECK``）**故意不释放环境锁**：在没人确认“到底生效了
没有”之前放第二个提交进来，两个提交就会互相踩。锁只能在这里释放——由调用方带着人的结论
调用本函数。

它是**事务内**函数（SRS 7.4）：状态变更与它的审计记录必须落在同一个事务里（D-11），而且
释放锁与写结论也必须是原子的——否则崩溃会留下“结论已记但锁还占着”的环境，那比原来的问题
更难解释。

结论只有两种，S1 的镜像也是这两种（``ManualCheckOutcome``）：``PASSED`` = 人确认已生效，
``FAILED`` = 人确认未生效。S4 不接受第三种，也不自己判断：判断是人的事（总体 SRS 4.4）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import ValidationError, unit_of_work
from sqlalchemy.orm import Session

from factory_dhis2 import audit
from factory_dhis2.enums import ExecutionStatus, ManualCheckOutcome
from factory_dhis2.envlock import manager as envlock
from factory_dhis2.execution import steps
from factory_dhis2.models.execution import ExecutionRow

#: S1 传入的是字符串，取值词汇与 ``ManualCheckOutcome`` 逐字一致
VALID_OUTCOMES: frozenset[str] = frozenset(item.value for item in ManualCheckOutcome)


def resolve_manual_check(
    execution_id: str,
    outcome: str,
    note: str,
    operator: str,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    """记录人工结论并释放环境锁（SRS 7.4 “事务内 / 释放锁”）。

    返回的是 **camelCase 纯 dict**：S1 的消费方式就是当字典读
    （``factory_control/testing/fakes.py`` 的 ``resolve_manual_check``），它不校验模型。
    """
    value = str(outcome or "").strip().upper()
    if value not in VALID_OUTCOMES:
        raise ValidationError(
            f"manual check outcome {outcome!r} is not one of "
            f"{sorted(VALID_OUTCOMES)}; S4 does not guess what the operator meant "
            "(DX-EXE-031)",
            details={
                "executionId": execution_id,
                "outcome": outcome,
                "allowed": sorted(VALID_OUTCOMES),
                "rule": "DX-EXE-031",
            },
        )
    applied = value == ManualCheckOutcome.PASSED.value
    status = ExecutionStatus.COMPLETED if applied else ExecutionStatus.FAILED

    with unit_of_work(session) as sess:
        execution = steps.require(sess, execution_id)
        _require_resolvable(execution)
        environment_key = execution.environment_key
        holder = envlock.holder(sess, environment_key)
        steps.set_status(
            sess,
            execution,
            status,
            applied=applied,
            detail=f"人工核实：{value}",
            finished=True,
        )
        execution.manual_check_outcome = value
        execution.manual_check_note = note
        execution.resolved_at = datetime.now(UTC)
        execution.resolved_by = operator
        # 释放必须在结论写下的同一事务里：DX-EXE-031 的“直至人工核实后由调用方释放”
        # 说的就是这一刻；若锁被别的执行持有，manager 抛 CONCURRENCY_ERROR，本次一并回滚。
        if holder == execution_id:
            envlock.release(
                sess,
                environment_key,
                execution_id,
                operator=operator,
                reason=f"MANUAL_CHECK_{value}",
            )
        audit.record(
            audit.MANUAL_CHECK_RESOLVED,
            object_type="Execution",
            object_id=execution_id,
            project_id=execution.project_id,
            before={"status": ExecutionStatus.NEEDS_MANUAL_CHECK.value, "lockHeld": holder == execution_id},
            after={
                "outcome": value,
                "status": status.value,
                "applied": applied,
                "note": note,
                "environmentKey": environment_key,
                "lockReleased": holder == execution_id,
            },
            operator=operator,
            session=sess,
        )
        sess.flush()
        return {
            "executionId": execution_id,
            "resolved": True,
            "releasesLock": True,
            "lockReleased": holder == execution_id,
            "environmentKey": environment_key,
            "outcome": value,
            "status": status.value,
            "applied": applied,
            "note": note,
            "resolvedBy": operator,
            "resolvedAt": execution.resolved_at.isoformat() if execution.resolved_at else None,
        }


def _require_resolvable(execution: ExecutionRow) -> None:
    """已经核实过的执行不第二次核实：两次结论不同会把环境锁语义搅浑。"""
    if execution.resolved_at is not None or execution.manual_check_outcome is not None:
        raise ValidationError(
            f"execution {execution.id} was already resolved as "
            f"{execution.manual_check_outcome!r} by {execution.resolved_by}; a manual check "
            "conclusion is final (DX-EXE-031)",
            details={
                "executionId": execution.id,
                "previousOutcome": execution.manual_check_outcome,
                "resolvedBy": execution.resolved_by,
                "rule": "DX-EXE-031",
            },
        )


__all__ = ["VALID_OUTCOMES", "resolve_manual_check"]
