"""执行器顺序守卫（DX-EXE-037、DX-BR-12）。

守卫的价值在于"前一步没成，后一步进不去"，所以这里测的是**四种类别**：单执行器放行、
前序已生效放行、前序失败或没执行过阻断、以及"看起来完成了但没生效"也阻断。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import new_id, unit_of_work

from factory_dhis2.enums import ExecutionMode, ExecutionStatus, ExecutorType
from factory_dhis2.errors import ExecutionOrderError
from factory_dhis2.execution import sequence
from factory_dhis2.execution.plan import order_executors
from factory_dhis2.models.execution import ExecutionRow

ENVIRONMENT = "seq-env"
HASH = "sha256:" + "a" * 64


def _execution(mode: ExecutionMode, status: ExecutionStatus, *, applied: bool | None) -> str:
    execution_id = new_id("dxexe")
    with unit_of_work() as session:
        session.add(
            ExecutionRow(
                id=execution_id,
                project_id="prj-seq",
                environment_key=ENVIRONMENT,
                executor_type=sequence.executor_of_mode(mode.value),
                mode=mode.value,
                payload_hash=HASH,
                status=status.value,
                applied=applied,
                operator="tester",
            )
        )
    return execution_id


@pytest.fixture(autouse=True)
def _environment(harness: Any) -> None:
    from tests.unit.execution import seed_environment

    seed_environment(harness, environment_key=ENVIRONMENT)


def test_a_single_executor_plan_has_no_predecessors_to_wait_for() -> None:
    """M2 只有 Metadata：它是第一个，守卫不该挡它。"""
    sequence.require_predecessors_applied(
        environment_key=ENVIRONMENT,
        payload_hash=HASH,
        plan_document={"executors": [ExecutorType.METADATA.value]},
        executor=ExecutorType.METADATA.value,
    )


def test_the_order_comes_from_one_place_only() -> None:
    """DX-EXE-037：计划里的排列由固定顺序决定，调用方给乱序也没用。"""
    assert order_executors(
        [
            ExecutorType.APP_UPLOAD.value,
            ExecutorType.TRACKER_DATA.value,
            ExecutorType.DATASTORE.value,
            ExecutorType.METADATA.value,
        ]
    ) == [
        ExecutorType.METADATA.value,
        ExecutorType.DATASTORE.value,
        ExecutorType.TRACKER_DATA.value,
        ExecutorType.APP_UPLOAD.value,
    ]


def test_a_later_executor_is_refused_until_the_metadata_step_has_applied() -> None:
    """前一步没有成功且生效的执行记录时，后一步就是进不去（不猜、不排队）。"""
    with pytest.raises(ExecutionOrderError) as excinfo:
        sequence.require_predecessors_applied(
            environment_key=ENVIRONMENT,
            payload_hash=HASH,
            plan_document={"executors": ["METADATA", ExecutorType.DATASTORE.value]},
            executor=ExecutorType.DATASTORE.value,
        )
    assert excinfo.value.details["pendingPredecessors"] == ["METADATA"]
    assert excinfo.value.details["order"] == ["METADATA", ExecutorType.DATASTORE.value]
    assert excinfo.value.code == "DX_EXECUTION_ORDER"


def test_a_failed_metadata_execution_still_blocks_the_next_step() -> None:
    _execution(ExecutionMode.COMMIT, ExecutionStatus.FAILED, applied=False)
    with pytest.raises(ExecutionOrderError):
        sequence.require_predecessors_applied(
            environment_key=ENVIRONMENT,
            payload_hash=HASH,
            plan_document={"executors": ["METADATA", ExecutorType.DATASTORE.value]},
            executor=ExecutorType.DATASTORE.value,
        )


def test_an_unconfirmed_execution_blocks_the_next_step() -> None:
    """NEEDS_MANUAL_CHECK 不是"成功"：结果没确认之前不得继续往下走。"""
    _execution(ExecutionMode.COMMIT, ExecutionStatus.NEEDS_MANUAL_CHECK, applied=None)
    with pytest.raises(ExecutionOrderError):
        sequence.require_predecessors_applied(
            environment_key=ENVIRONMENT,
            payload_hash=HASH,
            plan_document={"executors": ["METADATA", ExecutorType.DATASTORE.value]},
            executor=ExecutorType.DATASTORE.value,
        )


def test_a_completed_but_not_applied_execution_does_not_count() -> None:
    """``applied`` 为假的"完成"没往环境里放任何东西，不能当成前序已生效。"""
    _execution(ExecutionMode.COMMIT, ExecutionStatus.COMPLETED, applied=False)
    with pytest.raises(ExecutionOrderError):
        sequence.require_predecessors_applied(
            environment_key=ENVIRONMENT,
            payload_hash=HASH,
            plan_document={"executors": ["METADATA", ExecutorType.DATASTORE.value]},
            executor=ExecutorType.DATASTORE.value,
        )


def test_an_applied_metadata_execution_opens_the_next_step() -> None:
    execution_id = _execution(ExecutionMode.COMMIT, ExecutionStatus.COMPLETED, applied=True)
    sequence.require_predecessors_applied(
        environment_key=ENVIRONMENT,
        payload_hash=HASH,
        plan_document={"executors": ["METADATA", ExecutorType.DATASTORE.value]},
        executor=ExecutorType.DATASTORE.value,
    )
    assert execution_id


def test_the_guard_is_environment_and_payload_scoped() -> None:
    """换环境或换载荷就是另一次部署：别的载荷上元数据成功了不算数（DX-BR-02）。"""
    _execution(ExecutionMode.COMMIT, ExecutionStatus.COMPLETED, applied=True)
    with pytest.raises(ExecutionOrderError):
        sequence.require_predecessors_applied(
            environment_key=ENVIRONMENT,
            payload_hash="sha256:" + "b" * 64,
            plan_document={"executors": ["METADATA", ExecutorType.DATASTORE.value]},
            executor=ExecutorType.DATASTORE.value,
        )
