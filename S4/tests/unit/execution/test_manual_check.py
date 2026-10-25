"""人工核实结论与释放环境锁（DX-EXE-031、AC-EXE-05、DX-AC-04）。

这里成立的是“锁只因人工结论而释放”这一条：结论、终态、释放与审计必须是一个原子动作，
而且结论只有 PASSED / FAILED 两种——S4 不接受第三种，也不自己判断。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import (
    AuditQuery,
    NotFoundError,
    ValidationError,
    query_audit,
    unit_of_work,
)

from factory_dhis2.enums import ExecutionMode, ExecutionStatus
from factory_dhis2.envlock import manager as envlock
from factory_dhis2.execution import manual_check, steps
from factory_dhis2.execution.manual_check import resolve_manual_check
from factory_dhis2.models.execution import ExecutionRow
from tests.unit.execution import PROJECT, seed_environment


def _manual_check_execution(harness: Any, *, holder: str | None = None) -> str:
    """造一条 NEEDS_MANUAL_CHECK 的执行，并让它持有环境锁（``harness`` 只用于确保夹具生效）。"""
    assert harness is not None
    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key="env-unit",
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            payload_artifact_id=None,
            payload_hash="sha256:" + "b" * 64,
            idempotency_key="idem-manual-1",
            operator="ops-1",
        )
        steps.set_status(
            session,
            execution,
            ExecutionStatus.NEEDS_MANUAL_CHECK,
            detail="提交后没有拿到响应",
        )
        envlock.acquire(
            session, "env-unit", holder or execution.id, operator="ops-1", note="NEEDS_MANUAL_CHECK"
        )
        return execution.id


def test_passed_outcome_completes_the_execution_and_releases_the_lock(harness: Any) -> None:
    """PASSED → COMPLETED / applied=True，锁释放，审计与状态同一事务（D-11）。"""
    execution_id = _manual_check_execution(harness)

    result = resolve_manual_check(execution_id, "PASSED", "现场核对已生效", "ops-2")

    assert result["executionId"] == execution_id
    assert result["resolved"] is True
    assert result["releasesLock"] is True
    assert result["lockReleased"] is True
    assert result["status"] == ExecutionStatus.COMPLETED.value
    assert result["applied"] is True
    assert result["outcome"] == "PASSED"
    with unit_of_work() as session:
        row = session.get(ExecutionRow, execution_id)
        assert row is not None
        assert row.status == ExecutionStatus.COMPLETED.value
        assert row.applied is True
        assert row.manual_check_outcome == "PASSED"
        assert row.manual_check_note == "现场核对已生效"
        assert row.resolved_by == "ops-2"
        assert envlock.holder(session, "env-unit") is None
    events = query_audit(
        AuditQuery(event_type="dx.manual_check.resolved", object_id=execution_id)
    )
    assert events.items
    assert events.items[0].after is not None
    assert events.items[0].after["outcome"] == "PASSED"


def test_failed_outcome_marks_the_execution_failed_and_still_releases(harness: Any) -> None:
    """FAILED 同样释放锁：人已经确认过，环境不该继续被一次不存在的悬念占住。"""
    execution_id = _manual_check_execution(harness)

    result = resolve_manual_check(execution_id, "FAILED", "确认未生效", "ops-2")

    assert result["status"] == ExecutionStatus.FAILED.value
    assert result["applied"] is False
    with unit_of_work() as session:
        assert envlock.holder(session, "env-unit") is None


def test_unknown_outcome_is_refused_without_touching_anything(harness: Any) -> None:
    """第三种结论就是输入错误：S4 不猜人想说什么（DX-EXE-031）。"""
    execution_id = _manual_check_execution(harness)

    with pytest.raises(ValidationError) as caught:
        resolve_manual_check(execution_id, "MAYBE", "不确定", "ops-2")

    assert caught.value.error_code == "VALIDATION_ERROR"
    assert caught.value.details["allowed"] == ["FAILED", "PASSED"]
    with unit_of_work() as session:
        row = session.get(ExecutionRow, execution_id)
        assert row is not None
        assert row.status == ExecutionStatus.NEEDS_MANUAL_CHECK.value
        assert envlock.holder(session, "env-unit") == execution_id


def test_lowercase_outcome_is_accepted(harness: Any) -> None:
    """取值词汇与 S1 的镜像一致，大小写不敏感只是容错，不改变结论。"""
    execution_id = _manual_check_execution(harness)

    result = resolve_manual_check(execution_id, "passed", "ok", "ops-2")

    assert result["outcome"] == "PASSED"


def test_second_resolution_is_refused(harness: Any) -> None:
    """一次核实就是最终结论：第二次会把锁语义与追溯搅浑。"""
    execution_id = _manual_check_execution(harness)
    resolve_manual_check(execution_id, "PASSED", "第一次", "ops-2")

    with pytest.raises(ValidationError):
        resolve_manual_check(execution_id, "FAILED", "改主意", "ops-3")


def test_unknown_execution_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        resolve_manual_check("dxexe-does-not-exist", "PASSED", "note", "ops-2")


def test_lock_held_by_another_execution_is_not_released(harness: Any) -> None:
    """别人的锁不因为自己核实完就被释放（DX-EXE-030 的持有者语义）。"""
    holder_execution_id = _manual_check_execution(harness)
    with unit_of_work() as session:
        other = steps.create_execution(
            session,
            environment_key="env-unit",
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            idempotency_key="idem-manual-2",
            operator="ops-1",
        )
        steps.set_status(session, other, ExecutionStatus.NEEDS_MANUAL_CHECK)
        other_id = other.id

    result = resolve_manual_check(other_id, "PASSED", "确认", "ops-2")

    assert result["lockReleased"] is False
    with unit_of_work() as session:
        assert envlock.holder(session, "env-unit") == holder_execution_id


def test_resolution_runs_inside_a_callers_transaction(harness: Any) -> None:
    """事务内函数：调用方给它会话时，结论并入调用方的事务（SRS 7.4）。"""
    execution_id = _manual_check_execution(harness)

    with unit_of_work() as session:
        result = manual_check.resolve_manual_check(
            execution_id, "PASSED", "并入外层事务", "ops-2", session=session
        )

    assert result["resolved"] is True
    with unit_of_work() as session:
        assert envlock.holder(session, "env-unit") is None


@pytest.fixture(autouse=True)
def _seeded(harness: Any) -> Any:
    """每个测试都用 harness 夹具起一遍：环境档案与锁表都必须是干净的。"""
    seed_environment(harness, environment_key="env-unit")
    return harness
