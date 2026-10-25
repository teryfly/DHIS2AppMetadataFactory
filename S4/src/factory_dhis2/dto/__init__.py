"""S4 的公共 Pydantic 类型（00 文档 6.1）。"""

from __future__ import annotations

from factory_dhis2.dto.connection import CapabilityFreshnessInfo, CheckItem, ConnectionCheck
from factory_dhis2.dto.discovery import DiscoveryOutcome, DiscoveryScope
from factory_dhis2.dto.environment import (
    CommitWindow,
    CredentialRef,
    EnvironmentInfo,
    EnvironmentPatch,
    EnvironmentProtection,
    EnvironmentSpec,
    ExpiringCredential,
    ProtectionSpec,
)
from factory_dhis2.dto.execution import (
    Dhis2ValidationOutcome,
    DiffReportOutcome,
    DriftResult,
    ExecutionOutcome,
    ExecutionStatusInfo,
    PostVerifyOutcome,
    ReconcileResult,
    StepInfo,
)
from factory_dhis2.dto.issue import Issue, make_issue

__all__ = [
    "CapabilityFreshnessInfo",
    "CheckItem",
    "CommitWindow",
    "ConnectionCheck",
    "CredentialRef",
    "DiffReportOutcome",
    "Dhis2ValidationOutcome",
    "DiscoveryOutcome",
    "DiscoveryScope",
    "EnvironmentInfo",
    "EnvironmentPatch",
    "EnvironmentProtection",
    "EnvironmentSpec",
    "ExpiringCredential",
    "DriftResult",
    "ExecutionOutcome",
    "ExecutionStatusInfo",
    "Issue",
    "make_issue",
    "PostVerifyOutcome",
    "ProtectionSpec",
    "ReconcileResult",
    "StepInfo",
]
