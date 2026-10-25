"""维护动作（DX-EXE-013/045、FR-EXE-007、总体 SRS 15.2 MAINTENANCE）。

维护动作没有载荷，但它**仍然是提交类执行**：环境级独占锁、紧急锁定、PRODUCTION 的时间窗
一个都不能少（DX-EXE-030/031、DX-ENV-007/002）。而它的两条边界同样要被证明：只执行计划里
已列明并经批准的项（DX-EXE-045），MVP 只有一项、别的动作一次请求都不发（DX-EXE-013）。
每一项动作的结局都要进报告——「已经生效到哪里」是运维唯一能看见的边界（DX-EXE-046）。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import AuditQuery, query_audit, unit_of_work

from factory_dhis2.artifacts import read_json
from factory_dhis2.enums import CredentialScope, ExecutionMode, ExecutionStatus, StepStatus
from factory_dhis2.errors import (
    Dhis2ImportError,
    Dhis2TimeoutError,
    Dhis2UnreachableError,
    EnvironmentInvalidError,
    EnvironmentLockedError,
    OutsideCommitWindowError,
)
from factory_dhis2.execution import maintenance as maintenance_module
from factory_dhis2.execution import steps
from factory_dhis2.execution.maintenance import MAINTENANCE_RESOURCES, run_maintenance
from factory_dhis2.execution.plan import normalize_maintenance
from tests import window_not_now
from tests.unit.execution import (
    ENVIRONMENT,
    PROJECT,
    FakeClient,
    make_target,
    register_json_artifact,
    seed_environment,
)

CLEAR_CACHE = [{"action": "CLEAR_APP_CACHE"}]


class MaintenanceClient(FakeClient):
    """按脚本决定每个维护动作结局的假实例。"""

    def __init__(
        self,
        target: Any,
        *,
        scope: CredentialScope = CredentialScope.MAINTENANCE,
        objects: dict[tuple[str, str], dict[str, Any]] | None = None,
        reports: dict[str, dict[str, Any]] | None = None,
        expression_verdicts: dict[str, Any] | None = None,
        calls: list[Any] | None = None,
        responses: dict[str, Any] | None = None,
        error: Exception | None = None,
        fail_after: int = 0,
    ) -> None:
        super().__init__(
            target,
            scope=scope,
            objects=objects,
            reports=reports,
            expression_verdicts=expression_verdicts,
            calls=calls,
            responses=responses,
        )
        self.error = error
        self.fail_after = fail_after

    def with_scope(self, scope: CredentialScope) -> MaintenanceClient:
        return type(self)(
            self.target,
            scope=scope,
            objects=self.objects,
            reports=self.reports,
            expression_verdicts=self.expression_verdicts,
            calls=self.calls,
            responses=self.responses,
            error=self.error,
            fail_after=self.fail_after,
        )

    def call(self, logical_name: str, **kwargs: Any) -> Any:
        response = super().call(logical_name, **kwargs)
        if self.error is not None and len(self.calls) > self.fail_after:
            raise self.error
        return response


def _execution(plan_artifact_id: str | None, *, environment_key: str = ENVIRONMENT) -> str:
    with unit_of_work() as session:
        row = steps.create_execution(
            session,
            environment_key=environment_key,
            project_id=PROJECT,
            mode=ExecutionMode.MAINTENANCE,
            operator="ops-1",
        )
        row.plan_artifact_id = plan_artifact_id
        steps.mark_running(session, row)
        return row.id


@pytest.fixture
def maintenance(harness: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """装配环境、一个批准了 CLEAR_APP_CACHE 的计划，以及维护动作的注入点。"""
    seed_environment(harness)
    plan_artifact_id = register_json_artifact(
        {"kind": "EXECUTION_PLAN", "maintenanceActions": CLEAR_CACHE}, "EXECUTION_PLAN"
    )
    target = make_target()
    state: dict[str, Any] = {
        "plan_artifact_id": plan_artifact_id,
        "execution_id": _execution(plan_artifact_id),
        "target": target,
        "client": MaintenanceClient(target),
    }
    monkeypatch.setattr(maintenance_module, "load_target", lambda _key: target)
    monkeypatch.setattr(maintenance_module, "client_for", lambda _target, **_kw: state["client"])
    return state


def _run(state: dict[str, Any], actions: list[dict[str, Any]] | None = None, key: str = "k-maint") -> Any:
    return run_maintenance(
        ENVIRONMENT,
        state["execution_id"],
        actions if actions is not None else CLEAR_CACHE,
        operator="ops-1",
        idempotency_key=key,
    )


# --- 只执行计划里批准过的项 --------------------------------------------------


def test_an_action_approved_by_the_plan_is_executed_with_the_maintenance_scope(
    maintenance: dict[str, Any],
) -> None:
    """DX-EXE-045：计划里列明的动作才执行，并且用 maintenance 凭证范围而不是导入账户。"""
    outcome = _run(maintenance)

    assert outcome.status is ExecutionStatus.COMPLETED, outcome.model_dump()
    assert outcome.applied is True
    call = maintenance["client"].calls[0]
    assert call.logical_name == "maintenance.dataCache"
    assert call.scope is CredentialScope.MAINTENANCE
    assert call.params == {"valueStrategy": "INVALID"}, "清缓存要实例自己回填，不是预填充"
    with unit_of_work() as session:
        from factory_dhis2.envlock import manager as envlock

        row = steps.require(session, maintenance["execution_id"])
        assert row.status == ExecutionStatus.COMPLETED.value
        assert envlock.holder(session, ENVIRONMENT) is None, "维护结束必须释放环境锁"


def test_maintenance_writes_a_report_audit_and_the_step_outcome(
    maintenance: dict[str, Any],
) -> None:
    """DX-EXE-046/013：每个动作的结局都进执行报告与审计，报告是那份动作的事实。"""
    outcome = _run(maintenance)

    assert outcome.report_artifact_id
    document = read_json(outcome.report_artifact_id)
    assert document["kind"] == "EXECUTION_REPORT"
    assert document["mode"] == ExecutionMode.MAINTENANCE.value
    assert document["committing"] is True
    assert document["payload"] is None
    assert document["applied"] is True
    assert [item["action"] for item in document["maintenanceResults"]] == ["CLEAR_APP_CACHE"]
    assert document["maintenanceResults"][0]["result"] == "OK"
    assert document["maintenanceResults"][0]["credentialScope"] == "maintenance"
    assert document["maintenanceResults"][0]["logicalResource"] == MAINTENANCE_RESOURCES["CLEAR_APP_CACHE"]
    step = next(item for item in outcome.steps if item.step == "MAINTENANCE")
    assert step.status == StepStatus.COMPLETED.value
    events = query_audit(
        AuditQuery(event_type="dx.maintenance.executed", object_id=maintenance["execution_id"])
    )
    assert events.items[0].after["requested"] == ["CLEAR_APP_CACHE"]
    assert events.items[0].after["credentialScope"] == "maintenance"
    assert events.items[0].after["results"][0]["result"] == "OK"


def test_an_action_not_listed_in_the_plan_is_refused(maintenance: dict[str, Any]) -> None:
    """DX-EXE-045：计划没列明的动作是越权，一次 DHIS2 调用都不发。"""
    other_plan = register_json_artifact(
        {
            "kind": "EXECUTION_PLAN",
            "maintenanceActions": [{"action": "REBUILD_SEARCH_INDEX", "credentialScope": "maintenance"}],
        },
        "EXECUTION_PLAN",
    )
    maintenance["execution_id"] = _execution(other_plan)

    with pytest.raises(EnvironmentInvalidError) as excinfo:
        _run(maintenance, key="k-extra")

    assert excinfo.value.details["requested"] == ["CLEAR_APP_CACHE"]
    assert excinfo.value.details["planned"] == ["REBUILD_SEARCH_INDEX"]
    assert excinfo.value.details["rule"] == "DX-EXE-045"
    assert maintenance["client"].calls == []


def test_an_empty_plan_means_maintenance_is_off_by_default(maintenance: dict[str, Any]) -> None:
    """DX-EXE-013：计划里这一段是空清单就是「默认关闭」，请求任何动作都被拒。"""
    empty_plan = register_json_artifact({"kind": "EXECUTION_PLAN", "maintenanceActions": []}, "EXECUTION_PLAN")
    maintenance["execution_id"] = _execution(empty_plan)

    with pytest.raises(EnvironmentInvalidError) as excinfo:
        _run(maintenance, key="k-off")

    assert excinfo.value.details["planned"] == []
    assert maintenance["client"].calls == []


def test_an_execution_without_a_plan_approves_nothing(maintenance: dict[str, Any]) -> None:
    """DX-EXE-045：没有执行计划就没有任何已批准的维护动作。"""
    maintenance["execution_id"] = _execution(None)

    with pytest.raises(EnvironmentInvalidError) as excinfo:
        _run(maintenance, key="k-noplan")

    assert excinfo.value.details["rule"] == "DX-EXE-045"


def test_an_unsupported_action_is_refused_before_the_plan_is_read(maintenance: dict[str, Any]) -> None:
    """DX-EXE-013：MVP 之外的动作在归一化阶段就拒绝，连计划都不去读。"""
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        _run(maintenance, [{"action": "GENERATE_ANALYTICS_TABLE"}], key="k-unsupported")

    assert excinfo.value.details["supported"] == ["CLEAR_APP_CACHE"]
    assert maintenance["client"].calls == []


def test_a_maintenance_call_for_another_environment_is_refused(maintenance: dict[str, Any]) -> None:
    """DX-BR-02：维护调用必须点名它要动的那个环境，不能拿着别的环境的执行 ID。"""
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        run_maintenance(
            "env-someone-else",
            maintenance["execution_id"],
            CLEAR_CACHE,
            operator="ops-1",
            idempotency_key="k-wrong-env",
        )

    assert excinfo.value.details["executionEnvironmentKey"] == ENVIRONMENT
    assert maintenance["client"].calls == []


# --- 提交类执行的两道门 ------------------------------------------------------


def test_emergency_lock_blocks_maintenance(maintenance: dict[str, Any], harness: Any) -> None:
    """DX-ENV-007 / DX-BR-11：紧急锁定期间连清缓存这种动作也不做。"""
    seed_environment(harness, emergency_lock=True, lock_reason="演练")

    with pytest.raises(EnvironmentLockedError):
        _run(maintenance, key="k-locked")

    assert maintenance["client"].calls == []


def test_production_outside_the_commit_window_blocks_maintenance(
    maintenance: dict[str, Any], harness: Any
) -> None:
    """DX-ENV-002：维护动作同样改变环境，所以 PRODUCTION 的时间窗对它也成立。"""
    from sqlalchemy import select

    from factory_dhis2.models.environment import EnvironmentRow

    seed_environment(harness, commit_windows=[window_not_now()])
    with unit_of_work() as session:
        row = session.execute(
            select(EnvironmentRow).where(EnvironmentRow.key == ENVIRONMENT)
        ).scalars().one()
        row.environment_class = "PRODUCTION"

    with pytest.raises(OutsideCommitWindowError):
        _run(maintenance, key="k-window")

    assert maintenance["client"].calls == []


def test_maintenance_takes_the_environment_lock_for_its_whole_duration(
    maintenance: dict[str, Any], harness: Any
) -> None:
    """AC-EXE-04：维护动作进行期间，该环境的其它提交类执行被独占锁挡住。"""
    from factory_dhis2.envlock import manager as envlock

    holders: list[str | None] = []
    real_call = maintenance["client"].call

    def peeking_call(logical_name: str, **kwargs: Any) -> Any:
        with unit_of_work() as session:
            holders.append(envlock.holder(session, ENVIRONMENT))
        return real_call(logical_name, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(maintenance["client"], "call", peeking_call)
    try:
        outcome = _run(maintenance, key="k-lock-held")
    finally:
        monkey.undo()

    assert holders == [maintenance["execution_id"]]
    assert outcome.status is ExecutionStatus.COMPLETED


# --- 失败与结果不明 ----------------------------------------------------------


def test_a_refused_action_is_reported_and_stops_the_remaining_ones(
    maintenance: dict[str, Any],
) -> None:
    """DX-EXE-046/013：第一个失败之后不再往下做——报告里的「已生效范围」要说得清。"""
    maintenance["client"] = MaintenanceClient(
        maintenance["target"], error=Dhis2ImportError("instance refused", details={})
    )

    outcome = _run(maintenance, key="k-failed")

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.applied is False
    assert "CLEAR_APP_CACHE" in (outcome.detail or "")
    document = read_json(str(outcome.report_artifact_id))
    assert [item["result"] for item in document["maintenanceResults"]] == ["FAILED"]
    assert document["maintenanceResults"][0]["errorCode"] == "DHIS2_IMPORT_ERROR"
    assert document["maintenanceResults"][0]["detail"] == "instance refused"
    assert document["failure"].startswith("CLEAR_APP_CACHE:")
    step = next(item for item in outcome.steps if item.step == "MAINTENANCE")
    assert step.status == StepStatus.FAILED.value
    assert step.detail["failure"]
    with unit_of_work() as session:
        from factory_dhis2.envlock import manager as envlock

        assert envlock.holder(session, ENVIRONMENT) is None, "失败也要释放环境锁"
        events = query_audit(
            AuditQuery(event_type="dx.maintenance.executed", object_id=maintenance["execution_id"])
        )
        assert events.items[0].after["result"] == "FAILED"


def test_an_unconfirmed_action_keeps_the_lock_and_asks_for_manual_check(
    maintenance: dict[str, Any],
) -> None:
    """DX-EXE-031/035：请求发出去了却没有结论 = 不知道，锁保持，交人工核实。"""
    maintenance["client"] = MaintenanceClient(
        maintenance["target"], error=Dhis2TimeoutError("no answer", details={})
    )

    outcome = _run(maintenance, key="k-unconfirmed")

    assert outcome.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert outcome.applied is None
    assert outcome.report_artifact_id is None, "还没有结论，就不该有一份看起来像结论的报告"
    with unit_of_work() as session:
        from factory_dhis2.envlock import manager as envlock

        row = steps.require(session, maintenance["execution_id"])
        assert row.status == ExecutionStatus.NEEDS_MANUAL_CHECK.value
        assert row.completed_at is None, "待核实的执行没有结束"
        assert envlock.holder(session, ENVIRONMENT) == maintenance["execution_id"]
        events = query_audit(
            AuditQuery(event_type="dx.commit.needs_manual_check", object_id=maintenance["execution_id"])
        )
        assert events.items[0].after["lockHeld"] is True
    step = next(item for item in outcome.steps if item.step == "MAINTENANCE")
    assert step.status == StepStatus.RUNNING.value
    assert step.detail["unconfirmed"] is True


def test_a_connection_failure_is_also_an_unconfirmed_outcome(maintenance: dict[str, Any]) -> None:
    """DX-EXE-035：``DHIS2_UNREACHABLE`` 与超时同属「结果不明」，不是「没做成」。"""
    maintenance["client"] = MaintenanceClient(
        maintenance["target"], error=Dhis2UnreachableError("connection dropped", details={})
    )

    outcome = _run(maintenance, key="k-unreachable")

    assert outcome.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert outcome.applied is None


def test_an_unconfirmed_maintenance_replays_the_same_key_without_a_second_request(
    maintenance: dict[str, Any],
) -> None:
    """AC-EXE-05：「无法确认」本身就是本次调用的结论——同键重试拿到同一份待核实状态。"""
    maintenance["client"] = MaintenanceClient(
        maintenance["target"], error=Dhis2TimeoutError("no answer", details={})
    )

    first = _run(maintenance, key="k-retry")
    calls_before = len(maintenance["client"].calls)
    second = _run(maintenance, key="k-retry")

    assert second.status is first.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert len(maintenance["client"].calls) == calls_before


def test_a_repeated_key_replays_the_first_conclusion(maintenance: dict[str, Any]) -> None:
    """DX-EXE-004：清缓存可以重做，但同幂等键的两次调用必须给出同一个结论。"""
    first = _run(maintenance, key="k-replay")
    calls_before = len(maintenance["client"].calls)

    second = _run(maintenance, key="k-replay")

    assert second.execution_id == first.execution_id
    assert second.report_artifact_id == first.report_artifact_id
    assert len(maintenance["client"].calls) == calls_before


# --- 动作清单的归一化（DX-EXE-013 的口径只有一处）----------------------------


def test_normalize_maintenance_accepts_both_key_spellings() -> None:
    """DX-EXE-013：``action`` 与 ``name`` 是同一段的两个写法，归一后都带凭证范围。"""
    assert normalize_maintenance([{"action": "CLEAR_APP_CACHE"}, {"name": "CLEAR_APP_CACHE"}]) == [
        {"action": "CLEAR_APP_CACHE", "credentialScope": "maintenance"},
        {"action": "CLEAR_APP_CACHE", "credentialScope": "maintenance"},
    ]


def test_normalize_maintenance_is_off_when_nothing_is_listed() -> None:
    """DX-EXE-013：没人列出动作就是空清单——S4 不自己判断「顺手清一下缓存」。"""
    assert normalize_maintenance(None) == []
    assert normalize_maintenance([]) == []
