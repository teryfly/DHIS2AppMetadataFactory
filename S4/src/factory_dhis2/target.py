"""环境目标：一次调用所需的全部已解析上下文（DX-BR-02、DX-CLI-002）。

它是**值对象**：环境档案 + 该环境的 Capability Profile（决定端点长什么样）+ 凭证提供者
（按范围即时解析）。任何一次 DHIS2 调用都必须显式带着它，因为系统不存在默认环境。

不含数据库会话，也不含产物句柄：Client 因此不可能顺手改状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from factory_dhis2.client.auth import CredentialProvider
from factory_dhis2.client.tls import api_root
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.errors import EnvironmentInvalidError
from factory_dhis2.profiles.base import VersionProfile


@dataclass(frozen=True)
class EnvironmentTarget:
    key: str
    name: str
    base_url: str
    environment_class: EnvironmentClass
    auth_mode: AuthMode
    tls_required: bool
    llm_data_policy: LlmDataPolicy
    timeout_seconds: int
    status: EnvironmentStatus
    profile: VersionProfile
    dhis2_version: str
    credentials: CredentialProvider
    #: 按环境覆盖的异步导入阈值（DX-EXE-033）
    async_import_threshold: int | None = None
    capability_profile_artifact_id: str | None = None
    _root: str = field(init=False, repr=False, default="")

    def __post_init__(self) -> None:
        object.__setattr__(self, "_root", api_root(self.base_url, self.profile.api_prefix))

    @property
    def root(self) -> str:
        return self._root

    @property
    def is_production(self) -> bool:
        return self.environment_class is EnvironmentClass.PRODUCTION

    @property
    def disabled(self) -> bool:
        return self.status is EnvironmentStatus.DISABLED

    def scope_available(self, scope: CredentialScope) -> bool:
        return self.credentials.has(scope)

    def require_enabled(self, operation: str) -> None:
        """禁用中的环境不能做任何 DHIS2 操作（DX-ENV-001 的 status 语义）。"""
        if self.disabled:
            raise EnvironmentInvalidError(
                f"environment {self.key!r} is DISABLED; {operation} is refused",
                details={"environmentKey": self.key, "status": self.status.value},
            )


__all__ = ["EnvironmentTarget"]
