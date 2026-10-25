"""Tracker Data Executor（M3，本轮**注册但拒绝**，DX-EXE-002、SRS 12 章）。

M3 要交付的是新 Tracker 导入 API 的 VALIDATE / COMMIT（DX-TRK-001）、合成数据约束
（DX-TRK-002 / DX-BR-10）与真实数据只进 DEV/TEST 的流程（DX-TRK-004）。这些判定都建立在
FIXTURE_PACKAGE 的内容格式与 Tracker 导入结果解析之上；本轮两者都没有，因此**一次 DHIS2
调用都不发**（见 :mod:`executors.base` 的拒绝约定）。

拒绝而不是缺席：``EXECUTORS`` 里有这一格，S1 按 DX-EXE-037 的固定顺序推进时会在走到
Tracker 这一格时拿到 ``DX_FEATURE_NOT_ENABLED``，从而知道“M3 还没到”，而不是在前一步之后
静悄悄少了两个执行器。
"""

from __future__ import annotations

from factory_dhis2.enums import ExecutorType
from factory_dhis2.executors.base import EXECUTOR_MILESTONES, EXECUTOR_RULES, NotEnabledExecutor

TRACKER_MILESTONE = EXECUTOR_MILESTONES[ExecutorType.TRACKER_DATA]
TRACKER_RULE = EXECUTOR_RULES[ExecutorType.TRACKER_DATA]


class TrackerDataExecutor(NotEnabledExecutor):
    """M3：接口存在、四个方法一律拒绝。"""

    def __init__(self) -> None:
        super().__init__(ExecutorType.TRACKER_DATA)


#: 注册用单例
TRACKER_EXECUTOR = TrackerDataExecutor()

__all__ = ["TRACKER_MILESTONE", "TRACKER_RULE", "TRACKER_EXECUTOR", "TrackerDataExecutor"]
