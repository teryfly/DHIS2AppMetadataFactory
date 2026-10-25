"""关联数据检查：删除安全（DX-QRY-004、总体 SRS FR-CMP-009）。

删一个还挂着数据的元数据对象，DHIS2 要么拒绝、要么留下孤儿数据——两种都很糟，所以删除
候选集必须先过这一关。

判定方式**按类型由 Profile 定义**（``profiles/data/<系列>/associated_data.json``），代码里
不写死：不同版本能问的问题不同。两个兜底方向都偏向“不许删”：

* 类型没有登记判定方式 → 视为有关联数据（SRS 明说：其他类型一律返回“有关联数据”）；
* 判定所依赖的端点在实例上不可用 → 同样返回有关联数据，并把依据写进结果，让人看得见
  为什么被挡。判不出来就不许删，这是可逆与不可逆的不对称性决定的。
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import external_io, unit_of_work

from factory_dhis2.capability.runtime_profile import apply_environment_capability
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.enums import CredentialScope
from factory_dhis2.environment.loader import build_target
from factory_dhis2.errors import Dhis2Error
from factory_dhis2.queries.existence import _TruthyMapping

#: 判定不可用时的方向：保守，等于“不许删”
CONSERVATIVE = "HAS_DATA"


class AssociatedDataVerdict(_TruthyMapping):
    """是否存在关联业务数据，以及判断依据（真值性即结论）。"""

    def __init__(
        self,
        has_data: bool,
        basis: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__({"hasData": has_data, "basis": basis, "evidence": evidence or {}})

    def __bool__(self) -> bool:
        return bool(self.get("hasData"))

    @property
    def has_data(self) -> bool:
        return bool(self.get("hasData"))


#: 判定参数里属于“分页/投影”的键：它们不是过滤条件，交给 ``list_all`` 的参数处理。
_PAGING_PARAMS: frozenset[str] = frozenset({"pageSize", "page", "fields"})


@external_io
def has_associated_data(
    environment_key: str,
    dhis2_type: str,
    uid: str,
    *,
    program: str | None = None,
) -> AssociatedDataVerdict:
    """这个对象在实例里还挂着业务数据吗。"""
    with unit_of_work() as session:
        target = build_target(session, environment_key, require_probe=False)
    # 判定表按项目/版本决定，端点可用性按实例决定：装配能力产物后再取判定与发请求
    target = apply_environment_capability(target)
    check = target.profile.associated_data_check(dhis2_type)
    if check is None:
        return AssociatedDataVerdict(
            True,
            f"版本 Profile 未登记 {dhis2_type} 的关联数据判定方式，按保守方向处理",
            {"rule": "DX-QRY-004"},
        )
    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    evidence: dict[str, Any] = {}
    for item in check.checks:
        label = str(item.get("label") or "check")
        resource = str(item.get("resource") or "")
        params = {
            key: str(value).replace("{uid}", uid).replace("{program}", program or "")
            for key, value in (item.get("params") or {}).items()
        }
        nested_type = item.get("dhis2Type")
        try:
            if nested_type or resource == "metadata.read":
                # ``filter`` 参数的值**本身就是**过滤表达式（``optionSet.id:eq:<uid>``），
                # 再给它加一次 ``filter:`` 前缀会让实例把 ``filter`` 当成字段名去找，
                # 于是每条判定都恒为“命中/不命中”——那是比报错更糟的沉默。
                rows = list(
                    client.list_all(
                        str(nested_type or dhis2_type),
                        fields="id,code,name",
                        filters=[
                            value if key == "filter" else f"{key}:eq:{value}"
                            for key, value in params.items()
                            if key not in _PAGING_PARAMS
                        ],
                        page_size=1,
                    )
                )
                found = bool(rows)
                evidence[label] = {"matched": [row.get("id") for row in rows[:3]]}
            elif resource == "dataValues.query":
                payload = client.call(resource, params=params).payload
                values = payload.get("dataValues") if isinstance(payload, dict) else None
                found = bool(values)
                evidence[label] = {"rows": len(values or [])}
            else:
                payload = client.call(resource, params=params).payload
                found = _non_empty(payload)
                evidence[label] = {"responseKeys": sorted(payload)[:6] if isinstance(payload, dict) else None}
        except Dhis2Error as exc:
            evidence[label] = {"error": exc.error_code}
            if str(item.get("nonEmptyMeans") or "") == "HAS_DATA":
                return AssociatedDataVerdict(
                    True,
                    f"判定 {dhis2_type} 的端点不可用（{exc.message}），按保守方向处理",
                    evidence,
                )
            continue
        if found:
            return AssociatedDataVerdict(True, f"{label} 显示该对象仍在使用", evidence)
    return AssociatedDataVerdict(False, "所有登记的判定都为空", evidence)


def _non_empty(payload: Any) -> bool:
    if isinstance(payload, list):
        return bool(payload)
    if isinstance(payload, dict):
        for key in ("instances", "events", "enrollments", "trackedEntities", "dataValues"):
            if isinstance(payload.get(key), list) and payload[key]:
                return True
        return any(bool(value) for value in payload.values())
    return bool(payload)


__all__ = ["CONSERVATIVE", "AssociatedDataVerdict", "has_associated_data"]
