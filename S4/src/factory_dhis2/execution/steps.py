"""执行与步骤的持久化（DX-EXE-004、DX-EXE-036、NFR-REL-003/004）。

“同一幂等键重复调用时从已持久化的步骤继续，而不是重做”需要一个地方存放步骤事实：
每个步骤一行，完成时把它的产物引用写进 ``detail``。续跑因此是**查表**，不是推断。

执行行同时是幂等键 → 执行状态的唯一来源（DX-EXE-036）：S1 在重试 COMMIT 前必须先查
（CP-GATE-018、AC-EXE-05），而 COMMIT 是不可逆的，重复提交就是重复写入。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import new_id
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2.dto.execution import StepInfo
from factory_dhis2.enums import ExecutionMode, ExecutionStatus, ExecutorType, StepName, StepStatus
from factory_dhis2.errors import ConcurrencyError
from factory_dhis2.models.execution import ExecutionRow, ExecutionStepRow

#: S4 内部的进行态（不属于 S1 的三值 ``ExecutionStatus``，只在 ``dx.execution`` 里出现）。
RUNSTATE = "RUNNING"
PLANSTATE = "PLANNED"
ACTIVE_STATES: tuple[str, ...] = (PLANSTATE, RUNSTATE, ExecutionStatus.NEEDS_MANUAL_CHECK.value)


def create_execution(
    session: Session,
    *,
    environment_key: str,
    project_id: str | None,
    mode: ExecutionMode,
    executor_type: ExecutorType = ExecutorType.METADATA,
    payload_artifact_id: str | None = None,
    payload_hash: str | None = None,
    idempotency_key: str | None = None,
    operator: str | None = None,
) -> ExecutionRow:
    """登记一次执行。

    同一幂等键再次调用时**返回已有那一行**（DX-EXE-004），因此调用方拿到的执行 ID 与
    第一次一致；这也让“进行中”与“已完成”能被区分开，防止重复提交。
    """
    if idempotency_key is not None:
        existing = find_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return existing
    row = ExecutionRow(
        id=new_id("dxexe"),
        project_id=project_id,
        environment_key=environment_key,
        executor_type=executor_type.value,
        mode=mode.value,
        payload_artifact_id=payload_artifact_id,
        payload_hash=payload_hash,
        idempotency_key=idempotency_key,
        status=PLANSTATE,
        operator=operator,
        started_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def find_by_idempotency_key(session: Session, idempotency_key: str) -> ExecutionRow | None:
    return session.execute(
        select(ExecutionRow).where(ExecutionRow.idempotency_key == idempotency_key)
    ).scalars().first()


def get(session: Session, execution_id: str) -> ExecutionRow | None:
    return session.get(ExecutionRow, execution_id)


def require(session: Session, execution_id: str) -> ExecutionRow:
    from factory_foundation.api import NotFoundError

    row = session.get(ExecutionRow, execution_id)
    if row is None:
        raise NotFoundError(
            f"unknown execution {execution_id!r}", details={"executionId": execution_id}
        )
    return row


def set_status(
    session: Session,
    execution: ExecutionRow,
    status: ExecutionStatus,
    *,
    applied: bool | None = None,
    detail: str | None = None,
    finished: bool = False,
) -> ExecutionRow:
    execution.status = status.value
    if applied is not None:
        execution.applied = applied
    if detail is not None:
        execution.detail = detail
    if finished:
        execution.completed_at = datetime.now(UTC)
    session.flush()
    return execution


def remember_job(
    session: Session, execution: ExecutionRow, *, job_ref: str | None, poll_url: str | None
) -> None:
    """DX-EXE-033/034：任务引用与查询地址在提交后**立即**落库。

    这里的顺序是要害：先写库再轮询，进程在轮询期间崩溃时恢复才拿得到任务 ID，
    从而继续查询而不是重新提交（DX-AC-03）。
    """
    execution.async_job_ref = job_ref
    execution.poll_url = poll_url
    session.flush()


def completed_steps(session: Session, execution_id: str) -> set[str]:
    rows = session.execute(
        select(ExecutionStepRow).where(
            ExecutionStepRow.execution_id == execution_id,
            ExecutionStepRow.status == StepStatus.COMPLETED.value,
        )
    ).scalars().all()
    return {row.step for row in rows}


def step_detail(session: Session, execution_id: str, step: str) -> dict[str, Any]:
    row = session.get(ExecutionStepRow, (execution_id, step))
    return dict(row.detail or {}) if row else {}


def begin_step(session: Session, execution_id: str, step: StepName | str) -> ExecutionStepRow:
    name = step.value if isinstance(step, StepName) else str(step)
    row = session.get(ExecutionStepRow, (execution_id, name))
    if row is None:
        row = ExecutionStepRow(execution_id=execution_id, step=name)
        session.add(row)
    row.status = StepStatus.RUNNING.value
    row.started_at = row.started_at or datetime.now(UTC)
    row.completed_at = None
    session.flush()
    return row


def finish_step(
    session: Session,
    execution_id: str,
    step: StepName | str,
    *,
    status: StepStatus = StepStatus.COMPLETED,
    detail: dict[str, Any] | None = None,
) -> ExecutionStepRow:
    name = step.value if isinstance(step, StepName) else str(step)
    row = begin_step(session, execution_id, name)
    row.status = status.value
    row.completed_at = datetime.now(UTC)
    if detail:
        merged = dict(row.detail or {})
        merged.update(detail)
        row.detail = merged
    session.flush()
    return row


def skip_step(session: Session, execution_id: str, step: StepName | str, reason: str) -> None:
    row = begin_step(session, execution_id, step)
    row.status = StepStatus.SKIPPED.value
    row.completed_at = datetime.now(UTC)
    row.detail = {**(row.detail or {}), "reason": reason}
    session.flush()


def steps_as_info(session: Session, execution_id: str) -> list[StepInfo]:
    rows = session.execute(
        select(ExecutionStepRow)
        .where(ExecutionStepRow.execution_id == execution_id)
        .order_by(ExecutionStepRow.execution_id, ExecutionStepRow.step)
    ).scalars().all()
    return [
        StepInfo(
            step=row.step,
            status=row.status,
            started_at=row.started_at.isoformat() if row.started_at else None,
            completed_at=row.completed_at.isoformat() if row.completed_at else None,
            detail=dict(row.detail or {}),
        )
        for row in rows
    ]


def active_execution(session: Session, environment_key: str) -> ExecutionRow | None:
    """该环境仍在进行或未确认的执行（DX-ENV-008/009 的判定依据）。"""
    return session.execute(
        select(ExecutionRow)
        .where(
            ExecutionRow.environment_key == environment_key,
            ExecutionRow.status.in_(ACTIVE_STATES),
        )
        .order_by(ExecutionRow.started_at.desc())
        .limit(1)
    ).scalars().first()


def mark_running(session: Session, execution: ExecutionRow) -> None:
    """一个执行一旦开始就进入 RUNNING，直到给出终态。

    ``RUNNING`` / ``PLANNED`` 是 S4 内部的进行态：S1 的 ``ExecutionStatus`` 只有三个终态
    成员，因此对外报告时非终态一律映射为 NEEDS_MANUAL_CHECK 之外的最近已知结论。
    """
    execution.status = RUNSTATE
    session.flush()


def refuse_if_other_active(session: Session, execution: ExecutionRow) -> None:
    """同一执行之外的进行中执行是竞争（DX-EXE-030 的第二道防线）。"""
    current = active_execution(session, execution.environment_key)
    if current is not None and current.id != execution.id:
        raise ConcurrencyError(
            f"environment {execution.environment_key!r} already has execution "
            f"{current.id} in progress",
            details={
                "environmentKey": execution.environment_key,
                "otherExecutionId": current.id,
            },
        )


__all__ = [
    "active_execution",
    "begin_step",
    "completed_steps",
    "create_execution",
    "find_by_idempotency_key",
    "finish_step",
    "get",
    "mark_running",
    "refuse_if_other_active",
    "remember_job",
    "require",
    "set_status",
    "skip_step",
    "step_detail",
    "steps_as_info",
]
