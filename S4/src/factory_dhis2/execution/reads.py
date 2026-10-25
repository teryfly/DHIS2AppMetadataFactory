"""读回目标环境的对象（漂移检查、Snapshot、部署后验证、对账共用的读侧）。

这些读全部走 **discovery 凭证范围**（DX-DSC-004、DX-BR-04）：观察环境不等于修改环境。
``{typePath}`` 由该类型自己的目录项代入，因此代码里没有任何版本相关路径（DX-CLI-005）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.profiles.base import VersionProfile

#: 漂移与 Snapshot 需要的最小字段集：内容哈希要真实内容，因此带 “:all” 形态
DEFAULT_READ_FIELDS = "id,code,name,created,lastUpdated"
FULL_READ_FIELDS = "id,code,name,created,lastUpdated,:all"


def path_args(profile: VersionProfile, dhis2_type: str, uid: str) -> dict[str, str]:
    """``metadata.read.one`` 的两个槽：类型集合路径 + UID。"""
    return {"typePath": profile.type_resource(dhis2_type).path, "uid": uid}


def read_one(
    client: Dhis2Client,
    dhis2_type: str,
    uid: str,
    *,
    fields: str = FULL_READ_FIELDS,
) -> dict[str, Any] | None:
    """按 UID 读回一个对象；不存在时返回 ``None``（对象被人删掉也是一种漂移）。

    这里必须捕的是 **S4 自己的** ``Dhis2NotFoundError``：Client 把 HTTP 404 映射成它，而不是
    S0 的 ``NotFoundError``。之前捕错类型时，"CREATE 对象在提交前本来就不存在" 这条正常路径
    会把 Snapshot 直接炸掉——只有真的对着会回 404 的实例跑才会暴露（在线契约与内存实例都回 404）。
    """
    from factory_foundation.api import NotFoundError

    from factory_dhis2.errors import Dhis2NotFoundError

    try:
        payload = client.call(
            "metadata.read.one",
            dhis2_type=dhis2_type,
            path_args=path_args(client.target.profile, dhis2_type, uid),
            params={"fields": fields},
        ).payload
    except (Dhis2NotFoundError, NotFoundError):
        return None
    return payload if isinstance(payload, dict) else None


def read_many(
    client: Dhis2Client,
    dhis2_type: str,
    uids: Sequence[str],
    *,
    fields: str = DEFAULT_READ_FIELDS,
    batch_size: int = 50,
) -> dict[str, dict[str, Any]]:
    """按 UID 批量读（``id:in:[…]`` 过滤器），返回 UID → 行。

    分批是因为 ``in`` 过滤器把 UID 全塞在一个参数里，上千个 UID 一次请求会超出实例的
    URL 长度限制；``batch_size`` 与 ``drift_batch_size`` 设置一致，让 1,000 对象的漂移
    检查仍能在 60 秒内完成（DX-NFR-02）。
    """
    out: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(list(uids), batch_size):
        rows = list(
            client.list_all(
                dhis2_type,
                fields=fields,
                filters=[f"id:in:[{','.join(chunk)}]"],
                page_size=max(len(chunk), 1),
            )
        )
        for row in rows:
            uid = row.get("id")
            if isinstance(uid, str):
                out[uid] = row
    return out


def collection_of(package_payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = package_payload.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), max(1, size)):
        yield items[start : start + max(1, size)]


__all__ = [
    "DEFAULT_READ_FIELDS",
    "FULL_READ_FIELDS",
    "collection_of",
    "path_args",
    "read_many",
    "read_one",
]
