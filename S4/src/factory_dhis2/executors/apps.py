"""App Upload Executor（M5，本轮**注册但拒绝**，DX-M5-002、SRS 12 章）。

应用上传同样是**没有服务端 VALIDATE** 的执行器（DX-M5-002：上传后读取应用列表确认版本），
它需要的产物形态（自定义应用包 + 版本清单）与上传接口参数都由 M5 定义。本轮既没有包格式
也不该用真实实例试写，因此四个方法一律拒绝（:mod:`executors.base` 的约定）。

顺序上它排在最后（DX-EXE-037：Metadata → DataStore → Tracker → App Upload），所以 M2 的
部署根本不会走到这一格；被登记出来是为了让“执行器清单”与 SRS 12 章的里程碑分工一一对得上。
"""

from __future__ import annotations

from factory_dhis2.enums import ExecutorType
from factory_dhis2.executors.base import EXECUTOR_MILESTONES, EXECUTOR_RULES, NotEnabledExecutor

APP_UPLOAD_MILESTONE = EXECUTOR_MILESTONES[ExecutorType.APP_UPLOAD]
APP_UPLOAD_RULE = EXECUTOR_RULES[ExecutorType.APP_UPLOAD]


class AppUploadExecutor(NotEnabledExecutor):
    """M5：接口存在、四个方法一律拒绝。"""

    def __init__(self) -> None:
        super().__init__(ExecutorType.APP_UPLOAD)


APP_EXECUTOR = AppUploadExecutor()

__all__ = ["APP_UPLOAD_MILESTONE", "APP_UPLOAD_RULE", "APP_EXECUTOR", "AppUploadExecutor"]
