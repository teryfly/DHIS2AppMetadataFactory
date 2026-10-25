"""DISCOVERY_RESULT 产物内容格式（DX-DSC-001/005/009）。

S2 的 UPDATE 对象合并与 Diff 依赖 ``content``（范围内对象的当前完整内容，DX-DSC-001
【新增】）；``contentHash`` 是漂移检查的比对基准，**必须在排除易变字段之后**计算
（DX-DSC-007），否则仅 ``lastUpdated`` 变化就会被当成漂移（AC-EXE-07）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_DOCUMENT = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

SCHEMA_VERSION = 1


class DiscoveredObjectDocument(BaseModel):
    model_config = _DOCUMENT

    dhis2_type: str
    uid: str
    code: str | None = None
    name: str | None = None
    last_updated: str | None = None
    content_hash: str | None = None
    content: dict[str, Any] | None = None
    references: dict[str, Any] = Field(default_factory=dict)
    #: DX-DSC-006：仅组织层级有
    level: int | None = None
    parent: str | None = None


class OrgUnitDocument(BaseModel):
    """组织层级（UID、code、name、层级、父节点），供映射引用 OrgUnit。"""

    model_config = _DOCUMENT

    uid: str
    code: str | None = None
    name: str | None = None
    level: int | None = None
    parent: str | None = None
    path: str | None = None


class DiscoveryResultDocument(BaseModel):
    model_config = _DOCUMENT

    schema_version: int = SCHEMA_VERSION
    environment_key: str
    discovered_at: str | None = None
    version: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    objects: list[DiscoveredObjectDocument] = Field(default_factory=list)
    #: DX-DSC-003：default CategoryCombo / Category / CategoryOption / CategoryOptionCombo
    defaults: dict[str, str] = Field(default_factory=dict)
    org_units: list[OrgUnitDocument] = Field(default_factory=list)
    #: 本次发现用到的凭证范围（DX-DSC-004：永远只有 discovery）
    credential_scope: str = "discovery"
    write_attempted: bool = False


def build_discovery_result_document(
    *,
    environment_key: str,
    discovered_at: str | None,
    version: str | None,
    scope: dict[str, Any],
    objects: list[DiscoveredObjectDocument],
    defaults: dict[str, str] | None = None,
    org_units: list[OrgUnitDocument] | None = None,
) -> DiscoveryResultDocument:
    ordered = sorted(objects, key=lambda item: (item.dhis2_type, item.uid))
    return DiscoveryResultDocument(
        environment_key=environment_key,
        discovered_at=discovered_at,
        version=version,
        scope=dict(scope),
        objects=ordered,
        defaults=dict(sorted((defaults or {}).items())),
        org_units=list(org_units or []),
    )


def as_artifact_content(document: DiscoveryResultDocument) -> dict[str, Any]:
    return document.model_dump(mode="json", by_alias=True)


__all__ = [
    "SCHEMA_VERSION",
    "DiscoveredObjectDocument",
    "DiscoveryResultDocument",
    "OrgUnitDocument",
    "as_artifact_content",
    "build_discovery_result_document",
]
