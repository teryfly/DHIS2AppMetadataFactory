"""Greenfield 发现要读的 default 对象（DX-DSC-003）。

``default`` CategoryCombo / Category / CategoryOption / CategoryOptionCombo 是 DHIS2 每个
实例都有的基础设施，也是 S2 编译 DataElement 时的隐式默认值（FR-COV-007）。读它们不需要
范围参数，因此 Greenfield 项目可以在**什么都不配置**的情况下完成 DISCOVERY 阶段。

只取 ``name=default`` 的那几个：实例上往往有成百上千个 CategoryOption，全量拉取既慢又没
有意义。
"""

from __future__ import annotations

from typing import Any

from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.enums import DEFAULT_OBJECT_TYPES
from factory_dhis2.errors import Dhis2Error

#: 默认对象的 code 或 name：DHIS2 的初始数据用 name="default"，部分版本带 code="default"
DEFAULT_LOOKUP_FIELDS = "id,code,name,created,lastUpdated"


def read_defaults(client: Dhis2Client) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """返回 ``{类型: UID}`` 与 ``{类型: 对象}``；缺失的类型只记在日志里，不编造。"""
    uids: dict[str, str] = {}
    contents: dict[str, dict[str, Any]] = {}
    for dhis2_type in DEFAULT_OBJECT_TYPES:
        if not client.target.profile.has_type(dhis2_type):
            continue
        try:
            rows = list(
                client.list_all(
                    dhis2_type,
                    fields=DEFAULT_LOOKUP_FIELDS,
                    filters=["name:eq:default"],
                    page_size=5,
                )
            )
        except Dhis2Error:
            continue
        chosen = rows[0] if rows else None
        if chosen is None:
            continue
        uid = chosen.get("id")
        if isinstance(uid, str):
            uids[dhis2_type] = uid
            contents[dhis2_type] = chosen
    return uids, contents


__all__ = ["DEFAULT_LOOKUP_FIELDS", "read_defaults"]
