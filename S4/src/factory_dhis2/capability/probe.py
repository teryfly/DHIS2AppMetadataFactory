"""实例探测（DX-CAP-001/003、DX-CON-001）。

探测只回答三个问题：实例是什么版本、它的 Schema 怎么说、目录里的哪些端点真的在。
每一项都是**只读**请求（DX-BR-04）：一次探测之后实例必须与探测前完全一致，否则“探测”
本身就变成了未经批准的变更。

``unavailable_resources`` 是有用的负面事实：某些实例（尤其经代理部署的）不暴露
``/api/about`` 这类端点。把它记下来，S2 与报告才知道这是实例的实情，而不是我们的猜测。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.contract.capability_profile import DiscoveredSchema
from factory_dhis2.errors import Dhis2Error
from factory_dhis2.queries.existence import _text

SCHEMA_FIELDS = "name,required,properties[name],properties[required],properties[fieldType]"
#: /api/me 不带字段时不会返回 authorities / userRoles，必须显式要
_ME_FIELDS = "id,name,username,authorities[:is],userRoles[id,name],credentials"


@dataclass
class ProbeResult:
    """一次探测的结果。"""

    version: str
    probed_at: str
    schemas: list[DiscoveredSchema] = field(default_factory=list)
    authorities: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    org_scope: dict[str, Any] = field(default_factory=dict)
    unavailable_resources: list[str] = field(default_factory=list)
    resource_checks: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "authorities": sorted(self.authorities),
            "roles": sorted(self.roles),
            "orgScope": dict(self.org_scope),
            "unavailableResources": sorted(self.unavailable_resources),
            "resourceChecks": dict(sorted(self.resource_checks.items())),
            "schemasSynced": len(self.schemas),
        }


def probe_instance(
    client: Dhis2Client,
    *,
    schema_types: Sequence[str] = (),
    check_resources: bool = True,
) -> ProbeResult:
    """读取版本、Schema、账户权限与端点可用性（全部 GET）。"""
    payload = client.call("system.info").payload
    version = _text(payload if isinstance(payload, dict) else None, "version") or ""
    result = ProbeResult(version=version, probed_at=datetime.now(UTC).isoformat())

    me = client.call("me", params={"fields": _ME_FIELDS}).payload
    if isinstance(me, dict):
        result.authorities = [
            str(item.get("name"))
            for item in me.get("authorities") or []
            if isinstance(item, dict) and item.get("name")
        ]
        result.roles = [
            str(item.get("name"))
            for item in me.get("userRoles") or []
            if isinstance(item, dict) and item.get("name")
        ]
        credentials = me.get("credentials")
        if isinstance(credentials, dict):
            result.org_scope = {
                key: value
                for key, value in credentials.items()
                if isinstance(value, (str, int, bool))
            }

    for dhis2_type in schema_types:
        if not client.target.profile.has_type(dhis2_type):
            continue
        try:
            schema = client.call("schemas", dhis2_type=dhis2_type).payload
        except Dhis2Error:
            result.unavailable_resources.append(f"schemas:{dhis2_type}")
            continue
        if isinstance(schema, dict):
            result.schemas.append(_schema_of(dhis2_type, schema, client))

    if check_resources:
        for logical_name in sorted(
            {entry.logical_name for entry in client.target.profile.resources if entry.method == "GET"}
        ):
            if logical_name in {"system.info", "me", "schemas"}:
                continue
            try:
                client.call(
                    logical_name,
                    params={"pageSize": "1", "page": "1"},
                    dhis2_type=_type_of(logical_name),
                )
                result.resource_checks[logical_name] = 200
            except Dhis2Error as exc:
                status = (exc.details or {}).get("status")
                result.resource_checks[logical_name] = int(status) if isinstance(status, int) else 0
                if int(status or 0) == 404:
                    result.unavailable_resources.append(logical_name)
    return result


def _schema_of(dhis2_type: str, schema: dict[str, Any], client: Dhis2Client) -> DiscoveredSchema:
    """把 ``/api/schemas/<类型>`` 的属性描述转成我们发布的 Schema 片段。

    ``controlableFields`` 保留离线基线的清单：那是“本次部署有权改哪些字段”的项目决定，
    不是实例事实，实例的 Schema 里没有这个概念。
    """
    properties = schema.get("properties") or schema.get("propertySchemas") or []
    required: list[str] = []
    described: dict[str, Any] = {}
    rows = properties if isinstance(properties, list) else []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        if item.get("required"):
            required.append(name)
        described[name] = {
            "type": item.get("simpleType") or item.get("type"),
            "readOnly": bool(item.get("readOnly")),
        }
    baseline = client.target.profile.object_schema(dhis2_type)
    return DiscoveredSchema(
        dhis2_type=dhis2_type,
        required=sorted(required) or list(baseline.required if baseline else ()),
        properties=described,
        forbidden=list(baseline.forbidden) if baseline else [],
        controlable_fields=list(baseline.controlable_fields) if baseline else [],
    )


def _type_of(logical_name: str) -> str | None:
    prefix = "metadata."
    if logical_name.startswith(prefix):
        return logical_name[len(prefix) :] or None
    return None


__all__ = ["ProbeResult", "SCHEMA_FIELDS", "_ME_FIELDS", "probe_instance"]
