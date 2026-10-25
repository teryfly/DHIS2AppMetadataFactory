"""PostgreSQL harness for S3 tests.

Wraps the same idea as S2's :class:`factory_blueprint.testing.harness.BlueprintHarness`
but owns three schemas rather than two: S3's integration tests submit real patches
through real S2, so ``bp`` must exist and be migrated from S2's own migrations.
Dropping ``core`` too is what clears S0's append-only audit rows, which cannot be
DELETEd or TRUNCATEd.

**The database must be exclusive to this harness.**  :meth:`IntelligenceHarness.reset`
drops ``core``, ``bp`` and ``ai`` on every set-up, so pointing two sessions — or S2's
own suite, whose default is ``factory_test`` — at the same URL destroys one another's
data.  S3 therefore defaults to ``factory_test_s3``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from factory_blueprint.api import MIGRATION_CONFIG as BP_MIGRATION_CONFIG
from factory_blueprint.api import initialize as initialize_blueprint
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

from factory_intelligence.bootstrap import MIGRATION_CONFIG, initialize

TEST_URL_ENV = "FACTORY_TEST_DATABASE_URL"
DEFAULT_TEST_URL = "postgresql+psycopg://factory:factory@localhost:55432/factory_test_s3"


def resolve_test_database_url() -> str:
    return os.environ.get(TEST_URL_ENV) or DEFAULT_TEST_URL


@dataclass
class IntelligenceHarness:
    """A deterministic foundation + ``bp`` + ``ai`` for one test."""

    url: str
    object_store: InMemoryObjectStore = field(default_factory=InMemoryObjectStore)
    clock: FixedClock = field(default_factory=lambda: FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))

    def reset(self) -> None:
        """Drop and rebuild ``core``, ``bp`` and ``ai`` through the real migrations."""
        engine = create_engine(self.url)
        with engine.begin() as conn:
            for schema in ("core", "bp", "ai"):
                conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()
        run_migrations([CORE_MIGRATION_CONFIG, BP_MIGRATION_CONFIG, MIGRATION_CONFIG], url=self.url)
        initialize_foundation()
        initialize_blueprint()
        initialize()

    def setup(self) -> None:
        configure_engine(self.url)
        configure_object_store(self.object_store)
        self.reset()


@contextmanager
def harness(url: str | None = None) -> Iterator[IntelligenceHarness]:
    h = IntelligenceHarness(url=resolve_test_database_url() if url is None else url)
    h.setup()
    try:
        yield h
    finally:
        configure_object_store(InMemoryObjectStore())


__all__ = [
    "IntelligenceHarness",
    "harness",
    "resolve_test_database_url",
    "TEST_URL_ENV",
    "DEFAULT_TEST_URL",
]
