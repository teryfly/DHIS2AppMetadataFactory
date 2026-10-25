"""发现结果的公共类型（DX-DSC-001…009、SRS 7.2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from factory_dhis2.dto.issue import Issue

_OUTPUT = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DiscoveryOutcome(BaseModel):
    """``discover`` 的输出：产物 ID + 阻塞问题，字段名与 S1 的镜像一致。"""

    model_config = _OUTPUT

    discovery_result_artifact_id: str | None = None
    capability_profile_artifact_id: str | None = None
    blocking_issues: list[Issue] = Field(default_factory=list)
    version: str | None = None
    #: DX-DSC-003：Greenfield 发现读到的 default 对象（categoryCombo 等）
    default_object_ids: dict[str, str] = Field(default_factory=dict)
    object_count: int = 0
    discovery_id: str | None = None
    content_hash: str | None = None


class DiscoveryResultInfo(BaseModel):
    """``dx.discovery_index`` 的一行（S5IF-ENV11：审阅与部署视图要能列出历史发现）。

    ``discover()`` 只返回本次的产物 ID，列不出别人之前跑过什么——那些历史正是判断
    “这个环境上次看是什么时候”的依据。
    """

    model_config = _OUTPUT

    artifact_id: str
    project_id: str | None = None
    scope: str | None = None
    object_count: int = 0
    blocking_issues: list[Issue] = Field(default_factory=list)
    version: str | None = None
    discovered_at: datetime | None = None
    content_hash: str | None = None
    discovery_id: str | None = None
    environment_key: str | None = None


class DiscoveryScope(BaseModel):
    """可配置的发现范围（DX-DSC-002）。"""

    model_config = _OUTPUT

    #: 总体 SRS 14/7.1 的两种用途
    kind: str = "RUN_TARGET"
    dhis2_types: list[str] = Field(default_factory=list)
    code_prefixes: list[str] = Field(default_factory=list)
    programs: list[str] = Field(default_factory=list)
    org_unit_depth: int | None = None
    #: 明确对象清单（环境绑定编译时由调用方按 Registry 传入，DX-ASM-03）
    explicit_refs: list[dict[str, Any]] = Field(default_factory=list)
    greenfield: bool = False


class DiscoveryObjectCandidate(BaseModel):
    """发现结果里的一个候选对象（DX-DSC-011）。

    环境绑定的人工设置要“从该环境的候选对象里选”（S1 CP-UI-005），因此这里给出的字段就是
    选择界面需要的全部：UID、code、name、lastUpdated 与内容哈希。内容本身不返回 —— 一次
    检索可能有几千个命中，把 ``content`` 带上会让一个下拉框搬几十 MB。
    """

    model_config = _OUTPUT

    uid: str
    dhis2_type: str
    code: str | None = None
    name: str | None = None
    last_updated: str | None = None
    content_hash: str | None = None


class DiscoveryObjectSearch(BaseModel):
    """``search_discovery_objects`` 的输出：一页候选与**总数**（DX-DSC-011、DX-AC-12）。"""

    model_config = _OUTPUT

    discovery_artifact_id: str
    dhis2_type: str | None = None
    query: str | None = None
    items: list[DiscoveryObjectCandidate] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0


__all__ = [
    "DiscoveryObjectCandidate",
    "DiscoveryObjectSearch",
    "DiscoveryOutcome",
    "DiscoveryResultInfo",
    "DiscoveryScope",
]
