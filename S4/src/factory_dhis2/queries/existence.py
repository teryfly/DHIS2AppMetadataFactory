"""UID / code 存在性检查（DX-QRY-002/003、总体 SRS FR-REG-007/008）。

返回值的形状是一件需要小心对待的事：SRS 要求带上**占用对象的类型与 code**，而 S2 的既有
调用是 ``dict(module.uid_exists(...))`` 然后按真值判断（``factory_blueprint/s4/adapter.py``）。
如果直接把富信息塞进普通 dict，任何非空值都是真——“UID 已被占用”与“UID 可用”就再也分不
开，而这种错误会让 S2 把已占用的号继续分配出去。

因此这里返回的是一个 ``dict`` 子类：``bool(x)`` 严格等于 ``x["exists"]``，同时带着类型与
code。两边要求同时满足，且没有任何隐式真值。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from factory_foundation.api import external_io, unit_of_work

from factory_dhis2.capability.runtime_profile import apply_environment_capability
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.config import get_settings
from factory_dhis2.enums import CredentialScope
from factory_dhis2.environment.loader import build_target
from factory_dhis2.errors import Dhis2Error

EXISTENCE_FIELDS = "id,code,name,objectType,lastUpdated"


class _TruthyMapping(dict[str, Any]):
    """真值性由 ``exists`` 决定而不是“非空即真”的映射。"""

    __slots__ = ()

    @property
    def exists(self) -> bool:
        return bool(self.get("exists"))

    def __bool__(self) -> bool:
        return self.exists

    def __repr__(self) -> str:
        return f"{type(self).__name__}({dict(self)!r})"


class UidOccupancy(_TruthyMapping):
    """UID 是否被占用，以及占用它的对象是什么（DX-QRY-002）。"""

    def __init__(
        self,
        exists: bool,
        *,
        dhis2_type: str | None = None,
        code: str | None = None,
        name: str | None = None,
        basis: str | None = None,
    ) -> None:
        super().__init__(
            {
                "exists": exists,
                "dhis2Type": dhis2_type,
                "code": code,
                "name": name,
                "basis": basis,
            }
        )


class CodeOccupancy(_TruthyMapping):
    """code 是否已被使用，以及对应的 UID（DX-QRY-003）。"""

    def __init__(
        self,
        exists: bool,
        *,
        uid: str | None = None,
        name: str | None = None,
        basis: str | None = None,
    ) -> None:
        super().__init__(
            {"exists": exists, "uid": uid, "name": name, "basis": basis}
        )


@external_io
def uid_exists(environment_key: str, uids: Sequence[str]) -> dict[str, UidOccupancy]:
    """批量查 UID 是否被任何对象占用（DX-QRY-002）。

    先问实例的“可识别对象”接口（逻辑资源 ``identifiableObjects``，DX-QRY-002 点名的那个）。
    但 2.41.10 的实测结论是：**该接口可以整体返回空集**，哪怕 UID 确实属于一个存在的对象
    （``/api/dataElements/FTRrcoaog83`` 返回 200，而 identifiableObjects 对同一 UID 返回
    空）。把“查不到”当成“未被占用”会造成 UID 撞号，因此对没答上来的 UID 退回到按目录里的
    类型用 ``filter=id:in:[…]`` 扫一遍——那是实例真正支持的批量查询。
    两条路径的结论都写进 ``basis``，让调用方看得见依据。

    扫描**中途失败**时未知的 UID 一律按“已占用”报（``basis`` 以 ``inconclusive`` 开头）：
    宁可让 S2 换一个号，也不能把一个其实被占用的号分配出去。
    """
    if not uids:
        return {}
    wanted = list(dict.fromkeys(str(uid) for uid in uids))
    with unit_of_work() as session:
        target = build_target(session, environment_key, scope_hint=CredentialScope.DISCOVERY)
    client = Dhis2Client(apply_environment_capability(target), scope=CredentialScope.DISCOVERY)
    batch = max(1, get_settings().presence_batch_size)
    out: dict[str, UidOccupancy] = {}

    for chunk in _chunks(wanted, batch):
        rows = _query(client, f"id:in:[{','.join(chunk)}]")
        for row in rows:
            uid = row.get("id")
            if isinstance(uid, str) and uid in wanted:
                out[uid] = UidOccupancy(
                    True,
                    dhis2_type=_type_of(row),
                    code=_text(row, "code"),
                    name=_text(row, "name"),
                    basis="identifiableObjects",
                )
    unresolved = [uid for uid in wanted if uid not in out]
    if not unresolved:
        return {uid: out[uid] for uid in wanted}

    swept, sweep_incomplete = _sweep_catalogue(client, unresolved)
    for uid, row in swept.items():
        out[uid] = UidOccupancy(
            True,
            dhis2_type=row.get("_dhis2Type"),
            code=_text(row, "code"),
            name=_text(row, "name"),
            basis="catalogue-sweep",
        )
    for uid in unresolved:
        if uid in out:
            continue
        out[uid] = UidOccupancy(
            # 扫描不完整时报“已占用”：这是安全的一侧
            sweep_incomplete,
            basis=(
                "inconclusive:catalogue-sweep-incomplete"
                if sweep_incomplete
                else "catalogue-sweep-none"
            ),
        )
    return {uid: out[uid] for uid in wanted}


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    return [list(values[start : start + size]) for start in range(0, len(values), size)]


def _sweep_catalogue(
    client: Dhis2Client, uids: Sequence[str]
) -> tuple[dict[str, dict[str, Any]], bool]:
    """按目录里的类型批量过滤 UID，返回确实存在的那些，以及扫描是否**没做完**。

    一次请求查一类、一批 ``presence_batch_size`` 个 UID：取值全塞在同一个查询参数里，超过
    这个规模会顶到实例的 URL 长度限制。
    """
    found: dict[str, dict[str, Any]] = {}
    wanted = list(dict.fromkeys(uids))
    batch = max(1, get_settings().presence_batch_size)
    incomplete = False
    for dhis2_type in client.target.profile.supported_types:
        remaining = [uid for uid in wanted if uid not in found]
        if not remaining:
            break
        for chunk in _chunks(remaining, batch):
            try:
                rows = list(
                    client.list_all(
                        dhis2_type,
                        fields="id,code,name,lastUpdated",
                        filters=[f"id:in:[{','.join(chunk)}]"],
                        page_size=max(len(chunk), 1),
                    )
                )
            except Dhis2Error as exc:
                # 2.41.10 实测：目录登记的部分类型该实例根本没有端点
                # （programStageDataElements、validationNotifications 等回 404）。404 是
                # **这一类的完整否定答案**，不能算"没扫成"——否则每个不存在的 UID 都会被
                # 保守报成"已占用"，UID 池一会儿就被自己填满了。
                # 只有真正的失败（5xx、超时、权限）才让这次扫描不完整。
                if exc.error_code not in {"DHIS2_NOT_FOUND"}:
                    incomplete = True
                continue
            for row in rows:
                uid = row.get("id")
                if isinstance(uid, str) and uid in wanted:
                    found[uid] = {**row, "_dhis2Type": dhis2_type}
    return found, incomplete


@external_io
def code_exists(
    environment_key: str, dhis2_type: str, codes: Sequence[str]
) -> dict[str, CodeOccupancy]:
    """按对象类型批量查 code 是否已存在（DX-QRY-003）。

    一个 code 一次请求在几千个对象的 Registry 上会打出几千次请求，与 DX-NFR-02 的耗时预算
    正面冲突；2.41 实测支持 ``filter=code:in:[A,B]``，所以按批问。
    """
    if not codes:
        return {}
    wanted = list(dict.fromkeys(str(code) for code in codes))
    with unit_of_work() as session:
        target = build_target(session, environment_key, scope_hint=CredentialScope.DISCOVERY)
        has_type = target.profile.has_type(dhis2_type)
    if not has_type:
        # 类型不在目录里就无法判断：保守起见报“存在”，让 S2 换 code 而不是撞号后导入失败
        return {
            code: CodeOccupancy(
                True,
                basis=f"type {dhis2_type!r} is not in the resource catalogue",
            )
            for code in wanted
        }
    client = Dhis2Client(apply_environment_capability(target), scope=CredentialScope.DISCOVERY)
    batch = max(1, get_settings().presence_batch_size)
    out: dict[str, CodeOccupancy] = {}
    incomplete = False
    for chunk in _chunks(wanted, batch):
        filters = [f"code:in:[{','.join(chunk)}]"]
        try:
            rows = list(
                client.list_all(
                    dhis2_type,
                    fields="id,code,name",
                    filters=filters,
                    page_size=max(len(chunk), 1),
                )
            )
        except Dhis2Error as exc:
            # 与 UID 扫描同一口径：该类型端点不存在（404）是完整答案，不是"问不成"
            if exc.error_code != "DHIS2_NOT_FOUND":
                incomplete = True
            continue
        for row in rows:
            code = _text(row, "code")
            uid = row.get("id")
            if code in wanted and code not in out:
                out[code] = CodeOccupancy(
                    True,
                    uid=str(uid) if uid else None,
                    name=_text(row, "name"),
                    basis="metadata.read",
                )
    for code in wanted:
        if code not in out:
            out[code] = CodeOccupancy(
                # 问不成就是"不知道"，而"不知道"要按已被占用报，S2 才会换号而不是撞车
                incomplete,
                basis="inconclusive:read-failed" if incomplete else "metadata.read-none",
            )
    return {code: out[code] for code in wanted}


def _query(client: Dhis2Client, criterion: str) -> list[dict[str, Any]]:
    try:
        payload = client.call(
            "identifiableObjects",
            params={
                "criterion": criterion,
                "fields": EXISTENCE_FIELDS,
                "paging": "false",
            },
        ).payload
    except Dhis2Error:
        return []
    if not isinstance(payload, dict):
        return []
    rows = payload.get("identifiableObjects")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _type_of(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    value = row.get("objectType") or row.get("klass") or row.get("type")
    return str(value) if value else None


def _text(row: Mapping[str, Any] | None, key: str) -> str | None:
    if not row:
        return None
    value = row.get(key)
    return value if isinstance(value, str) and value else None


__all__ = ["CodeOccupancy", "EXISTENCE_FIELDS", "UidOccupancy", "code_exists", "uid_exists"]
