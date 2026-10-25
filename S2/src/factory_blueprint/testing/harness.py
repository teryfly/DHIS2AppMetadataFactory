"""PostgreSQL harness for S2 tests.

Wraps the foundation harness idea from S0 but owns the ``bp`` schema lifecycle:
both schemas are dropped and rebuilt from the real migrations, then S0's
foundation and S2's ``initialize()`` register their catalogs.  A memory object
store and a fixed clock make content writes deterministic.
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

from factory_blueprint.bootstrap import MIGRATION_CONFIG, initialize

TEST_URL_ENV = "FACTORY_TEST_DATABASE_URL"
#: A dedicated database: the harness drops ``core`` and ``bp`` on every reset, so
#: it must never point at a shared deployment database.
DEFAULT_TEST_URL = "postgresql+psycopg://factory:factory@localhost:55432/factory_test"


def resolve_test_database_url() -> str:
    url = os.environ.get(TEST_URL_ENV)
    if url:
        return url
    return DEFAULT_TEST_URL


@dataclass
class BlueprintHarness:
    url: str
    object_store: InMemoryObjectStore = field(default_factory=InMemoryObjectStore)
    clock: FixedClock = field(
        default_factory=lambda: FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    )

    def reset(self) -> None:
        """Drop ``core`` + ``bp`` and rebuild them through the real migrations."""
        engine = create_engine(self.url)
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS core CASCADE"))
            conn.execute(text("DROP SCHEMA IF EXISTS bp CASCADE"))
        engine.dispose()
        run_migrations([CORE_MIGRATION_CONFIG, MIGRATION_CONFIG], url=self.url)
        initialize_foundation()
        initialize()

    def setup(self) -> None:
        configure_engine(self.url)
        configure_object_store(self.object_store)
        self.reset()


@contextmanager
def harness(url: str | None = None) -> Iterator[BlueprintHarness]:
    h = BlueprintHarness(url=url or resolve_test_database_url())
    h.setup()
    try:
        yield h
    finally:
        # re-configure on next use; the process-wide providers are single slots
        configure_object_store(InMemoryObjectStore())


__all__ = [
    "BlueprintHarness",
    "harness",
    "resolve_test_database_url",
    "TEST_URL_ENV",
    "DEFAULT_TEST_URL",
]
