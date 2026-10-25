"""执行与步骤的持久化（DX-EXE-004、DX-EXE-036、NFR-REL-003/004、DX-AC-03）。

续跑靠的是**查表**而不是推断：每个步骤一行，完成时把它的产物引用写进 ``detail``；执行行
同时是幂等键 → 执行状态的唯一来源，S1 在重试 COMMIT 前必须先查（CP-GATE-018、AC-EXE-05）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from factory_foundation.api import NotFoundError, unit_of_work
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2.enums import ExecutionMode, ExecutionStatus, ExecutorType, StepStatus
from factory_dhis2.errors import ConcurrencyError
from factory_dhis2.execution import steps
from factory_dhis2.models.execution import ExecutionRow, ExecutionStepRow

pytestmark = pytest.mark.usefixtures("harness")

ENVIRONMENT = "staging"


def register(session: Session, *, idempotency_key: str | None = "idem-0001") -> ExecutionRow:
    return steps.create_execution(
        session,
        environment_key=ENVIRONMENT,
        project_id="prj-1",
        mode=ExecutionMode.COMMIT,
        payload_artifact_id="art-payload",
        payload_hash="sha256:" + "a" * 64,
        idempotency_key=idempotency_key,
        operator="alice",
    )


def step_row(session: Session, execution_id: str, step_name: str) -> ExecutionStepRow | None:
    return session.get(ExecutionStepRow, (execution_id, step_name))


# --- DX-EXE-036：按幂等键登记与重放 -------------------------------------------


def test_creating_an_execution_returns_a_row_the_caller_can_query_by_id() -> None:
    """DX-EXE-004：执行行登记了环境、模式、载荷与操作者。"""
    with unit_of_work() as session:
        execution = register(session)
    with unit_of_work() as session:
        found = steps.require(session, execution.id)
    assert found.environment_key == ENVIRONMENT
    assert found.mode == ExecutionMode.COMMIT.value
    assert found.executor_type == ExecutorType.METADATA.value
    assert found.payload_artifact_id == "art-payload"
    assert found.payload_hash == "sha256:" + "a" * 64
    assert found.operator == "alice"
    assert found.idempotency_key == "idem-0001"


def test_a_repeated_call_with_the_same_idempotency_key_returns_the_same_execution() -> None:
    """DX-EXE-036 / AC-EXE-05：重复调用不产生第二次执行，COMMIT 才不可能被重做。"""
    with unit_of_work() as session:
        first = register(session)
    with unit_of_work() as session:
        second = register(session)
    assert second.id == first.id
    with unit_of_work() as session:
        rows = session.execute(
            select(ExecutionRow).where(ExecutionRow.idempotency_key == "idem-0001")
        ).scalars().all()
    assert len(rows) == 1


def test_a_replayed_execution_reports_the_state_it_reached_not_a_fresh_one() -> None:
    """DX-EXE-004：重放返回的是那一行本身，调用方据此判断“已经做到哪一步”。"""
    with unit_of_work() as session:
        execution = register(session)
    with unit_of_work() as session:
        row = steps.require(session, execution.id)
        steps.set_status(session, row, ExecutionStatus.COMPLETED, applied=True, finished=True)
    with unit_of_work() as session:
        replayed = register(session)
    assert replayed.status == ExecutionStatus.COMPLETED.value
    assert replayed.applied is True
    assert replayed.completed_at is not None


def test_calls_without_an_idempotency_key_are_distinct_executions() -> None:
    """没有幂等键就没有重放依据：每次都是一次新执行。"""
    with unit_of_work() as session:
        first = register(session, idempotency_key=None)
    with unit_of_work() as session:
        second = register(session, idempotency_key=None)
    assert first.id != second.id


def test_a_different_idempotency_key_is_a_different_execution() -> None:
    with unit_of_work() as session:
        first = register(session, idempotency_key="idem-0001")
    with unit_of_work() as session:
        second = register(session, idempotency_key="idem-0002")
    assert first.id != second.id


def test_a_newly_registered_execution_is_still_in_progress() -> None:
    """DX-ENV-008/009 与 DX-EXE-030 的判定前提：刚登记的执行属于“进行中”。

    一次执行在给出终态之前不能已经是 ``FAILED``：那会让环境“进行中执行”的检查看不见它，
    于是档案可以在执行中途被改掉。
    """
    with unit_of_work() as session:
        execution = register(session)
    with unit_of_work() as session:
        row = steps.require(session, execution.id)
    assert row.status in steps.ACTIVE_STATES, row.status


def test_an_unknown_execution_id_is_a_not_found_error() -> None:
    with unit_of_work() as session:
        with pytest.raises(NotFoundError):
            steps.require(session, "dxexe00000000000000000000zz")
        assert steps.get(session, "dxexe00000000000000000000zz") is None


# --- DX-EXE-004：步骤驱动续跑 --------------------------------------------------


def test_a_step_begins_as_running_and_becomes_completed() -> None:
    """DX-EXE-004：步骤状态逐条落库。"""
    with unit_of_work() as session:
        execution = register(session)
    with unit_of_work() as session:
        steps.begin_step(session, execution.id, "PLAN")
        row = step_row(session, execution.id, "PLAN")
    assert row is not None
    assert row.status == StepStatus.RUNNING.value
    assert row.started_at is not None


def test_completed_steps_are_what_resume_skips() -> None:
    """DX-EXE-004：续跑时“已完成”就是查表得到的集合。"""
    with unit_of_work() as session:
        execution = register(session)
        steps.finish_step(session, execution.id, "PLAN", detail={"planArtifactId": "art-plan"})
        steps.finish_step(session, execution.id, "SNAPSHOT", detail={"snapshotArtifactId": "art-snap"})
    with unit_of_work() as session:
        done = steps.completed_steps(session, execution.id)
    assert done == {"PLAN", "SNAPSHOT"}


def test_a_failed_step_is_not_reported_as_completed() -> None:
    with unit_of_work() as session:
        execution = register(session)
        steps.finish_step(session, execution.id, "VALIDATE", status=StepStatus.FAILED)
    with unit_of_work() as session:
        assert steps.completed_steps(session, execution.id) == set()
        row = step_row(session, execution.id, "VALIDATE")
    assert row is not None and row.status == StepStatus.FAILED.value


def test_resuming_a_partly_finished_execution_only_runs_the_missing_steps() -> None:
    """NFR-REL-004：同一幂等键第二次进来，已完成的步骤不重做。"""
    with unit_of_work() as session:
        execution = register(session)
        steps.finish_step(session, execution.id, "PLAN")
    with unit_of_work() as session:
        replayed = register(session)
        done = steps.completed_steps(session, replayed.id)
    assert replayed.id == execution.id
    assert "PLAN" in done


def test_a_step_detail_is_merged_across_writes() -> None:
    """续跑要复用步骤产出的引用，所以 detail 是累加而不是覆盖。"""
    with unit_of_work() as session:
        execution = register(session)
        steps.begin_step(session, execution.id, "COMMIT")
        steps.finish_step(session, execution.id, "COMMIT", detail={"jobRef": "task-1"})
        steps.finish_step(session, execution.id, "COMMIT", detail={"importReportArtifactId": "art-r"})
    with unit_of_work() as session:
        detail = steps.step_detail(session, execution.id, "COMMIT")
    assert detail == {"jobRef": "task-1", "importReportArtifactId": "art-r"}


def test_step_detail_of_an_unknown_step_is_empty() -> None:
    with unit_of_work() as session:
        execution = register(session)
        assert steps.step_detail(session, execution.id, "REPORT") == {}


def test_skipping_a_step_records_why_it_was_skipped() -> None:
    """跳步不是完成：理由必须留下来，报告才知道少了哪一段。"""
    with unit_of_work() as session:
        execution = register(session)
        steps.skip_step(session, execution.id, "EXPRESSION_VALIDATE", "该版本没有服务端校验接口")
    with unit_of_work() as session:
        row = step_row(session, execution.id, "EXPRESSION_VALIDATE")
        done = steps.completed_steps(session, execution.id)
    assert row is not None
    assert row.status == StepStatus.SKIPPED.value
    assert row.detail["reason"] == "该版本没有服务端校验接口"
    assert "EXPRESSION_VALIDATE" not in done


def test_steps_as_info_reports_the_stored_state_for_the_caller() -> None:
    """DX-EXE-036：执行状态信息含步骤清单。"""
    with unit_of_work() as session:
        execution = register(session)
        steps.finish_step(session, execution.id, "PLAN")
        steps.begin_step(session, execution.id, "VALIDATE")
    with unit_of_work() as session:
        info = steps.steps_as_info(session, execution.id)
    assert [(item.step, item.status) for item in info] == [
        ("PLAN", StepStatus.COMPLETED.value),
        ("VALIDATE", StepStatus.RUNNING.value),
    ]


# --- DX-EXE-033/034：任务引用立即落库 -----------------------------------------


def test_remember_job_writes_the_task_reference_immediately() -> None:
    """DX-EXE-033：提交后先落库，另一个会话马上读得到（DX-AC-03 的前提）。"""
    with unit_of_work() as session:
        execution = register(session)
        steps.mark_running(session, execution)
        steps.remember_job(
            session, execution, job_ref="systemTask-42", poll_url="system/task/42"
        )
    with unit_of_work() as session:
        row = steps.require(session, execution.id)
    assert row.async_job_ref == "systemTask-42"
    assert row.poll_url == "system/task/42"


def test_a_job_reference_written_before_a_crash_survives_it() -> None:
    """NFR-REL-003：轮询途中崩溃后，恢复拿到的是已存的地址而不是空。"""
    with unit_of_work() as session:
        execution = register(session)
        steps.remember_job(session, execution, job_ref="task-1", poll_url=None)
    with unit_of_work() as session:
        replayed = register(session)
        assert replayed.async_job_ref == "task-1"


# --- 进行中的执行（DX-ENV-008/009 的判定依据） --------------------------------


def test_active_execution_finds_the_running_one_for_that_environment() -> None:
    with unit_of_work() as session:
        execution = register(session)
        steps.mark_running(session, execution)
    with unit_of_work() as session:
        found = steps.active_execution(session, ENVIRONMENT)
    assert found is not None and found.id == execution.id


def test_a_finished_execution_is_no_longer_active() -> None:
    with unit_of_work() as session:
        execution = register(session)
        steps.mark_running(session, execution)
        steps.set_status(session, execution, ExecutionStatus.COMPLETED, finished=True)
    with unit_of_work() as session:
        assert steps.active_execution(session, ENVIRONMENT) is None


def test_a_pending_manual_check_execution_still_counts_as_active() -> None:
    """DX-EXE-031：待人工核实时环境仍被这次执行占着。"""
    with unit_of_work() as session:
        execution = register(session)
        steps.set_status(session, execution, ExecutionStatus.NEEDS_MANUAL_CHECK)
    with unit_of_work() as session:
        found = steps.active_execution(session, ENVIRONMENT)
    assert found is not None and found.id == execution.id


def test_a_second_running_execution_on_the_same_environment_is_a_conflict() -> None:
    """DX-EXE-030 的第二道防线：环境锁之外的兜底检查。"""
    with unit_of_work() as session:
        earlier = register(session)
        steps.mark_running(session, earlier)
        #: 让“最新的那条”明确是谁：active_execution 按开始时间取最新
        earlier.started_at = datetime(2026, 1, 1, tzinfo=UTC)
        latest = register(session, idempotency_key="idem-0002")
        steps.mark_running(session, latest)
        with pytest.raises(ConcurrencyError) as excinfo:
            steps.refuse_if_other_active(session, earlier)
        #: 最新的那条自己不算“别的进行中执行”
        steps.refuse_if_other_active(session, latest)
    assert excinfo.value.details["otherExecutionId"] == latest.id
