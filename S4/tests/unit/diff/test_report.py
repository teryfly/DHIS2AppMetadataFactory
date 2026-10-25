"""Diff 的行计算与 DIFF_REPORT 产物（DX-DIFF-001、总体 SRS 15.5）。

``diff.report`` 是两类对比（提交前 ↔ 提交后、同一版本在两个环境）**共用的**那套机器，所以
这里断言的每一条都不是某个入口的细节，而是「同一个对象在两份报告里必须是同一个结论」这件事：

* 行的键是 ``(dhis2Type, UID)``，摘要的计数必须与行数一致（摘要与行漂移的报告比没有报告更糟）；
* 字段级明细只认非易变字段：2.41 那 16 个易变/派生字段（``lastUpdated``、``created``、
  ``href``、``id``、``access``……）单独变化必须显示为不变，否则任何人碰一下对象就能让整份
  报告变成「全部更新」（DX-DSC-007、AC-EXE-07）；
* 只被引用的对象显示为「仅引用」，因为本次部署没有改变它（DX-DIFF-001 的第四种展示）；
* 产物经 S0 登记并带来源 Snapshot，事后能原样读回（DX-ART-005、FD-ART-008）。

纯函数（行与摘要）与需要落库的两处（产物登记、Snapshot 索引的「最近一份」）都在本文件：它们
回答的是同一个问题——报告里的结论是不是真的来自这两份 Snapshot。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from factory_foundation.api import NotFoundError, TransactionMisuseError, unit_of_work

from factory_dhis2.artifacts import read_json, read_metadata
from factory_dhis2.diff import report
from factory_dhis2.discovery.canonical import object_hash
from factory_dhis2.enums import Disposition, ObjectChangeKind
from factory_dhis2.execution.snapshot import KIND_POST_COMMIT, KIND_PRE_COMMIT
from tests.unit.diff import (
    BASE,
    PROFILE,
    PROJECT,
    STAMP,
    add_snapshot_index,
    content_of,
    entry_of,
    payload_object,
)
from tests.unit.execution import register_json_artifact

pytestmark = pytest.mark.usefixtures("harness")

UID = "de000000001"
OTHER_UID = "de000000002"
NEW_UID = "de0000000003"


def _content(**changes: Any) -> dict[str, Any]:
    return content_of(UID, code="WEIGHT", **changes)


def _entry(
    uid: str = UID,
    *,
    dhis2_type: str = "dataElement",
    present: bool = True,
    content: dict[str, Any] | None = None,
    business_key: str | None = None,
) -> dict[str, Any]:
    """本文件统一用 ``_content`` 作默认内容，这样「不传内容」才真的表示两侧内容一致。"""
    return entry_of(
        uid,
        dhis2_type=dhis2_type,
        present=present,
        content=_content(id=uid) if content is None and present else content,
        business_key=business_key,
    )


def _row(
    baseline: dict[str, Any] | None,
    current: dict[str, Any] | None,
    *,
    disposition: str | None = None,
    dhis2_type: str = "dataElement",
    uid: str = UID,
) -> dict[str, Any]:
    return report.object_row(
        f"{dhis2_type}/{uid}", baseline, current, profile=PROFILE, disposition=disposition
    )


# --- 键与索引 ---------------------------------------------------------------


def test_an_entry_is_keyed_by_type_and_uid_so_one_uid_in_two_types_stays_two_objects() -> None:
    """DX-DIFF-001：对象行的键是 (dhis2Type, UID)；同一个 UID 在两个类型里是两个对象，没有 UID 的条目不进对比。"""
    document = {
        "objects": [
            {"uid": UID, "dhis2Type": "dataElement", "present": True},
            {"uid": UID, "dhis2Type": "categoryCombo", "present": True},
            {"uid": None, "dhis2Type": "dataElement"},
            {"dhis2Type": "dataElement", "present": True},
            "不是一条对象",
        ]
    }

    entries = report.entries_of(document)

    assert set(entries) == {f"dataElement/{UID}", f"categoryCombo/{UID}"}
    assert report.entry_key(entries[f"dataElement/{UID}"]) == f"dataElement/{UID}"
    assert report.entries_of(None) == {}
    assert report.entries_of({"objects": None}) == {}


def test_dispositions_come_from_payload_objects_and_the_later_document_wins() -> None:
    """DX-DIFF-001：处置只来自 Snapshot 的 payloadObjects——两侧都声明时以对比的另一侧为准。"""
    baseline = {
        "payloadObjects": [
            payload_object(UID, disposition=Disposition.CREATE.value),
            payload_object(OTHER_UID, business_key="bk.height", disposition=Disposition.REUSE.value),
            payload_object(UID, dhis2_type="program", disposition="UPDATE"),
            {"uid": None, "dhis2Type": "dataElement", "disposition": "CREATE"},
            {"uid": "noDispo", "dhis2Type": "dataElement"},
            "不成形的一行",
        ]
    }
    current = {
        "payloadObjects": [payload_object(UID, disposition=Disposition.REFERENCE.value)]
    }

    assert report.disposition_map(baseline, current) == {
        f"dataElement/{UID}": Disposition.REFERENCE.value,
        f"dataElement/{OTHER_UID}": Disposition.REUSE.value,
        f"program/{UID}": "UPDATE",
    }
    assert report.disposition_map(None) == {}


# --- 四种展示 ---------------------------------------------------------------


def test_an_object_that_only_exists_now_is_created() -> None:
    """DX-DIFF-001：基准侧没有、现在有了就是「新建」，两侧的哈希都留在行里供事后核对。"""
    row = _row(_entry(UID, present=False), _entry(UID), disposition="CREATE")

    assert row["kind"] == ObjectChangeKind.CREATED.value
    assert row["changedFields"] == []
    assert row["presentInBaseline"] is False
    assert row["presentInCurrent"] is True
    assert row["baselineContentHash"] is None
    assert row["currentContentHash"] == object_hash(_content(), PROFILE.volatile_fields)
    assert row["disposition"] == "CREATE"


def test_a_missing_baseline_entry_and_an_absent_object_are_the_same_thing() -> None:
    """DX-DIFF-001：清单里根本没这个对象与 present=false，在展示上是同一件事。"""
    from_list = _row(None, _entry(UID))
    from_snapshot = _row(_entry(UID, present=False), _entry(UID))

    assert from_list["kind"] == from_snapshot["kind"] == ObjectChangeKind.CREATED.value
    assert from_list["presentInBaseline"] is False


def test_an_object_that_is_gone_now_is_an_update_that_says_so() -> None:
    """DX-DIFF-001：SRS 只给四种展示，「消失了」仍是一次更新，用存在性字段把话说清楚。"""
    row = _row(_entry(UID), _entry(UID, present=False))

    assert row["kind"] == ObjectChangeKind.UPDATED.value
    assert row["changedFields"] == [{"field": report.PRESENCE_FIELD, "from": True, "to": False}]
    assert row["presentInBaseline"] is True and row["presentInCurrent"] is False
    assert "已不存在" in str(row["note"])


def test_an_object_on_neither_side_is_unchanged_with_a_reason() -> None:
    """DX-DIFF-001：两侧都没有的对象不编造差异，但要把「两侧都没有」说在前面。"""
    row = _row(_entry(UID, present=False), _entry(UID, present=False))

    assert row["kind"] == ObjectChangeKind.UNCHANGED.value
    assert row["changedFields"] == []
    assert "两侧都没有" in str(row["note"])


def test_a_field_that_really_changed_is_reported_at_field_level() -> None:
    """DX-DIFF-001 的「更新（字段级）」：name 变了就是变了，并说清改了哪个字段、从什么到什么。"""
    row = _row(_entry(UID), _entry(UID, content=_content(name="体重（公斤）")))

    assert row["kind"] == ObjectChangeKind.UPDATED.value
    assert row["changedFields"] == [{"field": "name", "from": "体重", "to": "体重（公斤）"}]


def test_an_identical_object_is_unchanged_even_between_two_snapshots() -> None:
    """DX-DIFF-001：内容一致就不该进差异——报告的价值在于它只说真的变了的东西。"""
    row = _row(_entry(UID), _entry(UID, content=dict(reversed(list(_content().items())))))

    assert row["kind"] == ObjectChangeKind.UNCHANGED.value
    assert row["changedFields"] == []
    assert row["baselineContentHash"] == row["currentContentHash"]


def test_a_referenced_object_shows_as_reference_only_rather_than_as_ours() -> None:
    """DX-DIFF-001 的第四种：内容没变但对象只是被引用，本次部署没有改变它。"""
    for referenced in sorted(report.REFERENCE_DISPOSITIONS):
        assert _row(_entry(UID), _entry(UID), disposition=referenced)["kind"] == (
            ObjectChangeKind.REFERENCE_ONLY.value
        )
    # CREATE / UPDATE / DEPRECATED 不是「仅引用」：它们本来就在本次部署的管辖之内
    for owned in (Disposition.CREATE.value, Disposition.UPDATE.value, Disposition.DEPRECATED.value):
        assert _row(_entry(UID), _entry(UID), disposition=owned)["kind"] == (
            ObjectChangeKind.UNCHANGED.value
        )
    assert _row(_entry(UID), _entry(UID))["kind"] == ObjectChangeKind.UNCHANGED.value


# --- 易变字段 ---------------------------------------------------------------


def test_the_shipped_2_41_volatile_list_is_the_one_the_diff_uses() -> None:
    """DX-DSC-007：排除表来自随包发布的版本数据（16 个字段），不是代码里写死的一串名字。"""
    assert len(PROFILE.volatile_fields) == 16
    assert {"lastUpdated", "created", "href", "id", "access"} <= set(PROFILE.volatile_fields)
    # 受控字段绝不在排除表里：它们变了就是真的变了
    assert "name" not in PROFILE.volatile_fields
    assert "code" not in PROFILE.volatile_fields


def test_flipping_every_volatile_field_is_not_a_change() -> None:
    """DX-DIFF-001、AC-EXE-07：16 个易变字段整体刷新也不构成变化，否则一次无关写入就能伪造差异。"""
    volatile_only = {
        **{name: f"volatile-{index}" for index, name in enumerate(PROFILE.volatile_fields)},
        "code": "WEIGHT",
        "name": "体重",
    }

    row = _row(_entry(UID), _entry(UID, content=volatile_only))

    assert row["kind"] == ObjectChangeKind.UNCHANGED.value
    assert row["changedFields"] == []
    assert (
        report.changed_fields(
            _content(), volatile_only, dhis2_type="dataElement", profile=PROFILE
        )
        == []
    )


def test_a_volatile_field_nested_in_the_content_is_not_a_change_either() -> None:
    """DX-DSC-007：排除表在嵌套结构里同样生效，否则 attributeValues 的取数时刻会淹没整份报告。"""
    before = _entry(
        UID,
        content=_content(
            attributeValues=[
                {"attribute": "sex", "value": "all", "lastUpdated": STAMP},
                {"attribute": "age", "value": "15", "created": STAMP},
            ]
        ),
    )
    after = _entry(
        UID,
        content=_content(
            attributeValues=[
                {"attribute": "age", "value": "15", "created": "2026-09-09T09:09:09.000"},
                {"attribute": "sex", "value": "all", "lastUpdated": "2026-09-09T09:09:09.000"},
            ]
        ),
    )

    assert _row(before, after)["kind"] == ObjectChangeKind.UNCHANGED.value


def test_a_volatile_key_inside_a_nested_object_is_skipped_while_recursing() -> None:
    """DX-DSC-007：递归比较时同样跳过易变键——嵌套的 ``lastUpdated`` 不是本次部署改的东西。"""
    differences = report.changed_fields(
        {"data": {"name": "体重", "lastUpdated": STAMP, "href": "x"}},
        {"data": {"name": "体重", "lastUpdated": "2026-09-09T09:09:09.000", "href": "y"}},
        dhis2_type="dataElement",
        profile=PROFILE,
    )
    assert differences == []

    nested = report.changed_fields(
        {"data": {"name": "体重", "lastUpdated": STAMP}},
        {"data": {"name": "身高", "lastUpdated": "2026-09-09T09:09:09.000"}},
        dhis2_type="dataElement",
        profile=PROFILE,
    )
    assert nested == [{"field": "data.name", "from": "体重", "to": "身高"}]


def test_a_list_element_that_really_changed_is_reported_once_for_the_list() -> None:
    """DX-DIFF-001：列表按内容比较——顺序不同不算变化，成员真的多了一个才算。"""
    reordered = _row(
        _entry(UID, content=_content(categories=[{"id": "a"}, {"id": "b"}])),
        _entry(UID, content=_content(categories=[{"id": "b"}, {"id": "a"}])),
    )
    real_change = _row(
        _entry(UID, content=_content(categories=[{"id": "a"}])),
        _entry(UID, content=_content(categories=[{"id": "a"}, {"id": "c"}])),
    )

    assert reordered["kind"] == ObjectChangeKind.UNCHANGED.value
    assert real_change["kind"] == ObjectChangeKind.UPDATED.value
    assert [item["field"] for item in real_change["changedFields"]] == ["categories"]


def test_nested_objects_are_compared_leaf_by_leaf_with_a_dotted_path() -> None:
    """DX-DIFF-001 的字段级明细：嵌套对象按点分路径报，只报真的变了的那一片。"""
    differences = report.changed_fields(
        {"data": {"name": "体重", "form": "number"}, "code": "WEIGHT"},
        {"data": {"name": "身高", "form": "number"}, "code": "WEIGHT"},
        dhis2_type="dataElement",
        profile=PROFILE,
    )

    assert differences == [{"field": "data.name", "from": "体重", "to": "身高"}]


def test_content_that_is_not_an_object_is_compared_as_a_whole() -> None:
    """DX-DIFF-001：DataStore 之类的值是裸 JSON，此时字段级明细以 DHIS2 类型为字段名。"""
    assert (
        report.changed_fields([1, 2], [1, 2], dhis2_type="dataValueSet", profile=PROFILE) == []
    )
    differences = report.changed_fields([1, 2], [1, 3], dhis2_type="dataValueSet", profile=PROFILE)

    assert differences == [{"field": "dataValueSet", "from": [1, 2], "to": [1, 3]}]


def test_a_field_that_appeared_or_disappeared_is_a_change_but_two_empties_are_not() -> None:
    """DX-DIFF-001：一侧多出/少掉一个字段是变化；``1`` 与 ``"1"`` 这类 JSON 形态差异不是。"""
    added = report.changed_fields(
        {"name": "体重"},
        {"name": "体重", "description": "成人体重"},
        dhis2_type="dataElement",
        profile=PROFILE,
    )
    assert added == [{"field": "description", "from": None, "to": "成人体重"}]
    assert (
        report.changed_fields(
            {"name": "体重"},
            {"name": "体重", "description": None},
            dhis2_type="dataElement",
            profile=PROFILE,
        )
        == []
    )
    assert (
        report.changed_fields(
            {"sortOrder": 1}, {"sortOrder": "1"}, dhis2_type="dataElement", profile=PROFILE
        )
        == []
    )


def test_forbidden_fields_of_the_type_are_not_compared() -> None:
    """DX-EXE-040：版本声明该类型不许比较的字段（``id``/``href``/``access``…）同样排除在外。"""
    forbidden = PROFILE.forbidden_fields("dataElement")
    assert forbidden

    differences = report.changed_fields(
        _content(**{field: "before" for field in forbidden}),
        _content(**{field: "after" for field in forbidden}),
        dhis2_type="dataElement",
        profile=PROFILE,
    )

    assert differences == []


# --- 行集合与摘要 -----------------------------------------------------------


def test_rows_cover_the_union_of_both_sides_in_a_stable_order() -> None:
    """DX-DIFF-001：两侧条目的并集逐个比对，行按 (类型, UID) 稳定排序——同一对输入要给同一份报告。"""
    baseline = report.entries_of(
        {
            "objects": [
                _entry(UID),
                _entry(OTHER_UID),
                _entry(UID, dhis2_type="program"),
            ]
        }
    )
    current = report.entries_of(
        {"objects": [_entry(OTHER_UID, content=content_of(OTHER_UID, name="身高"))]}
    )

    rows = report.build_rows(baseline, current, profile=PROFILE)

    keys = [f"{item['dhis2Type']}/{item['uid']}" for item in rows]
    assert keys == sorted(keys)
    assert keys == [f"dataElement/{UID}", f"dataElement/{OTHER_UID}", f"program/{UID}"]
    assert len(rows) == len(set(baseline) | set(current))


def test_the_row_carries_the_business_key_from_whichever_side_knows_it() -> None:
    """DX-DIFF-001：行要能以业务键呈现给设计侧，基准侧没有就从现状侧取，两边都没有就如实为空。"""
    from_baseline = _row(_entry(UID, business_key="bk.weight"), _entry(UID))
    from_current = _row(_entry(UID), _entry(UID, business_key="bk.weight"))
    from_neither = _row(_entry(UID), _entry(UID))

    assert from_baseline["businessKey"] == "bk.weight"
    assert from_current["businessKey"] == "bk.weight"
    assert from_neither["businessKey"] is None


def test_the_summary_counts_are_exactly_the_rows() -> None:
    """DX-DIFF-001：摘要里每种展示的计数之和必须等于行数——摘要与行漂移比没有摘要更糟。"""
    baseline = report.entries_of({"objects": [_entry(UID), _entry(OTHER_UID)]})
    current = report.entries_of(
        {
            "objects": [
                _entry(UID, content=_content(name="身高")),
                _entry(NEW_UID),
            ]
        }
    )
    rows = report.build_rows(
        baseline,
        current,
        profile=PROFILE,
        dispositions={f"dataElement/{NEW_UID}": Disposition.REFERENCE.value},
    )

    summary = report.summary_of(rows)

    kinds = [str(item["kind"]) for item in rows]
    assert summary["objects"] == len(rows) == 3
    assert sum(summary["byKind"].values()) == summary["objects"]
    assert set(summary["byKind"]) == {item.value for item in ObjectChangeKind}
    for kind in summary["byKind"]:
        assert summary["byKind"][kind] == kinds.count(kind)
    assert summary["changedFields"] == sum(len(item["changedFields"]) for item in rows)
    assert summary["changed"] is True


def test_the_summary_only_counts_the_four_displayed_kinds() -> None:
    """DX-DIFF-001：摘要只认那四种展示——一个不属于任何一类的行不会被塞进某个计数里凑数。"""
    rows = [{"kind": "SOMETHING_ELSE", "changedFields": [{"field": "name"}]}]

    summary = report.summary_of(rows)

    assert set(summary["byKind"]) == {item.value for item in ObjectChangeKind}
    assert sum(summary["byKind"].values()) == 0
    assert summary["objects"] == 1, "行还在报告里，只是没有一种展示认领它"
    assert summary["changedFields"] == 1
    # 由 build_rows 产出的行永远落在这四种里，所以上面那种错位不会发生在真实对比中
    built = report.build_rows(
        report.entries_of({"objects": [_entry(UID, present=False)]}),
        report.entries_of({"objects": [_entry(UID)]}),
        profile=PROFILE,
    )
    assert all(str(row["kind"]) in summary["byKind"] for row in built)


def test_an_empty_comparison_says_nothing_changed_rather_than_looking_like_a_failure() -> None:
    """DX-DIFF-001：两侧都没有对象时摘要是「零对象、无变化」——它与「没法比较」是两回事。"""
    summary = report.summary_of(report.build_rows({}, {}, profile=PROFILE))

    assert summary["objects"] == 0
    assert summary["changed"] is False
    assert set(summary["byKind"].values()) == {0}
    assert summary["changedFields"] == 0


# --- 文档与产物 -------------------------------------------------------------


def test_the_document_shape_is_shared_by_both_comparisons() -> None:
    """总体 SRS 15.5：两类对比共用一种文档形状，只靠 ``comparison`` 区分。"""
    rows = report.build_rows(
        report.entries_of({"objects": [_entry(UID)]}),
        report.entries_of({"objects": []}),
        profile=PROFILE,
    )
    summary = report.summary_of(rows)

    document = report.build_document(
        comparison="ENVIRONMENTS",
        project_id=PROJECT,
        environment_keys=["uat", "prod"],
        profile_versions=[PROFILE.profile_version],
        rows=rows,
        summary=summary,
        artifacts={"environmentA": "uat", "environmentB": "prod"},
        blueprint_version="3.1.0",
    )

    assert document["schemaVersion"] == 1
    assert document["kind"] == "DIFF_REPORT"
    assert document["comparison"] == "ENVIRONMENTS"
    assert document["projectKey"] == PROJECT
    assert document["environmentKeys"] == ["uat", "prod"]
    assert document["blueprintVersion"] == "3.1.0"
    assert document["profileVersions"] == [PROFILE.profile_version]
    assert document["objects"] == rows
    assert document["summary"] == summary
    assert document["artifacts"] == {"environmentA": "uat", "environmentB": "prod"}
    assert document["generatedAt"]


def test_saving_registers_a_diff_report_that_reads_back_with_its_sources() -> None:
    """DX-DIFF-001、DX-ART-005：DIFF_REPORT 经 S0 登记，来源是两份 Snapshot，内容能原样读回。"""
    baseline_id = register_json_artifact(
        {"kind": "SNAPSHOT", "snapshotKind": KIND_PRE_COMMIT, "objects": [_entry(UID)]},
        "SNAPSHOT",
        project_id=PROJECT,
    )
    current_id = register_json_artifact(
        {"kind": "SNAPSHOT", "snapshotKind": KIND_POST_COMMIT, "objects": [_entry(UID)]},
        "SNAPSHOT",
        project_id=PROJECT,
    )
    rows = report.build_rows(
        report.entries_of(read_json(baseline_id)),
        report.entries_of(read_json(current_id)),
        profile=PROFILE,
    )
    document = report.build_document(
        comparison="SNAPSHOT_VS_CURRENT",
        project_id=PROJECT,
        environment_keys=["env-unit"],
        profile_versions=[PROFILE.profile_version],
        rows=rows,
        summary=report.summary_of(rows),
        artifacts={"baselineSnapshot": baseline_id, "currentSnapshot": current_id},
    )

    artifact_id = report.save(
        document, project_id=PROJECT, source_artifact_ids=[baseline_id, current_id, ""]
    )

    stored = read_json(artifact_id)
    assert stored["objects"] == rows
    assert stored["summary"]["objects"] == len(stored["objects"])
    metadata = read_metadata(artifact_id)
    assert metadata.type == "DIFF_REPORT"
    assert metadata.creator_subsystem == "S4"
    assert metadata.project_id == PROJECT
    assert sorted(metadata.source_artifact_ids) == sorted([baseline_id, current_id])


def test_a_comparison_without_a_project_is_still_stored_under_a_diff_specific_owner() -> None:
    """DX-DIFF-001：环境级对比可以不属于任何项目，产物仍要有一个合法归属，不能被丢掉。"""
    document = report.build_document(
        comparison="ENVIRONMENTS",
        project_id=None,
        environment_keys=["a", "b"],
        profile_versions=[PROFILE.profile_version],
        rows=[],
        summary=report.summary_of([]),
        artifacts={},
    )

    artifact_id = report.save(document, project_id=None, source_artifact_ids=[])

    assert read_metadata(artifact_id).project_id == "diff-ENVIRONMENTS"
    assert read_json(artifact_id)["environmentKeys"] == ["a", "b"]


def test_storing_the_report_is_refused_inside_an_open_unit_of_work() -> None:
    """00 文档 6.2：产物暂存是外部 I/O，压在事务里必须被拒，而不是悄悄拖住数据库连接。"""
    with unit_of_work(), pytest.raises(TransactionMisuseError):
        report.save(
            {"comparison": "ENVIRONMENTS"}, project_id=PROJECT, source_artifact_ids=[]
        )


# --- 取基准 -----------------------------------------------------------------


def test_the_latest_snapshot_is_the_newest_of_the_requested_kinds() -> None:
    """DX-EXE-022：基准取「该执行最近一份指定用途的 Snapshot」，别的用途与别的执行都不算。"""
    execution_id = "dxexe-diff-latest"
    pre_old = register_json_artifact({"kind": "SNAPSHOT", "objects": []}, "SNAPSHOT")
    pre_new = register_json_artifact({"kind": "SNAPSHOT", "objects": []}, "SNAPSHOT")
    post = register_json_artifact({"kind": "SNAPSHOT", "objects": []}, "SNAPSHOT")
    add_snapshot_index(
        pre_old, environment_key="env-unit", execution_id=execution_id, kind=KIND_PRE_COMMIT, at=BASE
    )
    add_snapshot_index(
        pre_new,
        environment_key="env-unit",
        execution_id=execution_id,
        kind=KIND_PRE_COMMIT,
        at=BASE + timedelta(hours=1),
    )
    add_snapshot_index(
        post,
        environment_key="env-unit",
        execution_id=execution_id,
        kind=KIND_POST_COMMIT,
        at=BASE + timedelta(minutes=1),
    )

    assert report.latest_snapshot_artifact(execution_id, [KIND_PRE_COMMIT]) == pre_new
    assert report.latest_snapshot_artifact(execution_id, [KIND_POST_COMMIT]) == post
    assert report.latest_snapshot_artifact("dxexe-never-ran", [KIND_PRE_COMMIT]) is None


def test_a_missing_snapshot_reference_is_no_baseline_not_a_guessed_one() -> None:
    """DX-EXE-022：没有引用就是「没有基准」，返回 None 而不是猜一份；引用坏了就如实抛错。"""
    assert report.read_snapshot_document(None) is None
    artifact_id = register_json_artifact(
        {"kind": "SNAPSHOT", "objects": [_entry(UID)]}, "SNAPSHOT"
    )

    assert report.read_snapshot_document(artifact_id)["objects"][0]["uid"] == UID

    with pytest.raises(NotFoundError):
        report.read_snapshot_document("dxart00000000000000000missing")

