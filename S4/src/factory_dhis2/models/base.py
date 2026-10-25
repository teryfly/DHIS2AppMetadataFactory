"""``dx`` schema 表基类（SRS 5 章引言：“全部实体含创建与更新时间，略”）。

时间戳在这里提供，schema 只在 :class:`factory_foundation.api.DxBase` 上绑定一次，所以
没有一张表自己声明 ``schema=``，跨 schema 外键检查器看到的正好是 ``dx`` 的表
（00 文档 4.1、D-08）。
"""

from __future__ import annotations

from datetime import datetime

from factory_foundation.api import DxBase
from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DxTable(TimestampMixin, DxBase):
    """S4 拥有的每张表的抽象基类。"""

    __abstract__ = True


__all__ = ["DxTable", "TimestampMixin"]
