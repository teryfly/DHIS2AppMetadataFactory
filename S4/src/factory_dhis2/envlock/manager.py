"""环境级提交锁（DX-EXE-030/031、FR-EXE-012、NFR-CON-002）。

锁的**粒度是环境，不是项目**：同一实例上两个项目同时提交会互相踩到对方刚建好的对象，
所以“一个环境同一时刻只有一个提交类执行”必须是环境级的（AC-EXE-04、DX-AC-04）。

两层保证：

1. ``pg_advisory_xact_lock`` 串行化对同一环境的获取尝试——两个事务同时抢锁时，后一个
   等前一个提交后再看到结果，不会双双认为自己拿到了锁。
2. ``dx.environment_lock`` 一行一环境（主键就是 environmentKey），持锁者与非超时释放
   状态写在行里：执行进入 NEEDS_MANUAL_CHECK 时锁**保持**，直到人工核实后由调用方释放
   （DX-EXE-031）。表里没有过期列，也就没人能“等等就自动解锁”。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from factory_dhis2 import audit
from factory_dhis2.enums import ExecutionStatus
from factory_dhis2.errors import ConcurrencyError
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.models.lock import EnvironmentLockRow

#: advisory lock 的命名空间：与 S0/S1 可能用的其它键隔开（同一个 hashtext 空间里不撞车）
LOCK_NAMESPACE = 723_104  # "dx" 的稳定编号
#: 需要独占环境的执行模式（DX-EXE-030）
LOCKING_MODES: frozenset[str] = frozenset({"COMMIT", "FIXTURE", "DELETE", "MAINTENANCE"})


def _touch(session: Session, environment_key: str) -> None:
    """在同一事务里对该环境的获取尝试加咨询锁（跨项目也生效，因为键只有环境）。"""
    session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"),
        {"ns": LOCK_NAMESPACE, "key": environment_key},
    )


def holder(session: Session, environment_key: str) -> str | None:
    row = session.get(EnvironmentLockRow, environment_key)
    if row is None or row.holder_execution_id is None:
        return None
    return row.holder_execution_id


def require_free(session: Session, environment_key: str, operation: str) -> None:
    """不获取锁，只检查——供“先看一眼再决定”的调用点使用。"""
    current = holder(session, environment_key)
    if current is not None:
        raise ConcurrencyError(
            f"environment {environment_key!r} is locked by execution {current} while "
            f"{operation} requested exclusive access",
            details={
                "environmentKey": environment_key,
                "holderExecutionId": current,
                "operation": operation,
            },
        )


def acquire(
    session: Session,
    environment_key: str,
    execution_id: str,
    *,
    operator: str | None = None,
    note: str | None = None,
) -> EnvironmentLockRow:
    """获取环境锁；拿不到就是 ``CONCURRENCY_ERROR``（AC-EXE-04）。"""
    _touch(session, environment_key)
    row = session.get(EnvironmentLockRow, environment_key)
    if row is None:
        row = EnvironmentLockRow(environment_key=environment_key)
        session.add(row)
        session.flush()
    current = row.holder_execution_id
    if current is not None and current != execution_id:
        other = session.get(ExecutionRow, current)
        pending_manual_check = (
            other is not None and other.status == ExecutionStatus.NEEDS_MANUAL_CHECK.value
        )
        raise ConcurrencyError(
            f"another committing execution holds environment {environment_key!r}"
            + (
                ", and it is waiting for manual verification"
                if pending_manual_check
                else ""
            ),
            details={
                "environmentKey": environment_key,
                "holderExecutionId": current,
                "requestedExecutionId": execution_id,
                # DX-AC-04 要的就是 CONCURRENCY_ERROR：S1 按同一个 409 语义处理两种情况，
                # 差异放在 reason 里，而不是另造一个调用方没登记过的错误码
                "reason": "MANUAL_CHECK_PENDING" if pending_manual_check else "COMMIT_IN_PROGRESS",
                **(
                    {"holderDetail": other.detail}
                    if pending_manual_check and other is not None
                    else {}
                ),
            },
        )
    row.holder_execution_id = execution_id
    row.acquired_at = datetime.now(UTC)
    row.released_at = None
    row.released_by = None
    row.release_reason = None
    row.note = note
    audit.record(
        audit.ENV_LOCK_ACQUIRED,
        object_type="EnvironmentLock",
        object_id=environment_key,
        after={"environmentKey": environment_key, "holderExecutionId": execution_id},
        operator=operator,
        session=session,
    )
    return row


def release(
    session: Session,
    environment_key: str,
    execution_id: str,
    *,
    operator: str | None = None,
    reason: str | None = None,
) -> None:
    """释放锁；只有持锁者本人能释放，避免一次误释放把并行提交放进来。"""
    _touch(session, environment_key)
    row = session.get(EnvironmentLockRow, environment_key)
    if row is None or row.holder_execution_id is None:
        return
    if row.holder_execution_id != execution_id:
        raise ConcurrencyError(
            f"execution {execution_id} cannot release a lock held by "
            f"{row.holder_execution_id}",
            details={
                "environmentKey": environment_key,
                "holderExecutionId": row.holder_execution_id,
            },
        )
    row.holder_execution_id = None
    row.released_at = datetime.now(UTC)
    row.released_by = operator
    row.release_reason = reason
    audit.record(
        audit.ENV_LOCK_RELEASED,
        object_type="EnvironmentLock",
        object_id=environment_key,
        before={"holderExecutionId": execution_id},
        after={"releasedBy": operator, "reason": reason},
        operator=operator,
        session=session,
    )


def keeps_lock_after(status: str) -> bool:
    """DX-EXE-031：NEEDS_MANUAL_CHECK 时锁保持。"""
    return status == ExecutionStatus.NEEDS_MANUAL_CHECK.value


__all__ = [
    "LOCKING_MODES",
    "LOCK_NAMESPACE",
    "acquire",
    "holder",
    "keeps_lock_after",
    "release",
    "require_free",
]
