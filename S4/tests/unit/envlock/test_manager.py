"""环境级提交锁（DX-EXE-030/031、AC-EXE-04、DX-AC-04、NFR-CON-002、FR-EXE-012）。

锁的粒度是环境而不是项目，所以“两个项目同时对同一环境提交”必须只有一个能进去。这里
同时证明两层保证：``dx.environment_lock`` 一环境一行（主键就是 environmentKey），以及
``pg_advisory_xact_lock`` 让两个并发事务的获取尝试串行化——后者用两个真实会话、两个线程
跑一次，并用 ``pg_locks`` 确认第二个确实被挡在咨询锁上，不是靠睡一觉猜。

环境键与执行 ID 都带进程号：同机并跑两个测试进程时，咨询锁与主键不会互相撞上。
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime

import pytest
from factory_foundation.api import AuditQuery, new_session, query_audit, unit_of_work
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from factory_dhis2.enums import ExecutionStatus
from factory_dhis2.envlock import manager
from factory_dhis2.errors import ConcurrencyError
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.models.lock import EnvironmentLockRow

pytestmark = pytest.mark.usefixtures("harness")

_ID_BASE = f"dxexe{os.getpid():07d}"
EXECUTION_A = f"{_ID_BASE}a1"
EXECUTION_B = f"{_ID_BASE}b1"


def key(name: str) -> str:
    """本进程独占的环境键。"""
    return f"{name}-{os.getpid()}"


def add_execution(
    session: Session, execution_id: str, environment_key: str, status: str = "RUNNING"
) -> None:
    session.add(
        ExecutionRow(
            id=execution_id,
            environment_key=environment_key,
            mode="COMMIT",
            status=status,
        )
    )
    session.flush()


def lock_row(session: Session, environment_key: str) -> EnvironmentLockRow | None:
    return session.get(EnvironmentLockRow, environment_key)


def advisory_lock_count(environment_key: str) -> int:
    """这个环境的咨询锁上现在挂着多少个事务（持有的 + 等待的）。

    ``hashtext`` 返回 int4，而 ``pg_locks.objid`` 按无符号 32 位显示同一个值，所以两边都
    要抹掉符号位，否则查询永远数不到。
    """
    with new_session().bind.connect() as conn:
        return int(
            conn.execute(
                text(
                    "select count(*) from pg_locks where locktype = 'advisory' "
                    "and classid::bigint = :namespace "
                    "and objid::bigint = (hashtext(:key)::bigint & 4294967295)"
                ),
                {"namespace": manager.LOCK_NAMESPACE, "key": environment_key},
            ).scalar_one()
        )


def lock_events(session: Session, environment_key: str, event_type: str) -> list:
    return list(
        query_audit(
            AuditQuery(
                event_type=event_type,
                object_type="EnvironmentLock",
                object_id=environment_key,
            ),
            session=session,
        ).items
    )


# --- DX-EXE-030 / AC-EXE-04：获取与竞争 ---------------------------------------


def test_an_executing_execution_acquires_the_environment_lock() -> None:
    """DX-EXE-030：提交类执行拿到环境锁。"""
    environment = key("acquire")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        row = manager.acquire(session, environment, EXECUTION_A, operator="alice")
        holder = manager.holder(session, environment)
    assert row.holder_execution_id == EXECUTION_A
    assert row.acquired_at is not None
    assert row.released_at is None
    assert holder == EXECUTION_A


def test_a_second_execution_on_the_same_environment_is_refused() -> None:
    """AC-EXE-04 / DX-EXE-030：一个环境同一时刻只有一个提交类执行。"""
    environment = key("two-executions")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        add_execution(session, EXECUTION_B, environment)
        manager.acquire(session, environment, EXECUTION_A)
    with unit_of_work() as session, pytest.raises(ConcurrencyError) as excinfo:
        manager.acquire(session, environment, EXECUTION_B)
    assert excinfo.value.details["holderExecutionId"] == EXECUTION_A
    assert excinfo.value.details["requestedExecutionId"] == EXECUTION_B
    assert excinfo.value.code == "CONCURRENCY_ERROR"


def test_the_refused_execution_leaves_the_holder_untouched() -> None:
    """竞争失败者不能顺手把锁改写成自己的。"""
    environment = key("refused-keeps")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        add_execution(session, EXECUTION_B, environment)
        manager.acquire(session, environment, EXECUTION_A, note="A 的部署")
        acquired_at = lock_row(session, environment).acquired_at  # type: ignore[union-attr]
    with pytest.raises(ConcurrencyError), unit_of_work() as session:
        manager.acquire(session, environment, EXECUTION_B, note="B 的部署")
    with unit_of_work() as session:
        row = lock_row(session, environment)
    assert row is not None
    assert row.holder_execution_id == EXECUTION_A
    assert row.acquired_at == acquired_at
    assert row.note == "A 的部署"


def test_the_holder_itself_may_re_acquire_after_a_resume() -> None:
    """DX-EXE-004：同一幂等键续跑时重新拿锁不是竞争。"""
    environment = key("resume")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        first = manager.acquire(session, environment, EXECUTION_A)
        again = manager.acquire(session, environment, EXECUTION_A)
    assert first.environment_key == again.environment_key == environment
    assert again.holder_execution_id == EXECUTION_A


def test_acquiring_writes_the_acquired_audit_event() -> None:
    """附录 A：``dx.env_lock.acquired``。"""
    environment = key("audit-acquire")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        manager.acquire(session, environment, EXECUTION_A, operator="alice")
        events = lock_events(session, environment, "dx.env_lock.acquired")
    assert [event.after for event in events] == [
        {"environmentKey": environment, "holderExecutionId": EXECUTION_A}
    ]
    assert {event.source_subsystem for event in events} == {"S4"}


def test_a_free_environment_passes_the_check_without_taking_the_lock() -> None:
    """``require_free`` 只看不拿：供“先看一眼再决定”的调用点。"""
    environment = key("look-only")
    with unit_of_work() as session:
        manager.require_free(session, environment, "PLAN")
        assert manager.holder(session, environment) is None
        assert lock_row(session, environment) is None


def test_require_free_reports_the_holder() -> None:
    environment = key("require-held")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        manager.acquire(session, environment, EXECUTION_A)
        with pytest.raises(ConcurrencyError) as excinfo:
            manager.require_free(session, environment, "FIXTURE")
    assert excinfo.value.details["operation"] == "FIXTURE"
    assert excinfo.value.details["holderExecutionId"] == EXECUTION_A


# --- DX-EXE-031 / DX-AC-04：待人工核实的锁保持 --------------------------------


def test_a_holder_awaiting_manual_check_is_reported_as_pending_not_merged() -> None:
    """DX-AC-04：待人工核实时另一个项目拿到的是“有待核实”，不是笼统的竞争。"""
    environment = key("manual-pending")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment, ExecutionStatus.NEEDS_MANUAL_CHECK.value)
        add_execution(session, EXECUTION_B, environment)
        manager.acquire(session, environment, EXECUTION_A)
    with unit_of_work() as session, pytest.raises(ConcurrencyError) as excinfo:
        manager.acquire(session, environment, EXECUTION_B)
    assert excinfo.value.details["holderExecutionId"] == EXECUTION_A
    assert excinfo.value.code == "CONCURRENCY_ERROR"
    assert excinfo.value.details["reason"] == "MANUAL_CHECK_PENDING"


def test_the_pending_lock_row_is_unchanged_by_the_refused_attempt() -> None:
    """DX-EXE-031：锁保持——失败的尝试不留下任何改动，包括“谁在等”。"""
    environment = key("manual-keeps")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment, ExecutionStatus.NEEDS_MANUAL_CHECK.value)
        add_execution(session, EXECUTION_B, environment)
        manager.acquire(session, environment, EXECUTION_A, note="结果不明")
        acquired_at = lock_row(session, environment).acquired_at  # type: ignore[union-attr]
    with pytest.raises(ConcurrencyError), unit_of_work() as session:
        manager.acquire(session, environment, EXECUTION_B, note="想插队")
    with unit_of_work() as session:
        row = lock_row(session, environment)
    assert row is not None
    assert (row.holder_execution_id, row.note, row.released_at) == (
        EXECUTION_A,
        "结果不明",
        None,
    )
    assert row.acquired_at == acquired_at


def test_a_manual_check_holder_keeps_the_lock_until_the_caller_releases_it() -> None:
    """DX-EXE-031：锁不因超时自动释放，只能由人工核实后的调用方释放。"""
    assert manager.keeps_lock_after(ExecutionStatus.NEEDS_MANUAL_CHECK.value) is True
    assert manager.keeps_lock_after(ExecutionStatus.COMPLETED.value) is False
    assert manager.keeps_lock_after(ExecutionStatus.FAILED.value) is False


def test_a_pending_holder_releases_only_by_its_own_hand() -> None:
    """DX-AC-04：待人工核实期间，别的执行既拿不到也不能替它释放。"""
    environment = key("manual-hold")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment, ExecutionStatus.NEEDS_MANUAL_CHECK.value)
        add_execution(session, EXECUTION_B, environment)
        manager.acquire(session, environment, EXECUTION_A)
    with unit_of_work() as session:
        with pytest.raises(ConcurrencyError):
            manager.release(session, environment, EXECUTION_B, operator="mallory")
        assert manager.holder(session, environment) == EXECUTION_A


# --- 释放 --------------------------------------------------------------------


def test_the_holder_releases_the_lock_and_that_is_audited() -> None:
    """DX-EXE-031 / 附录 A：结束时释放并写 ``dx.env_lock.released``。"""
    environment = key("release")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        manager.acquire(session, environment, EXECUTION_A)
    with unit_of_work() as session:
        manager.release(
            session, environment, EXECUTION_A, operator="alice", reason="部署完成"
        )
        row = lock_row(session, environment)
        events = lock_events(session, environment, "dx.env_lock.released")
    assert row is not None
    assert row.holder_execution_id is None
    assert row.released_by == "alice"
    assert row.release_reason == "部署完成"
    assert row.released_at is not None
    assert [event.before for event in events] == [{"holderExecutionId": EXECUTION_A}]
    assert [event.after for event in events] == [{"releasedBy": "alice", "reason": "部署完成"}]


def test_a_non_holder_cannot_release_the_lock() -> None:
    """一次误释放就会把两个并行提交同时放进来。"""
    environment = key("steal")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        add_execution(session, EXECUTION_B, environment)
        manager.acquire(session, environment, EXECUTION_A)
    with unit_of_work() as session:
        with pytest.raises(ConcurrencyError) as excinfo:
            manager.release(session, environment, EXECUTION_B, operator="mallory")
        assert manager.holder(session, environment) == EXECUTION_A
    assert excinfo.value.details["holderExecutionId"] == EXECUTION_A


def test_releasing_an_unlocked_environment_is_not_an_error() -> None:
    """释放路径要能被重复调用：一次崩溃后的收尾不该因为“本来就没锁”而失败。"""
    environment = key("never-locked")
    with unit_of_work() as session:
        manager.release(session, environment, EXECUTION_A, operator="alice")
        assert manager.holder(session, environment) is None


def test_a_released_environment_can_be_acquired_again() -> None:
    """DX-EXE-030 的反面：释放之后下一个人可以进来。"""
    environment = key("recycle")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        add_execution(session, EXECUTION_B, environment)
        manager.acquire(session, environment, EXECUTION_A)
        manager.release(session, environment, EXECUTION_A)
        manager.acquire(session, environment, EXECUTION_B)
    with unit_of_work() as session:
        assert manager.holder(session, environment) == EXECUTION_B


# --- 一环境一行（结构保证） ---------------------------------------------------


def test_the_lock_table_holds_exactly_one_row_per_environment() -> None:
    """DX-EXE-030 的跨项目互斥就建立在这条主键上。"""
    environment = key("one-row")
    with pytest.raises(IntegrityError), unit_of_work() as session:
        session.add(
            EnvironmentLockRow(
                environment_key=environment,
                holder_execution_id=EXECUTION_A,
                acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        session.flush()
        session.add(
            EnvironmentLockRow(
                environment_key=environment,
                holder_execution_id=EXECUTION_B,
                acquired_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
        session.flush()


def test_acquiring_twice_for_the_same_environment_never_adds_a_second_row() -> None:
    environment = key("single-row")
    with unit_of_work() as session:
        add_execution(session, EXECUTION_A, environment)
        manager.acquire(session, environment, EXECUTION_A)
        manager.release(session, environment, EXECUTION_A)
        manager.acquire(session, environment, EXECUTION_A)
        rows = session.execute(
            select(EnvironmentLockRow).where(
                EnvironmentLockRow.environment_key == environment
            )
        ).scalars().all()
    assert len(rows) == 1


def test_the_lock_only_keys_on_the_environment_so_environments_do_not_block_each_other() -> None:
    """锁的粒度：不同环境互不影响。"""
    left, right = key("left"), key("right")
    with unit_of_work() as session:
        manager.acquire(session, left, EXECUTION_A)
        manager.acquire(session, right, EXECUTION_A)
    with unit_of_work() as session:
        assert manager.holder(session, left) == EXECUTION_A
        assert manager.holder(session, right) == EXECUTION_A


def test_only_committing_modes_are_expected_to_take_the_lock() -> None:
    """DX-EXE-030 的模式集合：COMMIT、Fixture、删除、维护。"""
    assert frozenset({"COMMIT", "FIXTURE", "DELETE", "MAINTENANCE"}) == manager.LOCKING_MODES


# --- 两个真实会话的竞争 -------------------------------------------------------


def test_two_sessions_racing_for_one_environment_are_serialised_by_the_advisory_lock() -> None:
    """AC-EXE-04：并发获取只有一个成功。

    第二个线程真的会阻塞在 ``pg_advisory_xact_lock`` 上——这里用 ``pg_locks`` 等到该环境
    挂着两把咨询锁（一把持有、一把等待）才放行第一个事务提交。没有咨询锁的实现会在这里让
    两个都“成功”，所以这条断言不是装饰。
    """
    environment = key("thread-race")
    outcomes: list[str] = []
    a_holds = threading.Event()
    release_a = threading.Event()

    def holder_thread() -> None:
        try:
            with unit_of_work() as session:
                add_execution(session, EXECUTION_A, environment)
                manager.acquire(session, environment, EXECUTION_A)
                a_holds.set()
                release_a.wait(timeout=25)
        except Exception as exc:  # noqa: BLE001 - 线程里如实回报
            outcomes.append(f"a-error:{type(exc).__name__}:{exc}")

    def challenger_thread() -> None:
        if not a_holds.wait(timeout=25):
            outcomes.append("b-never-started")
            return
        try:
            with unit_of_work() as session:
                add_execution(session, EXECUTION_B, environment)
                manager.acquire(session, environment, EXECUTION_B)
                outcomes.append("b-acquired")
        except ConcurrencyError:
            outcomes.append("b-concurrency-error")
        except Exception as exc:  # noqa: BLE001 - 线程里如实回报
            outcomes.append(f"b-error:{type(exc).__name__}:{exc}")

    holder = threading.Thread(target=holder_thread, daemon=True)
    challenger = threading.Thread(target=challenger_thread, daemon=True)
    holder.start()
    challenger.start()

    blocked = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if advisory_lock_count(environment) >= 2:
            blocked = True
            break
        time.sleep(0.01)
    release_a.set()
    challenger.join(timeout=25)
    holder.join(timeout=25)

    assert blocked, "第二个会话没有阻塞在咨询锁上：串行化没有发生"
    assert outcomes == ["b-concurrency-error"], outcomes
    with unit_of_work() as session:
        assert manager.holder(session, environment) == EXECUTION_A
