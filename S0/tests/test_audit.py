"""Audit service tests (FD-AUD)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from factory_foundation.api import (
    ArtifactCreate,
    AuditEventInput,
    AuditQuery,
    DataClassification,
    audit,
    get_audit,
    link_pending,
    query_audit,
    register_artifact,
    register_audit_event_types,
    run_full_chain_verify,
    stage_content,
    unit_of_work,
    verify_audit_chain,
)
from factory_foundation.common.errors import FactoryError
from factory_foundation.models.artifact import ArtifactRow


@pytest.fixture
def db(foundation):
    return foundation


@pytest.fixture
def events(db):
    register_audit_event_types(
        {
            "cp.gate.approved": "S1",
            "cp.gate.rejected": "S1",
            "core.test.write": "S0",
        }
    )


def test_audit_write_and_read(db, events):
    with unit_of_work():
        rec = audit(AuditEventInput(event_type="core.test.write", object_id="o1", project_id="p1"))
    assert rec.seq is None  # unlinked until link_pending
    got = get_audit(rec.id)
    assert got.event_type == "core.test.write"


def test_unregistered_event_type_rejected(db):
    register_audit_event_types({"cp.gate.approved": "S1"})
    with pytest.raises(FactoryError) as ei, unit_of_work():
        audit(AuditEventInput(event_type="cp.not.registered", object_id="x"))
    assert ei.value.error_code == "VALIDATION_ERROR"


def test_audit_failure_rolls_back_business(db, events):
    # FD-AC-02: business + audit in one UoW; the audit write fails -> all rolls back
    sh = stage_content(
        b"{}", project_id="p1", artifact_type="MANIFEST", media_type="application/json"
    )
    with pytest.raises(FactoryError), unit_of_work() as sess:
        register_artifact(
            sh,
            ArtifactCreate(
                project_id="p1",
                type="MANIFEST",
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S2",
            ),
            session=sess,
        )
        audit(AuditEventInput(event_type="bogus.type.here", object_id="z"), session=sess)
    with unit_of_work() as sess:
        assert sess.execute(select(func.count()).select_from(ArtifactRow)).scalar_one() == 0


def test_credential_redaction(db, events):
    # FD-AC-07: Authorization header masked, flagged redacted
    with unit_of_work():
        rec = audit(
            AuditEventInput(
                event_type="core.test.write",
                after={"headers": {"Authorization": "Bearer abcdef12345"}},
            )
        )
    stored = get_audit(rec.id)
    assert stored.redacted is True
    assert stored.after["headers"]["Authorization"] == "***"


def test_append_only_update_and_delete_blocked(db, events):
    from sqlalchemy.exc import DBAPIError

    with unit_of_work():
        rec = audit(AuditEventInput(event_type="core.test.write", object_id="o1"))
    with pytest.raises(DBAPIError), unit_of_work() as s:
        s.execute(
            text("UPDATE core.audit_event SET event_type='hacked' WHERE id=:i"), {"i": rec.id}
        )
    with pytest.raises(DBAPIError), unit_of_work() as s:
        s.execute(text("DELETE FROM core.audit_event WHERE id=:i"), {"i": rec.id})


def test_append_only_truncate_blocked(db, events):
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), unit_of_work() as s:
        s.execute(text("TRUNCATE core.audit_event"))


def test_link_and_verify_full_chain(db, events):
    with unit_of_work():
        audit(AuditEventInput(event_type="core.test.write", object_id="a"))
        audit(AuditEventInput(event_type="cp.gate.approved", object_id="b"))
    assert link_pending() == 2
    result = verify_audit_chain()
    assert result.ok is True
    assert result.checked == 2
    with unit_of_work() as s:
        seqs = s.execute(text("SELECT seq FROM core.audit_event ORDER BY seq")).scalars().all()
    assert seqs == [1, 2]


def test_verify_detects_tampering(db, events):
    # AC-SEC-01: bypass the guard, mutate a record, verify reports the break
    with unit_of_work():
        audit(AuditEventInput(event_type="core.test.write", object_id="a"))
        audit(AuditEventInput(event_type="core.test.write", object_id="b"))
        audit(AuditEventInput(event_type="cp.gate.approved", object_id="c"))
    link_pending()
    assert verify_audit_chain().ok is True
    with unit_of_work() as s:
        s.execute(text("ALTER TABLE core.audit_event DISABLE TRIGGER audit_event_guard"))
        s.execute(text("UPDATE core.audit_event SET operator_name='evil' WHERE seq=2"))
        s.execute(text("ALTER TABLE core.audit_event ENABLE TRIGGER audit_event_guard"))
    result = verify_audit_chain()
    assert result.ok is False
    assert result.first_break_seq == 2
    assert result.reason == "content_changed"


def test_verify_detects_missing_record(db, events):
    with unit_of_work():
        audit(AuditEventInput(event_type="core.test.write", object_id="a"))
        audit(AuditEventInput(event_type="core.test.write", object_id="b"))
    link_pending()
    result = verify_audit_chain(start_seq=1, end_seq=5)
    assert result.ok is False
    assert result.reason == "record_missing"


def test_run_full_chain_verify_writes_audit(db, events):
    with unit_of_work():
        audit(AuditEventInput(event_type="core.test.write", object_id="a"))
    link_pending()
    result = run_full_chain_verify()
    assert result.ok is True
    page = query_audit(AuditQuery(event_type="core.audit.chain_verified"))
    assert page.total == 1


def test_query_audit_filters(db, events):
    with unit_of_work():
        audit(AuditEventInput(event_type="core.test.write", object_id="o1", project_id="px"))
        audit(AuditEventInput(event_type="core.test.write", object_id="o2", project_id="py"))
        audit(AuditEventInput(event_type="cp.gate.approved", object_id="o3", project_id="px"))
    page = query_audit(AuditQuery(project_id="px"))
    assert page.total == 2
    by_obj = query_audit(AuditQuery(object_id="o3"))
    assert by_obj.items[0].event_type == "cp.gate.approved"
