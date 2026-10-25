"""Metadata Executor（M2 唯一真正交付的执行器，DX-EXE-002、总体 SRS 15.1）。

它是“统一接口”（DX-EXE-001）在元数据上的实现：四个方法各自**委派**给
:mod:`factory_dhis2.execution` 里已实现并测试过的模块。这一点重要：计划、校验、提交、
部署后验证各只有一份实现，否则“执行器接口说的”和“管线做的”迟早会不一样，而 S1 看到的是
接口。

因此 :meth:`commit` 不采信调用方传来的 ``plan_document`` 与 ``state_hash``，而是重走
:func:`execution.commit.commit`：那条路径按幂等键找到已有执行记录，从**持久化**的计划、
Snapshot 与步骤状态继续（DX-EXE-004），并自己完成准入复核（期望哈希、最近一次成功
VALIDATE、紧急锁定、提交时间窗、环境锁、提交前漂移检查，DX-EXE-005/030/035）。执行器这条
路与 ``api.commit`` 的区别只是入口不同，结论与副作用完全一致——同一个幂等键重复调用不会
提交两次。
"""

from __future__ import annotations

from typing import Any

from factory_dhis2.dto.execution import ExecutionOutcome, PostVerifyOutcome
from factory_dhis2.enums import ExecutorType
from factory_dhis2.execution import commit as commit_module
from factory_dhis2.execution import plan as plan_module
from factory_dhis2.execution import post_verify as post_verify_module
from factory_dhis2.execution import validate as validate_module
from factory_dhis2.execution.context import RunContext
from factory_dhis2.execution.validate import ValidateResult


class MetadataExecutor:
    """元数据执行器：载荷是 METADATA_PACKAGE，M2 已全部实现。"""

    executor_type = ExecutorType.METADATA
    milestone = "M2"

    @property
    def enabled(self) -> bool:
        return True

    def plan(self, context: RunContext, **kwargs: Any) -> dict[str, Any]:
        """生成 DX-EXE-010 的计划文档；``save=True`` 时同时登记为 EXECUTION_PLAN 产物。"""
        build = plan_module.build_plan(
            context,
            import_options=kwargs.get("import_options"),
            maintenance_actions=kwargs.get("maintenance_actions"),
            expected_hash=kwargs.get("expected_hash"),
            executor_types=kwargs.get("executor_types"),
            deletion_payload=bool(kwargs.get("deletion_payload", False)),
        )
        if kwargs.get("save"):
            build.document["planArtifactId"] = plan_module.save_plan(context, build)
        return build.document

    def validate(
        self,
        context: RunContext,
        options: dict[str, Any],
        plan_document: dict[str, Any],
        *,
        state_hash: str,
        execution_id: str | None = None,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> ValidateResult:
        """服务端 VALIDATE（DX-EXE-025…029）：元数据支持，故直接走管线那一步。"""
        return validate_module.validate(
            context,
            import_options=options,
            plan_document=plan_document,
            env_state_hash=state_hash,
            execution_id=execution_id,
            progress=progress,
            cancel=cancel,
        )

    def commit(
        self,
        context: RunContext,
        options: dict[str, Any],
        plan_document: dict[str, Any],
        *,
        state_hash: str,
        execution_id: str,
        progress: Any | None = None,
        cancel: Any | None = None,
        operator: str = "s1-run-orchestrator",
        idempotency_key: str | None = None,
    ) -> ExecutionOutcome:
        """COMMIT（DX-EXE-032）：委派给已获准入的那条路径，不另写一遍提交逻辑。"""
        return commit_module.commit(
            context.environment_key,
            context.project_id or f"env-{context.environment_key}",
            context.package.artifact_id,
            context.payload_hash,
            operator=operator,
            idempotency_key=idempotency_key or f"commit:{execution_id}",
            progress=progress,
            cancel=cancel,
            import_options=options,
        )

    def verify(
        self,
        context: RunContext,
        *,
        execution_id: str | None = None,
        progress: Any | None = None,
        cancel: Any | None = None,
        operator: str = "system",
        idempotency_key: str | None = None,
    ) -> PostVerifyOutcome:
        """部署后验证（DX-EXE-040…042）：按 UID 回读并做表达式复查。"""
        return post_verify_module.post_verify(
            context.environment_key,
            context.project_id or f"env-{context.environment_key}",
            context.package.artifact_id,
            execution_id,
            operator=operator,
            idempotency_key=idempotency_key or f"post-verify:{execution_id or context.package.artifact_id}",
            progress=progress,
            cancel=cancel,
        )


METADATA_EXECUTOR = MetadataExecutor()

__all__ = ["METADATA_EXECUTOR", "MetadataExecutor"]
