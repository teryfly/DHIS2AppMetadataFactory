"""提交时间窗判定（DX-ENV-002）。

时间窗按**环境自己的时区**解释：一个部署到亚洲实例的 PRODUCTION 环境，其“周二 20:00
起”指的是当地 20:00，不是 Factory 服务器时间，也不是 UTC。跨午夜窗口（22:00–02:00）是
真实需求，因此 ``end <= start`` 明确表示跨午夜，而不是一个校验错误。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from factory_dhis2.dto.environment import CommitWindow
from factory_dhis2.errors import EnvironmentInvalidError


def zone_of(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise EnvironmentInvalidError(
            f"unknown environment timezone: {timezone_name!r}",
            details={"timezone": timezone_name},
        ) from exc


def local_now(timezone_name: str, now: datetime | None = None) -> datetime:
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return reference.astimezone(zone_of(timezone_name))


def _minutes(hhmm: str) -> int:
    hour, _, minute = hhmm.partition(":")
    return int(hour) * 60 + int(minute)


def matches(window: CommitWindow, moment: datetime) -> bool:
    """``moment`` 是环境时区的本地时间。"""
    minutes = moment.hour * 60 + moment.minute
    weekday = moment.isoweekday()
    start = _minutes(window.start)
    end = _minutes(window.end)

    if window.wraps_midnight():
        # 22:00-02:00：今天 22:00 之后，或昨天窗口跨过来后的凌晨（周日 23:00 的“昨天”是周六，
        # 而周一 01:00 的“昨天”是周日 —— 星期号要绕回 7，不能算成 0）
        previous_day = weekday - 1 if weekday > 1 else 7
        if minutes >= start and weekday in window.days:
            return True
        return minutes < end and previous_day in window.days

    return weekday in window.days and start <= minutes < end


def within_commit_window(
    windows: list[CommitWindow],
    *,
    timezone_name: str,
    now: datetime | None = None,
    required: bool = True,
) -> tuple[bool, str | None]:
    """当前时刻是否落在任一时段内。

    ``required`` 为真但没有配置任何时间窗时判定为**不允许**：PRODUCTION 类环境必须
    显式给出时间窗（DX-ENV-002），空清单不是一种许可。
    """
    if not windows:
        return (not required, None if not required else "该环境未配置允许提交的时间窗")
    moment = local_now(timezone_name, now)
    for window in windows:
        if matches(window, moment):
            return True, None
    described = "; ".join(
        f"周{'一二三四五六日'[day - 1]} {window.start}-{window.end}" for window in windows for day in window.days
    )
    return (
        False,
        f"当前 {moment.strftime('%a %H:%M')} ({timezone_name}) 不在允许提交的时间窗内：{described}",
    )


def windows_as_documents(
    windows: list[CommitWindow], timezone_name: str
) -> list[dict[str, object]]:
    """S1 直接展示给审批人的形态，字段与键顺序固定（Gate 指纹要稳定）。"""
    out: list[dict[str, object]] = []
    for window in windows:
        out.append(
            {
                "days": list(window.days),
                "start": window.start,
                "end": window.end,
                "timezone": timezone_name,
                "wrapsMidnight": window.wraps_midnight(),
                "note": window.note,
            }
        )
    return out


def next_window_opening(
    windows: list[CommitWindow], timezone_name: str, now: datetime | None = None
) -> datetime | None:
    """下一次窗口开启的时间（错误信息里给操作者一个可等的时刻）。"""
    if not windows:
        return None
    moment = local_now(timezone_name, now)
    for day in range(0, 8):
        candidate = (moment + timedelta(days=day)).replace(second=0, microsecond=0)
        for window in windows:
            if candidate.isoweekday() not in window.days:
                continue
            hour, _, minute = window.start.partition(":")
            opening = candidate.replace(hour=int(hour), minute=int(minute))
            if opening >= moment:
                return opening.astimezone(UTC)
    return None


__all__ = [
    "local_now",
    "matches",
    "next_window_opening",
    "within_commit_window",
    "windows_as_documents",
    "zone_of",
]
