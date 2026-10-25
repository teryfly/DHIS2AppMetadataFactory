"""执行器注册表（DX-EXE-001/002/037、DX-BR-12）。

四个执行器都在这里，**包括本轮未启用的那三个**：注册的意义就是让“固定顺序”与“里程碑
分工”用同一套词汇成立。``ordered_executors`` 给出 DX-EXE-037 的顺序，``executor_for`` 按
类型解析——未启用的执行器照样解析得到，调用它时才拒绝。

``ENABLED_EXECUTORS`` 是里程碑分工的可查询视图（SRS 12 章：M2 只有 Metadata）；S1 想在编排
前知道“这一轮到底能跑哪些”，读这里，而不是靠试调用发现。
"""

from __future__ import annotations

from factory_dhis2.enums import EXECUTOR_ORDER, ExecutorType
from factory_dhis2.errors import FeatureNotEnabledError
from factory_dhis2.executors import apps as _apps
from factory_dhis2.executors import datastore as _datastore
from factory_dhis2.executors import metadata as _metadata
from factory_dhis2.executors import tracker as _tracker
from factory_dhis2.executors.base import (
    DEMO_USERS_FEATURE,
    DEMO_USERS_MILESTONE,
    DEMO_USERS_RULE,
    EXECUTOR_MILESTONES,
    EXECUTOR_RULES,
    Executor,
    NotEnabledExecutor,
    refuse,
)
from factory_dhis2.executors.demo_users import DEMO_USER_PROVISIONER, DemoUserProvisioner

#: 类型 → 执行器实例（本轮全部注册，含 M3/M5 的拒绝实现）
EXECUTORS: dict[ExecutorType, Executor] = {
    ExecutorType.METADATA: _metadata.METADATA_EXECUTOR,
    ExecutorType.TRACKER_DATA: _tracker.TRACKER_EXECUTOR,
    ExecutorType.DATASTORE: _datastore.DATASTORE_EXECUTOR,
    ExecutorType.APP_UPLOAD: _apps.APP_EXECUTOR,
}

#: 本轮真正可用的执行器（M2：只有 Metadata）
ENABLED_EXECUTORS: frozenset[ExecutorType] = frozenset(
    key for key, item in EXECUTORS.items() if getattr(item, "enabled", False)
)


def executor_for(executor_type: ExecutorType | str) -> Executor:
    """按类型解析执行器；名字不存在就是输入错误，而不是“大概没实现”。"""
    key = executor_type if isinstance(executor_type, ExecutorType) else ExecutorType(executor_type)
    found = EXECUTORS.get(key)
    if found is None:  # pragma: no cover - EXECUTORS 覆盖枚举全部成员
        raise FeatureNotEnabledError(
            f"S4 has no executor registered for type {key.value!r}",
            details={"executorType": key.value, "registered": sorted(item.value for item in EXECUTORS)},
        )
    return found


def ordered_executors(planned: list[str] | None = None) -> list[Executor]:
    """DX-EXE-037 / DX-BR-12：Metadata → DataStore → Tracker Data → App Upload。"""
    wanted = set(planned) if planned is not None else {item.value for item in EXECUTORS}
    return [EXECUTORS[item] for item in EXECUTOR_ORDER if item.value in wanted]


def require_enabled(executor_type: ExecutorType | str) -> Executor:
    """编排前的显式检查：未启用的执行器在这里就拒绝，而不是提交到一半才发现。"""
    key = executor_type if isinstance(executor_type, ExecutorType) else ExecutorType(executor_type)
    if key not in ENABLED_EXECUTORS:
        refuse(
            f"{key.value} executor",
            EXECUTOR_MILESTONES[key],
            rule=EXECUTOR_RULES[key],
            executorType=key.value,
            enabled=sorted(item.value for item in ENABLED_EXECUTORS),
        )
    return EXECUTORS[key]


__all__ = [
    "DEMO_USERS_FEATURE",
    "DEMO_USERS_MILESTONE",
    "DEMO_USERS_RULE",
    "DEMO_USER_PROVISIONER",
    "ENABLED_EXECUTORS",
    "EXECUTOR_MILESTONES",
    "EXECUTOR_RULES",
    "EXECUTORS",
    "DemoUserProvisioner",
    "Executor",
    "NotEnabledExecutor",
    "executor_for",
    "ordered_executors",
    "refuse",
    "require_enabled",
]
