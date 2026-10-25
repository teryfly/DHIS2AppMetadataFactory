"""环境档案与保护设置的公共类型（DX-ENV-001…009）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    DeletionPolicy,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)

_INPUT = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
_OUTPUT = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CredentialRef(BaseModel):
    """一个凭证范围的 Secret 引用（DX-ENV-003：永远不是凭证本身）。"""

    model_config = _INPUT

    scope: CredentialScope
    secret_ref: str
    expires_at: datetime | None = None
    label: str | None = None

    @field_validator("secret_ref")
    @classmethod
    def _ref_has_scheme(cls, value: str) -> str:
        if "://" not in value:
            raise ValueError("secretRef must be a Secret reference such as vault://path/name")
        return value


class CommitWindow(BaseModel):
    """允许提交的时间窗（DX-ENV-002：按环境时区的“星期 + 时间段”）。"""

    model_config = _INPUT

    #: ISO 星期：1=周一 … 7=周日
    days: list[int] = Field(default_factory=list)
    start: str = "00:00"
    end: str = "23:59"
    note: str | None = None

    @field_validator("days")
    @classmethod
    def _days_in_range(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("a commit window needs at least one weekday")
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("commit window weekdays are ISO 1..7")
        return sorted(set(value))

    @field_validator("start", "end")
    @classmethod
    def _clock_time(cls, value: str) -> str:
        hour, _, minute = value.partition(":")
        if not (hour.isdigit() and minute.isdigit()):
            raise ValueError("commit window times look like '20:00'")
        if not (0 <= int(hour) < 24 and 0 <= int(minute) < 60):
            raise ValueError("commit window times look like '20:00'")
        return f"{int(hour):02d}:{int(minute):02d}"

    def wraps_midnight(self) -> bool:
        return self.end <= self.start


class ProtectionSpec(BaseModel):
    """PRODUCTION 类环境的额外保护配置（DX-ENV-002）。"""

    model_config = _INPUT

    timezone: str = "UTC"
    commit_windows: list[CommitWindow] = Field(default_factory=list)
    #: DX-ENV-002：删除策略有**按类别的默认值**（非生产 ``SAFE_ONLY``、PRODUCTION ``FORBID``），
    #  所以"没给"是一个有意义的状态，不能提前折算成某个值 —— 提前折算会让 PRODUCTION 拿到
    #  非生产的默认，也让人无法区分"配了 SAFE_ONLY"与"什么都没配"。
    deletion_policy: DeletionPolicy | None = None
    #: 紧急锁定只能通过 ``set_emergency_lock`` 改（DX-ENV-007：要留下原因、时间与变更人并
    #  写审计），因此档案里出现 ``True`` 就是绕过那条路径，保存时拒绝。
    emergency_lock: bool = False
    #: M4 才生效（DX-ENV-002【调整】）：本轮只登记，读取时拒绝非默认值
    required_approvals: int | None = None
    operator_role: str | None = None


class EnvironmentSpec(BaseModel):
    """``create_environment(spec)`` 的输入（DX-ENV-001）。"""

    model_config = _INPUT

    environment_key: str
    name: str
    base_url: str
    environment_class: EnvironmentClass = EnvironmentClass.TEST
    version_constraint: str | None = None
    auth_mode: AuthMode = AuthMode.PAT
    credentials: list[CredentialRef] = Field(default_factory=list)
    tls_required: bool = True
    llm_data_policy: LlmDataPolicy = LlmDataPolicy.PRIVATE_NETWORK
    timeout_seconds: int = 60
    status: EnvironmentStatus = EnvironmentStatus.ACTIVE
    protection: ProtectionSpec | None = None
    #: 按环境覆盖的异步导入阈值（DX-EXE-033）
    async_import_threshold: int | None = None
    #: DX-ENV-001 的"备注"：登记用途、负责人之类，不参与任何判定
    notes: str | None = None

    def credential_map(self) -> dict[CredentialScope, CredentialRef]:
        return {item.scope: item for item in self.credentials}

    def secret_refs(self) -> dict[str, str]:
        return {item.scope.value: item.secret_ref for item in self.credentials}


class EnvironmentPatch(BaseModel):
    """``update_environment(key, patch)`` 的输入；未给出的字段保持不变。"""

    model_config = _INPUT

    name: str | None = None
    base_url: str | None = None
    version_constraint: str | None = None
    auth_mode: AuthMode | None = None
    credentials: list[CredentialRef] | None = None
    tls_required: bool | None = None
    llm_data_policy: LlmDataPolicy | None = None
    timeout_seconds: int | None = None
    status: EnvironmentStatus | None = None
    protection: ProtectionSpec | None = None
    async_import_threshold: int | None = None
    notes: str | None = None


class EnvironmentInfo(BaseModel):
    """环境档案（不含凭证值，DX-ENV-003；S1 的镜像只用到下面几个字段）。"""

    model_config = _OUTPUT

    environment_key: str
    name: str | None = None
    base_url: str | None = None
    #: 来自该环境最近一次 Capability Profile，而不是档案里的静态值
    version: str | None = None
    enabled: bool = True
    environment_class: EnvironmentClass = EnvironmentClass.TEST
    status: EnvironmentStatus = EnvironmentStatus.ACTIVE
    auth_mode: AuthMode = AuthMode.PAT
    tls_required: bool = True
    llm_data_policy: LlmDataPolicy = LlmDataPolicy.PRIVATE_NETWORK
    timeout_seconds: int = 60
    version_constraint: str | None = None
    #: 只有引用与到期日，永远没有值
    credentials: list[CredentialRef] = Field(default_factory=list)
    #: S1 的镜像按 ``secretRefs`` 展示（范围 → 引用），这里一并给出，省得调用方再遍历
    secret_refs: dict[str, str] = Field(default_factory=dict)
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    async_import_threshold: int | None = None
    profile_version: str | None = None


class EnvironmentProtection(BaseModel):
    """``get_protection`` 的输出（S1 在审批与提交前都会实时读，CP-WF-027）。"""

    model_config = _OUTPUT

    environment_key: str
    environment_class: str = EnvironmentClass.TEST.value
    commit_windows: list[dict[str, Any]] = Field(default_factory=list)
    within_commit_window: bool = True
    emergency_locked: bool = False
    lock_reason: str | None = None
    #: 总体 SRS ``llmDataPolicy`` 的对外形态；S1 取全部目标环境的最严格者（D-07）
    data_export_policy: dict[str, Any] = Field(default_factory=dict)
    allows_fixture_import: bool | None = None
    deletion_policy: str = DeletionPolicy.SAFE_ONLY.value
    timezone: str = "UTC"
    #: 锁是谁、什么时候改的：审批人要能在页面上看到这些（DX-ENV-007）
    lock_changed_at: str | None = None
    lock_changed_by: str | None = None
    llm_data_policy: str = LlmDataPolicy.PRIVATE_NETWORK.value
    required_approvals: int | None = None
    operator_role: str | None = None
    status: str = EnvironmentStatus.ACTIVE.value


class ExpiringCredential(BaseModel):
    """``list_expiring_credentials`` 的一行（DX-ENV-006，S1 每日任务调用）。"""

    model_config = _OUTPUT

    environment_key: str
    secret_ref: str
    expires_at: datetime | None = None
    scope: str | None = None
    days_left: int | None = None


__all__ = [
    "CommitWindow",
    "EnvironmentInfo",
    "EnvironmentPatch",
    "EnvironmentProtection",
    "EnvironmentSpec",
    "ExpiringCredential",
    "ProtectionSpec",
    "CredentialRef",
]
