"""Scaffold: the ``ai`` schema exists, registers, and stays inside its schema.

AI-DATA-01 (schema changes through migrations), FD-ASM-01 (registration), 00 doc 4.2
(schema ownership) and 00 doc 6.10 (start-up order).
"""

from __future__ import annotations

import pytest
from factory_foundation.api import (
    AiBase,
    has_active_uow,
    is_registered_event_type,
    list_artifact_types,
    new_id,
    new_session,
    register_error_codes,
    register_id_prefixes,
    unit_of_work,
)
from factory_foundation.db.fk_check import find_cross_schema_fk
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from factory_intelligence.bootstrap import MIGRATION_CONFIG, initialize
from factory_intelligence.errors import AiError
from factory_intelligence.models import DELETABLE_TABLES
from factory_intelligence.registries import (
    AI_AUDIT_EVENT_TYPES,
    AI_ERROR_CODES,
    AI_ID_PREFIXES,
)
from factory_intelligence.testing.harness import IntelligenceHarness

#: The tables SRS 5 lists as S3-owned, plus the two supporting rows the SRS implies
#: (a regression reference for AI-REG-003, per-case eval results for AI-EVAL-001).
EXPECTED_TABLES = {
    "input_parse",
    "agent_run",
    "agent_run_context",
    "chat_session",
    "model_call",
    "policy_decision",
    "org_policy",
    "policy_relaxation",
    "provider",
    "model",
    "prompt_version",
    "prompt_activation",
    "regression_ref",
    "eval_run",
    "eval_case_result",
    "confirmation_record",
    "desensitization_record",
    "budget",
}


def ai_tables() -> set[str]:
    """What the migrated ``ai`` schema actually holds, read from the database itself."""
    bind = new_session().bind
    assert bind is not None, "the harness session must be bound to the test database"
    return set(inspect(bind).get_table_names(schema="ai"))


@pytest.fixture(scope="module", autouse=True)
def _catalogs_registered() -> None:
    """Stand up the two process-level catalogs the guards below read.

    ``bootstrap._register_all`` is what does this in a running process, and these two calls are
    the part of it that needs no session — only the artifact-type and audit-event writes touch the
    database. Without the fixture the guards passed only because some other module in the same run
    had booted a harness: on a machine with no ``factory_test_s3`` cluster, ``new_id("aipr")``
    answered "unregistered id prefix" and the error-code loop found an empty catalog, so two green
    tests reported S3 as broken when the process simply had not started.

    Registering here is not a tautology. ``register`` refuses a code or prefix another subsystem
    already owns (FD-COM-001a) and rejects a prefix that is not ASCII alphanumeric (FD-ASM-01), so
    an ownership collision or a malformed prefix still fails this file — which is the drift these
    two tests exist to catch.
    """
    register_error_codes(AI_ERROR_CODES)
    register_id_prefixes(AI_ID_PREFIXES)


def test_the_ai_schema_was_built_by_the_real_migration(harness: IntelligenceHarness) -> None:
    """AI-DATA-01."""
    _ = harness
    tables = ai_tables()
    assert tables >= EXPECTED_TABLES, f"missing: {sorted(EXPECTED_TABLES - tables)}"


def test_the_version_table_lives_inside_the_ai_schema(harness: IntelligenceHarness) -> None:
    """00 doc 6.10: each schema migrates independently, so the version rows must not
    collide with ``core``'s or ``bp``'s."""
    _ = harness
    assert MIGRATION_CONFIG.version_table in ai_tables()


def test_initialize_is_idempotent_and_leaves_no_transaction_open(
    harness: IntelligenceHarness,
) -> None:
    """FD-ASM-01: start-up may be re-run by a second process in the same interpreter."""
    _ = harness
    initialize()
    initialize()
    assert not has_active_uow()
    with unit_of_work() as session:
        types = dict(
            (name, owners) for name, owners, _size in list_artifact_types(session=session)
        )
    # registered once, owner not duplicated by the second call
    assert types["NRR"] == ["S3"]
    assert types["AGENT_OUTPUT"] == ["S3"]


def test_every_registered_audit_event_type_is_accepted_by_the_catalog(
    harness: IntelligenceHarness,
) -> None:
    """Appendix A lists sixteen events; an unregistered one fails at write time inside
    the caller's transaction, which is the worst possible moment to discover it."""
    _ = harness
    assert len(AI_AUDIT_EVENT_TYPES) == 16
    with unit_of_work() as session:
        for event_type in AI_AUDIT_EVENT_TYPES:
            assert is_registered_event_type(event_type, session=session), event_type


def test_no_ai_table_reaches_outside_the_ai_schema(harness: IntelligenceHarness) -> None:
    """00 doc 4.2, D-08: no cross-schema foreign keys."""
    _ = harness
    offenders = find_cross_schema_fk([AiBase.metadata])
    assert not offenders, [(str(item), item.target_schema) for item in offenders]


def test_cleanup_covers_every_table_with_a_decision() -> None:
    """AI-DEL-001: the retain-or-delete decision is explicit for every table, so adding
    a table without stating it here fails this test rather than leaking project data."""
    all_tables = {table.name for table in AiBase.metadata.sorted_tables}
    # model_call is retained as aggregate values without content; policy_relaxation is
    # scoped to an organisation or environment; org_policy, budget and the registries
    # are not project data at all.
    retained = {
        "model_call",
        "policy_relaxation",
        "org_policy",
        "budget",
        "provider",
        "model",
        "prompt_version",
        "prompt_activation",
        "regression_ref",
        # an evaluation is of a prompt and a model, not of a project; deleting it would
        # erase the evidence AI-REG-003 gates activation on
        "eval_run",
        "eval_case_result",
    }
    deleted = set(DELETABLE_TABLES)
    assert all_tables == deleted | retained, sorted(
        all_tables ^ (deleted | retained)
    )


def test_every_error_code_s3_can_raise_is_registered() -> None:
    """00 doc 6.5: an unregistered code reports as not-retryable by default, which would
    silently turn a transient failure into a permanent one."""
    import inspect as py_inspect

    from factory_foundation.api import error_catalog

    from factory_intelligence import errors as errors_module

    for code, (owner, _retryable) in AI_ERROR_CODES.items():
        assert owner == "S3", code
        meta = error_catalog.get(code)
        assert meta is not None and meta.owner_subsystem == "S3", code

    declared = {member.code for _n, member in py_inspect.getmembers(
        errors_module, py_inspect.isclass
    ) if issubclass(member, AiError)}
    for code in declared:
        assert code in AI_ERROR_CODES or error_catalog.is_registered(code), code


def test_id_prefixes_are_owned_by_s3_and_mint() -> None:
    """FD-ASM-01: an unregistered prefix raises, so minting is proof of registration."""
    for prefix in AI_ID_PREFIXES:
        assert new_id(prefix).startswith(f"{prefix}-")


def test_session_argument_enlistsWith_the_caller_transaction(
    harness: IntelligenceHarness,
) -> None:
    """S1 calls ``initialize()`` and cleanup functions with its own session; S3 must not
    commit it (00 doc 6.2)."""
    _ = harness
    session: Session = new_session()
    try:
        with unit_of_work(session):
            initialize(session=session)
            assert has_active_uow()
            org_policy = AiBase.metadata.tables["ai.org_policy"]
            rows = session.execute(select(org_policy)).all()
            assert rows == []
    finally:
        session.close()
