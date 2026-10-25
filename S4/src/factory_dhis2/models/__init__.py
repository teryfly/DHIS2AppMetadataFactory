"""``dx`` schema 的全部表（导入本包即把它们登记到 ``DxBase.metadata``）。

迁移的 ``env.py`` 与 ``0001_dx_initial`` 都依赖这一点：正因为这里导入了所有模型，
迁移才能直接从 ORM 元数据建表，模型与迁移不可能漂移。
"""

from __future__ import annotations

from factory_dhis2.models.base import DxTable, TimestampMixin
from factory_dhis2.models.environment import (
    ConnectionTestRow,
    EnvironmentCredentialRow,
    EnvironmentProtectionRow,
    EnvironmentRow,
)
from factory_dhis2.models.execution import (
    DemoUserCredentialRow,
    ExecutionRow,
    ExecutionStepRow,
    SnapshotIndexRow,
    ValidationRecordRow,
)
from factory_dhis2.models.lock import EnvironmentLockRow
from factory_dhis2.models.profile import CapabilityProfileIndexRow, DiscoveryIndexRow

DX_TABLES: tuple[type[DxTable], ...] = (
    EnvironmentRow,
    EnvironmentCredentialRow,
    EnvironmentProtectionRow,
    ConnectionTestRow,
    CapabilityProfileIndexRow,
    DiscoveryIndexRow,
    ExecutionRow,
    ExecutionStepRow,
    ValidationRecordRow,
    SnapshotIndexRow,
    EnvironmentLockRow,
    DemoUserCredentialRow,
)

__all__ = [
    "CapabilityProfileIndexRow",
    "ConnectionTestRow",
    "DemoUserCredentialRow",
    "DiscoveryIndexRow",
    "DX_TABLES",
    "DxTable",
    "EnvironmentCredentialRow",
    "EnvironmentLockRow",
    "EnvironmentProtectionRow",
    "EnvironmentRow",
    "ExecutionRow",
    "ExecutionStepRow",
    "SnapshotIndexRow",
    "TimestampMixin",
    "ValidationRecordRow",
]
