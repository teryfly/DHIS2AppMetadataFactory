"""Branch-targeting tests to push coverage comfortably above 90%."""

from __future__ import annotations

import datetime as dt

import pytest

from factory_foundation.api import (
    ArtifactCreate,
    ArtifactQuery,
    ArtifactStatus,
    DataClassification,
    IdempotencyStatus,
    apply_retention,
    get_artifact,
    idempotency,
    query_artifacts,
    register_artifact,
    set_status,
    stage_content,
    unit_of_work,
    version_history,
)
from factory_foundation.common.clock import FixedClock
from factory_foundation.common.errors import NotFoundError
from factory_foundation.common.hashing import structure_hash
from factory_foundation.db.uow import current_session
from factory_foundation.objectstore.local import LocalFileObjectStore


@pytest.fixture
def db(foundation):
    return foundation


def _make(
    project, type, media, cls, creator, clock, *, status=ArtifactStatus.DRAFT, prev=None, sources=()
):
    # staging is external I/O: it must run before the transaction opens
    sh = stage_content(b"{}", project_id=project, artifact_type=type, media_type=media, clock=clock)
    with unit_of_work() as s:
        return register_artifact(
            sh,
            ArtifactCreate(
                project_id=project,
                type=type,
                media_type=media,
                data_classification=cls,
                creator_subsystem=creator,
                source_artifact_ids=list(sources),
                prev_version_id=prev,
                status=status,
            ),
            session=s,
            clock=clock,
        )


def test_query_each_filter(db):
    t0 = FixedClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    t1 = FixedClock(dt.datetime(2026, 6, 1, tzinfo=dt.UTC))
    a = _make("P", "MANIFEST", "application/json", DataClassification.PUBLIC, "S2", t0)
    b = _make(
        "P",
        "VALIDATION_REPORT",
        "application/json",
        DataClassification.INTERNAL,
        "S2",
        t1,
        status=ArtifactStatus.APPROVED,
    )

    assert query_artifacts(ArtifactQuery(project_id="P")).total == 2
    assert query_artifacts(ArtifactQuery(type="MANIFEST")).total == 1
    assert query_artifacts(ArtifactQuery(status=ArtifactStatus.APPROVED)).total == 1
    assert query_artifacts(ArtifactQuery(creator_subsystem="S2")).total == 2
    window = query_artifacts(
        ArtifactQuery(project_id="P", created_after=dt.datetime(2026, 3, 1, tzinfo=dt.UTC))
    )
    assert window.total == 1 and window.items[0].id == b.id
    before = query_artifacts(
        ArtifactQuery(
            project_id="P", created_before=dt.datetime(2026, 3, 1, tzinfo=dt.UTC), descending=False
        )
    )
    assert before.items[0].id == a.id


def test_version_history_three_versions(db):
    v1 = _make(
        "P",
        "MANIFEST",
        "application/json",
        DataClassification.PUBLIC,
        "S2",
        FixedClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)),
    )
    v2 = _make(
        "P",
        "MANIFEST",
        "application/json",
        DataClassification.PUBLIC,
        "S2",
        FixedClock(dt.datetime(2026, 1, 2, tzinfo=dt.UTC)),
        prev=v1.id,
    )
    v3 = _make(
        "P",
        "MANIFEST",
        "application/json",
        DataClassification.PUBLIC,
        "S2",
        FixedClock(dt.datetime(2026, 1, 3, tzinfo=dt.UTC)),
        prev=v2.id,
    )
    hist = version_history(v3.id)
    assert [h.version for h in hist] == [1, 2, 3]
    assert hist[0].status == ArtifactStatus.SUPERSEDED
    assert hist[1].status == ArtifactStatus.SUPERSEDED
    assert hist[2].status == ArtifactStatus.DRAFT


def test_set_status_and_apply_retention_filters(db):
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    info = _make(
        "RP", "MANIFEST", "application/json", DataClassification.PUBLIC, "S2", FixedClock(now)
    )
    with unit_of_work() as s:
        updated = set_status(info.id, ArtifactStatus.APPROVED, session=s)
    assert updated.status == ArtifactStatus.APPROVED
    purged = apply_retention("RP", before=now + dt.timedelta(days=1))
    assert info.id in purged
    assert get_artifact(info.id).content_purged is True


def test_idempotency_with_explicit_session(db):
    d = structure_hash({"a": 1})
    with unit_of_work() as s:
        res = idempotency.begin("scope", "k", d, session=s)
        assert res.created
        idempotency.complete("scope", "k", {"ok": True}, session=s)
        rec = idempotency.get("scope", "k", session=s)
        assert rec.status is IdempotencyStatus.COMPLETED
    # fail path with a fresh (self-owned) session
    d2 = structure_hash({"b": 2})
    idempotency.begin("scope", "k2", d2)
    failed = idempotency.fail("scope", "k2", error_code="X", session=None)
    assert failed.status is IdempotencyStatus.FAILED


def test_idempotency_get_missing(db):
    with pytest.raises(NotFoundError):
        idempotency.get("no-scope", "no-key")


def test_local_store_missing_ops(tmp_path):
    store = LocalFileObjectStore(tmp_path)
    assert store.exists("nope") is False
    store.delete("nope")  # no error
    assert store.exists("nope") is False


def test_current_session_returns_active(db):
    with unit_of_work() as s:
        assert current_session() is s
