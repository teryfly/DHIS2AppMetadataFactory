"""Shared pytest fixtures for S3.

The schemas are migrated once per session; each test then truncates ``ai`` and ``bp`` and
re-registers both catalogs, which keeps the suite fast without weakening isolation.
``bp`` is truncated because S3's runtime tests drive the **real** S2 (AI-AC-04 asks that S2
accept a confirmation record, which a stubbed port cannot answer) — and a seeded project left
behind by one test would decide the next one's verdict.
``core`` keeps accumulating artifacts and audit rows on purpose — S0's append-only
guards make truncating them undesirable, and no S3 assertion depends on an empty
``core`` — but ``core.idempotency_record`` *is* cleared, because a key reused between
tests would otherwise replay the previous test's result.

After re-registering the catalogs the harness seeds one ``ai.prompt_activation`` row per agent
type (see :func:`seed_prompt_activations`): AI-REG-002 makes a run *refuse* when its agent has
no activated prompt, so without this baseline every runtime test would be refused for a
deployment gap rather than exercising the code under test.

Process-global state (dependency injection, settings) is reset around every test: S3's
providers are single slots, and a leaked mock provider is the kind of failure that
would otherwise show up as a passing test that proved nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from factory_blueprint import api as blueprint_api
from factory_foundation.api import AiBase, configure_object_store, unit_of_work
from factory_foundation.testing import InMemoryObjectStore
from sqlalchemy import select, text
from sqlalchemy.engine import ScalarResult
from sqlalchemy.orm import Session

from factory_intelligence.bootstrap import initialize
from factory_intelligence.config import reset as reset_settings
from factory_intelligence.deps import reset_providers
from factory_intelligence.enums import AgentType
from factory_intelligence.models import AgentRunRow  # noqa: F401  (registers ai tables)
from factory_intelligence.models.registry import PromptActivationRow, PromptVersionRow
from factory_intelligence.testing.harness import IntelligenceHarness, resolve_test_database_url


def data_tables(session: Session, schema: str) -> list[str]:
    """Every data table of one schema, read from the catalog.

    Queried rather than listed: a table added by a migration in either subsystem is reset by
    this suite from the moment it exists, with nobody remembering to edit a constant.
    """
    found: ScalarResult[str] = session.execute(
        text(
            "select table_name from information_schema.tables "
            "where table_schema = :schema and table_type = 'BASE TABLE'"
        ),
        {"schema": schema},
    ).scalars()
    return sorted(name for name in found if "alembic" not in name)


def ai_data_tables() -> list[str]:
    """Every ``ai`` table, read from the ORM metadata so a new model cannot quietly skip
    the per-test reset (a stale row from another test would then decide a verdict)."""
    return sorted(table.name for table in AiBase.metadata.sorted_tables)


def seed_prompt_activations(session: Session) -> None:
    """Give every agent type an active prompt so a run is not refused for a deployment gap.

    Since AI-REG-002 is enforced (an un-activated agent now *refuses* rather than degrading to
    an empty placeholder), the runtime tests would otherwise all be refused: the harness
    registers the shipped prompt *versions* through ``initialize()`` but writes no *activation*,
    and activation is the AI-REG-003-gated fact a real deployment must establish.  These rows
    bypass that gate deliberately — they are the fixture's "somebody finished the deployment",
    not evidence a switch is justified.  The activation-gate tests that need an empty baseline
    delete them first (``clear_prompt_activations``), and the tests that exercise a *specific*
    activated version still call ``prompt.activate`` through the gate.
    """
    for agent in AgentType:
        version = session.execute(
            select(PromptVersionRow)
            .where(PromptVersionRow.agent_type == agent.value)
            .order_by(PromptVersionRow.version.desc())
        ).scalars().first()
        if version is None:
            continue
        session.add(
            PromptActivationRow(
                agent_type=agent.value,
                prompt_version_id=version.id,
                model_list=[],
                activated_by="test-fixture",
                eval_run_id="fixture-eval",
                regression_passed_ref="fixture-regression",
            )
        )
    session.flush()


@pytest.fixture(scope="session")
def ai_session() -> Iterator[IntelligenceHarness]:
    h = IntelligenceHarness(url=resolve_test_database_url())
    h.setup()
    yield h


@pytest.fixture
def harness(ai_session: IntelligenceHarness) -> Iterator[IntelligenceHarness]:
    """Fresh ``ai`` and ``bp`` state, an empty object store and registered catalogs."""
    store = InMemoryObjectStore()
    configure_object_store(store)
    ai_session.object_store = store
    reset_providers()
    reset_settings()
    with unit_of_work() as session:
        session.execute(text("TRUNCATE core.idempotency_record RESTART IDENTITY"))
        for schema in ("ai", "bp"):
            tables = data_tables(session, schema)
            assert tables, f"no data tables found in schema {schema}"
            session.execute(
                text(
                    "TRUNCATE "
                    + ", ".join(f"{schema}.{table}" for table in tables)
                    + " RESTART IDENTITY CASCADE"
                )
            )
        assert ai_data_tables()
        initialize(session=session)
        blueprint_api.initialize(session=session)
        # AI-REG-002 now refuses an un-activated run, so every agent starts deploy-complete
        seed_prompt_activations(session)
    yield ai_session
    reset_providers()
    reset_settings()


@pytest.fixture
def object_store(harness: IntelligenceHarness) -> InMemoryObjectStore:
    """The in-memory content store the artifacts of this test land in."""
    return harness.object_store
