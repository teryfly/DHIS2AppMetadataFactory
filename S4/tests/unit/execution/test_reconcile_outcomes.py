"""对账：给判断与证据，不给猜测（DX-EXE-035、AC-EXE-05、NFR-REL-003、DX-EXE-036）。

一次提交可能「请求已发出、响应没拿到、也没有任务 ID 可问」。对账不猜：它读回载荷对象的当前
状态，与**提交前 Snapshot** 以及**载荷本身**三方比对——Snapshot 回答「什么都没发生吗」，载荷
回答「变成我们要的样子了吗」，两者都不等于就是部分不一致。字段的比较口径与部署后验证完全
共用（``post_verify`` 那组函数），否则「对账说已生效」与「验证说有差异」会各自按一套规则成立。

S1 侧的两条入口都在这里被证明：拿着幂等键先确认这次执行到哪一步（DX-EXE-036，重试 COMMIT
前唯一允许的确认路径），以及对出 APPLIED / NOT_APPLIED / PARTIAL_MISMATCH / UNKNOWN（AC-EXE-05）。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import unit_of_work

from factory_dhis2.api import get_execution_by_key
from factory_dhis2.api import reconcile as reconcile_api
from factory_dhis2.enums import ExecutionMode, ExecutionStatus, ReconcileOutcome
from factory_dhis2.errors import Dhis2TimeoutError
from factory_dhis2.execution import reconcile as reconcile_module
from factory_dhis2.execution import snapshot as snapshot_module
from factory_dhis2.execution import steps
from factory_dhis2.execution.reconcile import (
    VERDICT_APPLIED,
    VERDICT_MISMATCH,
    VERDICT_NOT_APPLIED,
    VERDICT_UNKNOWN,
    ObjectFinding,
    Reconciliation,
    comparable_structure,
    index_baseline,
    judge,
    overall,
    payload_mismatches,
    reconcile_subjects,
    same_content,
)
from tests.unit.execution import (
    ENVIRONMENT,
    PROJECT,
    FakeClient,
    build_manifest,
    make_run_context,
    make_target,
    package_from_artifact,
    package_of,
    register_package_artifact,
    seed_environment,
)

UID = "de000000001"
OTHER_UID = "de000000002"
BASELINE_STAMP = "2026-01-01T00:00:00.000"


def _manifest(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "objects": [
            {"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "CREATE"},
        ],
        "uid_map": [
            {"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "CREATE"},
        ],
        "dispositions": {"bk.weight": "CREATE"},
        "controlled_fields": {},
        "deletion_candidates": [
            {"businessKey": "bk.retired", "dhis2Type": "dataElement", "uid": OTHER_UID, "reason": "下线"}
        ],
    }
    base.update(changes)
    return build_manifest(**base)


def _payload() -> dict[str, Any]:
    return {"dataElements": [{"id": UID, "name": "Weight", "shortName": "Wt"}]}


class UnreadableClient(FakeClient):
    """读不回来的实例：对账必须把它算成 UNKNOWN。"""

    def call(self, *_args: Any, **_kwargs: Any) -> Any:
        raise Dhis2TimeoutError("no answer", details={})


def _subject(
    uid: str = UID,
    *,
    disposition: str = "CREATE",
    dhis2_type: str = "dataElement",
    business_key: str | None = None,
) -> dict[str, Any]:
    return {
        "uid": uid,
        "dhis2_type": dhis2_type,
        "business_key": business_key or f"bk.{uid}",
        "disposition": disposition,
    }


def _absent_baseline() -> dict[str, Any]:
    return {"present": False, "content": None, "contentHash": None}


def _context(objects: dict[tuple[str, str], dict[str, Any]], **changes: Any) -> Any:
    manifest = _manifest()
    manifest.update(changes.pop("manifest_changes", None) or {})
    return make_run_context(package_of(manifest, changes.pop("payload", None) or _payload()), objects=objects, **changes)


# --- 对账对象 ---------------------------------------------------------------


def test_only_create_and_update_objects_can_be_reconciled() -> None:
    """DX-EXE-035：对账问的是「这次部署动过的对象」，REUSE / REFERENCE 不在其列。"""
    package = package_of(
        _manifest(
            objects=[
                {"businessKey": "bk.a", "dhis2Type": "dataElement", "uid": UID, "disposition": "CREATE"},
                {"businessKey": "bk.b", "dhis2Type": "dataElement", "uid": OTHER_UID, "disposition": "UPDATE"},
                {"businessKey": "bk.c", "dhis2Type": "categoryCombo", "uid": "cc0000000001", "disposition": "REUSE"},
                {"businessKey": "bk.d", "uid": "de000000004", "disposition": "UPDATE"},
                {"businessKey": "bk.e", "dhis2Type": "dataElement", "disposition": "UPDATE"},
            ],
            uid_map=[
                {"businessKey": "bk.b", "uid": OTHER_UID, "dhis2Type": "dataElement", "disposition": "UPDATE"},
                {"businessKey": "bk.d", "uid": "de000000004", "dhis2Type": None, "disposition": "UPDATE"},
                {"businessKey": "bk.e", "uid": None, "dhis2Type": "dataElement", "disposition": "UPDATE"},
            ],
            dispositions={"bk.b": "UPDATE", "bk.d": "UPDATE", "bk.e": "UPDATE"},
        ),
        _payload(),
    )

    subjects = reconcile_subjects(package, ExecutionMode.COMMIT.value)

    # bk.d 两处都给不出类型、bk.e 没有 UID → 读不回来的对象不进清单，而不是判成一个 UNKNOWN
    assert [(item["uid"], item["disposition"]) for item in subjects] == [
        (UID, "CREATE"),
        (OTHER_UID, "UPDATE"),
    ]
    assert subjects[1]["dhis2_type"] == "dataElement"


def test_the_disposition_may_come_from_the_manifest_map_instead_of_the_row() -> None:
    """DX-EXE-035：objects 段没写处置时退回 ``dispositions`` 段，同一套词汇不另立规则。"""
    package = package_of(
        _manifest(
            objects=[{"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID}],
            dispositions={"bk.weight": "UPDATE"},
        ),
        _payload(),
    )

    subjects = reconcile_subjects(package, ExecutionMode.COMMIT.value)

    assert [(item["uid"], item["disposition"]) for item in subjects] == [(UID, "UPDATE")]


def test_a_deletion_execution_reconciles_the_candidate_set() -> None:
    """DX-EXE-038：删除执行的对账对象是删除候选集，期望终态是「对象不在」。"""
    subjects = reconcile_subjects(package_of(_manifest(), _payload()), ExecutionMode.DELETE.value)

    assert [(item["uid"], item["disposition"]) for item in subjects] == [(OTHER_UID, "DEPRECATED")]


# --- 单对象三方比对 ----------------------------------------------------------


def test_an_object_that_matches_the_payload_is_applied() -> None:
    """AC-EXE-05：受控内容与载荷一致才是「已生效」。"""
    finding = judge(_context({("dataElement", UID): {"id": UID, "name": "Weight", "shortName": "Wt"}}),
                   _subject(), _absent_baseline())

    assert finding.verdict == VERDICT_APPLIED
    assert finding.fields == []
    assert finding.present_now is True and finding.present_in_baseline is False


def test_an_object_that_is_still_the_pre_commit_state_is_not_applied() -> None:
    """AC-EXE-05 的关键分支：对象在、但内容还是提交前那份 → 本次部署没有生效。"""
    context = _context(
        {("dataElement", UID): {"id": UID, "name": "Old name", "shortName": "Wt"}},
        payload={"dataElements": [{"id": UID, "name": "New name", "shortName": "Wt"}]},
        manifest_changes={
            "objects": [{"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "UPDATE"}],
            "uidToBusinessKey": [
                {"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "UPDATE"}
            ],
            "dispositions": {"bk.weight": "UPDATE"},
            "controlledFields": {"bk.weight": ["name"]},
        },
    )

    finding = judge(
        context,
        _subject(disposition="UPDATE", business_key="bk.weight"),
        {"present": True, "content": {"id": UID, "name": "Old name"}, "contentHash": "sha256:base"},
    )

    assert finding.verdict == VERDICT_NOT_APPLIED
    assert "与提交前一致" in (finding.detail or "")
    assert [item["field"] for item in finding.fields] == ["name"], "拒绝生效时也要给出差在哪个字段"


def test_a_changed_environment_that_is_not_our_change_is_a_partial_mismatch() -> None:
    """DX-EXE-035：既不等于载荷也不等于提交前——环境被动过，但不是按这次部署动的。"""
    context = _context(
        {("dataElement", UID): {"id": UID, "name": "Someone else", "shortName": "Wt"}},
        payload={"dataElements": [{"id": UID, "name": "New name", "shortName": "Wt"}]},
        manifest_changes={
            "objects": [{"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "UPDATE"}],
            "uidToBusinessKey": [
                {"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "UPDATE"}
            ],
            "dispositions": {"bk.weight": "UPDATE"},
            "controlledFields": {"bk.weight": ["name"]},
        },
    )

    finding = judge(
        context,
        _subject(disposition="UPDATE", business_key="bk.weight"),
        {"present": True, "content": {"id": UID, "name": "Old name"}, "contentHash": "sha256:base"},
    )

    assert finding.verdict == VERDICT_MISMATCH
    assert "已非提交前" in (finding.detail or "")


def test_a_payload_without_comparable_fields_is_judged_on_existence_only() -> None:
    """DX-EXE-035：载荷只列 UID 时唯一站得住的判断是「存在与否 + 与基准是否相同」。"""
    package = package_of(_manifest(), {"dataElements": [{"id": UID}]})
    context = make_run_context(package, objects={("dataElement", UID): {"id": UID, "name": "Whatever"}})

    created = judge(context, _subject(), _absent_baseline())
    unchanged = judge(
        context,
        _subject(),
        {"present": True, "content": {"id": UID, "name": "Whatever"}, "contentHash": "sha256:base"},
    )
    moved = judge(
        context,
        _subject(),
        {"present": True, "content": {"id": UID, "name": "Old name"}, "contentHash": "sha256:base"},
    )

    assert created.verdict == VERDICT_APPLIED and "提交前不存在" in created.detail
    assert unchanged.verdict == VERDICT_NOT_APPLIED
    assert moved.verdict == VERDICT_MISMATCH


def test_an_object_that_disappeared_after_being_present_is_a_mismatch() -> None:
    """DX-EXE-035：提交前在、现在读不到，不是「未生效」而是环境被改过。"""
    finding = judge(_context({}), _subject(), {"present": True, "content": {"id": UID}, "contentHash": "sha256:x"})

    assert finding.verdict == VERDICT_MISMATCH
    assert finding.present_now is False


def test_the_deletion_verdict_wants_the_object_to_be_gone() -> None:
    """DX-EXE-038：删除的期望终态是「对象不在」；仍在的对象按未生效或不一致报告。"""
    context = _context({("dataElement", OTHER_UID): {"id": OTHER_UID, "name": "Retired"}})
    subject = _subject(OTHER_UID, disposition="DEPRECATED")
    baseline = {"present": True, "content": {"id": OTHER_UID, "name": "Retired"}, "contentHash": "sha256:x"}

    still_there = judge(context, subject, baseline)
    changed = judge(
        context,
        subject,
        {"present": True, "content": {"id": OTHER_UID, "name": "Someone else"}, "contentHash": "sha256:x"},
    )
    no_baseline = judge(context, subject, _absent_baseline())
    gone = judge(_context({}), subject, baseline)

    assert still_there.verdict == VERDICT_NOT_APPLIED
    assert "对象仍在，且与提交前一致" in still_there.detail
    assert changed.verdict == VERDICT_MISMATCH
    # 没有基准就说不出「与提交前一致」：对象还在，但它为什么在无法判定，只能报不一致
    assert no_baseline.verdict == VERDICT_MISMATCH
    assert gone.verdict == VERDICT_APPLIED and gone.detail == "对象已不在环境中"


def test_sharing_fields_are_left_to_post_verify_not_double_judged() -> None:
    """DX-EXE-040 口径共用：对账只比受控内容，共享与权限不在这儿报差异。"""
    mismatches = payload_mismatches(
        {"name": "Weight", "sharing": {"public": "rw"}, "publicAccess": "rw", "code": "X"},
        {"name": "Old", "sharing": {"public": "none"}, "publicAccess": "none"},
        {"name", "sharing", "publicAccess"},
        make_target().profile,
    )

    assert [item["field"] for item in mismatches] == ["name"]


def test_a_thin_baseline_snapshot_does_not_invent_a_difference() -> None:
    """DX-DSC-007：薄读取的 Snapshot 只在**基准里出现的键**上比较，键集不同不等于内容不同。"""
    profile = make_target().profile

    assert same_content({"id": UID, "name": "Weight", "lastUpdated": BASELINE_STAMP},
                        {"id": UID, "name": "Weight", "lastUpdated": "2026-09-09", "extra": 1},
                        profile, "dataElement") is True
    assert same_content(None, {"id": UID}, profile, "dataElement") is False
    assert same_content({"code": "X"}, {"id": UID}, profile, "dataElement") is False
    assert same_content({"optionSet": {"name": "a"}}, {"optionSet": {"name": "b"}}, profile, "dataElement") is False
    assert same_content({"categories": [{"id": "a"}]}, {"categories": [{"id": "a"}]}, profile, "dataElement") is True


def test_nested_structures_are_compared_in_one_normalised_shape() -> None:
    """DX-DSC-007：嵌套里的易变字段同样被剥掉，否则每层都会报出假差异。"""
    excluded = {"lastUpdated", "href", "id"}

    assert comparable_structure({"b": 1, "a": {"id": "x", "name": "n"}, "lastUpdated": "t"}, excluded) == {
        "a": {"name": "n"},
        "b": 1,
    }
    assert comparable_structure([{"id": "x", "name": "n"}], excluded) == [{"name": "n"}]
    assert comparable_structure("scalar", excluded) == "scalar"


# --- 整体结论 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([], ReconcileOutcome.UNKNOWN),
        ([VERDICT_APPLIED], ReconcileOutcome.APPLIED),
        ([VERDICT_NOT_APPLIED], ReconcileOutcome.NOT_APPLIED),
        ([VERDICT_APPLIED, VERDICT_NOT_APPLIED], ReconcileOutcome.PARTIAL_MISMATCH),
        ([VERDICT_MISMATCH], ReconcileOutcome.PARTIAL_MISMATCH),
        ([VERDICT_UNKNOWN], ReconcileOutcome.UNKNOWN),
        ([VERDICT_APPLIED, VERDICT_UNKNOWN], ReconcileOutcome.UNKNOWN),
        ([VERDICT_NOT_APPLIED, VERDICT_UNKNOWN], ReconcileOutcome.UNKNOWN),
    ],
)
def test_the_overall_answer_is_only_as_confident_as_its_weakest_object(
    verdicts: list[str], expected: ReconcileOutcome
) -> None:
    """DX-EXE-035：掺进一个读不回来的对象，整体结论就退回「无法判定」——不猜。"""
    reconciliation = Reconciliation(
        findings=[
            ObjectFinding(
                dhis2_type="dataElement", uid=f"de{index:09d}", business_key=None, disposition="CREATE",
                verdict=verdict,
            )
            for index, verdict in enumerate(verdicts, start=1)
        ]
    )

    assert overall(reconciliation) is expected
    if verdicts:
        assert sum(reconciliation.counts.values()) == len(verdicts)
        assert reconciliation.as_evidence()["verdicts"] == reconciliation.counts


def test_a_baseline_index_skips_entries_it_cannot_address() -> None:
    """DX-EXE-022：Snapshot 里寻不到 UID 的条目不进索引，但基准的种类要写进证据。"""
    notes: list[str] = []
    index = index_baseline(
        {"objects": [{"uid": UID, "present": True}, {"present": True}, None]}, notes
    )

    assert list(index) == [UID]
    assert notes == []


def test_an_unreadable_object_is_unknown_rather_than_not_applied() -> None:
    """DX-EXE-035：一次网络抖动不能把结论写成「未生效」。"""
    client = FakeClient(make_target())

    class Broken(FakeClient):
        def call(self, *args: Any, **kwargs: Any) -> Any:
            self.calls.append(None)
            raise Dhis2TimeoutError("timed out", details={})

    context = make_run_context(package_of(_manifest(), _payload()), client=Broken(make_target()))

    finding = judge(context, _subject(), _absent_baseline())

    assert finding.verdict == VERDICT_UNKNOWN
    assert finding.present_now is None
    assert "读回失败" in (finding.detail or "")
    assert client.calls == []


# --- 入口：按幂等键确认，再对账 ---------------------------------------------


def test_s1_can_confirm_an_execution_by_its_idempotency_key(harness: Any) -> None:
    """DX-EXE-036 / AC-EXE-05：重试 COMMIT 之前，S1 唯一的确认路径是幂等键查状态。"""
    seed_environment(harness)
    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key=ENVIRONMENT,
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            operator="ops-1",
            idempotency_key="idem-confirm",
        )
        execution_id = execution.id
        steps.set_status(session, execution, ExecutionStatus.NEEDS_MANUAL_CHECK, detail="没有拿到响应")

    info = get_execution_by_key("idem-confirm")

    assert info is not None
    assert info.execution_id == execution_id
    assert info.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert info.applied is None, "待核实的执行不能被读成未生效"
    assert get_execution_by_key("idem-never-used") is None


def test_reconcile_lets_s1_ask_did_it_apply_for_each_of_the_three_answers(harness: Any) -> None:
    """AC-EXE-05：同一个幂等键对应的执行，对账给出 APPLIED / NOT_APPLIED / UNKNOWN 与证据。"""
    seed_environment(harness)
    artifact_id = register_package_artifact(_payload(), _manifest())
    package = package_from_artifact(artifact_id)

    applied = _reconcile_with(artifact_id, package, {("dataElement", UID): {"id": UID, "name": "Weight", "shortName": "Wt"}})
    absent = _reconcile_with(artifact_id, package, {})
    unreadable = _reconcile_with(artifact_id, package, {}, read_fails=True)

    assert applied.outcome is ReconcileOutcome.APPLIED
    assert applied.evidence["verdicts"] == {"APPLIED": 1}
    assert absent.outcome is ReconcileOutcome.NOT_APPLIED
    assert absent.evidence["objects"][0]["detail"] == "对象不存在，与提交前一致"
    assert unreadable.outcome is ReconcileOutcome.UNKNOWN
    assert unreadable.evidence["objects"][0]["verdict"] == VERDICT_UNKNOWN
    # 证据里看得见用到了哪些产物——人工核实要能复核而不是重跑
    assert applied.evidence["artifacts"]["payloadArtifactId"] == artifact_id
    assert applied.evidence["artifacts"]["snapshotArtifactId"]


def _reconcile_with(
    artifact_id: str,
    package: Any,
    objects: dict[tuple[str, str], dict[str, Any]],
    *,
    read_fails: bool = False,
) -> Any:
    """跑一次真实的 ``reconcile``：执行行与 Snapshot 都在库里，只把实例换成假的。"""
    target = make_target()
    client = UnreadableClient(target) if read_fails else FakeClient(target, objects=objects)
    context = make_run_context(package, client=client, target=target)
    snapshot_artifact_id, _row_id = snapshot_module.save(
        context,
        {
            "schemaVersion": 1,
            "kind": "SNAPSHOT",
            "snapshotKind": "PRE_COMMIT",
            "environmentKey": ENVIRONMENT,
            "objects": [{"dhis2Type": "dataElement", "uid": UID, "present": False, "content": None, "contentHash": None}],
            "payloadObjects": [],
            "objectCount": 1,
        },
        execution_id=None,
    )

    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key=ENVIRONMENT,
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            payload_artifact_id=artifact_id,
            payload_hash=package.artifact_hash,
            operator="ops-1",
            idempotency_key=f"idem-reconcile-{len(objects)}-{read_fails}",
        )
        execution.snapshot_artifact_id = snapshot_artifact_id
        execution_id = execution.id
        steps.set_status(session, execution, ExecutionStatus.NEEDS_MANUAL_CHECK, detail="提交后没有拿到响应")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(reconcile_module, "load_context", lambda *_args, **_kwargs: context)
    try:
        return reconcile_api(execution_id)
    finally:
        monkey.undo()


def test_reconcile_is_read_only_and_leaves_the_lock_alone(harness: Any) -> None:
    """NFR-REL-003：对账不写状态、不释放锁、不重新提交——它只是把事实读回来。"""
    from factory_dhis2.envlock import manager as envlock

    seed_environment(harness)

    artifact_id = register_package_artifact(_payload(), _manifest())
    package = package_from_artifact(artifact_id)
    context = make_run_context(
        package, objects={("dataElement", UID): {"id": UID, "name": "Weight", "shortName": "Wt"}}
    )
    snapshot_artifact_id, _row = snapshot_module.save(
        context,
        {
            "schemaVersion": 1,
            "kind": "SNAPSHOT",
            "snapshotKind": "PRE_COMMIT",
            "environmentKey": ENVIRONMENT,
            "objects": [{"dhis2Type": "dataElement", "uid": UID, "present": False, "content": None}],
            "payloadObjects": [],
            "objectCount": 1,
        },
    )
    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key=ENVIRONMENT,
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            payload_artifact_id=artifact_id,
            payload_hash=package.artifact_hash,
            operator="ops-1",
            idempotency_key="idem-read-only",
        )
        execution.snapshot_artifact_id = snapshot_artifact_id
        execution_id = execution.id
        steps.set_status(session, execution, ExecutionStatus.NEEDS_MANUAL_CHECK, detail="没有拿到响应")
        envlock.acquire(session, ENVIRONMENT, execution_id, operator="ops-1", note="COMMIT")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(reconcile_module, "load_context", lambda *_args, **_kwargs: context)
    try:
        first = reconcile_api(execution_id)
        again = reconcile_api(execution_id)
    finally:
        monkey.undo()

    assert first.outcome is again.outcome is ReconcileOutcome.APPLIED
    assert context.client.write_calls == [], "对账不重新提交任何东西"
    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) == execution_id, "对账不能替人工核实释放锁"
        row = session.get(steps.ExecutionRow, execution_id)
        assert row is not None
        assert row.status == ExecutionStatus.NEEDS_MANUAL_CHECK.value
        assert row.applied is None
