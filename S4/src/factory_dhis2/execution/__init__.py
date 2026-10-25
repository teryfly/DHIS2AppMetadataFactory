"""执行管线（SRS 3.7、总体 SRS 15.2）。

PLAN → DRIFT CHECK → SNAPSHOT → VALIDATE → COMMIT → ASYNC POLL → POST_VERIFY →
MAINTENANCE → REPORT，每一步的状态都落在 ``dx.execution_step`` 上，因此同一幂等键的
重复调用是从已完成步骤继续，而不是重做（DX-EXE-004）。

``commit`` 与 ``validate`` 有意**不**在这里重导出：包属性一旦与同名子模块冲突，
``factory_dhis2.execution.commit`` 就解析成函数而不是模块，按模块定位（monkeypatch、
``import … as module``、reload）全部失效。调用方走完整路径
``factory_dhis2.execution.commit.commit``、``factory_dhis2.execution.validate.validate``。
"""

from __future__ import annotations

from factory_dhis2.execution.context import RunContext, load_context
from factory_dhis2.execution.drift import check_drift, drift_watch
from factory_dhis2.execution.jobs import Submission, poll, submit
from factory_dhis2.execution.pipeline import prepare_and_validate
from factory_dhis2.execution.plan import build_plan, resolve_import_options, save_plan
from factory_dhis2.execution.report import build_document as build_report_document
from factory_dhis2.execution.report import save as save_report_artifact
from factory_dhis2.execution.snapshot import build as build_snapshot
from factory_dhis2.execution.snapshot import env_state_hash
from factory_dhis2.execution.snapshot import save as save_snapshot
from factory_dhis2.execution.steps import (
    completed_steps,
    create_execution,
    find_by_idempotency_key,
    finish_step,
    steps_as_info,
)
from factory_dhis2.execution.validate import last_passed

__all__ = [
    "RunContext",
    "Submission",
    "build_plan",
    "build_report_document",
    "build_snapshot",
    "check_drift",
    "completed_steps",
    "create_execution",
    "drift_watch",
    "env_state_hash",
    "find_by_idempotency_key",
    "finish_step",
    "last_passed",
    "load_context",
    "poll",
    "prepare_and_validate",
    "resolve_import_options",
    "save_plan",
    "save_report_artifact",
    "save_snapshot",
    "steps_as_info",
    "submit",
]
