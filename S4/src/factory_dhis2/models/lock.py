"""``dx.environment_lock`` —— 环境级提交锁（DX-EXE-030/031、NFR-CON-002、FR-EXE-012）。

主键就是 environmentKey：**一个环境最多一行**，因此锁天然跨项目互斥（执行行里的
project_id 与这里无关）。表中**没有过期时间列**——锁不因超时自动释放（DX-EXE-031），
只能由执行结束时或人工核实后释放。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from factory_dhis2.models.base import DxTable


class EnvironmentLockRow(DxTable):
    __tablename__ = "environment_lock"

    environment_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: 持锁的执行；为空表示当前无人持锁
    holder_execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 谁释放的、以什么理由（审计的镜像，便于解释一次等待）
    released_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 一次待人工核实的持锁说明（DX-AC-04：另一个项目此时提交会得到 CONCURRENCY_ERROR）
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_dx_lock_holder", "holder_execution_id"),)


__all__ = ["EnvironmentLockRow"]
