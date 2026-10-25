"""Profile 注册表（DX-CAP-001/002、DX-NFR-07）。

装载是**自动发现**的：``data/`` 下的每个目录就是一个 Profile。新增 DHIS2 版本只需要放
一个目录，注册表、Client 与执行器都不改——这正是 FR-CAP-001 想要的结果。

``Dhis242Profile`` 属 M4（SRS 12 章），本轮不随包发布；请求 2.42 实例会得到
``DHIS2_VERSION_UNSUPPORTED``，而不是被一个不存在的 Profile 假装支持。
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from factory_dhis2.errors import VersionUnsupportedError
from factory_dhis2.profiles.base import VersionProfile, load_profile

DATA_ROOT = Path(__file__).resolve().parent / "data"

_LOCK = RLock()
_BY_SERIES: dict[str, VersionProfile] = {}
_LOADED = False


def load_builtin_profiles() -> dict[str, VersionProfile]:
    """装载随包发布的版本 Profile（幂等）。"""
    global _LOADED
    with _LOCK:
        if _LOADED:
            return dict(_BY_SERIES)
        found: dict[str, VersionProfile] = {}
        if DATA_ROOT.is_dir():
            for directory in sorted(DATA_ROOT.iterdir()):
                if not (directory / "profile.json").is_file():
                    continue
                profile = load_profile(directory)
                found[profile.dhis2_series] = profile
        _BY_SERIES.clear()
        _BY_SERIES.update(found)
        _LOADED = True
        return dict(_BY_SERIES)


def register_profile(profile: VersionProfile) -> None:
    """运行期登记一个 Profile（测试注入、或部署方自带数据文件）。"""
    with _LOCK:
        _BY_SERIES[profile.dhis2_series] = profile


def available_series() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_BY_SERIES))


def profile_for_series(series: str) -> VersionProfile:
    """按 ``2.41`` 这样的系列号取 Profile。"""
    normalized = _normalize_series(series)
    with _LOCK:
        found = _BY_SERIES.get(normalized)
    if found is None:
        raise VersionUnsupportedError(
            f"no DHIS2 version profile for series {series!r}; "
            f"available: {', '.join(available_series()) or 'none loaded'}",
            details={"series": normalized, "available": list(available_series())},
        )
    return found


def profile_for_version(version: str) -> VersionProfile:
    """按实例上报的版本号（如 ``2.41.10``）取 Profile（DX-CON-001）。"""
    return profile_for_series(_normalize_series(version))


def _normalize_series(value: str) -> str:
    parts = value.strip().split(".")
    if len(parts) < 2:
        return value.strip()
    return f"{parts[0]}.{parts[1]}"


def newest_profile() -> VersionProfile:
    """最新的一个版本 Profile（系列号最大者）。

    只服务于**建立**能力记录的入口：连接测试与探测必须在还没有 Capability Profile 时
    也能问实例“你是什么版本”，此时按已装载的最新系列走，随后把实例上报的真实版本写进
    产物。任何依赖具体端点形状的执行操作都不走这条路（``build_target(require_probe=True)``）。
    """
    available = available_series()
    if not available:
        raise VersionUnsupportedError(
            "no DHIS2 version profile is loaded", details={"available": []}
        )
    newest = sorted(available, key=_series_key)[-1]
    return profile_for_series(newest)


def _series_key(series: str) -> tuple[int, int]:
    parts = series.split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):  # pragma: no cover - 目录里的系列号总是两段
        return 0, 0


def reset() -> None:
    """清空注册表（测试之间使用）。"""
    global _LOADED
    with _LOCK:
        _BY_SERIES.clear()
        _LOADED = False


__all__ = [
    "DATA_ROOT",
    "available_series",
    "load_builtin_profiles",
    "profile_for_series",
    "profile_for_version",
    "register_profile",
    "reset",
]
