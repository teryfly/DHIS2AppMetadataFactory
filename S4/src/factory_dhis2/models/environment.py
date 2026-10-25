"""``dx.environment`` / ``environment_credential`` / ``environment_protection`` /
``connection_test``（DX-ENV-001…009、DX-CON-004）。

环境是**全局对象**，不承载项目信息（D-03、DX-ENV-009）：这里没有任何 project_id 列。
凭证只存 Secret 引用（DX-ENV-003），本表在结构上就没有能装下凭证明文的字段。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_dhis2.enums import (
    AuthMode,
    DeletionPolicy,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.models.base import DxTable


class EnvironmentRow(DxTable):
    __tablename__ = "environment"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: environmentKey：S4 每个公共函数的显式第一参数（DX-ENV-004，无默认环境）
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    environment_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EnvironmentClass.TEST.value
    )
    version_constraint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auth_mode: Mapped[str] = mapped_column(String(8), nullable=False, default=AuthMode.PAT.value)
    tls_required: Mapped[bool] = mapped_column(nullable=False, default=True)
    llm_data_policy: Mapped[str] = mapped_column(
        String(24), nullable=False, default=LlmDataPolicy.PRIVATE_NETWORK.value
    )
    #: DX-NFR-01：默认 60 秒，可按环境配置
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EnvironmentStatus.ACTIVE.value
    )
    #: DX-EXE-033：异步导入阈值的**按环境覆盖**；为空时取全局配置（默认 500）
    async_import_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: DX-ENV-009：环境可被多个项目登记为目标环境，此处不记录项目
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_dx_environment_class", "environment_class", "status"),)


class EnvironmentCredentialRow(DxTable):
    """按凭证范围的 Secret 引用（DX-ENV-003、总体 SRS 21.3）。"""

    __tablename__ = "environment_credential"

    environment_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    #: DX-ENV-006：到期日期，供“即将到期凭证”查询
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    __table_args__ = (
        # 到期扫描是 S1 的每日任务（DX-ENV-006）
        Index("ix_dx_credential_expires_at", "expires_at"),
    )


class EnvironmentProtectionRow(DxTable):
    """PRODUCTION 类环境的额外保护设置（DX-ENV-002、FR-ENV-001）。"""

    __tablename__ = "environment_protection"

    #: 一环境一行：主键就是 environmentKey
    environment_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: 时间窗按环境时区解释（DX-ENV-002）
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    #: ``[{"days":[2,4],"start":"20:00","end":"23:00"}]``，ISO 星期 1=周一
    commit_windows: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    deletion_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DeletionPolicy.SAFE_ONLY.value
    )
    #: DX-ENV-007：开启后 COMMIT / Fixture / 维护动作全部拒绝，读操作不受影响
    emergency_lock: Mapped[bool] = mapped_column(nullable=False, default=False)
    lock_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    lock_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lock_changed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: M4 才生效的两个字段（DX-ENV-002【调整】）：现在就建表，读取时拒绝非默认值
    required_approvals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operator_role: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ConnectionTestRow(DxTable):
    """连接测试结果持久化（DX-CON-004）。"""

    __tablename__ = "connection_test"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    environment_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dhis2_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 每个检查项的结果：``[{"name":"tls","state":"PASSED","detail":...}]``
    checks: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    #: DX-CON-002：按凭证范围报告缺少哪些权限
    missing_authorities: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    #: DX-CON-002 的另一半：当时**实际具备**的权限。只存缺失项的话，事后无法复核
    #  “那次测试看到的账户到底有什么”，``get_last_connection_test`` 也只能报空。
    granted_authorities: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    required_scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    capability_profile_artifact_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    operator: Mapped[str | None] = mapped_column(String(120), nullable=True)

    __table_args__ = (
        UniqueConstraint("id", name="uq_dx_connection_test_id"),
        Index("ix_dx_connection_test_env_time", "environment_key", "tested_at"),
    )


__all__ = [
    "ConnectionTestRow",
    "EnvironmentCredentialRow",
    "EnvironmentProtectionRow",
    "EnvironmentRow",
]
