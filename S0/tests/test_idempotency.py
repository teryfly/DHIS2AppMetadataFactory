"""Idempotency tests (FD-IDEM)."""

from __future__ import annotations

import datetime as dt

import pytest

from factory_foundation.api import DHIS2_EXECUTION_SCOPE, IdempotencyStatus, idempotency
from factory_foundation.common.errors import IdempotencyConflictError
from factory_foundation.common.hashing import structure_hash
from factory_foundation.db.uow import unit_of_work


@pytest.fixture
def db(foundation):
    return foundation


def test_begin_complete_and_replay(db):
    digest = structure_hash({"req": 1})
    res = idempotency.begin("api.commit", "k1", digest)
    assert res.created and res.status is IdempotencyStatus.IN_PROGRESS
    idempotency.complete("api.commit", "k1", {"executionId": "ex1"})

    again = idempotency.begin("api.commit", "k1", digest)
    assert again.created is False
    assert again.already_completed is True
    assert again.first_result == {"executionId": "ex1"}


def test_same_key_different_digest_conflict(db):
    # FD-AC-06
    idempotency.begin("api.commit", "k2", structure_hash({"a": 1}))
    with pytest.raises(IdempotencyConflictError):
        idempotency.begin("api.commit", "k2", structure_hash({"a": 2}))


def test_in_progress_is_reported(db):
    d = structure_hash({"x": 1})
    idempotency.begin("s", "k", d)
    second = idempotency.begin("s", "k", d)
    assert second.status is IdempotencyStatus.IN_PROGRESS
    assert second.already_completed is False


def test_fail_records_error(db):
    d = structure_hash({"x": 1})
    idempotency.begin("s", "k", d)
    rec = idempotency.fail("s", "k", error_code="DHIS2_IMPORT_ERROR")
    assert rec.status is IdempotencyStatus.FAILED
    assert rec.error_code == "DHIS2_IMPORT_ERROR"


def test_dhis2_execution_never_expires(db):
    # FD-IDEM-003 / FD-BR-08
    past = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=-1)
    idempotency.begin(DHIS2_EXECUTION_SCOPE, "exec-1", structure_hash({"c": 1}), expires_at=past)
    idempotency.begin("other", "o-1", structure_hash({"c": 1}), expires_at=past)
    removed = idempotency.cleanup_expired(dt.datetime.now(dt.UTC))
    removed_keys = {k for (_s, k) in removed}
    assert "o-1" in removed_keys
    assert "exec-1" not in removed_keys
    # dx.execution record still present
    assert idempotency.get(DHIS2_EXECUTION_SCOPE, "exec-1").status is IdempotencyStatus.IN_PROGRESS


def test_begin_inside_uow_joins(db):
    d = structure_hash({"z": 1})
    with unit_of_work():
        res = idempotency.begin("s", "inuow", d)
        assert res.created
    # committed with the caller transaction
    assert idempotency.get("s", "inuow").status is IdempotencyStatus.IN_PROGRESS
