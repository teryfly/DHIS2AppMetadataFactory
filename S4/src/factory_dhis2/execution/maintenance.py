"""维护动作（DX-EXE-013 / DX-EXE-045、FR-EXE-007、总体 SRS 15.2 MAINTENANCE）。

维护动作与元数据载荷无关，但它**仍然是提交类执行**：它会改变环境状态，所以
``ExecutionMode.MAINTENANCE ∈ COMMITTING_MODES`` —— 环境级独占锁、紧急锁定检查一个都不能少
（DX-EXE-030/031、DX-ENV-007、DX-BR-11）。

三条边界：

* **默认关闭，且只能执行计划里已列明并经批准的项**（DX-EXE-013/045）。这里不做“要不要
  顺手清一下缓存”的判断：S4 不产生业务判断（总体 SRS 4.4）；
* MVP 只有一项：清理应用缓存。分析表生成之类延后，请求它就是
  ``DX_ENVIRONMENT_INVALID``（与 :func:`execution.plan.normalize_maintenance` 同一个口径，
  不另立一套拒绝方式）；
* 用 **maintenance 凭证范围**（DX-EXE-045、总体 SRS 21.3）：清缓存的账户不该是导入账户。

本函数没有载荷，因此不走 :func:`execution.context.load_context`（那里必须有包）：装配
环境的活由 :func:`environment.loader.build_target` 完成，与它同一条链路、同一个
``EnvironmentTarget``，凭证仍旧只在内存里出现（DX-BR-03）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import external_io, idempotency, structure_hash, unit_of_work

from factory_dhis2 import audit
from factory_dhis2.artifacts import read_json, register_staged, stage_document
from factory_dhis2.capability.runtime_profile import apply_environment_capability
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.dto.execution import ExecutionOutcome
from factory_dhis2.enums import (
    COMMITTING_MODES,
    CredentialScope,
    EnvironmentClass,
    ExecutionMode,
    ExecutionStatus,
    MaintenanceAction,
    StepName,
    StepStatus,
)
from factory_dhis2.environment.loader import build_target
from factory_dhis2.environment.protection import get_protection, reject_if_locked
from factory_dhis2.envlock import manager as envlock
from factory_dhis2.errors import (
    Dhis2Error,
    EnvironmentInvalidError,
    OutsideCommitWindowError,
)
from factory_dhis2.execution import steps
from factory_dhis2.execution.plan import normalize_maintenance
from factory_dhis2.registries import IdemScope
from factory_dhis2.target import EnvironmentTarget

#: DX-EXE-013：MVP 唯一支持的维护动作 → 逻辑资源名（端点由 Profile 解析，DX-CLI-005）
MAINTENANCE_RESOURCES: dict[str, str] = {
    MaintenanceAction.CLEAR_APP_CACHE.value: "maintenance.dataCache",
}

#: 清理应用缓存时传给该端点的参数值：让实例自己重新填充缓存，而不是预填充一份新的
_VALUE_STRATEGY = "INVALID"

#: 请求发出后拿不到响应 = 结果不明（与 COMMIT 同一处理方式，DX-EXE-031/035）
_INDETERMINATE_ERRORS: frozenset[str] = frozenset(
    {"DHIS2_TIMEOUT", "DHIS2_UNREACHABLE", "DHIS2_TASK_LOST"}
)


@external_io
def run_maintenance(
    environment_key: str,
    execution_id: str,
    actions: list[dict[str, Any]],
    *,
    operator: str,
    idempotency_key: str,
) -> ExecutionOutcome:
    """执行计划中已批准的维护动作（SRS 6.5 尾步、DX-EXE-045）。"""
    digest = structure_hash(
        {
            "operation": "run_maintenance",
            "environmentKey": environment_key,
            "executionId": execution_id,
            "actions": [str(item.get("action") if isinstance(item, dict) else item) for item in actions],
            "operator": operator,
        }
    )
    begun = idempotency.begin(IdemScope.EXECUTION, idempotency_key, digest)
    if begun.already_completed and begun.first_result:
        # 清缓存是可重复的，但结论必须与第一次一致，S1 才能把同一幂等键的两次调用看成一回事
        return ExecutionOutcome.model_validate(begun.first_result)

    # DX-EXE-013：不在支持清单里的动作在这里就被拒绝，一次 DHIS2 调用都不会发
    requested = normalize_maintenance(actions)

    with unit_of_work() as session:
        execution = steps.require(session, execution_id)
        plan_artifact_id = execution.plan_artifact_id
        execution_environment = execution.environment_key
    if execution_environment != environment_key:
        raise EnvironmentInvalidError(
            f"execution {execution_id} belongs to environment "
            f"{execution_environment!r}, not {environment_key!r}: a maintenance call must "
            "name the environment it is bound to (DX-BR-02)",
            details={
                "environmentKey": environment_key,
                "executionEnvironmentKey": execution_environment,
                "executionId": execution_id,
            },
        )
    # 计划文档是产物：读它在事务外（00 文档 6.2）
    _require_approved(plan_artifact_id, execution_id, requested)

    with unit_of_work() as session:
        # 提交类执行的两道门：紧急锁定与（PRODUCTION）允许提交的时间窗
        reject_if_locked(session, environment_key, "MAINTENANCE")
        protection = get_protection(environment_key, session=session)
        if (
            protection.environment_class == EnvironmentClass.PRODUCTION.value
            and not protection.within_commit_window
        ):
            raise OutsideCommitWindowError(
                f"environment {environment_key!r} is outside its allowed commit window; "
                "maintenance actions change the environment too (DX-ENV-002)",
                details={
                    "environmentKey": environment_key,
                    "windows": protection.commit_windows,
                    "timezone": protection.timezone,
                },
            )
        envlock.acquire(session, environment_key, execution_id, operator=operator, note="MAINTENANCE")
        steps.begin_step(session, execution_id, StepName.MAINTENANCE)
        plan_maintenance = [str(item["action"]) for item in requested]

    target = load_target(environment_key)
    try:
        results, failure = _execute(target, requested)
    except _UnconfirmedMaintenance as unconfirmed:
        # 请求发出去了但没拿到结论：这不是“失败”，而是“不知道”。锁保持，交人工核实
        return _needs_manual_check(
            environment_key=environment_key,
            execution_id=execution_id,
            operator=operator,
            idempotency_key=idempotency_key,
            action=unconfirmed.action,
            reason=unconfirmed.error.message,
        )

    applied = failure is None
    status = ExecutionStatus.COMPLETED if applied else ExecutionStatus.FAILED
    with unit_of_work() as session:
        project_of_execution = steps.require(session, execution_id).project_id
        steps.finish_step(
            session,
            execution_id,
            StepName.MAINTENANCE,
            status=StepStatus.COMPLETED if applied else StepStatus.FAILED,
            detail={"actions": results, "failure": failure},
        )
        step_infos = steps.steps_as_info(session, execution_id)

    report_document = _report_document(
        target=target,
        execution_id=execution_id,
        environment_key=environment_key,
        project_id=project_of_execution,
        status=status,
        applied=applied,
        results=results,
        failure=failure,
        step_infos=step_infos,
        operator=operator,
    )
    # 暂存在事务外；登记、终态、释放锁与审计落在同一段短事务里（00 文档 6.2、D-11）
    handle = stage_document(
        report_document,
        project_id=project_of_execution or f"env-{environment_key}",
        artifact_type="EXECUTION_REPORT",
    )
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        info = register_staged(
            handle,
            project_id=project_of_execution or f"env-{environment_key}",
            artifact_type="EXECUTION_REPORT",
            session=session,
        )
        report_artifact_id = info.id
        row.report_artifact_id = report_artifact_id
        steps.set_status(
            session,
            row,
            status,
            applied=applied,
            detail=None if applied else f"维护动作未成功：{failure}",
            finished=True,
        )
        envlock.release(
            session,
            environment_key,
            execution_id,
            operator=operator,
            reason="COMPLETED" if applied else "FAILED",
        )
        audit.record(
            audit.MAINTENANCE_EXECUTED,
            object_type="Execution",
            object_id=execution_id,
            project_id=row.project_id,
            after={
                "environmentKey": environment_key,
                "credentialScope": CredentialScope.MAINTENANCE.value,
                "requested": plan_maintenance,
                "results": results,
                "result": status.value,
                "reportArtifactId": report_artifact_id,
            },
            operator=operator,
            session=session,
        )
        step_infos = steps.steps_as_info(session, execution_id)

    outcome = ExecutionOutcome(
        execution_id=execution_id,
        status=status,
        report_artifact_id=report_artifact_id,
        applied=applied,
        environment_key=environment_key,
        steps=step_infos,
        detail=None if applied else f"维护动作未成功：{failure}",
    )
    idempotency.complete(IdemScope.EXECUTION, idempotency_key, outcome.model_dump(mode="json"))
    return outcome


def _require_approved(
    plan_artifact_id: str | None, execution_id: str, requested: list[dict[str, Any]]
) -> None:
    """DX-EXE-045：只执行计划里已列明的维护动作。

    计划里没这一段（``maintenanceActions`` 为空清单）就是“默认关闭”（DX-EXE-013），此时
    请求任何动作都是越权；计划读不到（产物缺失）同样拒绝，不靠“大概批准过了”放行。
    """
    if plan_artifact_id is None:
        raise EnvironmentInvalidError(
            f"execution {execution_id} has no execution plan, so no maintenance action is "
            f"approved for it (DX-EXE-045)",
            details={
                "executionId": execution_id,
                "requested": [str(item["action"]) for item in requested],
                "rule": "DX-EXE-045",
            },
        )
    plan_document = read_json(plan_artifact_id)
    planned = {
        str(item.get("action"))
        for item in (plan_document.get("maintenanceActions") or [])
        if isinstance(item, dict)
    }
    missing = [str(item["action"]) for item in requested if str(item["action"]) not in planned]
    if missing:
        raise EnvironmentInvalidError(
            f"maintenance action(s) {missing} are not listed in the approved plan for "
            f"execution {execution_id} (DX-EXE-013/045)",
            details={
                "executionId": execution_id,
                "planArtifactId": plan_artifact_id,
                "requested": missing,
                "planned": sorted(planned),
                "rule": "DX-EXE-045",
            },
        )


def client_for(
    target: EnvironmentTarget, *, scope: CredentialScope = CredentialScope.MAINTENANCE
) -> Dhis2Client:
    """DHIS2 Client 的装配点：也是测试注入点（同一环境换一个范围就是换一个账户）。"""
    return Dhis2Client(target, scope=scope)


def load_target(environment_key: str) -> EnvironmentTarget:
    """装配环境目标：require_probe 与执行类操作一致（DX-CAP-003 禁止猜测端点）。"""
    with unit_of_work() as session:
        target = build_target(session, environment_key, require_probe=True)
    return apply_environment_capability(target)


def _execute(
    target: EnvironmentTarget, requested: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str | None]:
    """逐个执行维护动作；第一个失败就停下，后面的动作不再执行。

    维护动作之间没有依赖关系，但一次已经失败的维护里继续往后做，报告里的“已生效范围”就
    说不清了（DX-EXE-046）。
    """
    # maintenance 范围（DX-EXE-045）；Client 按逻辑资源名解析端点（DX-CLI-005）
    client = client_for(target)
    results: list[dict[str, Any]] = []
    for item in requested:
        action = str(item["action"])
        resource = MAINTENANCE_RESOURCES[action]
        try:
            response = client.call(resource, params={"valueStrategy": _VALUE_STRATEGY})
        except Dhis2Error as exc:
            if _is_indeterminate(exc):
                raise _UnconfirmedMaintenance(action=action, error=exc) from exc
            results.append(
                {
                    "action": action,
                    "logicalResource": resource,
                    "credentialScope": CredentialScope.MAINTENANCE.value,
                    "result": "FAILED",
                    "errorCode": exc.error_code,
                    "detail": exc.message,
                }
            )
            return results, f"{action}: {exc.message}"
        results.append(
            {
                "action": action,
                "logicalResource": resource,
                "credentialScope": CredentialScope.MAINTENANCE.value,
                "result": "OK",
                "httpStatus": response.status,
            }
        )
    return results, None


class _UnconfirmedMaintenance(Exception):
    """维护请求已发出但没有结论：锁保持，交人工核实（DX-EXE-031）。"""

    def __init__(self, *, action: str, error: Dhis2Error) -> None:
        super().__init__(f"maintenance action {action} did not return a conclusion")
        self.action = action
        self.error = error


def _is_indeterminate(error: Dhis2Error) -> bool:
    return error.error_code in _INDETERMINATE_ERRORS


def _report_document(
    *,
    target: EnvironmentTarget,
    execution_id: str,
    environment_key: str,
    project_id: str | None,
    status: ExecutionStatus,
    applied: bool | None,
    results: list[dict[str, Any]],
    failure: str | None,
    step_infos: list[Any],
    operator: str,
) -> dict[str, Any]:
    """EXECUTION_REPORT 文档（DX-EXE-046）：维护动作没有载荷与 ImportReport，报告就是这些。"""
    return {
        "schemaVersion": 1,
        "kind": "EXECUTION_REPORT",
        "executionId": execution_id,
        "environmentKey": environment_key,
        "environmentClass": target.environment_class.value,
        "dhis2Version": target.dhis2_version,
        "projectKey": project_id,
        "mode": ExecutionMode.MAINTENANCE.value,
        "committing": ExecutionMode.MAINTENANCE in COMMITTING_MODES,
        "payload": None,
        "status": status.value,
        "applied": applied,
        "steps": [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            for item in step_infos
        ],
        "maintenanceResults": results,
        "failure": failure,
        "artifacts": {"importReport": None, "snapshot": None},
        "operator": operator,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _needs_manual_check(
    *,
    environment_key: str,
    execution_id: str,
    operator: str,
    idempotency_key: str,
    action: str,
    reason: str,
) -> ExecutionOutcome:
    """结果不明的维护：``NEEDS_MANUAL_CHECK`` 且**不释放**环境锁（DX-EXE-031）。"""
    detail = f"维护动作 {action} 无法确认：{reason}"
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        steps.finish_step(
            session,
            execution_id,
            StepName.MAINTENANCE,
            status=StepStatus.RUNNING,
            detail={"action": action, "unconfirmed": True, "reason": reason},
        )
        steps.set_status(
            session,
            row,
            ExecutionStatus.NEEDS_MANUAL_CHECK,
            applied=None,
            detail=detail,
            finished=False,
        )
        audit.record(
            audit.COMMIT_NEEDS_MANUAL_CHECK,
            object_type="Execution",
            object_id=execution_id,
            project_id=row.project_id,
            after={"reason": detail, "lockHeld": True, "mode": ExecutionMode.MAINTENANCE.value},
            operator=operator,
            session=session,
        )
        step_infos = steps.steps_as_info(session, execution_id)
    outcome = ExecutionOutcome(
        execution_id=execution_id,
        status=ExecutionStatus.NEEDS_MANUAL_CHECK,
        applied=None,
        environment_key=environment_key,
        steps=step_infos,
        detail=detail,
        issues=[],
    )
    # “无法确认”本身就是本次调用的结论：同键重试只会拿到同一个待核实状态，不会第二次发请求
    idempotency.complete(
        IdemScope.EXECUTION, idempotency_key, outcome.model_dump(mode="json")
    )
    return outcome


__all__ = [
    "MAINTENANCE_RESOURCES",
    "client_for",
    "load_target",
    "run_maintenance",
]
