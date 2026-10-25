"""UID 生成（DX-QRY-001、DX-AC-08）。

用实例的官方接口而不是自己拼随机串：UID 的**唯一性由实例保证**，S2 的池只是把分配动作
提前。自己生成的 UID 与实例上已有的对象撞号，会在导入时才暴露成最难查的一类错误。

分批是因为接口有单次上限，返回前校验形态（11 位、首位字母、字母数字）：形态不对的 UID
会在很多年后以一个“对象不存在”的形式回来找你，那时已经没有上下文了。
"""

from __future__ import annotations

import re

from factory_foundation.api import external_io, unit_of_work

from factory_dhis2.capability.runtime_profile import apply_environment_capability
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.config import get_settings
from factory_dhis2.enums import CredentialScope
from factory_dhis2.environment.loader import build_target
from factory_dhis2.errors import UidFormatInvalidError

#: DHIS2 的 UID 形态：11 位、首字符为字母、其余字母数字
UID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{10}$")
UID_LENGTH = 11


@external_io
def generate_uids(environment_key: str, count: int) -> list[str]:
    """向实例批量申请 UID（该接口不修改实例）。"""
    if count <= 0:
        return []
    with unit_of_work() as session:
        target = build_target(session, environment_key, scope_hint=CredentialScope.DISCOVERY)
    # DX-CLI-005：连引导接口也按该环境探测确认过的端点解析；读产物在事务外
    target = apply_environment_capability(target)
    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    batch = max(1, get_settings().uid_batch_limit)
    out: list[str] = []
    remaining = count
    while remaining > 0:
        size = min(batch, remaining)
        payload = client.call("system.id", params={"limit": str(size)}).payload
        codes = _codes_of(payload)
        if not codes:
            raise UidFormatInvalidError(
                f"the instance returned no UID for a request of {size}",
                details={
                    "environmentKey": environment_key,
                    "logicalResource": "system.id",
                    "requested": size,
                },
            )
        for uid in codes:
            _require_uid_shape(uid, environment_key)
            if uid not in out:
                out.append(uid)
        remaining = count - len(out)
        if len(codes) < size and remaining > 0:
            # 实例给了少于请求的数量：继续申请补齐，但绝不自己造号
            continue
    return out[:count]


def _codes_of(payload: object) -> list[str]:
    if isinstance(payload, dict):
        for key in ("codes", "id", "values"):
            value = payload.get(key)
            if isinstance(value, list):
                return [str(item) for item in value]
        if all(isinstance(item, str) for item in payload):
            return [str(item) for item in payload]
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return []


def _require_uid_shape(uid: str, environment_key: str) -> None:
    if not UID_PATTERN.fullmatch(uid):
        raise UidFormatInvalidError(
            f"the instance returned a malformed UID {uid!r} (length {len(uid)})",
            details={
                "environmentKey": environment_key,
                "uid": uid,
                "expected": "11 chars, first a letter, alphanumeric",
            },
        )


__all__ = ["UID_LENGTH", "UID_PATTERN", "generate_uids"]
