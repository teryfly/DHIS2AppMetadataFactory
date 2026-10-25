"""执行与状态：``dx.execution`` / ``execution_step`` / ``validation_record`` /
``snapshot_index``（SRS 5 章、DX-EXE-004、DX-EXE-029、DX-EXE-034）。

这三张表共同支撑“崩溃后从已持久化步骤继续，而不是重做”（DX-EXE-004、NFR-REL-004、
DX-AC-03）：任务 ID 与轮询地址在提交后**立即**落库，步骤状态逐条落库，VALIDATE 结果与
其“哈希 + 环境 + 那一刻环境状态”三要素一起落库（DX-EXE-029）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_dhis2.enums import ExecutionMode, ExecutionStatus, ExecutorType
from factory_dhis2.models.base import DxTable

#: 活跃态：占着环境锁或还需要人工核实的状态（DX-ENV-008/009 的判定依据）。
ACTIVE_STATUSES: tuple[str, ...] = (
    ExecutionStatus.FAILED.value,  # 未确认，见下
    ExecutionStatus.NEEDS_MANUAL_CHECK.value,
)


class ExecutionRow(DxTable):
    __tablename__ = "execution"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: 执行可以不属于某个项目（如对未绑定环境的维护动作）
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    environment_key: Mapped[str] = mapped_column(String(64), nullable=False)
    executor_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ExecutorType.METADATA.value
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default=ExecutionMode.VALIDATE.value)

    payload_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    plan_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    import_report_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    snapshot_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    report_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: DX-EXE-036：按幂等键查询执行状态是 COMMIT 重试前的唯一安全路径
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: DX-EXE-033/034：异步任务 ID 与查询地址，提交后立刻持久化
    async_job_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    poll_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ExecutionStatus.FAILED.value
    )
    #: “是否已生效”（DX-EXE-035 对账与 S1 的 ``applied`` 字段）
    applied: Mapped[bool | None] = mapped_column(nullable=True)

    #: DX-EXE-029 的第三要素：那一刻的环境状态哈希（由 Snapshot 派生）
    env_state_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    object_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 结果不明或人工核实的说明（不含任何凭证）
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    operator: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: NEEDS_MANUAL_CHECK 的解决记录（DX-EXE-031）
    manual_check_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    manual_check_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_dx_execution_idem"),
        # 进行中的执行按环境查（DX-ENV-008/009、环境锁）
        Index("ix_dx_execution_env_status", "environment_key", "status"),
        Index("ix_dx_execution_project", "project_id"),
    )


class ExecutionStepRow(DxTable):
    """DX-EXE-004：步骤级持久化，同幂等键重复调用时从已完成步骤之后继续。"""

    __tablename__ = "execution_step"

    execution_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    step: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 该步产出的产物 ID、计数等，续跑时直接复用而不重做
    detail: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_dx_execution_step_status", "execution_id", "status"),)


class ValidationRecordRow(DxTable):
    """DX-EXE-005/029：一次 VALIDATE 只对“载荷哈希 + 环境 + 那一刻状态”有效。"""

    __tablename__ = "validation_record"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    environment_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    env_state_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(32), nullable=False)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: PASSED / FAILED：COMMIT 只接受“该哈希 + 该状态”的最近一次 PASSED
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    import_report_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    snapshot_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        # 一次 VALIDATE 在同一环境状态下只留一个结论
        UniqueConstraint(
            "environment_key",
            "payload_hash",
            "env_state_hash",
            "result",
            name="uq_dx_validation_record_triple",
        ),
        Index("ix_dx_validation_record_env_time", "environment_key", "validated_at"),
    )


class SnapshotIndexRow(DxTable):
    """DX-EXE-022/023：Factory Snapshot 的索引（内容在产物存储，分级 CONFIDENTIAL）。"""

    __tablename__ = "snapshot_index"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    environment_key: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: ``PRE_COMMIT`` / ``POST_COMMIT`` / ``CURRENT``（DX-DIFF-001 的两类对比来源）
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="PRE_COMMIT")
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    dhis2_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    object_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at_snapshot: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_dx_snapshot_artifact"),
        Index("ix_dx_snapshot_env_time", "environment_key", "created_at_snapshot"),
        Index("ix_dx_snapshot_execution", "execution_id"),
    )


class DemoUserCredentialRow(DxTable):
    """DX-TRK-003（M3）：演示用户密码只以 Secret 引用记录。"""

    __tablename__ = "demo_user_credential"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_dx_demo_user_execution", "execution_id"),)


__all__ = [
    "ACTIVE_STATUSES",
    "DemoUserCredentialRow",
    "ExecutionRow",
    "ExecutionStepRow",
    "SnapshotIndexRow",
    "ValidationRecordRow",
]
