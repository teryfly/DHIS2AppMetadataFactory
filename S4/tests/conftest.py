"""S4 测试共用夹具。

迁移每个会话跑一次，需要数据库的测试再显式请求 ``harness``：它 truncate ``dx``、清空
``core.idempotency_record`` 并重新登记目录。``core`` 的产物与审计行**故意继续累积**——
S0 的 append-only 守卫使 truncate 它们既不合适也没必要，而没有任何 S4 断言依赖一个空的
``core``。幂等记录则必须逐测试清掉：一个跨测试复用的键会重放上一个测试的结果。

单元测试不请求 ``harness``，因此不需要数据库（同 S2/S3）。进程级全局状态（依赖注入、
设置）在每个测试前后重置：S4 的 transport 是唯一槽位，泄漏的假 transport 会表现为
“通过但没有证明任何东西”的测试。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from factory_foundation.api import DxBase, configure_object_store, unit_of_work
from factory_foundation.testing import InMemoryObjectStore
from sqlalchemy import text

from factory_dhis2 import deps
from factory_dhis2.bootstrap import initialize
from factory_dhis2.config import reset as reset_settings
from factory_dhis2.testing.harness import Dhis2Harness, resolve_test_database_url


def dx_data_tables() -> list[str]:
    """每个 ``dx`` 表名，取自 ORM 元数据。

    重置按这张清单执行，所以新增模型不可能悄悄跳过它——否则上一个测试留下的一行会替
    本测试决定结论。
    """
    return sorted(table.name for table in DxBase.metadata.sorted_tables)


@pytest.fixture(scope="session")
def dx_session() -> Iterator[Dhis2Harness]:
    h = Dhis2Harness(url=resolve_test_database_url())
    h.setup()
    yield h


@pytest.fixture(autouse=True)
def _reset_process_state() -> Iterator[None]:
    """单元与集成测试都要：不泄漏 transport、时钟、sleeper 与设置。"""
    deps.reset_providers()
    reset_settings()
    yield
    deps.reset_providers()
    reset_settings()


@pytest.fixture
def harness(dx_session: Dhis2Harness) -> Iterator[Dhis2Harness]:
    """干净的 ``dx``、空对象存储与已登记的目录。"""
    store = InMemoryObjectStore()
    configure_object_store(store)
    dx_session.object_store = store
    tables = dx_data_tables()
    assert tables, "dx 元数据里没有表：模型未导入"
    with unit_of_work() as session:
        session.execute(text("TRUNCATE core.idempotency_record RESTART IDENTITY"))
        session.execute(
            text(
                "TRUNCATE "
                + ", ".join(f"dx.{name}" for name in tables)
                + " RESTART IDENTITY CASCADE"
            )
        )
        initialize(session=session)
    deps.set_clock(dx_session.clock)
    yield dx_session


@pytest.fixture
def object_store(harness: Dhis2Harness) -> InMemoryObjectStore:
    """本测试的产物内容落在这里。"""
    return harness.object_store
