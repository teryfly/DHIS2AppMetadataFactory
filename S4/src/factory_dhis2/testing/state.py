"""把"环境已经被验证过"这件事写进 ``dx``（测试前置条件）。

DX-EXE-012 要求执行计划校验"对应凭证范围的服务账户具备所需权限"，而这份事实在系统里唯一
的权威来源是**已持久化的连接测试结果**（DX-CON-004）。S4 不访问实例数据库、也不许计划生成
时打 DHIS2，所以没有那一行就只能判定为"无法确认"并阻断。

真实流程里这一行由 ``test_connection`` 产生；测试常常只想表达"操作员测过了、权限没问题"，
而不必为此把整条 Client 链路装起来。这个辅助就是干这个的：它写的是与 ``tester.py`` 完全
相同的行结构，绝不伪造检查项结论。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import new_id, unit_of_work
from sqlalchemy.orm import Session

from factory_dhis2.enums import CredentialScope
from factory_dhis2.errors import EnvironmentInvalidError
from factory_dhis2.models.environment import ConnectionTestRow, EnvironmentRow

#: 连接测试通过时的默认检查项名字，与 ``connection/tester.py`` 逐项一致（DX-CON-001）
_DEFAULT_CHECKS: tuple[str, ...] = (
    "address",
    "tls",
    "auth",
    "version",
    "versionProfile",
    "account",
)


def record_connection_test(
    environment_key: str,
    *,
    scopes: tuple[CredentialScope, ...] | list[str] | None = None,
    result: str = "PASSED",
    dhis2_version: str | None = "2.41.10",
    missing_authorities: dict[str, list[str]] | None = None,
    granted_authorities: dict[str, list[str]] | None = None,
    checks: list[dict[str, Any]] | None = None,
    operator: str = "tester",
    tested_at: datetime | None = None,
    session: Session | None = None,
) -> str:
    """写入一行连接测试结果，返回它的 ID。"""
    wanted = [
        item.value if isinstance(item, CredentialScope) else str(item)
        for item in (scopes or (CredentialScope.DISCOVERY, CredentialScope.IMPORT))
    ]
    moment = tested_at or datetime.now(UTC)
    document = checks or [
        {
            "name": name,
            "state": result if name != "account" else "PASSED",
            "detail": f"由 {operator} 记录的前置结果",
        }
        for name in _DEFAULT_CHECKS
    ]

    def _write(tx: Session) -> str:
        known = tx.execute(
            EnvironmentRow.__table__.select().where(EnvironmentRow.key == environment_key)
        ).first()
        if known is None:
            raise EnvironmentInvalidError(
                f"unknown environment {environment_key!r}",
                details={"environmentKey": environment_key, "rule": "DX-ENV-004"},
            )
        row = ConnectionTestRow(
            id=new_id("dxcnt"),
            environment_key=environment_key,
            tested_at=moment,
            dhis2_version=dhis2_version,
            checks=document,
            missing_authorities=dict(missing_authorities or {}),
            granted_authorities=dict(granted_authorities or {scope: [] for scope in wanted}),
            result=result,
            required_scopes=wanted,
            capability_profile_artifact_id=None,
            operator=operator,
        )
        tx.add(row)
        tx.flush()
        return str(row.id)

    if session is not None:
        return _write(session)
    with unit_of_work() as tx:
        return _write(tx)


__all__ = ["record_connection_test"]
