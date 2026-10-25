"""即将到期凭证查询（DX-ENV-006）。

S1 的每日任务调用它并通知负责人：轮换一条生产环境的导入令牌需要在到期**之前**做，
到期之后做就是一次停摆。这里只报告引用与到期日——Fake 也好、真实也好，都不需要凭证值
（DX-BR-03）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2.dto.environment import ExpiringCredential
from factory_dhis2.enums import EnvironmentStatus
from factory_dhis2.models.environment import (
    EnvironmentCredentialRow,
    EnvironmentRow,
)


def list_expiring_credentials(
    *,
    within_days: int = 14,
    session: Session,
    now: datetime | None = None,
) -> list[ExpiringCredential]:
    """默认 14 天内到期的凭证引用清单。"""
    reference = now or datetime.now(UTC)
    horizon = reference + timedelta(days=within_days)
    rows = session.execute(
        select(EnvironmentCredentialRow, EnvironmentRow.name)
        .join(EnvironmentRow, EnvironmentRow.key == EnvironmentCredentialRow.environment_key)
        .where(
            EnvironmentCredentialRow.expires_at.is_not(None),
            EnvironmentCredentialRow.expires_at <= horizon,
            EnvironmentRow.status == EnvironmentStatus.ACTIVE.value,
        )
        .order_by(EnvironmentCredentialRow.expires_at)
    ).all()
    out: list[ExpiringCredential] = []
    for credential, _name in rows:
        expires_at = credential.expires_at
        days_left = (
            max(0, (expires_at - reference).days)
            if expires_at is not None
            else None
        )
        out.append(
            ExpiringCredential(
                environment_key=credential.environment_key,
                secret_ref=credential.secret_ref,
                expires_at=expires_at,
                scope=credential.scope,
                days_left=days_left,
            )
        )
    return out


def already_expired(
    *, session: Session, now: datetime | None = None
) -> list[ExpiringCredential]:
    """已过期但仍在使用的凭证（连接测试之外的一道提醒）。"""
    reference = now or datetime.now(UTC)
    rows = session.execute(
        select(EnvironmentCredentialRow).where(
            EnvironmentCredentialRow.expires_at.is_not(None),
            EnvironmentCredentialRow.expires_at < reference,
        )
    ).scalars().all()
    return [
        ExpiringCredential(
            environment_key=row.environment_key,
            secret_ref=row.secret_ref,
            expires_at=row.expires_at,
            scope=row.scope,
            days_left=0,
        )
        for row in rows
    ]


__all__ = ["already_expired", "list_expiring_credentials"]
