"""CAPABILITY_PROFILE 产物内容格式（DX-CAP-001/003/008）。

一个环境的 Capability Profile = 版本 Profile + 该实例的实际探测结果（总体 SRS 14.3）。
S2 读的就是这份文档，它据此选择端点、语法与受控字段；文档里没有的，S2 不许猜，S4 也不
会在运行时补（P-10“能力优先于假设”）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from factory_dhis2.profiles.base import VersionProfile

#: S2 的读取契约要求的字段名
_DOCUMENT = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

SCHEMA_VERSION = 1


class ResourceEntryDocument(BaseModel):
    model_config = _DOCUMENT

    logical_name: str
    path: str
    method: str = "GET"
    parameters: dict[str, str] = Field(default_factory=dict)
    paging: str | None = None
    tracker_api: bool = False
    credential_scope: str = "discovery"


class DiscoveredSchema(BaseModel):
    model_config = _DOCUMENT

    dhis2_type: str
    required: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    forbidden: list[str] = Field(default_factory=list)
    controlable_fields: list[str] = Field(default_factory=list)


class CapabilityProfileDocument(BaseModel):
    model_config = _DOCUMENT

    schema_version: int = SCHEMA_VERSION
    environment_key: str | None = None
    version: str
    profile_version: str
    expression_grammar_version: str | None = None
    resources: list[ResourceEntryDocument] = Field(default_factory=list)
    schemas: list[DiscoveredSchema] = Field(default_factory=list)
    import_parameters: dict[str, Any] = Field(default_factory=dict)
    deprecated: list[dict[str, Any]] = Field(default_factory=list)
    removed: list[dict[str, Any]] = Field(default_factory=list)
    descriptor_overrides: list[dict[str, Any]] = Field(default_factory=list)
    validation_rules: dict[str, Any] = Field(default_factory=dict)
    translation_format: str = "translations"
    sharing_format: str = "sharing"
    #: 探测本身：实例上报的能力（哪些端点真的在、哪些权限真的有）
    probed: dict[str, Any] = Field(default_factory=dict)
    probed_at: str | None = None
    api_prefix: str = "api"
    volatile_fields: list[str] = Field(default_factory=list)


def build_capability_profile_document(
    profile: VersionProfile,
    *,
    environment_key: str,
    dhis2_version: str,
    probed: dict[str, Any] | None = None,
    probed_at: str | None = None,
    schemas: list[DiscoveredSchema] | None = None,
) -> CapabilityProfileDocument:
    """版本 Profile ⊕ 探测结果 → 该环境的能力记录（DX-CAP-003）。"""
    return CapabilityProfileDocument(
        environment_key=environment_key,
        version=dhis2_version,
        profile_version=profile.profile_version,
        expression_grammar_version=profile.expression_grammar_version,
        resources=[
            ResourceEntryDocument(
                logical_name=entry.logical_name,
                path=entry.path,
                method=entry.method,
                parameters=dict(entry.parameters),
                paging=entry.paging,
                tracker_api=entry.tracker_api,
                credential_scope=entry.credential_scope,
            )
            for entry in sorted(profile.resources, key=lambda item: item.logical_name)
        ],
        # 探测到的 Schema 覆盖离线基线：实例是权威（0.2 权威顺序第 1、2 条）。
        # **按类型合并**而不是整体替换：同步范围之外的类型（调用方收窄了 ``schema_types``，
        # 或实例那几类答不出 Schema）基线必须还在产物里，否则 S2 读到的产物会「从有到无」，
        # 而它只读产物、不许再猜（DX-CAP-001/003）。
        schemas=_merge_schemas(profile, schemas),
        import_parameters=dict(profile.import_parameters),
        deprecated=[item.as_document() for item in profile.deprecated],
        removed=[item.as_document() for item in profile.removed],
        descriptor_overrides=list(profile.descriptor_overrides),
        validation_rules=dict(profile.validation_rules),
        translation_format=profile.translation_format,
        sharing_format=profile.sharing_format,
        probed=probed or {},
        probed_at=probed_at,
        api_prefix=profile.api_prefix,
        volatile_fields=list(profile.volatile_fields),
    )


def _merge_schemas(
    profile: VersionProfile, synced: list[DiscoveredSchema] | None
) -> list[DiscoveredSchema]:
    """离线基线 ⊕ 实例同步结果，按类型合并；同步到的类型以实例为准。

    基线取 :meth:`VersionProfile.object_schema`，也就是**已经压过描述器覆盖**的那一份
    （DX-CAP-001）：S2 只读产物，覆盖如果留在 S4 的代码里就等于没生效。
    """
    merged: dict[str, DiscoveredSchema] = {}
    for dhis2_type in profile.schema_view_types():
        schema = profile.object_schema(dhis2_type)
        if schema is None:
            continue
        merged[dhis2_type] = DiscoveredSchema(
            dhis2_type=dhis2_type,
            required=list(schema.required),
            properties=dict(schema.properties),
            forbidden=list(schema.forbidden),
            controlable_fields=list(schema.controlable_fields),
        )
    for item in synced or []:
        baseline = merged.get(item.dhis2_type)
        override = profile.descriptor_override(item.dhis2_type)
        baseline_item = baseline if baseline is not None else None
        merged[item.dhis2_type] = DiscoveredSchema(
            dhis2_type=item.dhis2_type,
            required=_overridden(
                override.get("required"),
                item.required,
                None if baseline_item is None else baseline_item.required,
            ),
            properties={**item.properties, **dict(override.get("properties") or {})},
            # forbidden / controlableFields 是项目与版本的决定，实例 Schema 里没有这个
            # 概念：同步答案里没有就沿用基线，而不是把它清空
            forbidden=_overridden(
                override.get("forbidden"),
                item.forbidden,
                None if baseline_item is None else baseline_item.forbidden,
            ),
            controlable_fields=_overridden(
                override.get("controlableFields"),
                item.controlable_fields,
                None if baseline_item is None else baseline_item.controlable_fields,
            ),
        )
    return [merged[key] for key in sorted(merged)]


def _overridden(
    override: Any, synced_value: Any, baseline_value: Any
) -> list[str]:
    """覆盖 > 实例同步 > 离线基线。

    这个顺序是两条规则的合取：DX-CAP-001 说覆盖表达"该版本该类型的字段差异"，0.2 权威顺序
    说实例是 Schema 的事实来源。覆盖里出现了这一项就是整项替换（空列表也是）。
    """
    if isinstance(override, str):
        return [override]
    if isinstance(override, (list, tuple)):
        return [str(item) for item in override]
    if isinstance(synced_value, (list, tuple)) and synced_value:
        return [str(item) for item in synced_value]
    if isinstance(baseline_value, (list, tuple)):
        return [str(item) for item in baseline_value]
    return []


def profile_from_document(
    document: CapabilityProfileDocument | Mapping[str, Any],
    *,
    base: VersionProfile,
) -> VersionProfile:
    """把某个环境**已持久化**的 Capability Profile 还原成运行期可用的 Profile。

    DX-CLI-005 要求"实际端点、方法、参数名、分页方式由**该环境的** Capability Profile 解析"，
    DX-CAP-003 要求所有人只读已持久化的产物、禁止运行时猜测。只看随包发布的版本 Profile 就
    等于把探测丢掉：实例不暴露的端点照样被调用、同步回来的 Schema 永远用不上。

    因此运行期端点目录以产物为准；产物里探测确认为不可用的逻辑资源被**移除**，让
    ``require_resource`` 明确报"这个环境不支持"，而不是发一次注定失败的请求。

    ``property_defaults`` / ``associated_data`` / ``read_fields`` / ``version_constraint``
    是项目与版本决定，不是实例事实，产物里没有它们——按 0.2 的权威顺序，这些继续取自随包的
    版本 Profile（``base``）。
    """
    from factory_dhis2.profiles.base import (
        DeprecatedItem,
        ObjectSchemaDef,
        ResourceEntry,
    )

    source = (
        document
        if isinstance(document, CapabilityProfileDocument)
        else CapabilityProfileDocument.model_validate(dict(document))
    )
    unavailable = _unavailable_logical_names(source.probed)
    entries = [
        ResourceEntry(
            logical_name=item.logical_name,
            path=item.path,
            method=item.method,
            parameters=dict(item.parameters),
            paging=item.paging,
            tracker_api=item.tracker_api,
            credential_scope=item.credential_scope,
        )
        for item in source.resources
        if item.logical_name not in unavailable
    ]
    schemas = [
        ObjectSchemaDef(
            dhis2_type=item.dhis2_type,
            required=tuple(item.required),
            properties=dict(item.properties),
            forbidden=tuple(item.forbidden),
            controlable_fields=tuple(item.controlable_fields),
        )
        for item in source.schemas
    ]
    return replace(
        base,
        profile_version=source.profile_version,
        dhis2_series=_series_of(source.version),
        expression_grammar_version=source.expression_grammar_version or base.expression_grammar_version,
        translation_format=source.translation_format,
        sharing_format=source.sharing_format,
        resources=tuple(entries),
        schemas=tuple(schemas),
        import_parameters=dict(source.import_parameters),
        deprecated=tuple(
            DeprecatedItem(
                kind=str(item.get("kind") or ""),
                name=str(item.get("name") or ""),
                removed_in=item.get("removedIn"),
                note=item.get("note"),
            )
            for item in source.deprecated
        ),
        removed=tuple(
            DeprecatedItem(
                kind=str(item.get("kind") or ""),
                name=str(item.get("name") or ""),
                removed_in=item.get("removedIn"),
                note=item.get("note"),
            )
            for item in source.removed
        ),
        descriptor_overrides=tuple(source.descriptor_overrides),
        validation_rules=dict(source.validation_rules),
        volatile_fields=tuple(source.volatile_fields) or base.volatile_fields,
        api_prefix=source.api_prefix,
    )


def _unavailable_logical_names(probed: Mapping[str, Any]) -> frozenset[str]:
    """探测确认可用的反面：该实例上不存在这些端点（``schemas:<类型>`` 不是逻辑资源名）。"""
    names = probed.get("unavailableResources") if isinstance(probed, Mapping) else None
    return frozenset(
        str(item) for item in (names or []) if isinstance(item, str) and ":" not in item
    )


def _series_of(version: str) -> str:
    parts = (version or "").strip().split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else (version or "")


def as_artifact_content(document: CapabilityProfileDocument) -> dict[str, Any]:
    """写进产物的 JSON：键序固定，同一输入永远得到同一哈希。"""
    return document.model_dump(mode="json", by_alias=True, exclude_none=False)


__all__ = [
    "SCHEMA_VERSION",
    "profile_from_document",
    "CapabilityProfileDocument",
    "DiscoveredSchema",
    "ResourceEntryDocument",
    "as_artifact_content",
    "build_capability_profile_document",
]
