"""部署后验证（DX-EXE-040…042、AC-EXE-06、DX-AC-10）。

两条主线都要成立：

1. **比对口径**——UPDATE 只比受控字段、CREATE 比载荷里的全部字段、引用与共享单独核对；
   环境里多出来的东西与 ``lastUpdated`` 都不算差异（DX-EXE-040）；
2. **失败不修正**——差异与延后表达式失败都只进报告，一次写调用都不发（DX-BR-09、DX-AC-10）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import unit_of_work

from factory_dhis2.artifacts import read_json
from factory_dhis2.enums import ExecutionStatus
from factory_dhis2.execution import post_verify, steps
from factory_dhis2.execution.post_verify import post_verify as post_verify_call
from factory_dhis2.execution.post_verify import verify_payload
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

DATA_ELEMENT_UID = "de000000001"
OPTION_SET_UID = "os0000000001"


def _manifest(
    *,
    controlled: dict[str, list[str]] | None = None,
    expressions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return build_manifest(
        objects=[
            {
                "businessKey": "bk.weight",
                "dhis2Type": "dataElement",
                "uid": DATA_ELEMENT_UID,
                "disposition": "UPDATE",
            },
            {
                "businessKey": "bk.yesno",
                "dhis2Type": "optionSet",
                "uid": OPTION_SET_UID,
                "disposition": "CREATE",
            },
        ],
        uid_map=[
            {
                "businessKey": "bk.weight",
                "uid": DATA_ELEMENT_UID,
                "dhis2Type": "dataElement",
                "disposition": "UPDATE",
            },
            {
                "businessKey": "bk.yesno",
                "uid": OPTION_SET_UID,
                "dhis2Type": "optionSet",
                "disposition": "CREATE",
            },
        ],
        dispositions={"bk.weight": "UPDATE", "bk.yesno": "CREATE"},
        controlled_fields=controlled if controlled is not None else {"bk.weight": ["name", "shortName"]},
        expressions=expressions,
    )


def _payload() -> dict[str, Any]:
    return {
        "dataElements": [
            {
                "id": DATA_ELEMENT_UID,
                "name": "Weight",
                "shortName": "Wt",
                "valueType": "NUMBER",
                "code": "DE_WEIGHT",
                "optionSet": {"id": OPTION_SET_UID},
            }
        ],
        "optionSets": [{"id": OPTION_SET_UID, "name": "YesNo", "version": "1"}],
    }


def _context(
    objects: dict[tuple[str, str], dict[str, Any]],
    *,
    controlled: dict[str, list[str]] | None = None,
    expressions: list[dict[str, Any]] | None = None,
    expression_verdicts: dict[str, Any] | None = None,
) -> Any:
    package = package_of(_manifest(controlled=controlled, expressions=expressions), _payload())
    return make_run_context(
        package,
        objects=objects,
        expression_verdicts=expression_verdicts or {},
    )


# --- DX-EXE-040：受控字段 ---------------------------------------------------


def test_update_object_mismatch_is_detected_on_controlled_field() -> None:
    """环境的 name 与载荷不同 → 差异（AC-EXE-06 的最小实例）。"""
    context = _context(
        {
            ("dataElement", DATA_ELEMENT_UID): {
                "id": DATA_ELEMENT_UID,
                "name": "Body weight",  # 被人改过
                "shortName": "Wt",
                "valueType": "NUMBER",
                "code": "DE_WEIGHT",
                "lastUpdated": "2026-01-05T00:00:00.000",
            },
            ("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 1},
        }
    )

    result = verify_payload(context)

    assert not result.consistent
    assert [row["field"] for row in result.diff_list] == ["name"]
    assert result.diff_list[0]["kind"] == post_verify.KIND_FIELD_MISMATCH
    assert result.diff_list[0]["businessKey"] == "bk.weight"
    assert result.issues and result.issues[0].rule_id == "DX-EXE-040"
    assert result.issues[0].object_key == "bk.weight"


def test_non_controlled_fields_are_ignored_for_update() -> None:
    """DX-EXE-040 只比受控字段：valueType / code 被人在环境里改了，不是本次部署的差异。"""
    context = _context(
        {
            ("dataElement", DATA_ELEMENT_UID): {
                "id": DATA_ELEMENT_UID,
                "name": "Weight",
                "shortName": "Wt",
                "valueType": "TEXT",  # 非受控
                "code": "SOMEONE_ELSE",  # 非受控
                "lastUpdated": "2026-01-05T00:00:00.000",
            },
            ("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 1},
        }
    )

    assert verify_payload(context).consistent


def test_create_object_is_compared_on_every_payload_field() -> None:
    """CREATE 对象比载荷里出现的全部字段（DX-EXE-040）：version 不同就是差异。"""
    context = _context(
        {
            ("dataElement", DATA_ELEMENT_UID): {
                "id": DATA_ELEMENT_UID,
                "name": "Weight",
                "shortName": "Wt",
                "valueType": "NUMBER",
                "code": "DE_WEIGHT",
                "optionSet": {"id": OPTION_SET_UID},
            },
            ("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 7},
        }
    )

    result = verify_payload(context)

    assert not result.consistent
    fields = {row["field"] for row in result.diff_list}
    assert fields == {"version"}
    assert result.diff_list[0]["dhis2Type"] == "optionSet"


def test_missing_object_after_commit_is_reported() -> None:
    context = _context(
        {("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 1}}
    )

    result = verify_payload(context)

    kinds = {row["kind"] for row in result.diff_list}
    assert post_verify.KIND_MISSING_OBJECT in kinds
    assert not result.consistent


def test_broken_reference_is_reported_as_reference_problem() -> None:
    """引用没落地（环境里指向别的选项集）→ REFERENCE_MISSING。"""
    context = _context(
        {
            ("dataElement", DATA_ELEMENT_UID): {
                "id": DATA_ELEMENT_UID,
                "name": "Weight",
                "shortName": "Wt",
                "optionSet": {"id": "zz000000001", "name": "Something else"},
            },
            ("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 1},
        },
        controlled={"bk.weight": ["name", "shortName", "optionSet"]},
    )

    result = verify_payload(context)

    kinds = {row["kind"] for row in result.diff_list}
    assert post_verify.KIND_REFERENCE_MISSING in kinds


def test_sharing_settings_are_checked_even_though_they_are_volatile() -> None:
    """``publicAccess`` 在内容哈希的排除表里，但共享设置必须仍然被核对（DX-EXE-040）。"""
    package = package_of(
        build_manifest(
            objects=[
                {
                    "businessKey": "bk.constant",
                    "dhis2Type": "constant",
                    "uid": "co0000000001",
                    "disposition": "CREATE",
                }
            ],
            uid_map=[
                {
                    "businessKey": "bk.constant",
                    "uid": "co0000000001",
                    "dhis2Type": "constant",
                    "disposition": "CREATE",
                }
            ],
            dispositions={"bk.constant": "CREATE"},
            controlled_fields={},
        ),
        {
            "constants": [
                {"id": "co0000000001", "name": "Threshold", "publicAccess": "rw", "sharing": {"public": "rw"}}
            ]
        },
    )
    context = make_run_context(
        package,
        objects={
            ("constant", "co0000000001"): {
                "id": "co0000000001",
                "name": "Threshold",
                "publicAccess": "none",
                "sharing": {"public": "none"},
            }
        },
    )

    result = verify_payload(context)

    assert not result.consistent
    assert {row["kind"] for row in result.diff_list} == {post_verify.KIND_SHARING_MISMATCH}


def test_read_back_uses_discovery_scope_and_never_writes() -> None:
    """验证是观察环境：全程 discovery 范围、零写调用（DX-BR-04、DX-AC-10）。"""
    context = _context(
        {
            ("dataElement", DATA_ELEMENT_UID): {
                "id": DATA_ELEMENT_UID,
                "name": "Weight",
                "shortName": "Wt",
            },
            ("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 1},
        }
    )

    result = verify_payload(context)

    assert result.consistent
    assert context.client.write_calls == []
    assert context.client.scopes_used() == {"discovery"}


# --- DX-EXE-041 / DX-AC-10：延后表达式 -------------------------------------


DEFERRED: list[dict[str, Any]] = [
    {
        "businessKey": "bk.rule",
        "field": "condition",
        "validation": "POST_VERIFY",
        "compiled": "V{weight} > 90",
        "references": [],
    }
]


def _manifest_with_rule() -> dict[str, Any]:
    manifest = _manifest(expressions=DEFERRED)
    manifest["objects"].append(
        {
            "businessKey": "bk.rule",
            "dhis2Type": "programRule",
            "uid": "pr000000001",
            "disposition": "CREATE",
        }
    )
    manifest["uidToBusinessKey"].append(
        {
            "businessKey": "bk.rule",
            "uid": "pr000000001",
            "dhis2Type": "programRule",
            "disposition": "CREATE",
        }
    )
    manifest["dispositions"]["bk.rule"] = "CREATE"
    return manifest


def test_deferred_expression_failure_produces_issue_and_no_write() -> None:
    """延后校验的表达式在部署后验证里失败：只报告，不修正（DX-AC-10）。"""
    package = package_of(_manifest_with_rule(), _payload())
    context = make_run_context(
        package,
        objects={
            ("dataElement", DATA_ELEMENT_UID): {
                "id": DATA_ELEMENT_UID,
                "name": "Weight",
                "shortName": "Wt",
            },
            ("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 1},
            ("programRule", "pr000000001"): {"id": "pr000000001", "name": "rule"},
        },
        expression_verdicts={
            "programRules.condition.validate": {"result": False, "message": "unknown variable"}
        },
    )

    result = verify_payload(context)

    assert result.expressions is not None
    assert result.expressions.checked == 1
    assert not result.expressions.ok
    assert not result.consistent
    assert [issue.rule_id for issue in result.issues] == ["DX-EXE-041"]
    assert context.client.write_calls == []


def test_passing_expression_and_fields_are_consistent() -> None:
    package = package_of(_manifest_with_rule(), _payload())
    context = make_run_context(
        package,
        objects={
            ("dataElement", DATA_ELEMENT_UID): {
                "id": DATA_ELEMENT_UID,
                "name": "Weight",
                "shortName": "Wt",
            },
            ("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 1},
            ("programRule", "pr000000001"): {"id": "pr000000001", "name": "rule"},
        },
        expression_verdicts={"programRules.condition.validate": {"result": True}},
    )

    assert verify_payload(context).consistent


# --- 持久化：产物 + POST_COMMIT Snapshot + 审计（DX-EXE-042）----------------


@pytest.fixture
def seeded_package(harness: Any) -> Iterator[tuple[str, Any]]:
    """一个真产物 + 一个用 FakeClient 装配的上下文（不碰 HTTP，也不碰别人的 transport）。"""
    del harness  # 夹具本身负责清库与装对象存储
    artifact_id = register_package_artifact(_payload(), _manifest())
    package = package_from_artifact(artifact_id)
    objects = {
        ("dataElement", DATA_ELEMENT_UID): {
            "id": DATA_ELEMENT_UID,
            "name": "Body weight",
            "shortName": "Wt",
        },
        ("optionSet", OPTION_SET_UID): {"id": OPTION_SET_UID, "name": "YesNo", "version": 1},
    }
    yield artifact_id, (package, objects)


def test_post_verify_persists_report_snapshot_and_audit(seeded_package: Any) -> None:
    artifact_id, (package, objects) = seeded_package
    target = make_target()
    client = FakeClient(target, objects=objects)
    context = make_run_context(package, client=client, target=target)
    seen: dict[str, Any] = {}

    def fake_snapshot_document(run_context: Any, *, cancel: Any = None) -> dict[str, Any]:
        seen["context"] = run_context
        return {
            "schemaVersion": 1,
            "kind": "SNAPSHOT",
            "snapshotKind": "POST_COMMIT",
            "environmentKey": run_context.environment_key,
            "environmentClass": "TEST",
            "dhis2Version": "2.41.10",
            "takenAt": "2026-01-06T00:00:00+00:00",
            "managedScope": [],
            "payloadObjects": [],
            "objects": [
                {
                    "dhis2Type": "dataElement",
                    "uid": DATA_ELEMENT_UID,
                    "present": True,
                    "lastUpdated": "2026-01-06T00:00:00.000",
                    "contentHash": "sha256:post",
                    "content": objects[("dataElement", DATA_ELEMENT_UID)],
                }
            ],
            "objectCount": 1,
        }

    monkey = pytest.MonkeyPatch()
    monkey.setattr(post_verify, "load_context", lambda *args, **kwargs: context)
    monkey.setattr(post_verify, "post_commit_document", fake_snapshot_document)
    try:
        outcome = post_verify_call(
            "env-unit",
            PROJECT,
            artifact_id,
            None,
            operator="ops-1",
            idempotency_key="idem-post-verify-1",
        )
    finally:
        monkey.undo()

    assert outcome.consistent is False
    assert outcome.execution_id is not None
    assert outcome.report_artifact_id is not None
    assert outcome.compared_objects == 2
    document = read_json(outcome.report_artifact_id)
    assert document["kind"] == "POST_VERIFICATION_REPORT"
    assert document["status"] == ExecutionStatus.FAILED.value
    assert document["autoRepairPerformed"] is False
    assert [row["field"] for row in document["diffList"]] == ["name"]
    assert document["artifacts"]["postCommitSnapshot"]

    with unit_of_work() as session:
        row = steps.require(session, str(outcome.execution_id))
        assert row.status == ExecutionStatus.FAILED.value
        assert row.applied is False
        assert row.mode == "POST_VERIFY"
    from factory_foundation.api import AuditQuery, query_audit

    events = query_audit(
        AuditQuery(event_type="dx.post_verify.completed", object_id=str(outcome.execution_id))
    )
    assert events.items, "部署后验证必须留下审计（附录 A）"
    # POST_COMMIT Snapshot 也要留下，否则 diff_snapshot_vs_current 没有“提交后”那一侧
    with unit_of_work() as session:
        from factory_dhis2.models.execution import SnapshotIndexRow

        snapshots = list(
            session.query(SnapshotIndexRow)
            .filter(SnapshotIndexRow.execution_id == str(outcome.execution_id))
            .all()
        )
    assert [item.kind for item in snapshots] == ["POST_COMMIT"]
    # 一次也没有写实例（DX-AC-10）
    assert client.write_calls == []


def test_post_verify_is_idempotent_per_key(seeded_package: Any) -> None:
    """同幂等键的第二次调用拿到同一个结论，不再打一次环境（DX-EXE-004）。"""
    artifact_id, (package, objects) = seeded_package
    target = make_target()
    client = FakeClient(target, objects=objects)
    context = make_run_context(package, client=client, target=target)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(post_verify, "load_context", lambda *args, **kwargs: context)
    monkey.setattr(
        post_verify,
        "post_commit_document",
        lambda run_context, *, cancel=None: {
            "schemaVersion": 1,
            "kind": "SNAPSHOT",
            "snapshotKind": "POST_COMMIT",
            "environmentKey": run_context.environment_key,
            "dhis2Version": "2.41.10",
            "managedScope": [],
            "payloadObjects": [],
            "objects": [],
            "objectCount": 0,
        },
    )
    try:
        first = post_verify_call(
            "env-unit", PROJECT, artifact_id, None, operator="ops-1", idempotency_key="idem-verify-twice"
        )
        calls_before = len(client.calls)
        second = post_verify_call(
            "env-unit", PROJECT, artifact_id, None, operator="ops-1", idempotency_key="idem-verify-twice"
        )
    finally:
        monkey.undo()

    assert second.execution_id == first.execution_id
    assert second.report_artifact_id == first.report_artifact_id
    assert len(client.calls) == calls_before
