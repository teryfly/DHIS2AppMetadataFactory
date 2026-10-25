"""组织层级读取（DX-DSC-006）。

映射要引用 OrgUnit，而引用一个不存在的组织单元是 BLOCKING 的编译错误——所以发现必须
把层级结构带回来（UID、code、name、层级、父节点），而不只是一个扁平清单。

按**配置深度**读取而不是整棵树：国家级实例的组织树可能有几十万节点，设计阶段真正需要的
通常只有前两三层的骨架。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.contract.discovery_result import OrgUnitDocument

ORG_UNIT_TYPE = "organisationUnit"
ORG_FIELDS = "id,code,name,created,lastUpdated,namePath,level,parentNode[id,name]"
MAX_NODES = 5000


@dataclass(frozen=True)
class OrgUnitRead:
    """一次组织层级读取的结果。

    ``truncated`` 不是"小提示"：发现结果一旦缺了节点，S2 的映射就会引用到一个"目标环境里
    不存在"的 OrgUnit，而那个 BLOCKING 错误要等到编译才发现。所以截断必须显式带出去。
    """

    documents: list[OrgUnitDocument] = field(default_factory=list)
    truncated: bool = False
    total_seen: int = 0


def read_org_units(
    client: Dhis2Client,
    depth: int | None,
    *,
    cancel: Any | None = None,
    max_nodes: int = MAX_NODES,
) -> OrgUnitRead:
    """读取到指定层级的组织单元。``depth`` 为空表示不读组织层级。"""
    if depth is None or depth <= 0:
        return OrgUnitRead()
    if not client.target.profile.has_type(ORG_UNIT_TYPE):
        return OrgUnitRead()
    rows = list(
        client.list_all(
            ORG_UNIT_TYPE,
            fields=ORG_FIELDS,
            filters=[f"level:le:{depth}"],
            page_size=client.settings.discovery_page_size,
            cancel=cancel,
        )
    )
    out: list[OrgUnitDocument] = []
    for row in rows[:max_nodes]:
        parent = row.get("parentNode")
        parent_uid = parent.get("id") if isinstance(parent, dict) else None
        level = row.get("level")
        out.append(
            OrgUnitDocument(
                uid=str(row.get("id") or ""),
                code=row.get("code") if isinstance(row.get("code"), str) else None,
                name=row.get("name") if isinstance(row.get("name"), str) else None,
                level=int(level) if isinstance(level, (int, str)) and str(level).isdigit() else None,
                parent=str(parent_uid) if parent_uid else None,
                path=row.get("namePath") if isinstance(row.get("namePath"), str) else None,
            )
        )
    kept = [item for item in out if item.uid]
    return OrgUnitRead(
        documents=kept,
        truncated=len(rows) > max_nodes,
        total_seen=len(rows),
    )


__all__ = ["MAX_NODES", "ORG_FIELDS", "ORG_UNIT_TYPE", "OrgUnitRead", "read_org_units"]
