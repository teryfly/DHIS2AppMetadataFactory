"""执行管线的公共类型（SRS 7.4）。

字段名与 S1 的镜像逐字对齐（``ports/dto.py``）：S1 会把返回值 ``model_dump()`` 后按它的
镜像重新校验，多余字段被忽略，缺字段用它的默认值——所以 **“有值”比“字段多”重要**：
``payload_hash`` 为空会让每个 Gate 看起来都是陈旧的。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from factory_dhis2.dto.issue import Issue
from factory_dhis2.enums import ExecutionStatus, ReconcileOutcome, StepStatus

_OUTPUT = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class StepInfo(BaseModel):
    """DX-EXE-004：步骤状态，续跑时据此跳过已完成步骤。"""

    model_config = _OUTPUT

    step: str
    status: str = StepStatus.PENDING.value
    started_at: str | None = None
    completed_at: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class DriftResult(BaseModel):
    """DX-EXE-020/021（AC-EXE-03）。"""

    model_config = _OUTPUT

    drifted: bool = False
    objects: list[dict[str, Any]] = Field(default_factory=list)
    environment_key: str | None = None
    checked_at: str | None = None


class Dhis2ValidationOutcome(BaseModel):
    """``prepare_and_validate`` 的输出（SRS 6.3：计划、Snapshot、ImportReport、漂移、问题）。"""

    model_config = _OUTPUT

    execution_plan_artifact_id: str | None = None
    snapshot_artifact_id: str | None = None
    import_report_artifact_id: str | None = None
    payload_artifact_id: str | None = None
    payload_hash: str | None = None
    passed: bool = True
    issues: list[Issue] = Field(default_factory=list)
    import_options: dict[str, Any] = Field(default_factory=dict)
    maintenance_actions: list[dict[str, Any]] = Field(default_factory=list)
    #: DX-EXE-011：非默认导入参数必须标记为“需在 DEPLOY_GATE 显式确认”
    requires_confirmation_items: list[dict[str, Any]] = Field(default_factory=list)
    execution_id: str | None = None
    drift: DriftResult | None = None
    environment_state_hash: str | None = None
    #: 支撑本次结论的 VALIDATE 记录（COMMIT 的准入依据，DX-EXE-005/029）
    validation_record_id: str | None = None
    #: 各步骤状态（续跑与报告的可见性；S1 的镜像忽略未知字段，不影响校验）
    steps: list[StepInfo] = Field(default_factory=list)
    #: DX-EXE-046：VALIDATE 也有执行报告产物，追溯链在这里不能断
    report_artifact_id: str | None = None


class ExecutionOutcome(BaseModel):
    """一次执行的结果（COMMIT、Fixture、删除、维护共用）。"""

    model_config = _OUTPUT

    execution_id: str | None = None
    status: ExecutionStatus = ExecutionStatus.COMPLETED
    report_artifact_id: str | None = None
    import_report_artifact_id: str | None = None
    applied: bool | None = None
    payload_hash: str | None = None
    steps: list[StepInfo] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    environment_key: str | None = None
    #: DX-TRK-003（M3）：演示用户只报告 Secret 引用
    demo_user_secret_refs: list[dict[str, Any]] = Field(default_factory=list)
    detail: str | None = None


class ReconcileResult(BaseModel):
    """DX-EXE-035：对账给出的判断与证据，人工确认由调用方决定。"""

    model_config = _OUTPUT

    outcome: ReconcileOutcome = ReconcileOutcome.UNKNOWN
    evidence: dict[str, Any] = Field(default_factory=dict)
    execution_id: str | None = None


class PostVerifyOutcome(BaseModel):
    """DX-EXE-040…042（AC-EXE-06）。"""

    model_config = _OUTPUT

    report_artifact_id: str | None = None
    consistent: bool = True
    issues: list[Issue] = Field(default_factory=list)
    execution_id: str | None = None
    compared_objects: int = 0


class DiffReportOutcome(BaseModel):
    """DX-DIFF-001。"""

    model_config = _OUTPUT

    diff_report_artifact_id: str | None = None
    objects: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class ExecutionStatusInfo(BaseModel):
    """DX-EXE-036：按幂等键查询执行状态（S1 在 COMMIT 重试前必须先确认）。"""

    model_config = _OUTPUT

    execution_id: str
    environment_key: str
    status: ExecutionStatus
    mode: str
    applied: bool | None = None
    idempotency_key: str | None = None
    payload_hash: str | None = None
    steps: list[StepInfo] = Field(default_factory=list)
    import_report_artifact_id: str | None = None
    snapshot_artifact_id: str | None = None
    report_artifact_id: str | None = None
    plan_artifact_id: str | None = None
    async_job_ref: str | None = None
    detail: str | None = None


__all__ = [
    "DiffReportOutcome",
    "Dhis2ValidationOutcome",
    "DriftResult",
    "ExecutionOutcome",
    "ExecutionStatusInfo",
    "PostVerifyOutcome",
    "ReconcileResult",
    "StepInfo",
]
