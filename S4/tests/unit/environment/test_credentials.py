"""即将到期凭证查询（DX-ENV-006、DX-BR-03）。

轮换一条生产环境的导入令牌必须在到期**之前**做，所以 S1 的每日任务靠这个查询提前预警。
查询只报告引用与到期日：既不需要、也不允许拿到凭证值。禁用中的环境不再被使用，也不该
继续出现在预警清单里。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from factory_foundation.api import unit_of_work
from sqlalchemy.orm import Session

from factory_dhis2.dto.environment import CredentialRef, EnvironmentSpec
from factory_dhis2.enums import CredentialScope, EnvironmentClass
from factory_dhis2.environment.credentials import already_expired, list_expiring_credentials
from factory_dhis2.environment.service import create_environment, disable_environment
from factory_dhis2.models.environment import EnvironmentCredentialRow

pytestmark = pytest.mark.usefixtures("harness")

#: 固定“现在”：预警窗口的判定必须是可重复的
NOW = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)


def register(
    session: Session,
    key: str,
    *,
    expiring_in: timedelta | None = None,
    environment_class: EnvironmentClass = EnvironmentClass.TEST,
    scopes: tuple[CredentialScope, ...] = (CredentialScope.DISCOVERY, CredentialScope.IMPORT),
) -> None:
    expires_at = NOW + expiring_in if expiring_in is not None else None
    create_environment(
        EnvironmentSpec(
            environment_key=key,
            name=key,
            base_url=f"https://{key}.example.org",
            environment_class=environment_class,
            version_constraint="2.41.x",
            credentials=[
                CredentialRef(
                    scope=scope,
                    secret_ref=f"memory://dx/{key}/{scope.value}",
                    expires_at=expires_at,
                )
                for scope in scopes
            ],
        ),
        session=session,
        operator="alice",
    )


def refs_of(items: object) -> list[str]:
    return [item.secret_ref for item in items]  # type: ignore[attr-defined]


# --- DX-ENV-006：窗口 --------------------------------------------------------


def test_a_credential_expiring_in_five_days_is_reported_for_a_fourteen_day_horizon() -> None:
    """DX-ENV-006：默认 14 天内的到期引用要出现在清单里。"""
    with unit_of_work() as session:
        register(session, "prod", expiring_in=timedelta(days=5))
    with unit_of_work() as session:
        found = list_expiring_credentials(within_days=14, session=session, now=NOW)
    assert sorted(refs_of(found)) == sorted(
        [
            f"memory://dx/prod/{CredentialScope.DISCOVERY.value}",
            f"memory://dx/prod/{CredentialScope.IMPORT.value}",
        ]
    )
    assert {item.environment_key for item in found} == {"prod"}


def test_a_credential_beyond_the_horizon_is_not_reported() -> None:
    """DX-ENV-006：窗口之外的凭证不该惊动负责人。"""
    with unit_of_work() as session:
        register(session, "prod", expiring_in=timedelta(days=30))
    with unit_of_work() as session:
        assert list_expiring_credentials(within_days=14, session=session, now=NOW) == []


def test_the_horizon_is_the_callers_to_choose() -> None:
    """同一份档案：3 天的窗口看不到 5 天后到期的令牌。"""
    with unit_of_work() as session:
        register(session, "prod", expiring_in=timedelta(days=5))
    with unit_of_work() as session:
        assert list_expiring_credentials(within_days=3, session=session, now=NOW) == []
        found = list_expiring_credentials(within_days=7, session=session, now=NOW)
    assert len(found) == 2


def test_the_default_horizon_is_fourteen_days() -> None:
    """DX-ENV-006：默认 14 天。"""
    with unit_of_work() as session:
        register(session, "soon", expiring_in=timedelta(days=13))
        register(session, "later", expiring_in=timedelta(days=20))
    with unit_of_work() as session:
        found = list_expiring_credentials(session=session, now=NOW)
    assert {item.environment_key for item in found} == {"soon"}


def test_a_credential_without_an_expiry_date_is_never_a_warning() -> None:
    """DX-ENV-006：到期日是可登记的，没登记就不是预警对象。"""
    with unit_of_work() as session:
        register(session, "prod", expiring_in=None)
    with unit_of_work() as session:
        assert list_expiring_credentials(within_days=365, session=session, now=NOW) == []


def test_the_report_carries_the_expiry_instant_and_the_days_left() -> None:
    """负责人要知道还有几天，才能安排轮换。"""
    with unit_of_work() as session:
        register(session, "prod", expiring_in=timedelta(days=5, hours=3))
    with unit_of_work() as session:
        item = list_expiring_credentials(within_days=14, session=session, now=NOW)[0]
    assert item.expires_at == NOW + timedelta(days=5, hours=3)
    assert item.days_left == 5
    assert item.scope == CredentialScope.DISCOVERY.value


def test_the_list_is_ordered_by_the_expiry_instant() -> None:
    """最紧的先出现：每日任务按顺序通知。"""
    with unit_of_work() as session:
        register(session, "late", expiring_in=timedelta(days=12))
        register(session, "urgent", expiring_in=timedelta(days=2))
    with unit_of_work() as session:
        found = list_expiring_credentials(within_days=14, session=session, now=NOW)
    assert [item.environment_key for item in found] == ["urgent", "urgent", "late", "late"]
    assert found[0].days_left == 2


def test_a_disabled_environment_is_left_out_of_the_warning_list() -> None:
    """DX-ENV-006/009：禁用的环境不再被使用，不占预警。"""
    with unit_of_work() as session:
        register(session, "retiring", expiring_in=timedelta(days=4))
        register(session, "active", expiring_in=timedelta(days=6))
    with unit_of_work() as session:
        disable_environment("retiring", session=session, operator="alice")
    with unit_of_work() as session:
        found = list_expiring_credentials(within_days=14, session=session, now=NOW)
    assert {item.environment_key for item in found} == {"active"}


def test_a_production_environment_is_reported_like_any_other() -> None:
    """DX-ENV-006 对生产环境更要报：这里只是查询，不做类别筛选。"""
    from factory_dhis2.dto.environment import CommitWindow, ProtectionSpec

    with unit_of_work() as session:
        create_environment(
            EnvironmentSpec(
                environment_key="prod",
                name="prod",
                base_url="https://prod.example.org",
                environment_class=EnvironmentClass.PRODUCTION,
                version_constraint="2.41.x",
                credentials=[
                    CredentialRef(
                        scope=CredentialScope.DISCOVERY,
                        secret_ref="memory://dx/prod/discovery",
                        expires_at=NOW + timedelta(days=6),
                    ),
                    CredentialRef(
                        scope=CredentialScope.IMPORT,
                        secret_ref="memory://dx/prod/import",
                        expires_at=NOW + timedelta(days=6),
                    ),
                ],
                protection=ProtectionSpec(
                    timezone="UTC", commit_windows=[CommitWindow(days=[2], start="01:00", end="02:00")]
                ),
            ),
            session=session,
            operator="alice",
        )
    with unit_of_work() as session:
        found = list_expiring_credentials(within_days=14, session=session, now=NOW)
    assert sorted(refs_of(found)) == ["memory://dx/prod/discovery", "memory://dx/prod/import"]


def test_only_the_reference_is_returned_never_a_value() -> None:
    """DX-BR-03：预警不需要凭证值，字段里也只有引用。"""
    with unit_of_work() as session:
        register(session, "prod", expiring_in=timedelta(days=1))
    with unit_of_work() as session:
        item = list_expiring_credentials(within_days=14, session=session, now=NOW)[0]
    document = item.model_dump(mode="json", by_alias=True)
    assert set(document) == {"environmentKey", "secretRef", "expiresAt", "scope", "daysLeft"}
    assert document["secretRef"].startswith("memory://")


def test_a_secret_reference_row_stores_only_the_reference() -> None:
    """DX-ENV-003：入库的引用列里就是引用（结构上没有别的位置可放）。"""
    with unit_of_work() as session:
        register(session, "prod", expiring_in=timedelta(days=1))
        row = session.get(EnvironmentCredentialRow, ("prod", CredentialScope.IMPORT.value))
        stored = {column.name: getattr(row, column.name) for column in EnvironmentCredentialRow.__table__.columns}
    assert stored["secret_ref"] == "memory://dx/prod/import"
    assert stored["expires_at"] == NOW + timedelta(days=1)


# --- 已经过期的那一类 ---------------------------------------------------------


def test_an_already_expired_credential_is_reported_as_expired() -> None:
    """DX-ENV-006：连接测试之外的一道提醒——已经过期了还在用。"""
    with unit_of_work() as session:
        register(session, "prod", expiring_in=timedelta(days=-2))
    with unit_of_work() as session:
        expired = already_expired(session=session, now=NOW)
    assert sorted(item.secret_ref for item in expired) == sorted(
        [
            f"memory://dx/prod/{CredentialScope.DISCOVERY.value}",
            f"memory://dx/prod/{CredentialScope.IMPORT.value}",
        ]
    )
    assert {item.days_left for item in expired} == {0}


def test_a_future_expiry_is_not_already_expired() -> None:
    with unit_of_work() as session:
        register(session, "prod", expiring_in=timedelta(days=2))
    with unit_of_work() as session:
        assert already_expired(session=session, now=NOW) == []


def test_an_expired_secret_of_a_disabled_environment_is_still_visible() -> None:
    """与预警不同：已经过期是既成事实，禁用中的环境也要看得见。"""
    with unit_of_work() as session:
        register(session, "retiring", expiring_in=timedelta(days=-1))
    with unit_of_work() as session:
        disable_environment("retiring", session=session, operator="alice")
    with unit_of_work() as session:
        expired = already_expired(session=session, now=NOW)
        warnings = list_expiring_credentials(within_days=14, session=session, now=NOW)
    assert len(expired) == 2
    assert warnings == []
