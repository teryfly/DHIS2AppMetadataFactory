"""版本能力 Profile（DX-CAP-001）。

一个 Profile 就是一**数据文件目录**加上读取它的类型：资源目录、Schema 包、导入参数、
弃用与移除清单、类型描述器覆盖、版本特有校验规则集、表达式语法版本标识。新增一个 DHIS2
版本 = 新增一个目录（DX-CAP-002、DX-NFR-07），Client 与执行器一行代码都不改。

本模块不含任何 HTTP 或数据库调用：Profile 是纯数据视图，探测结果由
:mod:`factory_dhis2.capability` 与之合成。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factory_dhis2.errors import VersionUnsupportedError

#: 资源目录里按类型展开的逻辑名前缀：``metadata.dataElement`` 等。
TYPE_RESOURCE_PREFIX = "metadata."
#: ``metadata.*`` 里的操作端点：它们有前缀但不是 DHIS2 类型
_OPERATION_RESOURCES: frozenset[str] = frozenset(
    {"read", "read.one", "read.associated", "import", "delete", "async", "validate"}
)


@dataclass(frozen=True)
class ResourceEntry:
    """逻辑资源名 → 实际端点、方法、参数名、分页方式（DX-CLI-005）。"""

    logical_name: str
    path: str
    method: str = "GET"
    parameters: dict[str, str] = field(default_factory=dict)
    paging: str | None = None
    tracker_api: bool = False
    #: 需要哪个凭证范围（DX-CON-001/002 据此检查权限）
    credential_scope: str = "discovery"

    def as_document(self) -> dict[str, Any]:
        """发布成 CAPABILITY_PROFILE 产物里的 ``resources`` 一项（S2 的读取形状）。"""
        out: dict[str, Any] = {
            "logicalName": self.logical_name,
            "path": self.path,
            "method": self.method,
            "parameters": dict(sorted(self.parameters.items())),
            "paging": self.paging,
            "trackerApi": self.tracker_api,
            "credentialScope": self.credential_scope,
        }
        return out


@dataclass(frozen=True)
class ObjectSchemaDef:
    """该版本某一元数据对象的 Schema 片段（DX-CAP-001）。"""

    dhis2_type: str
    required: tuple[str, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)
    forbidden: tuple[str, ...] = ()
    controlable_fields: tuple[str, ...] = ()

    def as_document(self) -> dict[str, Any]:
        return {
            "dhis2Type": self.dhis2_type,
            "required": list(self.required),
            "properties": dict(sorted(self.properties.items())),
            "forbidden": list(self.forbidden),
            "controlableFields": list(self.controlable_fields),
        }


@dataclass(frozen=True)
class DeprecatedItem:
    kind: str
    name: str
    removed_in: str | None = None
    note: str | None = None

    def as_document(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "name": self.name}
        if self.removed_in:
            out["removedIn"] = self.removed_in
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class AssociatedDataCheck:
    """DX-QRY-004：某类型“是否有关联业务数据”的判定方式（按版本登记，代码不写死）。"""

    dhis2_type: str
    checks: tuple[dict[str, Any], ...]

    def as_document(self) -> dict[str, Any]:
        return {"dhis2Type": self.dhis2_type, "checks": list(self.checks)}


@dataclass(frozen=True)
class VersionProfile:
    profile_version: str
    dhis2_series: str
    version_constraint: str
    expression_grammar_version: str
    translation_format: str = "translations"
    sharing_format: str = "sharing"
    resources: tuple[ResourceEntry, ...] = ()
    schemas: tuple[ObjectSchemaDef, ...] = ()
    import_parameters: dict[str, Any] = field(default_factory=dict)
    deprecated: tuple[DeprecatedItem, ...] = ()
    removed: tuple[DeprecatedItem, ...] = ()
    #: 类型描述器覆盖（DX-CAP-001）。每项形如
    #: ``{"dhis2Type": …, "required": […], "forbidden": […], "controlableFields": […],
    #: "properties": {字段: {…}}}``：三个清单出现了就整项替换，properties 按键合并。
    #: 生效在 :meth:`object_schema`，并随产物发布 —— S2 只读产物，覆盖不进产物等于没生效。
    descriptor_overrides: tuple[dict[str, Any], ...] = ()
    validation_rules: dict[str, Any] = field(default_factory=dict)
    #: DX-DSC-007：计算内容哈希时要排除的易变/派生字段
    volatile_fields: tuple[str, ...] = ()
    #: 2.41.10 实测：实例回读时**省略取值等于默认值的字段**。这里登记已知的每类型默认值，
    #  供部署后验证判断“环境里没这个键”到底是未生效还是默认值省略（DX-EXE-040）。
    property_defaults: dict[str, dict[str, Any]] = field(default_factory=dict)
    associated_data: tuple[AssociatedDataCheck, ...] = ()
    #: 读取列表时的字段串（DX-NFR-02：只取必要字段才可能在一分钟内读完一千对象）
    read_fields: str = "id,code,name,created,lastUpdated"
    #: API 前缀：baseUrl 与逻辑资源路径之间的这一段
    api_prefix: str = "api"
    #: 实例版本升级后仍然可用的最大补丁号差异（DX-CAP-005 的判定用）
    source_directory: str = ""

    # --- 资源目录 -----------------------------------------------------------

    def resource(self, logical_name: str) -> ResourceEntry | None:
        for entry in self.resources:
            if entry.logical_name == logical_name:
                return entry
        return None

    def require_resource(self, logical_name: str) -> ResourceEntry:
        """DX-CLI-005：按逻辑资源名调用；目录里没有就是版本不支持，而不是猜一个路径。"""
        found = self.resource(logical_name)
        if found is None:
            raise VersionUnsupportedError(
                f"profile {self.profile_version} has no logical resource {logical_name!r}",
                details={
                    "profileVersion": self.profile_version,
                    "logicalName": logical_name,
                },
            )
        return found

    def type_resource(self, dhis2_type: str) -> ResourceEntry:
        return self.require_resource(f"{TYPE_RESOURCE_PREFIX}{dhis2_type}")

    def has_type(self, dhis2_type: str) -> bool:
        return self.resource(f"{TYPE_RESOURCE_PREFIX}{dhis2_type}") is not None

    @property
    def supported_types(self) -> tuple[str, ...]:
        """目录里真正存在的 DHIS2 **类型**。

        刻意排除 ``metadata.read`` / ``metadata.import`` 这类操作端点：它们同样带
        ``metadata.`` 前缀，但不是一个类型。在线实例实测过把它们当类型扫的后果 ——
        ``GET /api/metadata.json?filter=id:in:[…]`` 会返回 200 而毫无意义，白白让 UID 存在性
        检查扫两类并且得出错误结论。
        """
        return tuple(
            sorted(
                entry.logical_name[len(TYPE_RESOURCE_PREFIX) :]
                for entry in self.resources
                if entry.logical_name.startswith(TYPE_RESOURCE_PREFIX)
                and "{" not in entry.path
                and entry.logical_name[len(TYPE_RESOURCE_PREFIX) :] not in _OPERATION_RESOURCES
            )
        )

    # --- Schema 与字段 ------------------------------------------------------

    def descriptor_override(self, dhis2_type: str) -> Mapping[str, Any]:
        """该类型的**描述器覆盖**（DX-CAP-001、总体 SRS 14.3）。

        覆盖表达的是"这个版本里这个类型的字段差异"，随包数据文件发布，出现差异时改数据而
        不改执行器（DX-NFR-07）。找不到的类型返回空映射。
        """
        for item in self.descriptor_overrides:
            if str(item.get("dhis2Type") or "") == dhis2_type:
                return item
        return {}

    def object_schema(self, dhis2_type: str) -> ObjectSchemaDef | None:
        """该类型**生效的** Schema：Schema 包那一份 ⊕ 描述器覆盖。

        覆盖压过包：``required``/``forbidden``/``controlableFields`` 整项替换（它们本来就是
        "这个类型在这个版本的清单"），``properties`` 按键合并、覆盖项赢——包里的属性清单
        往往来自实例同步，一刀切替换会把同步结果丢掉。
        """
        base: ObjectSchemaDef | None = None
        for schema in self.schemas:
            if schema.dhis2_type == dhis2_type:
                base = schema
                break
        override = self.descriptor_override(dhis2_type)
        if not override:
            return base
        properties = dict(base.properties) if base else {}
        extra = override.get("properties")
        if isinstance(extra, Mapping):
            properties.update(extra)
        return ObjectSchemaDef(
            dhis2_type=dhis2_type,
            required=_as_tuple(override.get("required"), base.required if base else ()),
            properties=properties,
            forbidden=_as_tuple(override.get("forbidden"), base.forbidden if base else ()),
            controlable_fields=_as_tuple(
                override.get("controlableFields"),
                base.controlable_fields if base else (),
            ),
        )

    def schema_view_types(self) -> tuple[str, ...]:
        """发布产物时该出现的类型：Schema 包里有的 ⊕ 描述器覆盖里有的。

        只有覆盖没有包片段的类型也要发出去——那正是覆盖存在的理由（这个版本的这个类型
        和别人不一样）。
        """
        names = {schema.dhis2_type for schema in self.schemas}
        names.update(str(item.get("dhis2Type") or "") for item in self.descriptor_overrides)
        names.discard("")
        return tuple(sorted(names))

    def controlable_fields(self, dhis2_type: str) -> tuple[str, ...]:
        schema = self.object_schema(dhis2_type)
        return schema.controlable_fields if schema else ()

    def forbidden_fields(self, dhis2_type: str) -> tuple[str, ...]:
        schema = self.object_schema(dhis2_type)
        return schema.forbidden if schema else ()

    def is_deprecated(self, kind: str, name: str) -> bool:
        return any(item.kind == kind and item.name == name for item in self.deprecated)

    def is_removed(self, kind: str, name: str) -> bool:
        return any(item.kind == kind and item.name == name for item in self.removed)

    def associated_data_check(self, dhis2_type: str) -> AssociatedDataCheck | None:
        for check in self.associated_data:
            if check.dhis2_type == dhis2_type:
                return check
        return None

    # --- 导入参数（DX-EXE-011）--------------------------------------------

    def import_defaults(self, family: str = "metadata") -> dict[str, Any]:
        block = self.import_parameters.get(family) or {}
        defaults: dict[str, Any] = dict(block.get("defaults") or {})
        return defaults

    def import_allowed_parameters(self, family: str = "metadata") -> dict[str, Any]:
        block = self.import_parameters.get(family) or {}
        return dict(block.get("allowed") or {})

    def async_mechanism(self, family: str = "metadata") -> dict[str, Any]:
        block = self.import_parameters.get(family) or {}
        return dict(block.get("async") or {})

    # --- 版本匹配（DX-CAP-005、DX-CON-001）--------------------------------

    def matches_version(self, version: str) -> bool:
        """``2.41.10`` 属于 ``2.41`` 系列；只看前两段，补丁号不影响端点形状。"""
        return _series_of(version) == self.dhis2_series

    def matches_constraint(self, constraint: str | None) -> bool:
        """环境档案的 ``versionConstraint``（如 ``2.41.x``）是否由本 Profile 覆盖。"""
        if not constraint:
            return True
        normalized = constraint.strip().lower()
        if normalized in {"", "any", "*"}:
            return True
        series = _series_of(normalized)
        if series == self.dhis2_series:
            return True
        # 允许 "2.41.0 - 2.41.99" 这类范围写法中的端点
        return normalized.split()[0].startswith(self.dhis2_series)


def _series_of(version: str) -> str:
    parts = version.strip().split(".")
    if len(parts) < 2:
        return version.strip()
    return f"{parts[0]}.{parts[1]}"


def _as_tuple(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """覆盖项里的清单：出现了就是**整项替换**（空列表也是），没出现才沿用被覆盖的那一份。"""
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return fallback


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_optional_json(path: Path) -> Any:
    """随版本补充的数据：缺失就是“没有额外约定”，不是装载失败。"""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_profile(directory: Path) -> VersionProfile:
    """从一个 Profile 目录装载数据（DX-CAP-001 的六个组成部分 + 表达式语法版本）。"""
    meta = _read_json(directory / "profile.json")
    resources_doc = _read_json(directory / "resources.json")
    entries = [
        ResourceEntry(
            logical_name=str(item["logicalName"]),
            path=str(item["path"]),
            method=str(item.get("method", "GET")).upper(),
            parameters={str(k): str(v) for k, v in (item.get("parameters") or {}).items()},
            paging=item.get("paging"),
            tracker_api=bool(item.get("trackerApi", False)),
            credential_scope=str(item.get("credentialScope", "discovery")),
        )
        for item in resources_doc.get("resources", [])
    ]
    # 按类型展开：``metadata.<type>`` -> 该类型的集合端点（数据驱动，不是代码里的表）
    for dhis2_type, plural in sorted((resources_doc.get("typeEndpoints") or {}).items()):
        entries.append(
            ResourceEntry(
                logical_name=f"{TYPE_RESOURCE_PREFIX}{dhis2_type}",
                path=str(plural),
                method="POST",
                parameters={"fields": "id,code,name,created,lastUpdated", "page": "page"},
                paging="pager",
            )
        )

    schemas_doc = _read_json(directory / "schemas.json")
    schemas = tuple(
        ObjectSchemaDef(
            dhis2_type=str(item["dhis2Type"]),
            required=tuple(item.get("required") or ()),
            properties=dict(item.get("properties") or {}),
            forbidden=tuple(item.get("forbidden") or ()),
            controlable_fields=tuple(item.get("controlableFields") or ()),
        )
        for item in schemas_doc.get("schemas", [])
    )

    def _items(key: str) -> tuple[DeprecatedItem, ...]:
        doc = _read_json(directory / f"{key}.json")
        return tuple(
            DeprecatedItem(
                kind=str(item["kind"]),
                name=str(item["name"]),
                removed_in=item.get("removedIn"),
                note=item.get("note"),
            )
            for item in doc.get(key, [])
        )

    associated_doc = _read_json(directory / "associated_data.json")
    associated = tuple(
        AssociatedDataCheck(
            dhis2_type=str(item["dhis2Type"]),
            checks=tuple(item.get("checks") or ()),
        )
        for item in associated_doc.get("checks", [])
    )

    volatile_doc = _read_json(directory / "volatile_fields.json")

    return VersionProfile(
        profile_version=str(meta["profileVersion"]),
        dhis2_series=str(meta["dhis2Series"]),
        version_constraint=str(meta.get("versionConstraint") or meta["dhis2Series"]),
        expression_grammar_version=str(meta["expressionGrammarVersion"]),
        translation_format=str(meta.get("translationFormat", "translations")),
        sharing_format=str(meta.get("sharingFormat", "sharing")),
        resources=tuple(entries),
        schemas=schemas,
        import_parameters=_read_json(directory / "import_params.json"),
        deprecated=_items("deprecated"),
        removed=_items("removed"),
        descriptor_overrides=tuple(
            _read_json(directory / "descriptor_overrides.json").get("overrides", [])
        ),
        validation_rules=_read_json(directory / "validation_rules.json"),
        property_defaults=dict(
            _read_optional_json(directory / "property_defaults.json").get("defaults") or {}
        ),
        volatile_fields=tuple(volatile_doc.get("fields", [])),
        associated_data=associated,
        read_fields=str(meta.get("readFields", "id,code,name,created,lastUpdated")),
        api_prefix=str(meta.get("apiPrefix", "api")).strip("/"),
        source_directory=str(directory),
    )


__all__ = [
    "AssociatedDataCheck",
    "DeprecatedItem",
    "ObjectSchemaDef",
    "ResourceEntry",
    "TYPE_RESOURCE_PREFIX",
    "VersionProfile",
    "load_profile",
]
