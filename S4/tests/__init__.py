"""S4 测试套件。

共用工具放在这里（而不是各目录的 conftest），因为单元与集成两侧都要用到同一批"环境形状"
的构造——一个 helper 只住在一边时，另一边会把它抄一遍，抄的那份就开始有自己的意见。
"""

from __future__ import annotations

from typing import Any

from factory_dhis2.environment.windows import local_now


def window_not_now(timezone_name: str = "UTC") -> dict[str, Any]:
    """一个**现在必然不成立**的提交时间窗。

    写死"周二 20:00–23:00"的"窗口外"断言每周都有三小时会失败，而那一小时里测试并没有发现
    缺陷，只是撞上了日历。窗口判定读的是真实时钟（``protection_of`` 不接受注入 clock），
    所以"窗口外"必须由当前时刻反推：选一个不是今天的星期号，断言在任何时刻都表达同一件事。
    """
    moment = local_now(timezone_name)
    day = 1 if moment.isoweekday() != 1 else 2
    return {"days": [day], "start": "03:00", "end": "04:00", "timezone": timezone_name}


__all__ = ["window_not_now"]
