"""执行器接口与“里程碑未启用”的拒绝约定（DX-EXE-001/002、SRS 12 章）。

DX-EXE-001 要求每个执行器都是同一张脸：``plan`` / ``validate`` / ``commit`` / ``verify``。
调用方（S1 的 Run 编排，DX-EXE-037）因此可以按固定顺序 Metadata → DataStore → Tracker →
App Upload 逐个推进，而不需要知道某个执行器这一轮到底有没有实现——**没实现的那一个会在
自己的一格里明确拒绝**，而不是悄悄跳过。

拒绝的约定沿用 S2 的 ``BP_FEATURE_NOT_ENABLED``（SRS 8 章“同一约定”）：

* 抛 :class:`factory_dhis2.errors.FeatureNotEnabledError`，代码 ``DX_FEATURE_NOT_ENABLED``；
* 消息里点名它属于哪个里程碑，调用方一看就知道“这是 M3/M5 的活，不是这里坏了”；
* 登记在 :mod:`factory_dhis2.registries` 里是**不可重试**的：重试一个尚未交付的能力只会
  得到同一个拒绝。

未启用的执行器仍然**注册**（``EXECUTORS`` 里有它），这样 DX-EXE-002 的里程碑清单、
DX-EXE-037 的固定顺序与计划的能力检查（DX-EXE-012）都按同一套词汇工作，不会出现“因为没
实现所以名字都没有”的漏洞。
"""

from __future__ import annotations

from typing import Any, NoReturn, Protocol, runtime_checkable

from factory_dhis2.dto.execution import ExecutionOutcome, PostVerifyOutcome
from factory_dhis2.enums import ExecutorType
from factory_dhis2.errors import FeatureNotEnabledError
from factory_dhis2.execution.context import RunContext

#: DX-EXE-002：执行器属于哪个里程碑（SRS 12 章的分工表）
EXECUTOR_MILESTONES: dict[ExecutorType, str] = {
    ExecutorType.METADATA: "M2",
    ExecutorType.TRACKER_DATA: "M3",
    ExecutorType.DATASTORE: "M5",
    ExecutorType.APP_UPLOAD: "M5",
}

#: 拒绝消息里给出的 SRS 出处，让调用方不需要再翻文档
EXECUTOR_RULES: dict[ExecutorType, str] = {
    ExecutorType.METADATA: "DX-EXE-025",
    ExecutorType.TRACKER_DATA: "DX-TRK-001",
    ExecutorType.DATASTORE: "DX-M5-001",
    ExecutorType.APP_UPLOAD: "DX-M5-002",
}

#: 演示用户不属于 ``ExecutorType``（它是 Fixture 导入里的一步），单独登记（DX-TRK-003）
DEMO_USERS_FEATURE = "demo_users"
DEMO_USERS_MILESTONE = "M3"
DEMO_USERS_RULE = "DX-TRK-003"


@runtime_checkable
class Executor(Protocol):
    """统一执行器接口（DX-EXE-001）。

    ``context`` 一律是 :class:`execution.context.RunContext`：环境目标 + 载荷 + Client。
    执行器不自己装配环境，也就无从绕过“显式绑定环境”这条线（DX-BR-02）。
    """

    executor_type: ExecutorType

    def plan(self, context: RunContext, **kwargs: Any) -> dict[str, Any]:
        """产出该执行器这次的执行计划文档（DX-EXE-010）。"""
        ...

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
    ) -> Any:
        """服务端校验；不支持服务端 VALIDATE 的执行器必须明确拒绝或走自定义校验。"""
        ...

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
    ) -> ExecutionOutcome:
        """提交（DX-EXE-032）。"""
        ...

    def verify(
        self,
        context: RunContext,
        *,
        execution_id: str | None = None,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> PostVerifyOutcome:
        """部署后验证（DX-EXE-040…042）。"""
        ...


def refuse(feature: str, milestone: str, *, rule: str, **details: Any) -> NoReturn:
    """里程碑未交付的能力一律拒绝，并且**一次外部调用都不发**。

    拒绝发生在任何 DHIS2 访问之前：一个还没实现的执行器如果先去问实例，就是在用真实环境
    做试验（总体 SRS 4.4 的“执行层只做协议与执行”不包括替未交付的功能猜行为）。
    """
    raise FeatureNotEnabledError(
        f"{feature} is not enabled in this build of S4: it is delivered with {milestone} "
        f"(SRS 12 章里程碑分工), {rule}",
        details={"feature": feature, "milestone": milestone, "rule": rule, **details},
    )


class NotEnabledExecutor:
    """属于后续里程碑的执行器：注册存在，四个方法一律拒绝。"""

    def __init__(self, executor_type: ExecutorType) -> None:
        self.executor_type = executor_type
        self.milestone = EXECUTOR_MILESTONES[executor_type]
        self.rule = EXECUTOR_RULES[executor_type]

    @property
    def enabled(self) -> bool:
        return False

    def _refuse(self, operation: str) -> NoReturn:
        refuse(
            f"{self.executor_type.value} executor {operation}",
            self.milestone,
            rule=self.rule,
            executorType=self.executor_type.value,
        )

    def plan(self, context: RunContext, **kwargs: Any) -> dict[str, Any]:
        return self._refuse("plan")

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
    ) -> Any:
        return self._refuse("validate")

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
    ) -> ExecutionOutcome:
        return self._refuse("commit")

    def verify(
        self,
        context: RunContext,
        *,
        execution_id: str | None = None,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> PostVerifyOutcome:
        return self._refuse("verify")


__all__ = [
    "DEMO_USERS_FEATURE",
    "DEMO_USERS_MILESTONE",
    "DEMO_USERS_RULE",
    "EXECUTOR_MILESTONES",
    "EXECUTOR_RULES",
    "Executor",
    "NotEnabledExecutor",
    "refuse",
]
