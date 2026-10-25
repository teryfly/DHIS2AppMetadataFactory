"""API surface + gap coverage (new exports, PHI-excluded pack, error edges)."""

from __future__ import annotations

import pytest

import factory_foundation.api as api
from factory_foundation.api import (
    ArtifactCreate,
    DataClassification,
    Subsystem,
    bind_operation_context,
    cleanup_expired_idempotency,
    error_catalog,
    get_artifact,
    pack,
    raise_for_code,
    register_artifact,
    stage_content,
    trace_span,
    unit_of_work,
    unpack,
)
from factory_foundation.common.errors import FactoryError
from factory_foundation.testing import FoundationHarness


def test_all_names_importable():
    for name in api.__all__:
        assert hasattr(api, name), name


def test_verify_chain_alias_matches_service():
    assert api.verify_chain is api.verify_audit_chain


def test_subsystem_schema_map_all():
    assert Subsystem.S0.schema.value == "core"
    assert Subsystem.S2.schema.value == "bp"
    assert Subsystem.S3.schema.value == "ai"
    assert Subsystem.S4.schema.value == "dx"


def test_unknown_error_code_not_retryable():
    assert error_catalog.is_retryable("NO_SUCH_CODE") is False
    # a base (non-mapped) but registered code still raises FactoryError with the code
    with pytest.raises(FactoryError) as ei:
        raise_for_code("LLM_TIMEOUT", "timed out")
    assert ei.value.error_code == "LLM_TIMEOUT"
    assert ei.value.retryable is True


def test_trace_span_with_active_context():
    with (
        bind_operation_context(operator_name="trace-user", project_id="tp"),
        trace_span("unit-op", kind="test") as span,
    ):
        assert span is not None  # no-op or otel span, either way context attaches


def test_pack_excludes_phi_content(harness_db):
    # PHI artifact packed without content -> imported as purged, metadata+hash kept
    sh = stage_content(
        b"patient data", project_id="p", artifact_type="MANIFEST", media_type="application/json"
    )
    with unit_of_work() as s:
        info = register_artifact(
            sh,
            ArtifactCreate(
                project_id="p",
                type="MANIFEST",
                media_type="application/json",
                data_classification=DataClassification.PHI_PII,
                creator_subsystem="S2",
            ),
            session=s,
        )
    packed = pack([info.id], include_phi_content=False)
    result = unpack(packed.archive, "p_copy")
    imported = get_artifact(result.id_map[info.id])
    assert imported.content_purged is True
    assert imported.data_classification == DataClassification.PHI_PII
    assert imported.content_hash == info.content_hash


def test_cleanup_expired_idempotency_alias(harness_db):
    import datetime as dt

    from factory_foundation.api import idempotency
    from factory_foundation.common.hashing import structure_hash

    past = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    idempotency.begin("s", "gone", structure_hash({"a": 1}), expires_at=past)
    removed = cleanup_expired_idempotency(dt.datetime.now(dt.UTC))
    assert ("s", "gone") in removed


@pytest.fixture
def harness_db():
    import os

    h = FoundationHarness(
        url=os.environ.get(
            "FACTORY_TEST_DATABASE_URL",
            "postgresql+psycopg://factory:factory@localhost:5432/factory_test",
        )
    )
    h.setup()
    try:
        yield h
    finally:
        h.teardown()
