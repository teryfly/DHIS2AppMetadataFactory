"""DataStore Executor（M5，本轮**注册但拒绝**，DX-M5-001、SRS 12 章）。

DataStore 没有服务端 VALIDATE（DX-M5-001），所以它的“校验”必须由 S4 自己实现：namespace /
key / value 变更集的自定义校验 + 写入后的读后比对。这套东西与 S2 的
``DATASTORE_CHANGESET`` 产物格式一起属于 M5；本轮没有该格式的读取实现，因此不在这里猜格式、
也不发任何一次 dataStore 写请求（:mod:`executors.base` 的拒绝约定）。

``dataStore.write`` 已经登记在 2.41 的资源目录里，所以**端点存在**不代表**执行器可用**——
能力检查（DX-EXE-012）与里程碑判定是两件事，后者只能由这里给出。
"""

from __future__ import annotations

from factory_dhis2.enums import ExecutorType
from factory_dhis2.executors.base import EXECUTOR_MILESTONES, EXECUTOR_RULES, NotEnabledExecutor

DATASTORE_MILESTONE = EXECUTOR_MILESTONES[ExecutorType.DATASTORE]
DATASTORE_RULE = EXECUTOR_RULES[ExecutorType.DATASTORE]


class DataStoreExecutor(NotEnabledExecutor):
    """M5：接口存在、四个方法一律拒绝。"""

    def __init__(self) -> None:
        super().__init__(ExecutorType.DATASTORE)


DATASTORE_EXECUTOR = DataStoreExecutor()

__all__ = ["DATASTORE_MILESTONE", "DATASTORE_RULE", "DATASTORE_EXECUTOR", "DataStoreExecutor"]
