"""连接测试（DX-CON）。"""

from __future__ import annotations

from factory_dhis2.connection.authorities import (
    SUPERUSER_AUTHORITY,
    ScopeReport,
    evaluate,
    is_superuser,
)
from factory_dhis2.connection.tester import test_connection

__all__ = [
    "SUPERUSER_AUTHORITY",
    "ScopeReport",
    "evaluate",
    "is_superuser",
    "test_connection",
]
