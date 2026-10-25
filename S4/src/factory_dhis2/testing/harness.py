"""S4 测试用的 PostgreSQL harness。

与 S2/S3 的 harness 同一思路，但只拥有两个 schema：``core``（S0）与 ``dx``（S4）。S4 的
集成测试不需要 ``bp``/``ai``/``cp``：它的输入是**产物 ID**，测试用 S0 直接造出所需产物即可。

**数据库必须是本 harness 独占的。** ``reset()`` 每次都会 DROP 掉 ``core`` 与 ``dx``，把两个
会话指向同一个 URL 会互相毁数据，所以 S4 的默认库是 ``factory_test_s4``（S2 用
``factory_test``，S3 用 ``factory_test_s3``）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from factory_foundation.api import (
    CORE_MIGRATION_CONFIG,
    FixedClock,
    configure_engine,
    configure_object_store,
    initialize_foundation,
    run_migrations,
)
from factory_foundation.testing import InMemoryObjectStore
from sqlalchemy import create_engine, text

from factory_dhis2.bootstrap import MIGRATION_CONFIG, initialize

TEST_URL_ENV = "FACTORY_TEST_DATABASE_URL"
DEFAULT_TEST_URL = "postgresql+psycopg://factory:factory@localhost:55432/factory_test_s4"


def resolve_test_database_url() -> str:
    return os.environ.get(TEST_URL_ENV) or DEFAULT_TEST_URL


@dataclass
class Dhis2Harness:
    """一个确定性的 foundation + ``dx``，供单个测试使用。"""

    url: str
    object_store: InMemoryObjectStore = field(default_factory=InMemoryObjectStore)
    clock: FixedClock = field(
        default_factory=lambda: FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    )

    def reset(self) -> None:
        """通过真实迁移重建 ``core`` 与 ``dx``。"""
        engine = create_engine(self.url)
        with engine.begin() as conn:
            for schema in ("core", "dx"):
                conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()
        run_migrations([CORE_MIGRATION_CONFIG, MIGRATION_CONFIG], url=self.url)
        initialize_foundation()
        initialize()

    def setup(self) -> None:
        configure_engine(self.url)
        configure_object_store(self.object_store)
        self.reset()


@contextmanager
def harness(url: str | None = None) -> Iterator[Dhis2Harness]:
    h = Dhis2Harness(url=resolve_test_database_url() if url is None else url)
    h.setup()
    try:
        yield h
    finally:
        configure_object_store(InMemoryObjectStore())


__all__ = [
    "DEFAULT_TEST_URL",
    "Dhis2Harness",
    "TEST_URL_ENV",
    "harness",
    "resolve_test_database_url",
]
