"""Shared pytest fixtures for S2.

The schema is migrated once per session; each test then truncates ``bp`` and
re-registers the catalogs, which keeps integration tests fast without weakening
isolation.  ``core`` keeps accumulating artifacts and audit records on purpose —
S0's append-only guards make truncation undesirable, and no S2 assertion depends
on an empty core.

S2's production default for the read-only DHIS2 collaborator is the real S4 adapter,
so every test installs the in-memory stub instead: no test may reach a live instance,
and a test that wants to see what S2 asked for reads the stub's recorded calls.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from factory_foundation.api import configure_object_store, unit_of_work
from factory_foundation.testing import InMemoryObjectStore
from sqlalchemy import text

from factory_blueprint import deps
from factory_blueprint.bootstrap import initialize
from factory_blueprint.s4.stub import StubDhis2
from factory_blueprint.testing.harness import BlueprintHarness, resolve_test_database_url

BP_DATA_TABLES = [
    "blueprint_version",
    "blueprint_draft",
    "blueprint_patch",
    "registry_entry",
    "environment_binding",
    "uid_allocation",
    "uid_pool",
    "decision",
    "business_key_index",
    "dependency_edge",
    "build_record",
    "validation_run",
    "import_batch",
    "prepared_content",
]


@pytest.fixture(scope="session")
def bp_session() -> Iterator[BlueprintHarness]:
    h = BlueprintHarness(url=resolve_test_database_url())
    h.setup()
    yield h


@pytest.fixture(autouse=True)
def _stubbed_dhis2() -> Iterator[None]:
    """Keep every test off the real S4; a test may replace this with its own double."""
    deps.set_dhis2(StubDhis2())
    try:
        yield
    finally:
        deps.reset_providers()


@pytest.fixture
def harness(bp_session: BlueprintHarness) -> Iterator[BlueprintHarness]:
    """Fresh ``bp`` state, empty object store and registered catalogs per test."""
    store = InMemoryObjectStore()
    configure_object_store(store)
    bp_session.object_store = store
    with unit_of_work() as session:
        # core accumulates artifacts and audit rows on purpose (append-only), but
        # idempotency keys must be per test or a retried key collides
        session.execute(text("TRUNCATE core.idempotency_record RESTART IDENTITY"))
        session.execute(
            text(
                "TRUNCATE "
                + ", ".join(f"bp.{t}" for t in sorted(BP_DATA_TABLES))
                + " RESTART IDENTITY"
            )
        )
        initialize(session=session)
    yield bp_session
