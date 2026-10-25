"""运行期使用环境**已持久化**的 Capability Profile（DX-CLI-005、DX-CAP-003）。

版本 Profile 说"2.41 一般长这样"，Capability Profile 说"这个实例经探测确认长这样"。
前者随包发布、后者是产物，两者都可能被装载成 ``VersionProfile`` 给 Client 用——但只有后者
带这台机器的实情（哪些端点其实不存在、Schema 实际怎么说）。

这里负责把后者读出来并缓存。缓存的键是**产物 ID**：产物不可变，一次重新探测必然产生新的
产物 ID，因此不存在"缓存过期"这种失效模式；环境被标记为过期时 ``loader`` 会拒绝装配，
而不是悄悄用旧的那份（DX-CAP-005）。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from threading import RLock
from typing import Any

from factory_dhis2.contract.capability_profile import (
    CapabilityProfileDocument,
    profile_from_document,
)
from factory_dhis2.errors import InputArtifactInvalidError
from factory_dhis2.profiles.base import VersionProfile
from factory_dhis2.target import EnvironmentTarget

#: 每个工作进程同时服务的环境数量级很小；有界即可，别让它无上限增长。
_MAX_CACHED = 32
_CACHE: OrderedDict[str, VersionProfile] = OrderedDict()
_LOCK = RLock()

CACHE_SIZE = _MAX_CACHED


def runtime_profile(
    *, artifact_id: str | None, environment_key: str, base: VersionProfile
) -> VersionProfile:
    """该环境运行期该用的 Profile：有产物就读产物，没有就按版本 Profile 引导。

    ``base`` 是随包的版本 Profile：连接测试与探测本身就是"还没有产物"时唯一能用的目录
    （bootstrap 路径），其余执行与查询路径都要求先探测（``require_probe=True``）。
    """
    if artifact_id is None:
        return base
    key = f"{environment_key}@{artifact_id}"
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
    document = _read_document(artifact_id)
    resolved = profile_from_document(document, base=base)
    with _LOCK:
        _CACHE[key] = resolved
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_CACHED:
            _CACHE.popitem(last=False)
    return resolved


def _read_document(artifact_id: str) -> CapabilityProfileDocument:
    from factory_dhis2.artifacts import read_json

    try:
        return CapabilityProfileDocument.model_validate(read_json(artifact_id))
    except InputArtifactInvalidError:
        raise
    except Exception as exc:  # noqa: BLE001 - 产物坏了要说清是哪一份、坏在哪
        raise InputArtifactInvalidError(
            f"the persisted Capability Profile {artifact_id!r} cannot be read: {exc}",
            artifact_id=artifact_id,
            details={"artifactId": artifact_id, "rule": "DX-CAP-003"},
        ) from exc


def apply_environment_capability(target: EnvironmentTarget) -> EnvironmentTarget:
    """把目标的 Profile 换成该环境**探测确认过**的那一份。

    必须在事务外调用：读产物内容是对象存储 I/O（00 文档 6.2）。
    """
    resolved = runtime_profile(
        artifact_id=target.capability_profile_artifact_id,
        environment_key=target.key,
        base=target.profile,
    )
    if resolved is target.profile:
        return target
    return replace(target, profile=resolved)


def clear_cache() -> None:
    """测试与热重载用：清空运行期能力缓存。"""
    with _LOCK:
        _CACHE.clear()


def cache_info() -> dict[str, Any]:
    with _LOCK:
        return {"entries": len(_CACHE), "limit": _MAX_CACHED, "keys": list(_CACHE)}


__all__ = [
    "CACHE_SIZE",
    "apply_environment_capability",
    "cache_info",
    "clear_cache",
    "runtime_profile",
]
