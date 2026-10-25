"""``prepare_and_validate``：R3 步的全部编排（SRS 6.3、DX-EXE-001…029）。

顺序是需求定的：**计划 → 漂移 → Snapshot → VALIDATE → 服务端表达式校验**。每一步都持久化
（DX-EXE-004），所以同一幂等键重复调用是从已完成的步骤继续，而不是重做——这一步重做的
代价不只是时间：Snapshot 会覆盖，VALIDATE 记录会多出无意义的一行。

S4 不判断 Gate（C-08、DX-EXE-001）：这里没有“是否允许执行”的业务判断，只有“环境配不配、
载荷对不对得上”的事实校验。
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import external_io, idempotency, unit_of_work

from factory_dhis2.dto.execution import Dhis2ValidationOutcome, DriftResult
from factory_dhis2.dto.issue import Issue, make_issue
from factory_dhis2.enums import (
    CredentialScope,
    ExecutionMode,
    ExecutionStatus,
    IssueLevel,
    SourceStage,
    StepName,
    StepStatus,
)
from factory_dhis2.errors import Dhis2Error
from factory_dhis2.execution import drift, report, snapshot, steps
from factory_dhis2.execution import validate as validate_module
from factory_dhis2.execution.context import load_context
from factory_dhis2.execution.expressions import deferred, server_checkable
from factory_dhis2.execution.plan import build_plan, save_plan
from factory_dhis2.payload.hash import verify_expected_hash
from factory_dhis2.registries import IdemScope


@external_io
def prepare_and_validate(
    environment_key: str,
    project_id: str,
    package_artifact_id: str,
    expected_hash: str | None,
    *,
    import_options: dict[str, Any] | None = None,
    maintenance_actions: list[dict[str, Any]] | None = None,
    operator: str,
    idempotency_key: str,
    progress: Any | None = None,
    cancel: Any | None = None,
) -> Dhis2ValidationOutcome:
    """生成执行计划、漂移检查、Snapshot，并以同一载荷与参数做服务端校验。"""
    digest = {
        "operation": "prepare_and_validate",
        "environmentKey": environment_key,
        "projectId": project_id,
        "packageArtifactId": package_artifact_id,
        "expectedHash": expected_hash,
        "importOptions": dict(import_options or {}),
        "maintenanceActions": list(maintenance_actions or []),
    }
    begun = idempotency.begin(IdemScope.EXECUTION, idempotency_key, _hash(digest))
    if begun.already_completed and begun.first_result:
        return Dhis2ValidationOutcome.model_validate(begun.first_result)

    context = load_context(
        environment_key,
        package_artifact_id,
        project_id=project_id,
        scope=CredentialScope.DISCOVERY,
    )
    # VALIDATE 允许不声明期望哈希（DX-EXE-005 的“必须声明”约束的是**提交类**函数；S1 在
    # PACKAGE_GATE 之前手上也可能只有产物 ID）。声明了就必须一致（AC-EXE-02），不声明就取
    # 产物自身哈希并写回结果，让调用方能在 COMMIT 时把它报回来。
    payload_hash = (
        context.package.artifact_hash
        if expected_hash is None
        else verify_expected_hash(context.package, expected_hash)
    )

    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key=environment_key,
            project_id=project_id,
            mode=ExecutionMode.VALIDATE,
            payload_artifact_id=package_artifact_id,
            payload_hash=payload_hash,
            idempotency_key=idempotency_key,
            operator=operator,
        )
        steps.mark_running(session, execution)
        execution_id = execution.id
        already = steps.completed_steps(session, execution_id)

    plan_artifact_id: str | None = None
    if StepName.PLAN.value in already:
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            plan_artifact_id = row.plan_artifact_id
            plan_detail = steps.step_detail(session, execution_id, StepName.PLAN.value)
        plan_document = dict(plan_detail.get("document") or {})
        options = dict(plan_document.get("importOptions") or {})
        confirmation = list(plan_document.get("requiresConfirmation") or [])
        actions = list(plan_document.get("maintenanceActions") or [])
        issues = _issues_from_document(plan_document)
    else:
        if progress is not None:
            progress.report(step=StepName.PLAN.value, completed_fraction=0.05)
        plan = build_plan(
            context,
            import_options=import_options,
            maintenance_actions=maintenance_actions,
            expected_hash=payload_hash,
        )
        plan_document, options = plan.document, plan.import_options
        confirmation, actions = plan.requires_confirmation, plan.maintenance_actions
        issues = plan.issues
        plan_artifact_id = save_plan(context, plan)
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            row.plan_artifact_id = plan_artifact_id
            steps.finish_step(
                session,
                execution_id,
                StepName.PLAN,
                detail={"planArtifactId": plan_artifact_id, "document": plan_document},
            )

    # checked on both paths: a resumed execution restores the persisted document rather
    # than rebuilding the plan, so a blocker that only the fresh path honours is a blocker
    # the second attempt walks straight past (DX-EXE-012)
    blockers = [issue for issue in issues if issue.is_blocking]
    if blockers:
        return _finish_failed(
            context=context,
            execution_id=execution_id,
            plan_artifact_id=plan_artifact_id,
            payload_hash=payload_hash,
            issues=issues,
            import_options=options,
            maintenance_actions=actions,
            requires_confirmation=confirmation,
            operator=operator,
            idempotency_key=idempotency_key,
            reason="执行计划不成立（DX-EXE-012）",
        )

    # 漂移检查
    if StepName.DRIFT_CHECK.value in already:
        with unit_of_work() as session:
            detail = steps.step_detail(session, execution_id, StepName.DRIFT_CHECK.value)
        drift_result = DriftResult.model_validate(detail.get("result") or {"drifted": False})
    else:
        if progress is not None:
            progress.report(step=StepName.DRIFT_CHECK.value, completed_fraction=0.2)
        try:
            drift_result = drift.check(context, cancel=cancel)
        except Dhis2Error as exc:
            return _finish_failed(
                context=context,
                execution_id=execution_id,
                plan_artifact_id=plan_artifact_id,
                payload_hash=payload_hash,
                issues=[
                    make_issue(
                        "DX-CLI-001",
                        IssueLevel.BLOCKING,
                        f"漂移检查无法完成：{exc.message}",
                        stage=SourceStage.MAPPING,
                    )
                ],
                import_options=options,
                maintenance_actions=actions,
                requires_confirmation=confirmation,
                operator=operator,
                idempotency_key=idempotency_key,
                reason="漂移检查无法读取目标环境",
            )
        with unit_of_work() as session:
            steps.finish_step(
                session,
                execution_id,
                StepName.DRIFT_CHECK,
                status=StepStatus.COMPLETED if not drift_result.drifted else StepStatus.FAILED,
                detail={"result": drift_result.model_dump(mode="json")},
            )

    # Snapshot（同时给出 VALIDATE 有效性三要素里的“那一刻状态”）
    if StepName.SNAPSHOT.value in already:
        with unit_of_work() as session:
            detail = steps.step_detail(session, execution_id, StepName.SNAPSHOT.value)
            row = steps.require(session, execution_id)
        snapshot_artifact_id = detail.get("snapshotArtifactId") or row.snapshot_artifact_id
        state_hash = detail.get("envStateHash") or row.env_state_hash or ""
    else:
        if progress is not None:
            progress.report(step=StepName.SNAPSHOT.value, completed_fraction=0.35)
        document = snapshot.build(context)
        state_hash = snapshot.env_state_hash(document)
        snapshot_artifact_id, _ = snapshot.save(
            context, document, execution_id=execution_id
        )
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            row.snapshot_artifact_id = snapshot_artifact_id
            row.env_state_hash = state_hash
            steps.finish_step(
                session,
                execution_id,
                StepName.SNAPSHOT,
                detail={
                    "snapshotArtifactId": snapshot_artifact_id,
                    "envStateHash": state_hash,
                },
            )

    if drift_result.drifted:
        issues = [
            make_issue(
                "DX-EXE-020",
                IssueLevel.BLOCKING,
                f"{item.get('dhis2Type')} {item.get('uid')} 在观测基准之后被修改"
                f"（{item.get('kind')}）",
                object_key=item.get("businessKey"),
                object_uid=item.get("uid"),
            )
            for item in drift_result.objects
            if item.get("kind") not in {drift.NO_BASELINE, "UPDATED_ONLY"}
        ]
        return _finish_failed(
            context=context,
            execution_id=execution_id,
            plan_artifact_id=plan_artifact_id,
            payload_hash=payload_hash,
            issues=issues,
            import_options=options,
            maintenance_actions=actions,
            requires_confirmation=confirmation,
            operator=operator,
            idempotency_key=idempotency_key,
            reason="DRIFT_DETECTED",
            snapshot_artifact_id=snapshot_artifact_id,
            drift=drift_result,
        )

    # VALIDATE：与 COMMIT 完全相同的载荷与参数，只换 importMode
    if StepName.VALIDATE.value in already:
        with unit_of_work() as session:
            detail = steps.step_detail(session, execution_id, StepName.VALIDATE.value)
        result = _ValidateCache.restore(detail)
    else:
        if progress is not None:
            progress.report(step=StepName.VALIDATE.value, completed_fraction=0.5)
        try:
            result = validate_module.validate(
                context,
                import_options=options,
                plan_document=plan_document,
                env_state_hash=state_hash or "",
                execution_id=execution_id,
                progress=progress,
                cancel=cancel,
            )
        except Dhis2Error as exc:
            return _finish_failed(
                context=context,
                execution_id=execution_id,
                plan_artifact_id=plan_artifact_id,
                payload_hash=payload_hash,
                issues=[
                    make_issue(
                        exc.error_code,
                        IssueLevel.BLOCKING,
                        f"服务端校验无法完成：{exc.message}",
                    )
                ],
                import_options=options,
                maintenance_actions=actions,
                requires_confirmation=confirmation,
                operator=operator,
                idempotency_key=idempotency_key,
                reason="服务端校验未能执行",
                snapshot_artifact_id=snapshot_artifact_id,
                drift=drift_result,
            )
        with unit_of_work() as session:
            steps.finish_step(
                session,
                execution_id,
                StepName.VALIDATE,
                status=StepStatus.COMPLETED if result.passed else StepStatus.FAILED,
                detail={
                    "importReportArtifactId": result.report_artifact_id,
                    "passed": result.passed,
                    "issueCount": len(result.summary.issues),
                    "validationRecordId": result.validation_record_id,
                    # DX-EXE-004：续跑恢复的是**同一个结论**，问题清单与表达式逐条结果也要
                    # 一起存下来，否则重放得到的是一份"通过但没有任何明细"的空视图
                    "issues": [issue.model_dump(mode="json") for issue in result.issues],
                    "expression": (
                        None
                        if result.expression is None
                        else result.expression.as_document(phase="VALIDATE")
                    ),
                },
            )

    with unit_of_work() as session:
        expression_results = _expression_arrangement(context)

    outcome = Dhis2ValidationOutcome(
        execution_plan_artifact_id=plan_artifact_id,
        snapshot_artifact_id=snapshot_artifact_id,
        import_report_artifact_id=result.report_artifact_id,
        payload_artifact_id=package_artifact_id,
        payload_hash=payload_hash,
        passed=result.passed,
        issues=result.issues,
        import_options=options,
        maintenance_actions=actions,
        requires_confirmation_items=confirmation,
        execution_id=execution_id,
        drift=drift_result,
        environment_state_hash=state_hash,
        validation_record_id=result.validation_record_id,
    )

    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        steps.finish_step(
            session,
            execution_id,
            StepName.EXPRESSION_VALIDATE,
            status=StepStatus.COMPLETED
            if (result.expression is None or result.expression.ok)
            else StepStatus.FAILED,
            detail={
                "atValidate": len(expression_results["atValidate"]),
                "atPostVerify": len(expression_results["atPostVerify"]),
                "failed": []
                if result.expression is None
                else [issue.model_dump(mode="json") for issue in result.expression.failed],
            },
        )
        final_status = ExecutionStatus.COMPLETED if result.passed else ExecutionStatus.FAILED
        steps.set_status(
            session,
            row,
            final_status,
            applied=False if not result.passed else None,
            detail=None if result.passed else "服务端校验未通过：不会自动提交（DX-EXE-029）",
            finished=True,
        )
        document = report.build_document(
            context=context,
            execution_id=execution_id,
            status=final_status,
            applied=None,
            steps=steps.steps_as_info(session, execution_id),
            import_report_artifact_id=result.report_artifact_id,
            snapshot_artifact_id=snapshot_artifact_id,
            operator=operator,
            issues=result.issues,
            extra={
                "expressionArrangement": expression_results,
                # DX-EXE-028：VALIDATE 阶段的表达式**逐条**结论要留在校验报告里，而不只是
                # 失败项转成的 issue——审批人需要看到"哪些表达式验过且通过了"
                "expressionValidation": (
                    None
                    if result.expression is None
                    else result.expression.as_document(phase="VALIDATE")
                ),
            },
        )
        outcome.steps = steps.steps_as_info(session, execution_id)
    # DX-EXE-046：执行报告是产物，不是只存在于返回值里的临时结构——VALIDATE 这一步也要留档，
    # 否则"哪一次校验、用什么参数、结果如何"在追溯链上断了
    report_artifact_id = report.save(context, document)
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        row.report_artifact_id = report_artifact_id
    outcome.report_artifact_id = report_artifact_id
    idempotency.complete(
        IdemScope.EXECUTION, idempotency_key, outcome.model_dump(mode="json")
    )
    return outcome


class _ValidateCache:
    """续跑时从已持久化的 VALIDATE 步骤结果恢复结论。

    重放而不重做（DX-EXE-004）：VALIDATE 已经打过实例，结论与 ImportReport 都在库里，
    再打一次只会多一条无意义的校验记录。
    """

    @staticmethod
    def restore(detail: dict[str, Any]) -> validate_module.ValidateResult:
        from factory_dhis2.execution.importreport import ImportReportSummary

        issues = [
            Issue.model_validate(item)
            for item in (detail.get("issues") or [])
            if isinstance(item, dict)
        ]
        return validate_module.ValidateResult(
            passed=bool(detail.get("passed")),
            summary=ImportReportSummary(
                passed=bool(detail.get("passed")),
                issues=issues,
                artifact_id=detail.get("importReportArtifactId"),
            ),
            report_artifact_id=detail.get("importReportArtifactId"),
            validation_record_id=detail.get("validationRecordId"),
            expression=_restore_expression(detail.get("expression")),
        )


def _restore_expression(document: Any) -> Any:
    """把 VALIDATE 步骤里存下的表达式结论还原成 :class:`ExpressionCheckReport`。"""
    if not isinstance(document, dict):
        return None
    from factory_dhis2.execution.expressions import ExpressionCheckReport

    return ExpressionCheckReport(
        checked=int(document.get("checked") or 0),
        passed=int(document.get("passed") or 0),
        failed=[
            Issue.model_validate(item)
            for item in (document.get("failed") or [])
            if isinstance(item, dict)
        ],
        skipped=[item for item in (document.get("skipped") or []) if isinstance(item, dict)],
        results=[item for item in (document.get("results") or []) if isinstance(item, dict)],
    )


def _expression_arrangement(context: Any) -> dict[str, list[dict[str, Any]]]:
    return {
        "atValidate": [
            {"businessKey": item.get("businessKey"), "field": item.get("field")}
            for item in server_checkable(context.package)
        ],
        "atPostVerify": [
            {"businessKey": item.get("businessKey"), "field": item.get("field")}
            for item in deferred(context.package)
        ],
    }


def _finish_failed(
    *,
    context: Any,
    execution_id: str,
    plan_artifact_id: str | None,
    payload_hash: str,
    issues: list[Any],
    import_options: dict[str, Any],
    maintenance_actions: list[dict[str, Any]],
    requires_confirmation: list[dict[str, Any]],
    operator: str,
    idempotency_key: str,
    reason: str,
    snapshot_artifact_id: str | None = None,
    drift: DriftResult | None = None,
    import_report_artifact_id: str | None = None,
) -> Dhis2ValidationOutcome:
    """未通过就是未通过：状态落库并返回，绝不自动重试或自动提交（DX-BR-09）。"""
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        steps.set_status(
            session,
            row,
            ExecutionStatus.FAILED,
            applied=False,
            detail=reason,
            finished=True,
        )
        step_infos = steps.steps_as_info(session, execution_id)
        # DX-EXE-046: every execution produces an EXECUTION_REPORT, a failure included.
        # A blocked plan or a failed VALIDATE that leaves no report breaks the traceability
        # chain 21.4 and AC-AUD-01 ask for -- "which run, with what parameters, ended how"
        # has to be answerable from artifacts, and the approver reads exactly this document
        # when the next attempt is put in front of them.
        document = report.build_document(
            context=context,
            execution_id=execution_id,
            status=ExecutionStatus.FAILED,
            applied=False,
            steps=step_infos,
            import_report_artifact_id=import_report_artifact_id,
            snapshot_artifact_id=snapshot_artifact_id,
            operator=operator,
            issues=issues,
            extra={"failureReason": reason},
        )
    report_artifact_id = report.save(context, document)
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        row.report_artifact_id = report_artifact_id
    outcome = Dhis2ValidationOutcome(
        execution_plan_artifact_id=plan_artifact_id,
        snapshot_artifact_id=snapshot_artifact_id,
        payload_artifact_id=context.package.artifact_id,
        payload_hash=payload_hash,
        passed=False,
        issues=issues,
        import_options=import_options,
        maintenance_actions=maintenance_actions,
        requires_confirmation_items=requires_confirmation,
        execution_id=execution_id,
        drift=drift,
        steps=step_infos,
        import_report_artifact_id=import_report_artifact_id,
    )
    idempotency.complete(IdemScope.EXECUTION, idempotency_key, outcome.model_dump(mode="json"))
    return outcome


def _issues_from_document(document: dict[str, Any]) -> list[Any]:
    """续跑时从计划文档里恢复执行计划自己提出的问题。

    计划生成时算出的阻塞项只有写进文档才可能在续跑时还在：``build_plan`` 的返回值随着那次
    调用一起消失，而恢复路径读的是持久化的文档。漏掉它会让一个本应阻止提交的计划在续跑后
    直通 VALIDATE（DX-EXE-012）。
    """
    from factory_dhis2.dto.issue import Issue

    return [Issue.model_validate(item) for item in document.get("issues") or []]


def _hash(value: Any) -> str:
    from factory_foundation.api import structure_hash

    return structure_hash(value)


__all__ = ["prepare_and_validate"]
