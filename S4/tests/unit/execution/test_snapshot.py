"""Snapshot（DX-EXE-022/023、DX-EXE-029、FR-SNP-001/002）。

Snapshot 是 **S4 保存的目标环境状态记录**——不是数据库备份，也不是 DHIS2 的事务快照。它
回答两件事：提交前那个环境长什么样，以及「那一刻的状态」是什么（VALIDATE 有效性的第三
要素）。范围按 DX-EXE-022 是 **Managed Scope ∪ 载荷对象 ∪ 观测基准对象** 的并集：载荷可能
引用范围外的对象，那些对象被改动同样影响本次部署。

``env_state_hash`` 只由 UID + 内容哈希参与：把大段内容塞进指纹，一次无关的字段展开就会
让 VALIDATE 被误判为过期（DX-EXE-029）。而内容哈希的定义只有一处，就是
:mod:`discovery.canonical` 的那一份排除表（DX-DSC-007、AC-EXE-07）。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import AuditQuery, query_audit, unit_of_work

from factory_dhis2.artifacts import read_json
from factory_dhis2.discovery.canonical import object_hash
from factory_dhis2.enums import CredentialScope
from factory_dhis2.errors import Dhis2TimeoutError
from factory_dhis2.execution import snapshot
from factory_dhis2.execution.reads import DEFAULT_READ_FIELDS, FULL_READ_FIELDS
from tests.unit.execution import (
    ENVIRONMENT,
    PROJECT,
    Call,
    FakeClient,
    build_manifest,
    make_run_context,
    make_target,
    package_from_artifact,
    package_of,
    register_package_artifact,
)

UID = "de000000001"
OTHER_UID = "de000000002"
COMBO_UID = "qAZsZ5kBwLk"
PROGRAM_UID = "pg0000000001"
STAMP = "2026-01-01T00:00:00.000"


def _row(uid: str, *, name: str = "Weight", stamp: str = STAMP) -> dict[str, Any]:
    return {"id": uid, "code": f"code-{uid}", "name": name, "lastUpdated": stamp, "created": stamp}


class ReadingClient(FakeClient):
    """把 ``list_all`` 也记进调用记录，并可模拟批量过滤器漏掉某些 UID（2.41 实测会漏）。"""

    def __init__(
        self,
        target: Any,
        *,
        scope: CredentialScope = CredentialScope.DISCOVERY,
        objects: dict[tuple[str, str], dict[str, Any]] | None = None,
        reports: dict[str, dict[str, Any]] | None = None,
        expression_verdicts: dict[str, Any] | None = None,
        calls: list[Any] | None = None,
        responses: dict[str, Any] | None = None,
        dropped: set[str] | None = None,
    ) -> None:
        super().__init__(
            target,
            scope=scope,
            objects=objects,
            reports=reports,
            expression_verdicts=expression_verdicts,
            calls=calls,
            responses=responses,
        )
        # 与 ``calls`` 一样按引用传递：换凭证范围不能换掉这份「会漏读」的名单
        self.dropped = dropped if dropped is not None else set()

    def with_scope(self, scope: CredentialScope) -> ReadingClient:
        return type(self)(
            self.target,
            scope=scope,
            objects=self.objects,
            reports=self.reports,
            expression_verdicts=self.expression_verdicts,
            calls=self.calls,
            responses=self.responses,
            dropped=self.dropped,
        )

    def list_all(  # type: ignore[override]
        self,
        dhis2_type: str,
        *,
        fields: str,
        filters: Any = (),
        page_size: int | None = None,
        limit: int | None = None,
        progress: Any = None,
        cancel: Any = None,
    ) -> Any:
        self.calls.append(
            Call(
                "metadata.read",
                self.default_scope,
                dhis2_type=dhis2_type,
                params={"fields": fields, "filters": list(filters)},
            )
        )
        rows = list(
            super().list_all(
                dhis2_type,
                fields=fields,
                filters=filters,
                page_size=page_size,
                limit=limit,
                progress=progress,
                cancel=cancel,
            )
        )
        return iter([row for row in rows if row.get("id") not in self.dropped])


def _manifest(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "objects": [
            {"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "CREATE"},
            {"businessKey": "bk.program", "dhis2Type": "program", "uid": PROGRAM_UID, "disposition": "REUSE"},
            {"businessKey": "default.categoryCombo", "dhis2Type": "categoryCombo", "uid": COMBO_UID,
             "disposition": "REFERENCE"},
        ],
        "uid_map": [
            {"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "CREATE"},
            {"businessKey": "bk.program", "uid": PROGRAM_UID, "dhis2Type": "program", "disposition": "REUSE"},
            {"businessKey": "bk.nameless", "uid": None, "dhis2Type": "dataElement", "disposition": "REFERENCE"},
        ],
        "dispositions": {"bk.weight": "CREATE", "bk.program": "REUSE"},
        "controlled_fields": {},
        "managed_scope": [
            {"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID},
            # 寻不到东西的三种写法：空 UID、没有 UID、没有类型
            {"businessKey": "orphan.empty.uid", "dhis2Type": "dataElement", "uid": ""},
            {"businessKey": "orphan.no.uid", "dhis2Type": "dataElement", "uid": None},
            {"businessKey": "default.categoryCombo", "dhis2Type": "", "uid": COMBO_UID},
        ],
        "baselines": [
            {"businessKey": "default.categoryCombo", "uid": COMBO_UID, "observedHash": "sha256:combo"},
            {"businessKey": "orphan.no.uid.at.all", "uid": None, "observedHash": "sha256:nothing"},
        ],
    }
    base.update(changes)
    return build_manifest(**base)


def _payload() -> dict[str, Any]:
    return {"dataElements": [_row(UID)], "programs": [{"id": PROGRAM_UID, "name": "Antenatal"}]}


def _context(
    objects: dict[tuple[str, str], dict[str, Any]],
    *,
    package: Any = None,
    client: FakeClient | None = None,
) -> Any:
    resolved = package or package_of(_manifest(), _payload())
    return make_run_context(
        resolved,
        objects=objects,
        client=client or ReadingClient(make_target(), objects=objects),
    )


# --- 范围 -------------------------------------------------------------------


def test_the_scope_is_the_union_of_managed_scope_payload_and_baselines() -> None:
    """DX-EXE-022：Snapshot 的范围是并集而不是交集——范围外的引用同样影响本次部署。"""
    document = snapshot.build(_context({("dataElement", UID): _row(UID)}))

    uids = {item["uid"] for item in document["objects"]}
    assert uids == {UID, PROGRAM_UID, COMBO_UID}
    assert document["objectCount"] == len(document["objects"])
    assert document["snapshotKind"] == snapshot.KIND_PRE_COMMIT
    assert document["environmentKey"] == ENVIRONMENT
    assert [item["uid"] for item in document["payloadObjects"]] == [UID, PROGRAM_UID]


def test_entries_that_cannot_be_addressed_are_never_read_as_an_empty_uid() -> None:
    """DX-EXE-022：既没类型也没 UID 的条目不进 Snapshot——按空 UID 去读只会读错东西。"""
    document = snapshot.build(_context({("dataElement", UID): _row(UID)}))

    assert all(item["uid"] and item["dhis2Type"] for item in document["objects"])
    assert "orphan.empty.uid" not in str(document["objects"])
    assert "" not in [item["uid"] for item in document["objects"]]


def test_a_type_is_backfilled_from_the_manifest_objects_when_the_scope_row_omits_it() -> None:
    """DX-EXE-022：Snapshot 必须能寻址每个对象——范围行没给类型时从 objects 段补齐。"""
    document = snapshot.build(_context({("dataElement", UID): _row(UID)}))

    combo = next(item for item in document["objects"] if item["uid"] == COMBO_UID)
    assert combo["dhis2Type"] == "categoryCombo", "范围行没给类型时要从 objects 段补齐"


def test_the_reads_are_batched_per_type_with_the_minimal_field_set() -> None:
    """DX-NFR-02：同一类型的 UID 一次批量读回，字段集是漂移/Snapshot 共用的那一份。"""
    context = _context({("dataElement", UID): _row(UID)})

    snapshot.build(context)

    reads = [item for item in context.client.calls if item.logical_name == "metadata.read"]
    assert {item.dhis2_type for item in reads} == {"categoryCombo", "dataElement", "program"}
    assert all(item.params["fields"] == DEFAULT_READ_FIELDS for item in reads)
    assert any(f"id:in:[{UID}]" in str(item.params["filters"]) for item in reads)


def test_an_object_the_batch_read_missed_is_read_back_one_by_one() -> None:
    """DX-EXE-023：某些版本的 ``id:in`` 过滤器会漏掉新建对象——漏了就单独再读一次。"""
    client = ReadingClient(
        make_target(),
        objects={("dataElement", UID): _row(UID), ("dataElement", OTHER_UID): _row(OTHER_UID)},
        dropped={OTHER_UID},
    )
    package = package_of(
        _manifest(
            objects=[
                {"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "CREATE"},
                {"businessKey": "bk.other", "dhis2Type": "dataElement", "uid": OTHER_UID, "disposition": "CREATE"},
                {"businessKey": "default.categoryCombo", "dhis2Type": "categoryCombo", "uid": COMBO_UID,
                 "disposition": "REFERENCE"},
            ],
            uid_map=[
                {"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "CREATE"},
                {"businessKey": "bk.other", "uid": OTHER_UID, "dhis2Type": "dataElement", "disposition": "CREATE"},
            ],
            managed_scope=[{"businessKey": "bk.other", "dhis2Type": "dataElement", "uid": OTHER_UID}],
            baselines=[],
        ),
        _payload(),
    )
    context = _context({}, package=package, client=client)

    document = snapshot.build(context)

    entry = next(item for item in document["objects"] if item["uid"] == OTHER_UID)
    assert entry["present"] is True
    assert entry["content"]["name"] == "Weight"
    singles = [
        item for item in context.client.calls
        if item.logical_name == "metadata.read.one" and item.path_args.get("uid") == OTHER_UID
    ]
    assert singles, "批量漏掉的对象必须逐个补读"
    assert singles[0].params["fields"] == FULL_READ_FIELDS


def test_a_missing_object_is_recorded_as_absent_not_dropped() -> None:
    """DX-EXE-023：环境里没有也是一个事实——Snapshot 记 present=false 而不是跳过。"""
    entries = {item["uid"]: item for item in snapshot.build(_context({}))["objects"]}

    assert entries[UID]["present"] is False
    assert entries[UID]["contentHash"] is None
    assert entries[UID]["content"] is None
    assert entries[UID]["lastUpdated"] is None


def test_each_entry_carries_the_per_uid_state_it_was_asked_to_record() -> None:
    """DX-EXE-023：每个 UID 记下 present / lastUpdated / 内容哈希 / 原文四件事。"""
    document = snapshot.build(_context({("dataElement", UID): _row(UID)}))

    entry = next(item for item in document["objects"] if item["uid"] == UID)
    assert entry["dhis2Type"] == "dataElement"
    assert entry["present"] is True
    assert entry["lastUpdated"] == STAMP
    assert entry["contentHash"] == object_hash(_row(UID), make_target().profile.volatile_fields)
    assert entry["content"]["name"] == "Weight"


def test_the_snapshot_honours_cancellation_between_types() -> None:
    """DX-EXE-023：Snapshot 也要能被取消，一次全量读不能停不下来。"""

    class _Cancel:
        def __init__(self) -> None:
            self.seen = 0

        def raise_if_cancelled(self) -> None:
            self.seen += 1
            if self.seen > 1:
                raise Dhis2TimeoutError("cancelled", details={})

    context = _context({("dataElement", UID): _row(UID)})

    with pytest.raises(Dhis2TimeoutError):
        snapshot.build(context, kind=snapshot.KIND_POST_COMMIT, cancel=_Cancel())

    reads = [item for item in context.client.calls if item.logical_name == "metadata.read"]
    assert len(reads) == 1, "取消必须停在下一类之前"


# --- 状态指纹 ---------------------------------------------------------------


def test_the_state_hash_is_stable_for_identical_content() -> None:
    """DX-EXE-029：同样的内容给出同样的指纹——取数时刻与字段顺序都不参与。"""
    first = snapshot.build(_context({("dataElement", UID): _row(UID)}))
    second = snapshot.build(_context({("dataElement", UID): dict(sorted(_row(UID).items(), reverse=True))}))

    assert snapshot.env_state_hash(first) == snapshot.env_state_hash(second)


def test_the_state_hash_ignores_a_volatile_only_change() -> None:
    """AC-EXE-07：``lastUpdated`` 单独刷新不改变状态指纹，否则任何人碰一下就能让 VALIDATE 过期。"""
    volatile = make_target().profile.volatile_fields
    refreshed = _row(UID, stamp="2026-09-09T09:09:09.000")

    assert object_hash(refreshed, volatile) == object_hash(_row(UID), volatile)
    assert snapshot.env_state_hash(snapshot.build(_context({("dataElement", UID): refreshed}))) == (
        snapshot.env_state_hash(snapshot.build(_context({("dataElement", UID): _row(UID)})))
    )


def test_the_state_hash_changes_when_content_changes() -> None:
    """DX-EXE-029：非易变字段一变，指纹就变——这正是 VALIDATE 只对那一刻状态有效的原因。"""
    before = snapshot.env_state_hash(snapshot.build(_context({("dataElement", UID): _row(UID)})))
    after = snapshot.env_state_hash(
        snapshot.build(_context({("dataElement", UID): _row(UID, name="Renamed")}))
    )

    assert before != after


def test_the_state_hash_is_per_environment() -> None:
    """DX-BR-02：状态指纹绑定环境，另一个环境里的同样内容不是同一次校验。"""
    document = snapshot.build(_context({("dataElement", UID): _row(UID)}))

    assert snapshot.env_state_hash(document) != snapshot.env_state_hash(
        {**document, "environmentKey": "env-other"}
    )


def test_an_absent_object_still_occupies_its_slot_in_the_hash() -> None:
    """DX-EXE-029：present=false 也参与指纹——「它不在」同样是那一刻的状态。"""
    absent = snapshot.build(_context({("dataElement", UID): _row(UID)}))
    entries = [item for item in absent["objects"] if item["uid"] != UID]

    assert snapshot.env_state_hash({**absent, "objects": entries}) != snapshot.env_state_hash(absent)


# --- 落库 -------------------------------------------------------------------


def _stored_context(objects: dict[tuple[str, str], dict[str, Any]], harness: Any) -> Any:
    """落库的用例要一个**真**产物：Snapshot 的 source 引用的是载荷产物 ID。"""
    del harness
    artifact_id = register_package_artifact(_payload(), _manifest())
    return _context(objects, package=package_from_artifact(artifact_id))


def test_saving_a_snapshot_registers_the_artifact_index_and_the_audit(harness: Any) -> None:
    """DX-EXE-022：Snapshot 是带索引行的 CONFIDENTIAL 产物——事后 Diff 与对账都从那里找它。"""
    context = _stored_context({("dataElement", UID): _row(UID)}, harness)
    document = snapshot.build(context, kind=snapshot.KIND_POST_COMMIT)

    artifact_id, row_id = snapshot.save(
        context, document, execution_id="dxexe-fake-1", kind=snapshot.KIND_POST_COMMIT
    )
    stored = read_json(artifact_id)

    assert stored["snapshotKind"] == snapshot.KIND_POST_COMMIT
    assert stored["managedScope"] == document["managedScope"]
    assert stored["objects"] == document["objects"]
    with unit_of_work() as session:
        from factory_dhis2.models.execution import SnapshotIndexRow

        index = session.get(SnapshotIndexRow, row_id)
        assert index is not None
        assert index.kind == snapshot.KIND_POST_COMMIT
        assert index.artifact_id == artifact_id
        assert index.content_hash
        assert index.object_count == document["objectCount"]
        assert index.dhis2_version == "2.41.10"
        assert index.execution_id == "dxexe-fake-1"
        assert index.scope == {"managedScopeEntries": len(document["managedScope"]),
                               "objects": document["objectCount"]}
    events = query_audit(AuditQuery(event_type="dx.snapshot.created", object_id=row_id))
    assert events.items[0].after["kind"] == snapshot.KIND_POST_COMMIT
    assert events.items[0].after["artifactId"] == artifact_id
    assert context.project_id == PROJECT


def test_a_snapshot_without_an_execution_is_still_indexed(harness: Any) -> None:
    """DX-EXE-023：Diff 用的 CURRENT Snapshot 不属于任何执行，它同样要能被找到。"""
    context = _stored_context({("dataElement", UID): _row(UID)}, harness)
    document = snapshot.build(context, kind=snapshot.KIND_CURRENT)

    artifact_id, row_id = snapshot.save(context, document, kind=snapshot.KIND_CURRENT)

    with unit_of_work() as session:
        from factory_dhis2.models.execution import SnapshotIndexRow

        index = session.get(SnapshotIndexRow, row_id)
        assert index is not None and index.execution_id is None
        assert index.kind == snapshot.KIND_CURRENT
    assert artifact_id
