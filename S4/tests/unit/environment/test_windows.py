"""提交时间窗判定（DX-ENV-002、DX-AC 时间窗部分）。

时间窗按**环境自己的时区**解释，所以这些测试都显式给出一个时区名并比较它在 UTC 时刻
上的表现：一个用服务器时区或 UTC 判定窗口开启的实现，会在生产环境上把不该允许的提交
判成允许。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from factory_dhis2.dto.environment import CommitWindow
from factory_dhis2.environment.windows import (
    local_now,
    matches,
    next_window_opening,
    windows_as_documents,
    within_commit_window,
    zone_of,
)
from factory_dhis2.errors import EnvironmentInvalidError
from tests import window_not_now

#: 2026-01-05 是星期一：周二 6 日、周三 7 日、周四 8 日
MONDAY = datetime(2026, 1, 5, tzinfo=UTC)
TUESDAY = datetime(2026, 1, 6, tzinfo=UTC)
WEDNESDAY = datetime(2026, 1, 7, tzinfo=UTC)
SUNDAY = datetime(2026, 1, 11, tzinfo=UTC)

#: “周二、周四 20:00–23:00”
TWO_EVENINGS = CommitWindow(days=[2, 4], start="20:00", end="23:00")
#: 跨午夜窗口：22:00–02:00
CROSS_MIDNIGHT = CommitWindow(days=[2], start="22:00", end="02:00")


def local(moment: datetime, hour: int, minute: int) -> datetime:
    """构造环境时区的本地时刻。"""
    return moment.replace(hour=hour, minute=minute)


# --- 星期与时间段的匹配 ------------------------------------------------------


def test_a_window_matches_only_the_weekdays_it_lists() -> None:
    """DX-ENV-002：“周二、周四 20:00–23:00”只在周二与周四成立。"""
    assert matches(TWO_EVENINGS, local(TUESDAY, 21, 0)) is True
    assert matches(TWO_EVENINGS, local(WEDNESDAY, 21, 0)) is False
    assert matches(TWO_EVENINGS, local(MONDAY, 21, 0)) is False


def test_the_listed_days_all_match_at_the_same_clock_time() -> None:
    assert matches(TWO_EVENINGS, local(TUESDAY, 22, 30)) is True
    assert matches(TWO_EVENINGS, local(datetime(2026, 1, 8, tzinfo=UTC), 22, 30)) is True


def test_the_window_starts_at_its_start_time_inclusively() -> None:
    assert matches(TWO_EVENINGS, local(TUESDAY, 20, 0)) is True
    assert matches(TWO_EVENINGS, local(TUESDAY, 19, 59)) is False


def test_the_window_ends_at_its_end_time_exclusively() -> None:
    assert matches(TWO_EVENINGS, local(TUESDAY, 22, 59)) is True
    assert matches(TWO_EVENINGS, local(TUESDAY, 23, 0)) is False


def test_a_midnight_crossing_window_covers_the_starting_evening() -> None:
    """DX-ENV-002：22:00–02:00 在起始日的 23:00 仍然开着。"""
    assert matches(CROSS_MIDNIGHT, local(TUESDAY, 23, 0)) is True


def test_a_midnight_crossing_window_carries_over_into_the_next_day() -> None:
    """DX-ENV-002：周二列出的 22:00–02:00 窗口跨过午夜，凌晨的周三仍在窗口内。"""
    assert matches(CROSS_MIDNIGHT, local(WEDNESDAY, 1, 0)) is True
    assert matches(CROSS_MIDNIGHT, local(WEDNESDAY, 1, 59)) is True


def test_a_midnight_crossing_window_opens_at_2300_on_every_listed_day() -> None:
    """DX-ENV-002：22:00–02:00 同时列出周二与周三时，周三 23:00 的归属要看得清。

    周三 23:00 只有在**周三自己被列入**时才在窗口内：列出周二的那一段窗口从周二 22:00
    开到周三 02:00，到周三 23:00 早已关闭。
    """
    listed_wednesday_too = CommitWindow(days=[2, 3], start="22:00", end="02:00")
    assert matches(listed_wednesday_too, local(WEDNESDAY, 23, 0)) is True
    assert matches(CROSS_MIDNIGHT, local(WEDNESDAY, 23, 0)) is False


def test_a_midnight_crossing_window_closes_at_its_end_time() -> None:
    assert matches(CROSS_MIDNIGHT, local(WEDNESDAY, 2, 0)) is False
    assert matches(CROSS_MIDNIGHT, local(WEDNESDAY, 3, 0)) is False


def test_a_sunday_window_carries_over_into_the_small_hours_of_monday() -> None:
    """DX-ENV-002：周日 22:00–02:00 覆盖到周一凌晨，跨周界的星期序号要回绕。"""
    window = CommitWindow(days=[7], start="22:00", end="02:00")
    assert matches(window, local(SUNDAY, 23, 0)) is True
    assert matches(window, local(datetime(2026, 1, 12, tzinfo=UTC), 1, 0)) is True


# --- 时区：判定用环境时区，不是 UTC ------------------------------------------


def test_the_window_is_evaluated_in_the_environment_timezone_not_utc() -> None:
    """DX-ENV-002：同一个 UTC 时刻，在亚洲实例的本地时间里落在窗口内。"""
    window = CommitWindow(days=[1], start="08:00", end="09:00")
    instant = datetime(2026, 1, 5, 0, 30, tzinfo=UTC)  # 上海时间 08:30 周一
    assert within_commit_window([window], timezone_name="Asia/Shanghai", now=instant)[0] is True
    assert within_commit_window([window], timezone_name="UTC", now=instant)[0] is False


def test_an_instant_outside_the_local_window_is_refused_even_in_the_right_timezone() -> None:
    window = CommitWindow(days=[1], start="08:00", end="09:00")
    instant = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)  # 上海 20:00
    inside, reason = within_commit_window([window], timezone_name="Asia/Shanghai", now=instant)
    assert inside is False
    assert reason is not None and "Asia/Shanghai" in reason


def test_local_now_converts_a_utc_instant_into_the_environment_clock() -> None:
    moment = local_now("Asia/Shanghai", datetime(2026, 1, 5, 0, 30, tzinfo=UTC))
    assert (moment.hour, moment.minute) == (8, 30)
    assert moment.tzinfo is not None


def test_naive_instants_are_read_as_utc_before_converting() -> None:
    moment = local_now("UTC", datetime(2026, 1, 5, 3, 30))
    assert moment.hour == 3


def test_an_unknown_timezone_is_an_error_not_a_silent_utc_fallback() -> None:
    """DX-ENV-002：时区拼错时不能悄悄退回 UTC，否则窗口会提前或延后数小时。"""
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        zone_of("Mars/Phobos")
    assert excinfo.value.details["timezone"] == "Mars/Phobos"


# --- 空清单的判定方向 --------------------------------------------------------


def test_no_windows_configured_means_not_allowed_when_a_window_is_required() -> None:
    """DX-ENV-002：PRODUCTION 类环境没有时间窗就不是“随时可以”。"""
    inside, reason = within_commit_window([], timezone_name="UTC", now=MONDAY, required=True)
    assert inside is False
    assert reason is not None


def test_no_windows_configured_means_allowed_when_none_is_required() -> None:
    """非 PRODUCTION 环境不受时间窗约束。"""
    inside, reason = within_commit_window([], timezone_name="UTC", now=MONDAY, required=False)
    assert inside is True
    assert reason is None


def test_any_of_several_windows_lets_the_commit_through() -> None:
    """DX-ENV-002：落在任一时段内即为允许。"""
    windows = [
        CommitWindow(days=[2], start="01:00", end="02:00"),
        CommitWindow(days=[4], start="20:00", end="23:00"),
    ]
    assert within_commit_window(windows, timezone_name="UTC", now=local(datetime(2026, 1, 8, tzinfo=UTC), 21, 0))[0] is True
    assert within_commit_window(windows, timezone_name="UTC", now=local(TUESDAY, 21, 0))[0] is False


# --- 下一次开启与文档形态 ----------------------------------------------------


def test_next_window_opening_returns_a_future_utc_instant() -> None:
    """给操作者一个可等的时刻（DX-ENV-002 的拒绝理由要能落地）。"""
    now = datetime(2026, 1, 7, 10, 0, tzinfo=UTC)  # 上海 18:00 周三
    opening = next_window_opening([TWO_EVENINGS], "Asia/Shanghai", now)
    assert opening is not None
    assert opening.tzinfo is not None
    assert opening.utcoffset() == timedelta(0)
    assert opening > now.astimezone(UTC)
    # 周四 20:00 上海 = 周四 12:00 UTC
    assert opening == datetime(2026, 1, 8, 12, 0, tzinfo=UTC)


def test_next_window_opening_of_an_empty_list_is_not_a_promise() -> None:
    assert next_window_opening([], "UTC", MONDAY) is None


def test_windows_as_documents_carries_the_timezone_for_the_approver() -> None:
    """S1 审批界面直接展示这份形态（Gate 指纹要稳定）。"""
    documents = windows_as_documents([TWO_EVENINGS, CROSS_MIDNIGHT], "Asia/Shanghai")
    assert documents[0] == {
        "days": [2, 4],
        "start": "20:00",
        "end": "23:00",
        "timezone": "Asia/Shanghai",
        "wrapsMidnight": False,
        "note": None,
    }
    assert documents[1]["wrapsMidnight"] is True


def test_the_helper_window_is_outside_the_current_moment_whenever_it_runs() -> None:
    """"窗口外"的测试构造必须**任何时候**都成立。

    写死星期的窗口每周有若干小时会落进"现在"，于是断言失败的原因不是缺陷而是日历——
    一个按时钟变化的套件会让人在凌晨的重跑里误判自己的改动。
    """
    spec = window_not_now()
    window = CommitWindow(days=spec["days"], start=spec["start"], end=spec["end"])
    assert not matches(window, local_now("UTC"))
    assert not within_commit_window([window], timezone_name="UTC")[0]
