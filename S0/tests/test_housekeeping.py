"""Housekeeping tests (run_housekeeping; FD-ASM-02)."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from factory_foundation.api import (
    get_object_store,
    register_artifact_types,
    run_housekeeping,
    stage_content,
)
from factory_foundation.common.clock import FixedClock
from factory_foundation.common.errors import TransactionMisuseError
from factory_foundation.db.uow import unit_of_work
from factory_foundation.models.artifact import ArtifactRow, ArtifactStagingRow


@pytest.fixture
def db(foundation):
    return foundation


def _count_staging() -> int:
    with unit_of_work() as s:
        return len(s.execute(select(ArtifactStagingRow)).scalars().all())


def test_expired_staging_is_cleaned(db):
    old = FixedClock(dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    sh = stage_content(
        b"orphan content",
        project_id="p",
        artifact_type="MANIFEST",
        media_type="application/json",
        clock=old,
    )
    assert len(db.object_store.keys()) == 1
    assert _count_staging() == 1

    result = run_housekeeping(now=dt.datetime(2020, 1, 3, tzinfo=dt.UTC))
    assert sh.handle in result.staging_removed
    assert _count_staging() == 0
    # staged content purged from object store
    assert len(db.object_store.keys()) == 0


def test_fresh_staging_survives(db):
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    stage_content(
        b"new",
        project_id="p",
        artifact_type="MANIFEST",
        media_type="application/json",
        clock=FixedClock(now),
    )
    result = run_housekeeping(now=now + dt.timedelta(hours=1))
    assert result.staging_removed == []


def test_housekeeping_rejects_active_uow(db):
    with pytest.raises(TransactionMisuseError), unit_of_work():
        run_housekeeping()


def test_pending_deletion_retry(db):
    # an artifact marked purged whose content lingers gets deleted on the sweep
    register_artifact_types({"PURGE_TEST": ["S0"]})
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    sh = stage_content(
        b"x" * 10,
        project_id="p",
        artifact_type="PURGE_TEST",
        media_type="application/json",
        clock=FixedClock(now),
    )
    from factory_foundation.api import ArtifactCreate, DataClassification, register_artifact

    with unit_of_work() as s:
        info = register_artifact(
            sh,
            ArtifactCreate(
                project_id="p",
                type="PURGE_TEST",
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S0",
            ),
            session=s,
        )
    # mark purged in DB but leave content in the store (simulate failed delete)
    with unit_of_work() as s:
        row = s.get(ArtifactRow, info.id)
        row.content_purged = True
    assert len(db.object_store.keys()) == 1  # still present
    run_housekeeping(now=now)
    assert len(db.object_store.keys()) == 0  # retried delete removed it


def test_get_object_store_after_configure(db):
    assert get_object_store() is db.object_store
