"""对账（DX-EXE-035、AC-EXE-05、NFR-REL-003）。

对账给的是**判断 + 证据**，不是结论的替代品：它必须只读、必须把用到的产物 ID 摆在证据里、
必须在读不回来的时候老实承认 UNKNOWN。三类用例分别守住这三条。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import unit_of_work

from factory_dhis2.enums import ExecutionMode, ExecutionStatus, ReconcileOutcome
from factory_dhis2.execution import reconcile as reconcile_module
from factory_dhis2.execution import steps
from factory_dhis2.execution.reconcile import (
    VERDICT_APPLIED,
    VERDICT_MISMATCH,
    VERDICT_NOT_APPLIED,
    VERDICT_UNKNOWN,
    ObjectFinding,
    Reconciliation,
    index_baseline,
    judge,
    overall,
    reconcile,
)
from tests.unit.execution import (
    PROJECT,
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


def _manifest() -> dict[str, Any]:
    return build_manifest(
        objects=[
            {
                "businessKey": "bk.weight",
                "dhis2Type": "dataElement",
                "uid": UID,
                "disposition": "CREATE",
            },
            {
                "businessKey": "bk.height",
                "dhis2Type": "dataElement",
                "uid": OTHER_UID,
                "disposition": "CREATE",
            },
        ],
        uid_map=[
            {"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "CREATE"},
            {"businessKey": "bk.height", "uid": OTHER_UID, "dhis2Type": "dataElement", "disposition": "CREATE"},
        ],
        dispositions={"bk.weight": "CREATE", "bk.height": "CREATE"},
        controlled_fields={},
    )


def _payload() -> dict[str, Any]:
    return {
        "dataElements": [
            {"id": UID, "name": "Weight", "shortName": "Wt"},
            {"id": OTHER_UID, "name": "Height", "shortName": "Ht"},
        ]
    }


def _subject(uid: str, business_key: str) -> dict[str, Any]:
    return {
        "uid": uid,
        "dhis2_type": "dataElement",
        "business_key": business_key,
        "disposition": "CREATE",
    }


def _context(objects: dict[tuple[str, str], dict[str, Any]]) -> Any:
    return make_run_context(package_of(_manifest(), _payload()), objects=objects)


def _absent_baseline() -> dict[str, Any]:
    return {"present": False, "content": None, "contentHash": None}


# --- 单对象判定 ------------------------------------------------------------


def test_all_objects_absent_is_not_applied() -> None:
    """一个都没建成：与提交前一致 → 未生效（AC-EXE-05 的“未生效”一侧）。"""
    context = _context({})

    findings = [
        judge(context, _subject(UID, "bk.weight"), _absent_baseline()),
        judge(context, _subject(OTHER_UID, "bk.height"), _absent_baseline()),
    ]

    assert [item.verdict for item in findings] == [VERDICT_NOT_APPLIED, VERDICT_NOT_APPLIED]
    assert overall(Reconciliation(findings=findings)) is ReconcileOutcome.NOT_APPLIED


def test_all_objects_present_and_matching_is_applied() -> None:
    context = _context(
        {
            ("dataElement", UID): {"id": UID, "name": "Weight", "shortName": "Wt"},
            ("dataElement", OTHER_UID): {"id": OTHER_UID, "name": "Height", "shortName": "Ht"},
        }
    )

    findings = [
        judge(context, _subject(UID, "bk.weight"), _absent_baseline()),
        judge(context, _subject(OTHER_UID, "bk.height"), _absent_baseline()),
    ]

    assert [item.verdict for item in findings] == [VERDICT_APPLIED, VERDICT_APPLIED]
    assert overall(Reconciliation(findings=findings)) is ReconcileOutcome.APPLIED


def test_some_changed_some_not_is_partial_mismatch() -> None:
    """部分不一致：一个是我们要的样子，另一个根本没建出来。"""
    context = _context({("dataElement", UID): {"id": UID, "name": "Weight", "shortName": "Wt"}})

    findings = [
        judge(context, _subject(UID, "bk.weight"), _absent_baseline()),
        judge(context, _subject(OTHER_UID, "bk.height"), _absent_baseline()),
    ]

    assert {item.verdict for item in findings} == {VERDICT_APPLIED, VERDICT_NOT_APPLIED}
    assert overall(Reconciliation(findings=findings)) is ReconcileOutcome.PARTIAL_MISMATCH


def test_update_object_unchanged_since_snapshot_is_not_applied() -> None:
    """现状 == 提交前就是未生效，即使对象确实存在。"""
    package = package_of(
        build_manifest(
            objects=[
                {
                    "businessKey": "bk.weight",
                    "dhis2Type": "dataElement",
                    "uid": UID,
                    "disposition": "UPDATE",
                }
            ],
            uid_map=[
                {
                    "businessKey": "bk.weight",
                    "uid": UID,
                    "dhis2Type": "dataElement",
                    "disposition": "UPDATE",
                    "code": None,
                }
            ],
            dispositions={"bk.weight": "UPDATE"},
            controlled_fields={"bk.weight": ["name"]},
        ),
        {"dataElements": [{"id": UID, "name": "New name"}]},
    )
    context = make_run_context(
        package,
        objects={("dataElement", UID): {"id": UID, "name": "Old name", "lastUpdated": "2026-01-01T00:00:00.000"}},
    )
    baseline = {
        "present": True,
        "content": {"id": UID, "name": "Old name", "lastUpdated": "2026-01-01T00:00:00.000"},
        "contentHash": "sha256:baseline",
    }

    finding = judge(context, {"uid": UID, "dhis2_type": "dataElement", "business_key": "bk.weight", "disposition": "UPDATE"}, baseline)

    assert finding.verdict == VERDICT_NOT_APPLIED
    assert finding.baseline_hash == "sha256:baseline"
    assert finding.fields  # 载荷与现状的差异也一并给出证据


def test_unreadable_object_is_unknown_not_not_applied() -> None:
    """读不回来 = 信息缺失，不能当成“没生效”——否则一次网络抖动就把结论写死。"""
    from factory_dhis2.errors import Dhis2TimeoutError

    target = make_target()

    class BrokenClient(FakeClient):
        """读不回任何东西的实例：模拟提交后网络中断。"""

        def call(self, *args: Any, **kwargs: Any) -> Any:
            self.calls.append(None)  # 只记录“试过”
            raise Dhis2TimeoutError("timed out", details={})

    context = make_run_context(
        package_of(_manifest(), _payload()),
        client=BrokenClient(target),
        target=target,
    )

    finding = judge(context, _subject(UID, "bk.weight"), _absent_baseline())

    assert finding.verdict == VERDICT_UNKNOWN
    assert overall(Reconciliation(findings=[finding])) is ReconcileOutcome.UNKNOWN


# --- 全量入口：只读、证据带产物 ID ------------------------------------------


def test_reconcile_returns_evidence_with_artifact_ids(harness: Any) -> None:
    """``reconcile`` 的 evidence 里必须能看见用了哪些产物（人工核实要看证据）。"""
    del harness
    artifact_id = register_package_artifact(_payload(), _manifest())
    package = package_from_artifact(artifact_id)
    target = make_target()
    client = FakeClient(
        target,
        objects={
            ("dataElement", UID): {"id": UID, "name": "Weight", "shortName": "Wt"},
            ("dataElement", OTHER_UID): {"id": OTHER_UID, "name": "Height", "shortName": "Ht"},
        },
    )
    context = make_run_context(package, client=client, target=target)
    snapshot_document = {
        "kind": "SNAPSHOT",
        "snapshotKind": "PRE_COMMIT",
        "environmentKey": target.key,
        "objects": [
            {"dhis2Type": "dataElement", "uid": UID, "present": False, "content": None, "contentHash": None},
            {"dhis2Type": "dataElement", "uid": OTHER_UID, "present": False, "content": None, "contentHash": None},
        ],
        "payloadObjects": [],
        "objectCount": 2,
    }

    monkey = pytest.MonkeyPatch()
    monkey.setattr(reconcile_module, "load_context", lambda *args, **kwargs: context)
    try:
        with unit_of_work() as session:
            execution = steps.create_execution(
                session,
                environment_key=target.key,
                project_id=PROJECT,
                mode=ExecutionMode.COMMIT,
                payload_artifact_id=artifact_id,
                payload_hash=package.artifact_hash,
                idempotency_key="idem-reconcile-1",
                operator="ops-1",
            )
            execution_id = execution.id
        from factory_dhis2.execution import snapshot as snapshot_module

        snapshot_artifact_id, _ = snapshot_module.save(
            context, snapshot_document, execution_id=execution_id
        )
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            row.snapshot_artifact_id = snapshot_artifact_id
            steps.set_status(
                session, row, ExecutionStatus.NEEDS_MANUAL_CHECK, detail="提交后没有拿到响应"
            )

        result = reconcile(execution_id)
    finally:
        monkey.undo()

    assert result.outcome is ReconcileOutcome.APPLIED
    assert result.execution_id == execution_id
    evidence = result.evidence
    assert evidence["artifacts"]["payloadArtifactId"] == artifact_id
    assert evidence["artifacts"]["snapshotArtifactId"] == snapshot_artifact_id
    assert evidence["verdicts"] == {"APPLIED": 2}
    assert [item["uid"] for item in evidence["objects"]] == [UID, OTHER_UID]

    # 对账不写任何东西：状态仍然是 NEEDS_MANUAL_CHECK，锁也没被碰过
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        assert row.status == ExecutionStatus.NEEDS_MANUAL_CHECK.value
        assert client.write_calls == []


def test_reconcile_of_execution_without_payload_is_unknown(harness: Any) -> None:
    del harness
    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key="env-unit",
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            operator="ops-1",
            idempotency_key="idem-reconcile-2",
        )
        execution_id = execution.id

    result = reconcile(execution_id)

    assert result.outcome is ReconcileOutcome.UNKNOWN
    assert "没有载荷产物" in result.evidence["notes"][0]


def test_index_baseline_keeps_only_addressable_entries() -> None:
    notes: list[str] = []
    document = {
        "snapshotKind": "PRE_COMMIT",
        "objects": [{"uid": UID, "dhis2Type": "dataElement", "present": True}, "junk"],
    }

    index = index_baseline(document, notes)

    assert list(index) == [UID]
    assert notes == ["比对基准是 PRE_COMMIT Snapshot"]


def test_missing_snapshot_is_recorded_as_missing_baseline(harness: Any) -> None:
    """没有基准就答不了“有没有变化”，这一点要写进证据而不是被忽略。"""
    del harness
    artifact_id = register_package_artifact(_payload(), _manifest())
    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key="env-unit",
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            payload_artifact_id=artifact_id,
            operator="ops-1",
            idempotency_key="idem-reconcile-3",
        )
        execution_id = execution.id

    package = package_from_artifact(artifact_id)
    context = make_run_context(package, objects={})
    monkey = pytest.MonkeyPatch()
    monkey.setattr(reconcile_module, "load_context", lambda *args, **kwargs: context)
    try:
        result = reconcile(execution_id)
    finally:
        monkey.undo()

    # 基准缺失要写在证据里，而不是悄悄按“没有差异”处理
    assert any("没有提交前 Snapshot" in note for note in result.evidence["notes"])
    assert result.outcome is ReconcileOutcome.NOT_APPLIED


def test_object_finding_document_shape() -> None:
    finding = ObjectFinding(
        dhis2_type="dataElement",
        uid=UID,
        business_key="bk.weight",
        disposition="CREATE",
        verdict=VERDICT_MISMATCH,
    )

    document = finding.as_document()

    assert document["dhis2Type"] == "dataElement"
    assert document["verdict"] == "PARTIAL_MISMATCH"
    assert document["presentNow"] is None
