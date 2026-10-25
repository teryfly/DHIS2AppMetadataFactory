"""Artifact storage tests (FD-ART)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from factory_foundation.api import (
    ArtifactCreate,
    ArtifactQuery,
    DataClassification,
    apply_retention,
    diff,
    get_artifact,
    lineage,
    open_content,
    pack,
    purge_content,
    query_artifacts,
    register_artifact,
    register_artifact_types,
    set_status,
    stage_content,
    unit_of_work,
    unpack,
    version_history,
)
from factory_foundation.artifacts.dto import ArtifactStatus
from factory_foundation.common.errors import ArtifactError, ArtifactPurgedError, NotFoundError
from factory_foundation.common.hashing import structure_hash

CREATOR = "S2"  # owner of MANIFEST
TYPE = "MANIFEST"


def _make(project="p1", type=TYPE, creator=CREATOR, cls=DataClassification.INTERNAL, **kw):
    sh = stage_content(
        kw.get("content", b"{}"),
        project_id=project,
        artifact_type=type,
        media_type=kw.get("media", "application/json"),
    )
    create = ArtifactCreate(
        project_id=project,
        type=type,
        media_type=kw.get("media", "application/json"),
        data_classification=cls,
        creator_subsystem=creator,
        workflow_id=kw.get("workflow_id"),
        source_artifact_ids=kw.get("sources", []),
        prev_version_id=kw.get("prev"),
        redaction_ref=kw.get("redaction_ref"),
        build_record=kw.get("build_record"),
    )
    with unit_of_work():
        return register_artifact(sh, create)


def test_register_and_get(foundation):
    info = _make(content=b'{"a":1}')
    assert info.version == 1 and info.status == ArtifactStatus.DRAFT
    again = get_artifact(info.id)
    assert again.content_hash == info.content_hash
    assert again.size == 7


def test_stage_hash_mismatch_aborts(foundation):
    # FD-AC-01: expected hash that doesn't match content
    wrong = structure_hash({"totally": "different"})
    with pytest.raises(ArtifactError) as ei:
        stage_content(b"real content", project_id="p", artifact_type=TYPE, expected_hash=wrong)
    assert "expected hash" in str(ei.value)
    # nothing registered because staging never returned a handle


def test_non_owner_cannot_create(foundation):
    # FD-AC-05: S3 is not an owner of MANIFEST (owner is S2)
    sh = stage_content(b"{}", project_id="p", artifact_type=TYPE, media_type="application/json")
    create = ArtifactCreate(
        project_id="p",
        type=TYPE,
        media_type="application/json",
        data_classification=DataClassification.INTERNAL,
        creator_subsystem="S3",
    )
    with pytest.raises(ArtifactError), unit_of_work():
        register_artifact(sh, create)


def test_unknown_type_rejected(foundation):
    sh = stage_content(b"{}", project_id="p", artifact_type="TYPE_A", media_type="application/json")
    create = ArtifactCreate(
        project_id="p",
        type="TYPE_A",
        media_type="application/json",
        data_classification=DataClassification.INTERNAL,
        creator_subsystem="S1",
    )
    with pytest.raises(ArtifactError), unit_of_work():
        register_artifact(sh, create)


def test_classification_downgrade_rejected(foundation):
    # FD-AC-04: CONFIDENTIAL source, INTERNAL derived, no redaction ref
    src = _make(cls=DataClassification.CONFIDENTIAL)
    sh = stage_content(
        b"derived", project_id="p1", artifact_type=TYPE, media_type="application/json"
    )
    create = ArtifactCreate(
        project_id="p1",
        type=TYPE,
        media_type="application/json",
        data_classification=DataClassification.INTERNAL,
        creator_subsystem=CREATOR,
        source_artifact_ids=[src.id],
    )
    with pytest.raises(ArtifactError), unit_of_work():
        register_artifact(sh, create)


def test_classification_downgrade_allowed_with_redaction(foundation):
    src = _make(cls=DataClassification.CONFIDENTIAL)
    info = _make(cls=DataClassification.INTERNAL, sources=[src.id], redaction_ref="redaction-1")
    assert info.data_classification == DataClassification.INTERNAL


def test_versioning_supersedes_previous(foundation):
    v1 = _make(content=b"one")
    v2 = _make(content=b"two", prev=v1.id)
    assert v2.version == 2
    assert get_artifact(v1.id).status == ArtifactStatus.SUPERSEDED
    hist = version_history(v2.id)
    assert [a.version for a in hist] == [1, 2]


def test_lineage_sources_and_derived(foundation):
    base = _make(content=b"base")
    derived = _make(content=b"derived", sources=[base.id])
    up = lineage(derived.id)
    assert base.id in up.sources
    down = lineage(base.id)
    assert derived.id in down.derived


def test_query_pagination_and_filter(foundation):
    for i in range(5):
        _make(content=f"c{i}".encode(), project="qproj", workflow_id="w1" if i < 3 else "w2")
    page = query_artifacts(ArtifactQuery(project_id="qproj", workflow_id="w1", limit=10))
    assert page.total == 3
    assert len(page.items) == 3


def test_set_status(foundation):
    info = _make()
    with unit_of_work():
        updated = set_status(info.id, ArtifactStatus.APPROVED)
    assert updated.status == ArtifactStatus.APPROVED


def test_open_content_returns_classification(foundation):
    info = _make(content=b"payload", cls=DataClassification.RESTRICTED)
    chunks, classification, media = open_content(info.id)
    assert classification == DataClassification.RESTRICTED
    assert b"".join(chunks) == b"payload"


def test_purge_then_read_raises(foundation):
    info = _make(content=b"secret data")
    purge_content(info.id)
    assert get_artifact(info.id).content_purged is True
    with pytest.raises(ArtifactPurgedError):
        open_content(info.id)


def test_apply_retention(foundation):
    old = _make(content=b"old")
    future = datetime.now(UTC) + timedelta(days=1)
    purged = apply_retention("p1", before=future)
    assert old.id in purged
    assert get_artifact(old.id).content_purged is True


def test_apply_retention_audits_everything_it_clears(foundation):
    """21.4 / FD-ART-011: a retention sweep has to leave a record per artifact.

    The single-artifact purge audits; the bulk path did not, so content could disappear
    under a retention rule with nothing in the append-only chain saying so -- the one
    question an audit trail exists to answer.
    """
    from factory_foundation.api import AuditQuery, query_audit

    first = _make(content=b"one")
    second = _make(content=b"two")
    future = datetime.now(UTC) + timedelta(days=1)
    purged = apply_retention("p1", before=future)
    assert {first.id, second.id} <= set(purged)

    recorded = {
        event.object_id
        for event in query_audit(AuditQuery(event_type="core.artifact.retention")).items
    }
    assert recorded == {first.id, second.id}, (
        "each purged artifact owes its own retention event, not one for the sweep"
    )


def test_diff_refuses_two_different_artifact_types(foundation):
    """FD-ART-012: diffing across types produces a confident answer about nothing."""
    package = _make(type="METADATA_PACKAGE", content=b'{"x": 1}')
    report = _make(type="VALIDATION_REPORT", content=b'{"x": 1}')
    with pytest.raises(ArtifactError) as caught:
        diff(package.id, report.id)
    assert caught.value.details["aType"] == "METADATA_PACKAGE"
    assert caught.value.details["bType"] == "VALIDATION_REPORT"


def test_diff_json_and_line(foundation):
    a = _make(content=b'{"x": 1}')
    b = _make(content=b'{"x": 2}')
    result = diff(a.id, b.id)
    assert result.kind == "json" and result.changed
    paths = [row["path"] for row in result.lines]
    assert "/x" in paths

    t1 = _make(content=b"line1\nline2", media="text/plain")
    t2 = _make(content=b"line1\nchanged", media="text/plain")
    tr = diff(t1.id, t2.id)
    assert tr.kind == "line" and tr.changed


def test_pack_unpack_roundtrip_and_hash_verify(foundation):
    # FD-AC-08: tamper -> whole unpack fails
    a = _make(content=b'{"k": 1}', cls=DataClassification.INTERNAL)
    b = _make(content=b'{"j": 2}', sources=[a.id])
    packed = pack([a.id, b.id])
    assert packed.archive_hash.startswith("sha256:")

    # clean unpack into a new project
    result = unpack(packed.archive, "p_import")
    assert len(result.id_map) == 2
    new_a = result.id_map[a.id]
    imported = get_artifact(new_a)
    assert imported.project_id == "p_import"
    assert imported.type == a.type
    # relationship restored: new b's source is new a
    new_b = result.id_map[b.id]
    assert new_a in lineage(new_b).sources

    # tamper the archive content and expect failure
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(packed.archive)) as zin:
        manifest = zin.read("manifest.json")
        with zipfile.ZipFile(buf, "w") as zout:
            for name in zin.namelist():
                if name == "manifest.json":
                    continue
                data = zin.read(name)
                if name.startswith("content/"):
                    data = b"TAMPERED"
                zout.writestr(name, data)
            zout.writestr("manifest.json", manifest)
    with pytest.raises(ArtifactError):
        unpack(buf.getvalue(), "p_bad")


def test_unpack_preserves_version_and_creator(foundation):
    a = _make(content=b'{"v": 1}')
    b = _make(content=b'{"v": 2}', prev=a.id)
    packed = pack([a.id, b.id])
    res = unpack(packed.archive, "proj2")
    assert get_artifact(res.id_map[b.id]).version == 2
    assert get_artifact(res.id_map[b.id]).creator_subsystem == CREATOR


def _fail_the_store_on(store, monkeypatch, fail_on):
    """Make the object store refuse its ``fail_on``-th write; return every key it was asked for."""
    seen = []
    original = store.put

    def put(key, source, *, media_type="application/octet-stream"):
        seen.append(key)
        if len(seen) == fail_on:
            raise ArtifactError(f"the store refused object {len(seen)}")
        return original(key, source, media_type=media_type)

    monkeypatch.setattr(store, "put", put)
    return seen


def test_an_aborted_unpack_leaves_no_object_behind(foundation, monkeypatch):
    """FD-ART-015: "nothing imported" has to cover the bytes, not only the rows.

    Content is written before registration, and object storage has no transaction to roll
    back — so a store that fails halfway would otherwise leave an object every later count
    of the project's storage includes and no artifact can ever reach.
    """
    from factory_foundation.objectstore.provider import get_object_store

    a = _make(content=b'{"k": 1}')
    b = _make(content=b'{"j": 2}', sources=[a.id])
    packed = pack([a.id, b.id])

    store = get_object_store()
    before = set(store.keys())
    asked = _fail_the_store_on(store, monkeypatch, fail_on=2)

    with pytest.raises(ArtifactError):
        unpack(packed.archive, "p_abort")

    # the first object went in and came back out; the second never landed
    assert len(asked) == 2, asked
    assert set(store.keys()) == before, sorted(set(store.keys()) - before)
    # and no row was registered to point at a location that is not there
    assert query_artifacts(ArtifactQuery(project_id="p_abort", limit=10)).total == 0


def test_a_cleanup_that_fails_names_the_objects_left(foundation, monkeypatch):
    """The abort is the error worth raising, but the orphaned key is the one worth keeping."""
    from factory_foundation.objectstore.provider import get_object_store

    a = _make(content=b'{"k": 1}')
    b = _make(content=b'{"j": 2}', sources=[a.id])
    packed = pack([a.id, b.id])

    store = get_object_store()
    asked = _fail_the_store_on(store, monkeypatch, fail_on=2)

    def refuse_delete(key):
        raise ArtifactError("the store will not delete")

    monkeypatch.setattr(store, "delete", refuse_delete)

    with pytest.raises(ArtifactError) as raised:
        unpack(packed.archive, "p_orphan")

    details = raised.value.details
    assert details["objectKeys"] == [asked[0]], details
    assert details["cause"] == "ArtifactError"
    assert store.exists(asked[0])
    assert not store.exists(asked[1])


def test_register_artifact_type_merges_owners(foundation):
    register_artifact_types({"CUSTOM": ["S2"]})
    register_artifact_types({"CUSTOM": ["S4"]})
    from factory_foundation.api import list_artifact_types

    entry = next(e for e in list_artifact_types() if e[0] == "CUSTOM")
    assert set(entry[1]) == {"S2", "S4"}


def test_get_missing_raises(foundation):
    with pytest.raises(NotFoundError):
        get_artifact("art-does-not-exist")


def test_unpack_fails_when_the_store_cannot_read_back_what_it_wrote(foundation):
    """FD-BR-02: nothing is registered from an archive until each object has been read
    back and hashed -- a store that writes one thing and returns another is refused."""
    from factory_foundation.api import configure_object_store, get_object_store
    from factory_foundation.objectstore.base import ReadHandle

    a = _make(content=b'{"k": 1}')
    packed = pack([a.id])
    inner = get_object_store()

    class _LyingStore:
        """Delegates writes to ``inner`` but hands back different bytes on read."""

        def put(self, key, source, *, media_type="application/octet-stream"):
            return inner.put(key, source, media_type=media_type)

        def open_read(self, key):
            captured = inner.open_read(key)
            return ReadHandle(key=captured.key, chunks=iter([b"tampered-bytes"]), size=None)

        def delete(self, key):
            return inner.delete(key)

        def exists(self, key):
            return inner.exists(key)

    original = inner
    configure_object_store(_LyingStore())
    try:
        with pytest.raises(ArtifactError) as caught:
            unpack(packed.archive, "p_lying")
        assert "readback" in caught.value.message
    finally:
        configure_object_store(original)
    assert query_artifacts(ArtifactQuery(project_id="p_lying")).total == 0
