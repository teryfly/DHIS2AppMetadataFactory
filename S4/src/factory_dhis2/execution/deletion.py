"""定向删除（DX-EXE-038、FR-CMP-009、总体 SRS 15.6）。

删除是这套管线里**唯一不可逆且实例不提供回滚**的动作，所以它的准入比普通 COMMIT 更硬，
顺序也不能换：

1. 删除候选集是**它自己的载荷**（``importStrategy=DELETE`` / 逻辑资源 ``metadata.delete``），
   不是把上次部署的包反过来（DX-EXE-038）；
2. 先服务端 VALIDATE —— 与 COMMIT 同一载荷、同一参数，只换 ``importMode``（DX-BR-05 对删除
   同样成立：验过的就是提交的）；
3. 再**逐个候选**重做关联数据检查（DX-QRY-004）。VALIDATE 之后到删除之间环境可能被人写入
   了业务数据，所以这一步必须在提交前当场做一次；
4. PRODUCTION 按其 ``deletionPolicy`` 决定（``FORBID`` 一律拒绝）；紧急锁定同样拒绝
   （DX-ENV-007 / DX-BR-11）；
5. 只要有一个候选被判“有关联数据”，**整批不删**：删除载荷按 ``atomicMode=ALL`` 提交，
   删一半比不删更难解释。

任何拒绝都是 ``DX_DELETION_FORBIDDEN``，details 里给出被拒对象与依据；执行记录与锁的处理
与 COMMIT 同构（DX-EXE-030/031：结束即释放，异常时不留下无主的锁）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from factory_foundation.api import external_io, idempotency, structure_hash, unit_of_work

from factory_dhis2 import audit
from factory_dhis2.client.session import Dhis2Response
from factory_dhis2.dto.execution import ExecutionOutcome
from factory_dhis2.enums import (
    CredentialScope,
    DeletionPolicy,
    ExecutionMode,
    ExecutionStatus,
    StepName,
    StepStatus,
)
from factory_dhis2.environment.protection import get_protection, reject_if_locked
from factory_dhis2.envlock import manager as envlock
from factory_dhis2.errors import DeletionForbiddenError, Dhis2Error, InputArtifactInvalidError
from factory_dhis2.execution import jobs, report, snapshot, steps
from factory_dhis2.execution import validate as validate_module
from factory_dhis2.execution.context import RunContext, load_context
from factory_dhis2.execution.importreport import parse_report, save_report
from factory_dhis2.execution.plan import async_import_decision, build_plan
from factory_dhis2.payload.hash import verify_expected_hash
from factory_dhis2.payload.package import MetadataPackage
from factory_dhis2.registries import IdemScope

#: S2 把删除候选集放在载荷的这个集合键里（``envbound.prepared_payload``）
DELETION_COLLECTION = "@DELETE"

#: 删除使用的导入策略：与 COMMIT 同一载荷，只把策略换成 DELETE（DX-EXE-038）
DELETE_STRATEGY = "DELETE"


@external_io
def delete_objects(
    environment_key: str,
    project_id: str,
    deletion_artifact_id: str,
    expected_hash: str,
    *,
    operator: str,
    idempotency_key: str,
    progress: Any | None = None,
    cancel: Any | None = None,
) -> ExecutionOutcome:
    """提交一个删除载荷（SRS 7.4）。"""
    digest = structure_hash(
        {
            "operation": "delete_objects",
            "environmentKey": environment_key,
            "projectId": project_id,
            "deletionArtifactId": deletion_artifact_id,
            "expectedHash": expected_hash,
            "operator": operator,
        }
    )
    begun = idempotency.begin(IdemScope.EXECUTION, idempotency_key, digest)
    if begun.already_completed and begun.first_result:
        # 删除不可逆：同键重放只能给出第一次的结论，绝不能删第二次（NFR-REL-003）
        return ExecutionOutcome.model_validate(begun.first_result)

    in_flight = _in_flight_execution(idempotency_key)
    if in_flight is not None:
        # 上一次删除已经提交出去（可能崩在轮询里），幂等记录却没写下结论。此时**绝不**再建
        # 一次执行、更不再删一次：按 DX-EXE-035 的口径返回"待人工核实"，让调用方用已存的
        # 任务引用与 Snapshot 对账。没有这条分支时，同键重入会撞上执行行的幂等键唯一约束。
        return in_flight

    context = load_context(
        environment_key,
        deletion_artifact_id,
        project_id=project_id,
        scope=CredentialScope.IMPORT,
    )
    payload_hash = verify_expected_hash(context.package, expected_hash)
    candidates = deletion_candidates(context.package)

    # 准入 1：紧急锁定与删除策略（DX-ENV-007、DX-EXE-038）
    with unit_of_work() as session:
        reject_if_locked(session, environment_key, "DELETE")
        protection = get_protection(environment_key, session=session)
    _require_policy_allows(environment_key, protection, candidates)

    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key=environment_key,
            project_id=project_id,
            mode=ExecutionMode.DELETE,
            payload_artifact_id=deletion_artifact_id,
            payload_hash=payload_hash,
            idempotency_key=idempotency_key,
            operator=operator,
        )
        steps.mark_running(session, execution)
        envlock.acquire(session, environment_key, execution.id, operator=operator, note="DELETE")
        execution_id = execution.id

    try:
        plan = build_plan(
            context,
            import_options={"importStrategy": DELETE_STRATEGY},
            expected_hash=payload_hash,
            deletion_payload=True,
        )
        if plan.blockers:
            return _refused(
                context=context,
                environment_key=environment_key,
                project_id=project_id,
                execution_id=execution_id,
                payload_hash=payload_hash,
                operator=operator,
                idempotency_key=idempotency_key,
                reason="删除计划不成立（DX-EXE-012/038）",
                issues=plan.issues,
            )
        options = {**plan.import_options, "importStrategy": DELETE_STRATEGY}

        # 准入 2：Snapshot —— VALIDATE 三要素里的“那一刻状态”（DX-EXE-029），也是对账基准
        if progress is not None:
            progress.report(step=StepName.SNAPSHOT.value, completed_fraction=0.15)
        document = snapshot_document(context, cancel=cancel)
        state_hash = snapshot.env_state_hash(document)
        # 暂存在事务外、登记自开短事务，执行行随后在自己的短事务里更新（00 文档 6.2）
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
                detail={"snapshotArtifactId": snapshot_artifact_id, "envStateHash": state_hash},
            )

        # 准入 3：先 VALIDATE（DX-EXE-038 “提交前必须先 VALIDATE”）
        if progress is not None:
            progress.report(step=StepName.VALIDATE.value, completed_fraction=0.3)
        validation = validate_module.validate(
            context,
            import_options=options,
            plan_document=plan.document,
            env_state_hash=state_hash,
            execution_id=execution_id,
            progress=progress,
            cancel=cancel,
            check_server_expressions=False,
        )
        with unit_of_work() as session:
            steps.finish_step(
                session,
                execution_id,
                StepName.VALIDATE,
                status=StepStatus.COMPLETED if validation.passed else StepStatus.FAILED,
                detail={
                    "importReportArtifactId": validation.report_artifact_id,
                    "passed": validation.passed,
                },
            )
        if not validation.passed:
            return _refused(
                context=context,
                environment_key=environment_key,
                project_id=project_id,
                execution_id=execution_id,
                payload_hash=payload_hash,
                operator=operator,
                idempotency_key=idempotency_key,
                reason="删除载荷的服务端校验未通过：不提交删除",
                issues=validation.issues,
                import_report_artifact_id=validation.report_artifact_id,
                snapshot_artifact_id=snapshot_artifact_id,
            )

        # 准入 4：当场重做关联数据检查（DX-QRY-004）。判不出来就是“有关联数据”，即不许删
        if progress is not None:
            progress.report(step=StepName.DRIFT_CHECK.value, completed_fraction=0.45)
        refusals = _associated_data_refusals(environment_key, candidates, cancel=cancel)
        if refusals:
            with unit_of_work() as session:
                row = steps.require(session, execution_id)
                steps.set_status(
                    session,
                    row,
                    ExecutionStatus.FAILED,
                    applied=False,
                    detail="删除安全检查未通过",
                    finished=True,
                )
                envlock.release(
                    session, environment_key, execution_id, operator=operator, reason="DELETION_FORBIDDEN"
                )
            raise DeletionForbiddenError(
                f"{len(refusals)} 个删除候选仍有可识别的关联数据，整批删除被拒绝"
                "（DX-QRY-004、DX-EXE-038）",
                details={
                    "environmentKey": environment_key,
                    "executionId": execution_id,
                    "deletionArtifactId": deletion_artifact_id,
                    "payloadHash": payload_hash,
                    "refused": refusals,
                    "rule": "DX-EXE-038",
                },
            )

        # 提交删除：同一载荷、同一参数，importMode=COMMIT（DX-BR-05）
        if progress is not None:
            progress.report(step=StepName.COMMIT.value, completed_fraction=0.6)
        params = {**{key: str(value) for key, value in options.items()}, "importMode": "COMMIT"}
        submission = jobs.submit(
            context.client,
            resource="metadata.delete",
            body=context.package.payload,
            params=params,
            use_async=bool(
                async_import_decision(plan.document, context, context.package.object_count)[
                    "async"
                ]
            ),
            execution_id=execution_id,
        )
        response: Dhis2Response | None
        if submission.async_import:
            response = jobs.poll(context.client, submission, cancel=cancel)
            report_document = jobs.report_of(response)
        else:
            response = submission.response
            report_document = (
                response.payload
                if response is not None and isinstance(response.payload, dict)
                else {}
            )

        summary = parse_report(
            report_document, package=context.package, profile=context.target.profile
        )
        status = ExecutionStatus.COMPLETED if summary.passed else ExecutionStatus.FAILED
        import_report_artifact_id = save_report(
            project_id=project_id,
            document=report_document,
            mode="DELETE",
            environment_key=environment_key,
            source_artifact_ids=[deletion_artifact_id],
        )
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            row.import_report_artifact_id = import_report_artifact_id
            steps.finish_step(
                session,
                execution_id,
                StepName.COMMIT,
                status=StepStatus.COMPLETED if summary.passed else StepStatus.FAILED,
                detail={"httpStatus": response.status if response is not None else None},
            )
            steps.set_status(
                session,
                row,
                status,
                applied=summary.passed,
                detail=None if summary.passed else "实例拒绝了本次删除",
                finished=True,
            )
            envlock.release(
                session,
                environment_key,
                execution_id,
                operator=operator,
                reason="COMPLETED" if summary.passed else "FAILED",
            )
            audit.record(
                audit.DELETE_EXECUTED,
                object_type="Execution",
                object_id=execution_id,
                project_id=project_id,
                after={
                    "environmentKey": environment_key,
                    "deletionArtifactId": deletion_artifact_id,
                    "payloadHash": payload_hash,
                    "candidates": len(candidates),
                    "result": status.value,
                    "applied": summary.passed,
                    "importReportArtifactId": import_report_artifact_id,
                    "snapshotArtifactId": snapshot_artifact_id,
                    "deletionPolicy": protection.deletion_policy,
                    "environmentClass": protection.environment_class,
                },
                operator=operator,
                session=session,
            )
            step_infos = steps.steps_as_info(session, execution_id)

        # 执行报告是产物：暂存与登记都在事务外自开短事务，写完再回填执行行（00 文档 6.2）
        report_artifact_id = report.save(
            context,
            report.build_document(
                context=context,
                execution_id=execution_id,
                status=status,
                applied=summary.passed,
                steps=step_infos,
                import_report_artifact_id=import_report_artifact_id,
                snapshot_artifact_id=snapshot_artifact_id,
                operator=operator,
                issues=summary.issues,
                extra={"deletionCandidates": candidates, "importOptions": options},
            ),
        )
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            row.report_artifact_id = report_artifact_id

        outcome = ExecutionOutcome(
            execution_id=execution_id,
            status=status,
            report_artifact_id=report_artifact_id,
            import_report_artifact_id=import_report_artifact_id,
            applied=summary.passed,
            payload_hash=payload_hash,
            environment_key=environment_key,
            steps=step_infos,
            issues=summary.issues,
        )
        idempotency.complete(
            IdemScope.EXECUTION,
            idempotency_key,
            outcome.model_dump(mode="json"),
        )
        return outcome
    except DeletionForbiddenError:
        raise
    except Dhis2Error as exc:
        # 删除请求发出去之后失败：结果不明，锁保持（DX-EXE-031），由调用方对账后人工核实
        return _needs_manual_check(
            environment_key=environment_key,
            project_id=project_id,
            execution_id=execution_id,
            payload_hash=payload_hash,
            operator=operator,
            idempotency_key=idempotency_key,
            reason=f"删除请求无法确认：{exc.message}",
        )
    except Exception:
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
        raise


def deletion_candidates(package: MetadataPackage) -> list[dict[str, Any]]:
    """删除候选集：manifest ``deletionCandidates`` ∪ 载荷 ``@DELETE``（同一集合的两种写法）。

    两个来源都必须能按 UID 寻址——名字都对不上的候选不能送去删除，那只会删错东西或让
    实例报一串不知所云的错。
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = list(package.deletion_candidates())
    stated = package.payload.get(DELETION_COLLECTION)
    if isinstance(stated, list):
        rows += [item for item in stated if isinstance(item, dict)]
    for row in rows:
        uid = row.get("uid") or row.get("id")
        dhis2_type = row.get("dhis2Type") or row.get("dhis2_type")
        if (uid is None or dhis2_type is None) and isinstance(row.get("businessKey"), str):
            # manifest objects 里补一次：删除候选常只写 businessKey + reason
            for listed in package.objects():
                if listed.get("businessKey") == row["businessKey"]:
                    uid = uid or listed.get("uid")
                    dhis2_type = dhis2_type or listed.get("dhis2Type")
                    break
        if not isinstance(uid, str) or not uid or not isinstance(dhis2_type, str) or not dhis2_type:
            raise InputArtifactInvalidError(
                f"deletion candidate {row.get('businessKey') or row!r} has no resolvable "
                "(dhis2Type, uid) pair; an unaddressable object must never be deleted",
                artifact_id=package.artifact_id,
                details={
                    "artifactId": package.artifact_id,
                    "candidate": row.get("businessKey"),
                    "rule": "DX-EXE-038",
                },
            )
        key = (dhis2_type, uid)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "uid": uid,
                "dhis2_type": dhis2_type,
                "business_key": row.get("businessKey"),
                "reason": row.get("reason"),
                "program": row.get("program"),
            }
        )
    return sorted(out, key=lambda item: (item["dhis2_type"], item["uid"]))


def associated_data_check(
    environment_key: str, dhis2_type: str, uid: str, *, program: str | None = None
) -> Any:
    """DX-QRY-004 的接缝：只读查询，自己的短事务与 discovery 范围都在那侧处理。"""
    from factory_dhis2.queries.associated_data import has_associated_data

    return has_associated_data(environment_key, dhis2_type, uid, program=program)


def verdict_has_data(result: Any) -> bool:
    """把关联数据检查的返回归一成“有没有关联数据”。

    查询侧返回的是 :class:`factory_dhis2.queries.associated_data.AssociatedDataVerdict`
    （带依据的 Mapping），但也允许是裸 ``bool``；删除侧只认一个保守方向的答案：
    **认不出来就当有**（DX-QRY-004）。
    """
    if isinstance(result, bool):
        return result
    if isinstance(result, Mapping):
        for key in ("hasData", "has_data", "associated", "hasAssociatedData"):
            if key in result:
                return bool(result[key])
        return True
    for attribute in ("has_data", "hasData"):
        value = getattr(result, attribute, None)
        if value is not None:
            return bool(value)
    return True


def _associated_data_refusals(
    environment_key: str,
    candidates: list[dict[str, Any]],
    *,
    cancel: Any | None = None,
) -> list[dict[str, Any]]:
    refused: list[dict[str, Any]] = []
    for candidate in candidates:
        if cancel is not None:
            cancel.raise_if_cancelled()
        try:
            result = associated_data_check(
                environment_key,
                candidate["dhis2_type"],
                candidate["uid"],
                program=candidate.get("program"),
            )
        except Dhis2Error as exc:
            # 判不出来就不许删（DX-QRY-004 的保守方向）：把无法判定当成有关联数据
            refused.append({**candidate, "evidence": f"关联数据检查无法完成：{exc.message}"})
            continue
        if verdict_has_data(result):
            refused.append({**candidate, "evidence": _evidence_of(result)})
    return refused


def _evidence_of(result: Any) -> Any:
    """把判定依据带进拒绝细节：被拒的对象要能解释为什么被拒。"""
    if isinstance(result, Mapping):
        for key in ("evidence", "basis", "detail"):
            if result.get(key):
                return {key: result[key]}
    for attribute in ("evidence", "basis", "detail"):
        value = getattr(result, attribute, None)
        if value:
            return value
    return "关联数据检查判定为“有关联数据”"


def _require_policy_allows(
    environment_key: str, protection: Any, candidates: list[dict[str, Any]]
) -> None:
    """DX-EXE-038：PRODUCTION 按其删除策略决定；``FORBID`` 对任何类别都是一律拒绝。"""
    if protection.deletion_policy != DeletionPolicy.FORBID.value:
        return
    raise DeletionForbiddenError(
        f"environment {environment_key!r} has deletionPolicy=FORBID; the deletion payload "
        f"with {len(candidates)} candidate(s) is refused",
        details={
            "environmentKey": environment_key,
            "environmentClass": protection.environment_class,
            "deletionPolicy": protection.deletion_policy,
            "candidates": len(candidates),
            "rule": "DX-EXE-038",
        },
    )


def snapshot_document(context: RunContext, *, cancel: Any | None = None) -> dict[str, Any]:
    """删除前的 Snapshot：内容哈希的定义只有一处，就是 :mod:`execution.snapshot`。"""
    return snapshot.build(context, kind=snapshot.KIND_PRE_COMMIT, cancel=cancel)


def _refused(
    *,
    context: RunContext,
    environment_key: str,
    project_id: str | None,
    execution_id: str,
    payload_hash: str,
    operator: str,
    idempotency_key: str,
    reason: str,
    issues: list[Any],
    import_report_artifact_id: str | None = None,
    snapshot_artifact_id: str | None = None,
) -> ExecutionOutcome:
    """准入未过：状态落库、锁释放、返回未通过——绝不“试试看”地提交删除。"""
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        steps.set_status(
            session, row, ExecutionStatus.FAILED, applied=False, detail=reason, finished=True
        )
        envlock.release(session, environment_key, execution_id, operator=operator, reason="REFUSED")
        step_infos = steps.steps_as_info(session, execution_id)

    report_artifact_id: str | None = None
    if snapshot_artifact_id is not None:
        # 已经留下了事实（一份 Snapshot、一次 VALIDATE）就必须进执行报告（DX-EXE-046）
        report_artifact_id = report.save(
            context,
            report.build_document(
                context=context,
                execution_id=execution_id,
                status=ExecutionStatus.FAILED,
                applied=False,
                steps=step_infos,
                import_report_artifact_id=import_report_artifact_id,
                snapshot_artifact_id=snapshot_artifact_id,
                operator=operator,
                issues=issues,
                extra={"reason": reason},
            ),
        )
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            row.report_artifact_id = report_artifact_id
            step_infos = steps.steps_as_info(session, execution_id)
    outcome = ExecutionOutcome(
        execution_id=execution_id,
        status=ExecutionStatus.FAILED,
        report_artifact_id=report_artifact_id,
        import_report_artifact_id=import_report_artifact_id,
        applied=False,
        payload_hash=payload_hash,
        environment_key=environment_key,
        steps=step_infos,
        issues=issues,
        detail=reason,
    )
    idempotency.complete(IdemScope.EXECUTION, idempotency_key, outcome.model_dump(mode="json"))
    return outcome


def _in_flight_execution(idempotency_key: str) -> ExecutionOutcome | None:
    """同幂等键已有一次未完成的删除时，返回"待人工核实"结论而不是再删一次（DX-EXE-035）。"""
    with unit_of_work() as session:
        row = steps.find_by_idempotency_key(session, idempotency_key)
        if row is None or row.status not in (steps.PLANSTATE, steps.RUNSTATE):
            return None
        step_infos = steps.steps_as_info(session, row.id)
        job_ref = row.async_job_ref or row.poll_url
        reason = (
            "该幂等键已有一次删除在进行中：删除不可逆，S4 不会重发（DX-EXE-034、NFR-REL-003）。"
            "请按已持久化的任务引用与 Snapshot 对账后人工核实"
            + (f"（任务 {job_ref}）" if job_ref else "（尚无任务引用）")
        )
        steps.set_status(
            session, row, ExecutionStatus.NEEDS_MANUAL_CHECK, applied=None, detail=reason
        )
        audit.record(
            audit.COMMIT_NEEDS_MANUAL_CHECK,
            object_type="Execution",
            object_id=row.id,
            project_id=row.project_id,
            after={"reason": "REPEATED_DELETE_KEY", "jobRef": job_ref, "lockHeld": True},
            session=session,
        )
        return ExecutionOutcome(
            execution_id=row.id,
            status=ExecutionStatus.NEEDS_MANUAL_CHECK,
            applied=None,
            payload_hash=row.payload_hash,
            environment_key=row.environment_key,
            steps=step_infos,
            detail=reason,
            issues=[],
        )


def _needs_manual_check(
    *,
    environment_key: str,
    project_id: str | None,
    execution_id: str,
    payload_hash: str,
    operator: str,
    idempotency_key: str,
    reason: str,
) -> ExecutionOutcome:
    """删除请求结果不明：NEEDS_MANUAL_CHECK + 锁保持（DX-EXE-031/035）。"""
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
        audit.record(
            audit.COMMIT_NEEDS_MANUAL_CHECK,
            object_type="Execution",
            object_id=execution_id,
            project_id=project_id,
            after={"reason": reason, "lockHeld": True, "mode": ExecutionMode.DELETE.value},
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
    idempotency.complete(
        IdemScope.EXECUTION,
        idempotency_key,
        outcome.model_dump(mode="json"),
    )
    return outcome


__all__ = [
    "DELETE_STRATEGY",
    "DELETION_COLLECTION",
    "associated_data_check",
    "deletion_candidates",
    "delete_objects",
    "verdict_has_data",
    "snapshot_document",
]
