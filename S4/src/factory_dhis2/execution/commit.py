"""COMMIT（DX-EXE-030…036、AC-EXE-02/04/05、DX-AC-03/04/05）。

一次提交的准入顺序是硬性的，缺一项就把整次提交挡在门外：

1. 期望哈希 == 载荷产物哈希，且存在**同一哈希**的最近一次成功 VALIDATE（DX-EXE-005、
   AC-EXE-02）；COMMIT 用的导入参数取自那次 VALIDATE 的执行计划，因此“验过的”与
   “提交的”不只是相似，而是同一份参数（DX-BR-05）。
2. 紧急锁定关闭、当前处于允许提交的时间窗内（DX-ENV-007/002）。
3. 拿到环境级独占锁（DX-EXE-030）——拿不到就是 ``CONCURRENCY_ERROR``。
4. 再跑一次漂移检查（DX-EXE-032）：审批到提交之间环境可能已经被人改过。

提交之后只有三种结局：生效、未生效、**无法确认**。第三种不是失败而是信息缺失，所以它
进入 NEEDS_MANUAL_CHECK 并**保留环境锁**，等人工核实（DX-EXE-031、AC-EXE-05、DX-AC-04）；
S4 绝不猜测、也绝不自动重发（NFR-REL-003）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory_foundation.api import (
    external_io,
    idempotency,
    structure_hash,
    unit_of_work,
)

from factory_dhis2 import audit
from factory_dhis2.artifacts import read_json
from factory_dhis2.dto.execution import ExecutionOutcome
from factory_dhis2.enums import (
    CredentialScope,
    ExecutionMode,
    ExecutionStatus,
    ExecutorType,
    StepName,
    StepStatus,
)
from factory_dhis2.environment.protection import get_protection, reject_if_locked
from factory_dhis2.envlock import manager as envlock
from factory_dhis2.errors import (
    Dhis2Error,
    DriftDetectedError,
    OutsideCommitWindowError,
    PayloadHashMismatchError,
)
from factory_dhis2.execution import drift, jobs, report, sequence, steps
from factory_dhis2.execution.context import RunContext, load_context
from factory_dhis2.execution.importreport import parse_report, save_report
from factory_dhis2.execution.plan import async_import_decision
from factory_dhis2.execution.snapshot import build as build_snapshot
from factory_dhis2.execution.snapshot import env_state_hash as snapshot_state_hash
from factory_dhis2.execution.snapshot import save as save_snapshot
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.payload.hash import require_prior_validation, verify_expected_hash
from factory_dhis2.registries import IdemScope


@external_io
def commit(
    environment_key: str,
    project_id: str,
    package_artifact_id: str,
    expected_hash: str,
    *,
    operator: str,
    idempotency_key: str,
    progress: Any | None = None,
    cancel: Any | None = None,
    import_options: dict[str, Any] | None = None,
) -> ExecutionOutcome:
    """把已批准的载荷提交到环境（SRS 6.4）。"""
    digest = structure_hash(
        {
            "operation": "commit",
            "environmentKey": environment_key,
            "projectId": project_id,
            "packageArtifactId": package_artifact_id,
            "expectedHash": expected_hash,
            "operator": operator,
        }
    )
    begun = idempotency.begin(IdemScope.EXECUTION, idempotency_key, digest)
    resumable = _resumable_execution(idempotency_key)
    if resumable is not None:
        # DX-EXE-034 / DX-AC-03：上一次调用已把任务提交出去并在轮询中崩了。这一次**继续查
        # 问那个任务**，而不是重新提交——重新提交可能把已生效的导入再做一遍。
        return _resume_poll(
            resumable,
            environment_key=environment_key,
            package_artifact_id=package_artifact_id,
            idempotency_key=idempotency_key,
            progress=progress,
            cancel=cancel,
        )
    replayed = _replay(begun, idempotency_key)
    if replayed is not None:
        return replayed

    context = load_context(
        environment_key, package_artifact_id, project_id=project_id, scope=CredentialScope.IMPORT
    )
    payload_hash = verify_expected_hash(context.package, expected_hash)

    # 准入 1：同哈希的成功 VALIDATE，以及它当时用的导入参数
    with unit_of_work() as session:
        validation = require_prior_validation(session, environment_key, payload_hash)
        validated_execution = session.get(ExecutionRow, validation.execution_id)
        plan_artifact_id = validated_execution.plan_artifact_id if validated_execution else None
        validated_state = validation.env_state_hash
    # 产物读取是外部 I/O，必须在事务之外（00 文档 6.2）
    plan_document = _plan_document(plan_artifact_id)
    options_from_plan = dict(plan_document.get("importOptions") or {})
    options = options_from_plan or _default_options(context)
    if import_options and import_options != options:
        raise PayloadHashMismatchError(
            "COMMIT never takes new import parameters: they are the ones the successful "
            "VALIDATE used (DX-BR-05)",
            details={
                "requested": import_options,
                "validated": options,
                "rule": "DX-EXE-032",
            },
        )

    # 计划里那一份异步决定是权威（VALIDATE 与 COMMIT 必须走同一条路）；产物读不到时按
    # DX-EXE-033 用对象数与环境阈值**重算**，而不是退回同步——大载荷同步提交会顶穿超时。
    decision = async_import_decision(
        plan_document, context, context.package.object_count, caller_options=import_options
    )
    use_async = bool(decision["async"])

    # 准入 2：紧急锁定与时间窗（PRODUCTION 的时间窗按环境时区判定）
    with unit_of_work() as session:
        reject_if_locked(session, environment_key, "COMMIT")
        protection = get_protection(environment_key, session=session)
        if not protection.within_commit_window:
            raise OutsideCommitWindowError(
                f"environment {environment_key!r} is outside its allowed commit window",
                details={
                    "environmentKey": environment_key,
                    "windows": protection.commit_windows,
                    "timezone": protection.timezone,
                },
            )

    # 准入 3：多执行器部署里，计划排在前面的执行器必须已经生效（DX-EXE-037、DX-BR-12）。
    # 放在拿锁之前：这一步被挡住时不该留下一次「已拿到锁但永远执行不了」的执行。
    sequence.require_predecessors_applied(
        environment_key=environment_key,
        payload_hash=payload_hash,
        plan_document=plan_document,
        executor=ExecutorType.METADATA.value,
    )

    # 准入 3：执行行 + 环境锁在同一事务里落地（崩溃时不会留下无主的锁或无锁的执行）
    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key=environment_key,
            project_id=project_id,
            mode=ExecutionMode.COMMIT,
            payload_artifact_id=package_artifact_id,
            payload_hash=payload_hash,
            idempotency_key=idempotency_key,
            operator=operator,
        )
        steps.mark_running(session, execution)
        envlock.acquire(session, environment_key, execution.id, operator=operator, note="COMMIT")
        audit.record(
            audit.COMMIT_STARTED,
            object_type="Execution",
            object_id=execution.id,
            project_id=project_id,
            after={
                "environmentKey": environment_key,
                "payloadHash": payload_hash,
                "importOptions": options,
                "asyncImport": use_async,
            },
            operator=operator,
            session=session,
        )
        execution_id = execution.id

    try:
        # 准入 4：提交前再次漂移检查（DX-EXE-032）
        if progress is not None:
            progress.report(step=StepName.DRIFT_CHECK.value, completed_fraction=0.1)
        drift_result = drift.check(context, cancel=cancel)
        if drift_result.drifted:
            with unit_of_work() as session:
                row = steps.require(session, execution_id)
                steps.finish_step(
                    session,
                    execution_id,
                    StepName.DRIFT_CHECK,
                    status=StepStatus.FAILED,
                    detail={"drifted": True, "objects": drift_result.objects[:20]},
                )
                steps.set_status(
                    session,
                    row,
                    ExecutionStatus.FAILED,
                    applied=False,
                    detail="提交前漂移检查未通过",
                    finished=True,
                )
                envlock.release(session, environment_key, execution_id, operator=operator, reason="DRIFT_DETECTED")
                audit.record(
                    audit.COMMIT_FAILED,
                    object_type="Execution",
                    object_id=execution_id,
                    project_id=project_id,
                    after={"reason": "DRIFT_DETECTED", "objects": len(drift_result.objects)},
                    operator=operator,
                    session=session,
                )
            raise DriftDetectedError(
                f"environment {environment_key!r} changed after the observation baseline",
                details={
                    "environmentKey": environment_key,
                    "executionId": execution_id,
                    "objects": drift_result.objects[:20],
                    "rule": "AC-EXE-03",
                },
            )

        # 提交前 Snapshot：既是“那一刻状态”的证据，也是事后对比与对账的基准（DX-EXE-022）
        if progress is not None:
            progress.report(step=StepName.SNAPSHOT.value, completed_fraction=0.2)
        snapshot_document = build_snapshot(context)
        state_hash = snapshot_state_hash(snapshot_document)
        snapshot_artifact_id, _snapshot_row_id = save_snapshot(
            context, snapshot_document, execution_id=execution_id
        )
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            row.snapshot_artifact_id = snapshot_artifact_id
            row.env_state_hash = state_hash
            steps.finish_step(
                session,
                execution_id,
                StepName.SNAPSHOT,
                detail={"snapshotArtifactId": snapshot_artifact_id, "envStateHash": state_hash},
            )

        # VALIDATE 记录里的状态哈希与此刻一致才允许提交（DX-EXE-029）
        _require_state_still_validated(
            environment_key, payload_hash, state_hash, validated_state
        )

        import_client = context.client.with_scope(CredentialScope.IMPORT)
        params = {**options, "importMode": "COMMIT"}
        if progress is not None:
            progress.report(step=StepName.COMMIT.value, completed_fraction=0.35)
        try:
            submission = jobs.submit(
                import_client,
                resource="metadata.import",
                body=context.package.payload,
                params=params,
                use_async=use_async,
                execution_id=execution_id,
            )
        except Dhis2Error as exc:
            return _needs_manual_check(
                context=context,
                execution_id=execution_id,
                environment_key=environment_key,
                project_id=project_id,
                operator=operator,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                reason=str(exc.message),
                error=exc,
            )

        if submission.async_import:
            if progress is not None:
                progress.report(
                    step=StepName.ASYNC_POLL.value, message=f"任务 {submission.job_ref}"
                )
            with unit_of_work() as session:
                steps.finish_step(
                    session,
                    execution_id,
                    StepName.COMMIT,
                    detail={"jobRef": submission.job_ref, "pollUrl": submission.poll_url},
                )
            try:
                response = jobs.poll(import_client, submission, cancel=cancel)
                report_document = jobs.report_of(response)
            except Dhis2Error as exc:
                return _needs_manual_check(
                    context=context,
                    execution_id=execution_id,
                    environment_key=environment_key,
                    project_id=project_id,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    reason=f"异步任务无法确认：{exc.message}",
                    error=exc,
                    job_ref=submission.job_ref,
                )
            # 轮询到终态这件事要留下步骤：恢复与审计都按「哪些里程碑真的做完了」说话
            with unit_of_work() as session:
                steps.finish_step(
                    session,
                    execution_id,
                    StepName.ASYNC_POLL,
                    detail={"jobRef": submission.job_ref, "polls": submission.polls},
                )
        else:
            if submission.response is None:
                return _needs_manual_check(
                    context=context,
                    execution_id=execution_id,
                    environment_key=environment_key,
                    project_id=project_id,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    reason="同步提交没有返回任何响应，结果无法确认",
                    error=Dhis2Error("no response for a synchronous import"),
                )
            response = submission.response
            report_document = (
                response.payload if isinstance(response.payload, dict) else {}
            )
            with unit_of_work() as session:
                steps.finish_step(
                    session, execution_id, StepName.COMMIT, detail={"httpStatus": response.status}
                )

        return _finalize(
            context=context,
            execution_id=execution_id,
            environment_key=environment_key,
            project_id=project_id,
            package_artifact_id=package_artifact_id,
            operator=operator,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            snapshot_artifact_id=snapshot_artifact_id,
            report_document=report_document,
        )
    except DriftDetectedError:
        raise
    except Exception:
        # 未预期的失败：锁必须释放，否则一次程序缺陷会让该环境永久无法提交
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            if row.status not in (
                ExecutionStatus.COMPLETED.value,
                ExecutionStatus.NEEDS_MANUAL_CHECK.value,
            ):
                steps.set_status(
                    session, row, ExecutionStatus.FAILED, applied=False, detail="执行异常终止", finished=True
                )
                envlock.release(session, environment_key, execution_id, operator=operator, reason="ERROR")
                audit.record(
                    audit.COMMIT_FAILED,
                    object_type="Execution",
                    object_id=execution_id,
                    project_id=project_id,
                    after={"reason": "INTERNAL"},
                    operator=operator,
                    session=session,
                )
        raise


def _needs_manual_check(
    *,
    context: RunContext,
    execution_id: str,
    environment_key: str,
    project_id: str | None,
    operator: str,
    idempotency_key: str,
    payload_hash: str,
    reason: str,
    error: Any,
    job_ref: str | None = None,
) -> ExecutionOutcome:
    """DX-EXE-035：提交结果无法确认——锁保持，等人工核实。"""
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        steps.set_status(
            session,
            row,
            ExecutionStatus.NEEDS_MANUAL_CHECK,
            applied=None,
            detail=reason,
            finished=False,
        )
        if job_ref is not None and row.async_job_ref is None and row.poll_url is None:
            # 只有提交时没能落库才在这里补：已经存好的查询地址不能被这次补写抹掉（DX-EXE-034）
            steps.remember_job(session, row, job_ref=job_ref, poll_url=None)
        audit.record(
            audit.COMMIT_NEEDS_MANUAL_CHECK,
            object_type="Execution",
            object_id=execution_id,
            project_id=project_id,
            after={"reason": reason, "lockHeld": True, "jobRef": job_ref},
            operator=operator,
            session=session,
        )
        step_infos = steps.steps_as_info(session, execution_id)
    outcome = ExecutionOutcome(
        execution_id=execution_id,
        status=ExecutionStatus.NEEDS_MANUAL_CHECK,
        applied=None,
        payload_hash=payload_hash,
        environment_key=environment_key,
        steps=step_infos,
        detail=reason,
        issues=[],
    )
    # 结果不明本身就是本次调用的结论：幂等记录完成，重试同一键只会得到同一个待核实状态，
    # 而不是第二次提交（AC-EXE-05、DX-AC-03）
    idempotency.complete(
        IdemScope.EXECUTION,
        idempotency_key,
        {"executionId": execution_id, "status": ExecutionStatus.NEEDS_MANUAL_CHECK.value},
    )
    return outcome


def _require_state_still_validated(
    environment_key: str,
    payload_hash: str,
    current_state_hash: str,
    validated_state_hash: str | None,
) -> None:
    if validated_state_hash is None or validated_state_hash == current_state_hash:
        return
    from factory_dhis2.errors import ValidationStaleError

    raise ValidationStaleError(
        f"the environment state changed since VALIDATE for payload {payload_hash} on "
        f"{environment_key!r}: re-run VALIDATE before COMMIT (DX-EXE-029)",
        details={
            "environmentKey": environment_key,
            "payloadHash": payload_hash,
            "validatedState": validated_state_hash,
            "currentState": current_state_hash,
        },
    )


def _plan_document(plan_artifact_id: str | None) -> dict[str, Any]:
    """取那次 VALIDATE 用的执行计划：导入参数以它为准（DX-BR-05）。"""
    if plan_artifact_id is None:
        return {}
    return read_json(plan_artifact_id)


def _default_options(context: RunContext) -> dict[str, Any]:
    from factory_dhis2.execution.plan import resolve_import_options

    resolved, _ = resolve_import_options(context.target.profile, None)
    return resolved


def _finalize(
    *,
    context: RunContext,
    execution_id: str,
    environment_key: str,
    project_id: str | None,
    package_artifact_id: str,
    operator: str,
    idempotency_key: str,
    payload_hash: str,
    snapshot_artifact_id: str | None,
    report_document: dict[str, Any],
) -> ExecutionOutcome:
    """把一份最终 ImportReport 落成就绪的终态：报告产物、执行状态、释放锁、审计、幂等完成。

    新提交与恢复轮询（:func:`_resume_poll`）共用这一段，因为「结果已经问到了」之后的处理
    不该取决于结果是在哪一次调用里问到的（DX-EXE-034）。
    """
    summary = parse_report(
        report_document, package=context.package, profile=context.target.profile
    )
    import_report_artifact_id = save_report(
        project_id=project_id,
        document=report_document,
        mode="COMMIT",
        environment_key=environment_key,
        source_artifact_ids=[package_artifact_id],
    )
    applied = summary.passed
    status = ExecutionStatus.COMPLETED if applied else ExecutionStatus.FAILED
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        row.import_report_artifact_id = import_report_artifact_id
        steps.set_status(
            session,
            row,
            status,
            applied=applied,
            detail=None if applied else "实例拒绝了本次提交",
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
            audit.COMMIT_COMPLETED if applied else audit.COMMIT_FAILED,
            object_type="Execution",
            object_id=execution_id,
            project_id=project_id,
            after={
                "importReportArtifactId": import_report_artifact_id,
                "snapshotArtifactId": snapshot_artifact_id,
                "applied": applied,
                "issues": len(summary.issues),
            },
            operator=operator,
            session=session,
        )
        step_infos = steps.steps_as_info(session, execution_id)
        report_document_row = report.build_document(
            context=context,
            execution_id=execution_id,
            status=status,
            applied=applied,
            steps=step_infos,
            import_report_artifact_id=import_report_artifact_id,
            snapshot_artifact_id=snapshot_artifact_id,
            operator=operator,
            issues=summary.issues,
        )

    report_artifact_id = report.save(context, report_document_row)
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        row.report_artifact_id = report_artifact_id

    outcome = ExecutionOutcome(
        execution_id=execution_id,
        status=status,
        report_artifact_id=report_artifact_id,
        import_report_artifact_id=import_report_artifact_id,
        applied=applied,
        payload_hash=payload_hash,
        environment_key=environment_key,
        steps=step_infos,
        issues=summary.issues,
    )
    idempotency.complete(
        IdemScope.EXECUTION,
        idempotency_key,
        {"executionId": execution_id, "status": status.value, "applied": applied},
    )
    return outcome


@dataclass(frozen=True)
class _Resumable:
    """一次「已提交、正在轮询、但调用中断了」的执行（DX-EXE-034）。"""

    execution_id: str
    environment_key: str
    project_id: str | None
    payload_hash: str | None
    operator: str | None
    snapshot_artifact_id: str | None


def _resumable_execution(idempotency_key: str) -> _Resumable | None:
    """同幂等键重入时，是否有一个已提交出去的任务该继续查询而不是重新提交。

    三个条件缺一个就不算：状态还是 ``RUNNING``（说明没写下终态）、持久化了任务引用或查询
    地址（说明提交真的发生过）、模式是 COMMIT（``commit()`` 只恢复自己那一类）。没有任务
    引用时按原样回放 NEEDS_MANUAL_CHECK —— 那才是 DX-EXE-035 说的"结果不明"。
    """
    with unit_of_work() as session:
        row = steps.find_by_idempotency_key(session, idempotency_key)
        if row is None or row.status != steps.RUNSTATE:
            return None
        if not (row.async_job_ref or row.poll_url):
            return None
        if row.mode != ExecutionMode.COMMIT.value:
            return None
        return _Resumable(
            execution_id=row.id,
            environment_key=row.environment_key,
            project_id=row.project_id,
            payload_hash=row.payload_hash,
            operator=row.operator,
            snapshot_artifact_id=row.snapshot_artifact_id,
        )


def _resume_poll(
    resumable: _Resumable,
    *,
    environment_key: str,
    package_artifact_id: str,
    idempotency_key: str,
    progress: Any | None,
    cancel: Any | None,
) -> ExecutionOutcome:
    """DX-EXE-034 / DX-AC-03：按已持久化的任务 ID 继续查询，禁止重新提交。

    这里**不重新跑准入**（哈希、时间窗、漂移、取锁）：那些是「要不要把这份载荷交出去」的
    判断，而载荷早就交出去了，锁也早就被这个执行持有。现在唯一要做的是问出结果。
    """
    context = load_context(
        environment_key,
        package_artifact_id,
        project_id=resumable.project_id,
        scope=CredentialScope.IMPORT,
    )
    import_client = context.client.with_scope(CredentialScope.IMPORT)
    with unit_of_work() as session:
        row = steps.require(session, resumable.execution_id)
        submission = jobs.resume(import_client, row)
    if submission is None:  # pragma: no cover - _resumable_execution 已排除这一情形
        raise AssertionError("a resumable execution must carry a task reference")

    if progress is not None:
        progress.report(step=StepName.ASYNC_POLL.value, message=f"恢复任务 {submission.job_ref}")
    operator = resumable.operator or "recovery"
    try:
        response = jobs.poll(import_client, submission, cancel=cancel)
        report_document = jobs.report_of(response)
    except Dhis2Error as exc:
        return _needs_manual_check(
            context=context,
            execution_id=resumable.execution_id,
            environment_key=environment_key,
            project_id=resumable.project_id,
            operator=operator,
            idempotency_key=idempotency_key,
            payload_hash=resumable.payload_hash or "",
            reason=f"恢复轮询时仍无法确认该任务：{exc.message}",
            error=exc,
            job_ref=submission.job_ref,
        )
    with unit_of_work() as session:
        steps.finish_step(
            session,
            resumable.execution_id,
            StepName.ASYNC_POLL,
            detail={"jobRef": submission.job_ref, "polls": submission.polls, "resumed": True},
        )
    return _finalize(
        context=context,
        execution_id=resumable.execution_id,
        environment_key=environment_key,
        project_id=resumable.project_id,
        package_artifact_id=package_artifact_id,
        operator=operator,
        idempotency_key=idempotency_key,
        payload_hash=resumable.payload_hash or "",
        snapshot_artifact_id=resumable.snapshot_artifact_id,
        report_document=report_document,
    )


def _replay(begun: Any, idempotency_key: str) -> ExecutionOutcome | None:
    """同幂等键的重复调用只回放已记录的结果，绝不第二次提交（NFR-REL-003）。"""
    row = None
    with unit_of_work() as session:
        row = steps.find_by_idempotency_key(session, idempotency_key)
        if row is None:
            return None
        step_infos = steps.steps_as_info(session, row.id)
    if begun.created and row.status == steps.PLANSTATE:
        # 记录存在但从未真正开始（上一次调用在装配前就崩了）：继续执行它
        return None
    status = ExecutionStatus(row.status) if row.status in set(item.value for item in ExecutionStatus) else ExecutionStatus.NEEDS_MANUAL_CHECK
    return ExecutionOutcome(
        execution_id=row.id,
        status=status,
        report_artifact_id=row.report_artifact_id,
        import_report_artifact_id=row.import_report_artifact_id,
        applied=row.applied,
        payload_hash=row.payload_hash,
        environment_key=row.environment_key,
        steps=step_infos,
        detail=row.detail,
    )


def _read_only_session() -> Any:
    """给需要回读的地方一个会话（终态已落库，这里只读步骤清单）。"""
    from factory_foundation.api import current_session, has_active_uow, new_session

    if has_active_uow():
        return current_session()
    return new_session()


__all__ = ["commit"]
