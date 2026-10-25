"""能力与发现索引：``dx.capability_profile_index`` / ``dx.discovery_index``。

内容在 S0 产物存储里（DX-CAP-003、DX-DSC-005），本 schema 只保存“哪个环境在哪一刻有
哪份产物、其哈希是什么、是否过期”（D-14 数据零冗余）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_dhis2.models.base import DxTable


class CapabilityProfileIndexRow(DxTable):
    __tablename__ = "capability_profile_index"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    environment_key: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dhis2_version: Mapped[str] = mapped_column(String(32), nullable=False)
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: DX-CAP-005/006：版本变化后标记过期，下一次操作前强制重新探测
    stale: Mapped[bool] = mapped_column(nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    artifact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        Index("ix_dx_capability_env_time", "environment_key", "probed_at"),
        UniqueConstraint("artifact_id", name="uq_dx_capability_artifact"),
    )


class DiscoveryIndexRow(DxTable):
    __tablename__ = "discovery_index"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    environment_key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 可为空：设计基准环境的发现不属于某个项目
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: DX-DSC-002 的范围表达原样保存，便于复现“当时按什么范围发现”
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: DX-DSC-008：带幂等键的发现，重复调用返回同一产物
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    object_count: Mapped[int] = mapped_column(default=0)
    #: DX-DSC-010：发现索引要给出"当时那个实例是什么版本"，否则列表里这一栏永远是空的
    dhis2_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    blocking_issues: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_dx_discovery_artifact"),
        UniqueConstraint("idempotency_key", name="uq_dx_discovery_idem"),
        Index("ix_dx_discovery_env_time", "environment_key", "discovered_at"),
        Index("ix_dx_discovery_project", "project_id"),
    )


__all__ = ["CapabilityProfileIndexRow", "DiscoveryIndexRow", "Text"]
