"""服务端 VALIDATE（DX-EXE-025…029、AC-EXE-01、FR-VAL-001…004）。

三条不可让步的规则在这一层落实：

* VALIDATE 提交的是与 COMMIT **完全相同的载荷与完全相同的导入参数**，只把
  ``importMode`` 换成 VALIDATE（DX-BR-05、FR-VAL-001）——否则“验过的”与“提交的”是两件
  东西，校验结论就没有意义；
* VALIDATE 不得使目标环境产生任何变化（AC-EXE-01）：这里只用导入接口的只读语义；
* VALIDATE 失败时**禁止任何形式的自动提交**（DX-EXE-029、FR-VAL-003），且结果只对
  “该载荷哈希 + 该环境 + 该时刻环境状态”有效，这三项一起写入 ``dx.validation_record``。

事务形态：本函数是外部 I/O，因此 **DHIS2 调用期间不持有数据库事务**——读写各开一段短
事务，状态变更与其审计记录在同一短事务里（D-11、00 文档 6.2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import new_id, unit_of_work
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2 import audit
from factory_dhis2.client.session import Dhis2Response
from factory_dhis2.enums import CredentialScope
from factory_dhis2.execution import jobs
from factory_dhis2.execution.context import RunContext
from factory_dhis2.execution.expressions import (
    ExpressionCheckReport,
    check_expressions,
    server_checkable,
)
from factory_dhis2.execution.importreport import ImportReportSummary, parse_report, save_report
from factory_dhis2.execution.plan import async_import_decision
from factory_dhis2.models.execution import ValidationRecordRow


@dataclass
class ValidateResult:
    """一次服务端校验的结论。"""

    passed: bool = True
    summary: ImportReportSummary = field(default_factory=ImportReportSummary)
    report_artifact_id: str | None = None
    env_state_hash: str | None = None
    expression: ExpressionCheckReport | None = None
    validation_record_id: str | None = None
    job_ref: str | None = None

    @property
    def issues(self) -> list[Any]:
        out: list[Any] = list(self.summary.issues)
        if self.expression is not None:
            out.extend(self.expression.failed)
        return out


def validate(
    context: RunContext,
    *,
    import_options: dict[str, Any],
    plan_document: dict[str, Any],
    env_state_hash: str,
    execution_id: str | None = None,
    progress: Any | None = None,
    cancel: Any | None = None,
    check_server_expressions: bool = True,
) -> ValidateResult:
    """以 ``importMode=VALIDATE`` 提交同一载荷（SRS 6.3 的 VALIDATE 步）。"""
    import_client = context.client.with_scope(CredentialScope.IMPORT)

    if execution_id is not None:
        with unit_of_work() as session:
            audit.record(
                audit.VALIDATE_STARTED,
                object_type="Execution",
                object_id=execution_id,
                project_id=context.project_id,
                after={
                    "environmentKey": context.environment_key,
                    "payloadHash": context.package.artifact_hash,
                    "importOptions": {**import_options, "importMode": "VALIDATE"},
                },
                session=session,
            )

    params = {**import_options, "importMode": "VALIDATE"}
    # 与 COMMIT 用同一条规则（DX-EXE-033）：计划里没有那一项时按对象数重算，不退回同步
    use_async = bool(
        async_import_decision(
            plan_document, context, context.package.object_count, caller_options=import_options
        )["async"]
    )
    submission = jobs.submit(
        import_client,
        resource="metadata.import",
        body=context.package.payload,
        params=params,
        use_async=use_async,
        # DX-EXE-033/034: an async VALIDATE has to persist its task reference before it
        # starts polling, exactly as COMMIT does.  Without it a crash mid-poll loses the job
        # and the recovery path resubmits -- which is the one thing FR-EXE-009 forbids, and
        # it is not a theoretical case on a package large enough to go asynchronous.
        execution_id=execution_id,
    )
    if submission.async_import:
        if progress is not None:
            progress.report(step="VALIDATE", message="异步校验任务已提交")
        response = jobs.poll(import_client, submission, cancel=cancel)
        report_document = jobs.report_of(response)
    else:
        response = submission.response or Dhis2Response(status=500, payload=None)
        report_document = response.payload if isinstance(response.payload, dict) else {}

    summary = parse_report(
        report_document, package=context.package, profile=context.target.profile
    )

    expression: ExpressionCheckReport | None = None
    if check_server_expressions:
        items = server_checkable(context.package)
        if items:
            if progress is not None:
                progress.report(
                    step="EXPRESSION_VALIDATE", message=f"{len(items)} 条表达式待实例校验"
                )
            expression = check_expressions(
                context.client,
                items,
                package=context.package,
                profile=context.target.profile,
                phase="VALIDATE",
                cancel=cancel,
            )

    passed = summary.passed and (expression is None or expression.ok)

    # 暂存是外部 I/O，先在事务外写对象存储；登记与校验记录、审计同处一段短事务（D-11）
    report_artifact_id = save_report(
        project_id=context.project_id,
        document=report_document,
        mode="VALIDATE",
        environment_key=context.environment_key,
        source_artifact_ids=[context.package.artifact_id],
    )
    summary.artifact_id = report_artifact_id
    for issue in summary.issues:
        issue.artifact_id = report_artifact_id

    with unit_of_work() as session:
        record_id = record_validation(
            session,
            environment_key=context.environment_key,
            payload_hash=context.package.artifact_hash,
            env_state_hash=env_state_hash,
            execution_id=execution_id or "validate-only",
            result="PASSED" if passed else "FAILED",
            report_artifact_id=report_artifact_id,
            issue_count=len(summary.issues)
            + (len(expression.failed) if expression is not None else 0),
        )
        if execution_id is not None:
            audit.record(
                audit.VALIDATE_COMPLETED,
                object_type="Execution",
                object_id=execution_id,
                project_id=context.project_id,
                after={
                    "result": "PASSED" if passed else "FAILED",
                    "importReportArtifactId": report_artifact_id,
                    "issues": len(summary.issues),
                    "expressionsFailed": len(expression.failed) if expression else 0,
                    "envStateHash": env_state_hash,
                },
                session=session,
            )

    return ValidateResult(
        passed=passed,
        summary=summary,
        report_artifact_id=report_artifact_id,
        env_state_hash=env_state_hash,
        expression=expression,
        validation_record_id=record_id,
        job_ref=submission.job_ref,
    )


def record_validation(
    session: Session,
    *,
    environment_key: str,
    payload_hash: str,
    env_state_hash: str,
    execution_id: str,
    result: str,
    report_artifact_id: str | None,
    issue_count: int,
    snapshot_artifact_id: str | None = None,
) -> str:
    """写入 VALIDATE 记录（DX-EXE-029 的三要素）。"""
    existing = session.execute(
        select(ValidationRecordRow).where(
            ValidationRecordRow.environment_key == environment_key,
            ValidationRecordRow.payload_hash == payload_hash,
            ValidationRecordRow.env_state_hash == env_state_hash,
            ValidationRecordRow.result == result,
        )
    ).scalars().first()
    if existing is not None:
        # 同一状态同一结论不堆重复行，只把最新一次的报告引用与时间刷上去
        existing.issue_count = issue_count
        existing.import_report_artifact_id = report_artifact_id
        existing.validated_at = datetime.now(UTC)
        session.flush()
        return existing.id
    record_id = new_id("dxvld")
    session.add(
        ValidationRecordRow(
            id=record_id,
            environment_key=environment_key,
            payload_hash=payload_hash,
            env_state_hash=env_state_hash,
            execution_id=execution_id,
            validated_at=datetime.now(UTC),
            result=result,
            import_report_artifact_id=report_artifact_id,
            snapshot_artifact_id=snapshot_artifact_id,
            issue_count=issue_count,
        )
    )
    session.flush()
    return record_id


def last_passed(
    session: Session, environment_key: str, payload_hash: str
) -> ValidationRecordRow | None:
    """该环境对该载荷最近一次成功校验（COMMIT 的准入依据）。"""
    return session.execute(
        select(ValidationRecordRow)
        .where(
            ValidationRecordRow.environment_key == environment_key,
            ValidationRecordRow.payload_hash == payload_hash,
            ValidationRecordRow.result == "PASSED",
        )
        .order_by(ValidationRecordRow.validated_at.desc())
        .limit(1)
    ).scalars().first()


__all__ = [
    "ValidateResult",
    "last_passed",
    "record_validation",
    "validate",
]
