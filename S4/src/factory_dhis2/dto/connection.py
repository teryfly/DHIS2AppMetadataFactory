"""连接测试与能力新鲜度的公共类型（DX-CON、DX-CAP-005/006）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from factory_dhis2.enums import CapabilityFreshness, CheckState

_OUTPUT = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CheckItem(BaseModel):
    """一个检查项的结果（DX-CON-001）。"""

    model_config = _OUTPUT

    name: str
    state: str = CheckState.PASSED.value
    detail: str | None = None
    #: 该检查用到的凭证范围（DX-CON-002 报告按范围给出）
    scope: str | None = None
    missing_authorities: list[str] = Field(default_factory=list)


class ConnectionCheck(BaseModel):
    """``test_connection`` 的输出，字段名与 S1 的镜像一致。"""

    model_config = _OUTPUT

    ok: bool = False
    version: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    capability_profile_artifact_id: str | None = None
    message: str | None = None
    #: DX-CON-002：逐范围报告实际具备与缺少的权限
    granted_authorities: dict[str, list[str]] = Field(default_factory=dict)
    missing_authorities: dict[str, list[str]] = Field(default_factory=dict)
    account_roles: dict[str, list[str]] = Field(default_factory=dict)
    account_org_scope: dict[str, Any] = Field(default_factory=dict)
    environment_key: str | None = None
    tested_at: str | None = None
    #: 与 ``ok`` 同行的持久化事实（S1 的镜像按 ``result``/``dhis2Version``/``requiredScopes``
    #  显示一次测试的结论、实例版本与当时被检查的范围）
    result: str = "FAILED"
    dhis2_version: str | None = None
    required_scopes: list[str] = Field(default_factory=list)
    #: 一次已持久化测试的身份（DX-CON-004）。``test_connection`` 现场返回时为新登记的一行，
    #  ``get_last_connection_test`` 读历史时给出同一行的 ID 与操作者。
    connection_test_id: str | None = None
    operator: str | None = None


class CapabilityProfileIndexInfo(BaseModel):
    """``dx.capability_profile_index`` 的最新一行（DX-CAP-009）。

    聚合视图（S1 CP-UI-012）只能显示**最近一次探测**的结果，因此本模型必须能在完全不访问
    DHIS2 的前提下给出：Profile 版本、实例版本、探测时间、是否过期及原因、产物 ID 与内容
    哈希。与 :class:`CapabilityFreshnessInfo` 的区别是这里没有任何实时判定 —— ``freshness``
    函数会真读实例版本，本模型只读 `dx`。
    """

    model_config = _OUTPUT

    profile_index_id: str
    environment_key: str
    profile_version: str
    dhis2_version: str
    probed_at: datetime
    stale: bool
    stale_reason: str | None = None
    artifact_id: str
    content_hash: str


class CapabilityFreshnessInfo(BaseModel):
    """``check_capability_freshness`` 的输出（DX-CAP-006，S1 在每个步骤前调用）。

    除了三态判定，还把**已持久化的那份能力记录**的元数据一起给出：S1/S5 据此显示“上次
    探测是什么时候、基于哪个版本”，而不必再读一次产物。``content`` 故意留空——每个 Run
    步骤前都会调用本函数，把几十 MB 的产物塞进返回值会让一次轻量检查变成大搬运。
    """

    model_config = _OUTPUT

    state: CapabilityFreshness = CapabilityFreshness.VALID
    capability_profile_artifact_id: str | None = None
    version: str | None = None
    profile_version: str | None = None
    reason: str | None = None
    #: 索引行自己的事实（S1 的镜像字段名 ``artifactId`` / ``contentHash`` / ``probedAt``…）
    artifact_id: str | None = None
    content_hash: str | None = None
    dhis2_version: str | None = None
    probed_at: datetime | None = None
    stale: bool | None = None
    stale_reason: str | None = None
    freshness: str | None = None
    content: dict[str, Any] | None = None


__all__ = ["CapabilityFreshnessInfo", "CapabilityProfileIndexInfo", "CheckItem", "ConnectionCheck"]
