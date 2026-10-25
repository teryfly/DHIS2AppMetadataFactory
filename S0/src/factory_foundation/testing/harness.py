"""PostgreSQL-backed foundation harness (FD-TST-001).

Provisions a clean ``core`` schema (dropping + re-running the real migration) so
append-only audit rows — which cannot be DELETEd or TRUNCATEd — are wiped via
schema drop instead.  A memory object store, fixed clock and memory secret
backend are installed so a test gets a fully deterministic foundation.

The database is resolved from ``FACTORY_TEST_DATABASE_URL`` when set, otherwise a
``testcontainers`` PostgreSQL container is started if that package is installed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import create_engine, text

from factory_foundation.bootstrap import initialize_foundation
from factory_foundation.common.clock import FixedClock
from factory_foundation.db.engine import configure_engine, reset_engine
from factory_foundation.db.migrations import (
    CORE_MIGRATION_CONFIG,
    SCHEMA_ORDER,
    run_migrations,
)
from factory_foundation.objectstore.memory import InMemoryObjectStore
from factory_foundation.objectstore.provider import (
    configure_object_store,
    reset_object_store,
)
from factory_foundation.secrets import install_development_backends, resolver
from factory_foundation.secrets.memory import MemorySecretBackend


def _default_clock() -> FixedClock:
    return FixedClock(datetime(2026, 1, 1, tzinfo=UTC))


def resolve_test_database_url() -> str:
    url = os.environ.get("FACTORY_TEST_DATABASE_URL")
    if url:
        return url
    try:  # optional dependency
        from testcontainers.postgres import PostgresContainer
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "No PostgreSQL for tests: set FACTORY_TEST_DATABASE_URL or install "
            "testcontainers. Example: "
            "postgresql+psycopg://user:pass@localhost:5432/factory_test"
        ) from exc
    container = PostgresContainer("postgres:16-alpine")
    container.start()
    return str(container.get_connection_url(driver="psycopg"))


@dataclass
class FoundationHarness:
    url: str
    object_store: InMemoryObjectStore = field(default_factory=InMemoryObjectStore)
    clock: FixedClock = field(default_factory=_default_clock)
    secret_backend: MemorySecretBackend = field(default_factory=MemorySecretBackend)

    def reset(self) -> None:
        """Drop and rebuild the clean core schema + registries.

        Every subsystem schema is stood up (FD-TST-001) so a subsystem can point its own
        migration config at ``run_migrations`` without S0 knowing it; only ``core`` is
        dropped, since the rest belong to whoever created them.
        """
        eng = create_engine(self.url)
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS core CASCADE"))
            for schema in SCHEMA_ORDER:
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        eng.dispose()
        run_migrations([CORE_MIGRATION_CONFIG], url=self.url)
        initialize_foundation()

    def setup(self) -> None:
        configure_engine(self.url)
        configure_object_store(self.object_store)
        # a fresh memory secret backend registered under both schemes
        if "memory" not in resolver._backends:  # noqa: SLF001
            install_development_backends(resolver)
        resolver.register(self.secret_backend)
        self.reset()

    def teardown(self) -> None:
        reset_engine()
        reset_object_store()


def harness(url: str | None = None) -> Iterator[FoundationHarness]:
    h = FoundationHarness(url=url or resolve_test_database_url())
    h.setup()
    try:
        yield h
    finally:
        h.teardown()
