"""部署后验证的比对口径与读回链路（DX-EXE-040…042、AC-EXE-06、DX-AC-10）。

`test_post_verify.py` 已经证明了主线（受控字段、引用、共享、失败不修正）。这个文件补的是
**口径的边角**：比对口径一旦有两处定义，「对账说已生效」和「验证说有差异」就会各自成立。
所以嵌套列表怎么对齐、空值算不算缺失、``1`` 与 ``"1"`` 是不是同一个事实、清单为空时读回
哪一批——都要在这里被钉住。

另外两条边界同样要被证明：对象是**按 UID 读回**的（不是按名字猜），而延后到此刻的表达式
失败**只报告**，绝不改写提交的结论（D-05、AC-EXE-06）。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import unit_of_work

from factory_dhis2.artifacts import read_json
from factory_dhis2.enums import ExecutionMode, ExecutionStatus, StepStatus
from factory_dhis2.execution import post_verify, steps
from factory_dhis2.execution.post_verify import (
    KIND_FIELD_MISMATCH,
    KIND_MISSING_FIELD,
    KIND_MISSING_OBJECT,
    KIND_REFERENCE_MISSING,
    KIND_SHARING_MISMATCH,
    ObjectVerification,
    VerificationResult,
    check_references,
    check_sharing,
    child_identifier,
    compare_object,
    diff_list_entries,
    diff_value,
    fields_to_compare,
    member_ids,
    objects_to_verify,
    same_scalar,
    sharing_view,
    verify_payload,
)
from factory_dhis2.execution.post_verify import (
    post_verify as post_verify_call,
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

DATA_ELEMENT_UID = "de000000001"
OPTION_SET_UID = "os0000000001"
CATEGORY_UID = "cc0000000001"


def _profile() -> Any:
    return make_target().profile


def _volatile() -> frozenset[str]:
    return frozenset(_profile().volatile_fields)


# --- 标量与空值 --------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "actual", "same"),
    [
        (1, "1", True),
        ("1", 1, True),
        (1.0, 1, True),
        (True, "true", True),
        (False, 0, True),
        (None, "", True),
        (None, 0, False),
        ("Weight", "Weight", True),
        ("Weight", "weight", False),
        ("abc", 1, False),
        (2, "two", False),
    ],
)
def test_json_shape_differences_are_not_content_differences(
    expected: Any, actual: Any, same: bool
) -> None:
    """DX-EXE-040：``1`` 与 ``"1"``、``true`` 与 ``"true"`` 是同一个事实，不是差异。"""
    assert same_scalar(expected, actual) is same


def test_only_a_truly_empty_value_counts_as_absent() -> None:
    """DX-EXE-040：``""`` / ``[]`` / ``None`` 在 DHIS2 的回读里本来就不出现，不能报缺失。"""
    assert post_verify.is_empty(None) is True
    assert post_verify.is_empty("") is True
    assert post_verify.is_empty([]) is True
    assert post_verify.is_empty({}) is True
    assert post_verify.is_empty(0) is False
    assert post_verify.is_empty(False) is False


# --- 字段比对（DX-EXE-040）---------------------------------------------------


def test_a_nested_dict_only_reports_the_keys_the_payload_states() -> None:
    """DX-EXE-040：只比载荷里出现的东西——实例回读得更丰富不是差异。"""
    findings = diff_value(
        "categoryCombo",
        {"id": CATEGORY_UID, "name": "default"},
        {"id": CATEGORY_UID, "name": "default", "isDefault": True, "lastUpdated": "2026-02-02"},
        volatile=_volatile(),
    )

    assert findings == []


def test_a_nested_key_that_should_be_there_but_is_not_is_a_missing_field() -> None:
    """DX-EXE-040：载荷声明了非空值而环境里没有 → MISSING_FIELD，而不是泛泛的「不一致」。"""
    findings = diff_value("translation", {"name": "Weight"}, {"code": "X"}, volatile=_volatile())

    assert [item["kind"] for item in findings] == [KIND_MISSING_FIELD]
    assert findings[0]["field"] == "translation.name"
    assert findings[0]["actual"] is None


def test_an_empty_nested_value_that_the_instance_dropped_is_not_a_difference() -> None:
    """DX-EXE-040：载荷里的空值被实例省掉是常态，不报差异。"""
    assert diff_value("sharing", {"comment": "", "objects": None}, {"public": "none"}, volatile=_volatile()) == []


def test_a_scalar_expected_against_a_dict_actual_is_one_difference_not_a_crash() -> None:
    """DX-EXE-040：形态对不上时给一条差异，比对不能因为实例回错了形状就中断。"""
    findings = diff_value("optionSet", "os0000000001", {"id": OPTION_SET_UID}, volatile=_volatile())

    assert len(findings) == 1
    assert findings[0]["kind"] == KIND_FIELD_MISMATCH
    assert findings[0]["field"] == "optionSet"


def test_a_list_of_scalars_is_compared_as_a_set_not_a_sequence() -> None:
    """DX-EXE-040：纯标量列表按集合比——顺序不是受控事实。"""
    assert diff_value("categories", ["a", "b"], ["b", "a"], volatile=_volatile()) == []
    findings = diff_value("categories", ["a", "b"], ["a", "c"], volatile=_volatile())
    assert [item["field"] for item in findings] == ["categories"]


def test_a_list_expected_against_a_missing_value_is_a_single_difference() -> None:
    """DX-EXE-040：该有列表的地方回成了 ``None``，报一条而不是逐元素报一屏。"""
    findings = diff_list_entries("dataInputPeriods", [{"id": "a"}], None, volatile=_volatile())

    assert len(findings) == 1
    assert findings[0]["expected"] == [{"id": "a"}]


def test_nested_entries_are_aligned_by_identifier_then_compared_field_by_field() -> None:
    """DX-EXE-040：子对象按标识对齐，然后逐字段比，易变字段仍然排除在外。"""
    findings = diff_list_entries(
        "dataElementGroups",
        [{"id": "g1", "name": "Alpha", "lastUpdated": "2020-01-01", "code": "G1"}],
        [{"id": "g1", "name": "Beta", "code": "G1", "lastUpdated": "2026-06-06"}],
        volatile=_volatile(),
    )

    assert [item["field"] for item in findings] == ["dataElementGroups[g1].name"]


def test_alignment_falls_back_to_code_then_name() -> None:
    """DX-EXE-040：没有 id 的子对象用 code/name 对齐，否则每次重读都会被当成「新对象」。"""
    assert child_identifier({"code": "G1", "name": "Alpha"}) == "G1"
    assert child_identifier({"name": "Alpha"}) == "Alpha"
    assert child_identifier({"description": "no identifier"}) is None
    assert child_identifier({"id": "", "name": "Alpha"}) == "Alpha"

    findings = diff_list_entries(
        "operands",
        [{"code": "left", "value": "1"}],
        [{"code": "left", "value": "2"}],
        volatile=_volatile(),
    )
    assert [item["field"] for item in findings] == ["operands[left].value"]


def test_a_reference_only_entry_that_is_not_there_is_a_missing_reference() -> None:
    """DX-EXE-040：载荷里只写 ``{"id": …}`` 的条目没落地 → REFERENCE_MISSING，而不是字段差异。"""
    findings = diff_list_entries("categories", [{"id": CATEGORY_UID}], [], volatile=_volatile())

    assert [item["kind"] for item in findings] == [KIND_REFERENCE_MISSING]


def test_a_scalar_among_dict_entries_is_reported_instead_of_being_skipped() -> None:
    """DX-EXE-040：字典列表里冒出标量时也要报出来，不能静默跳过那一项。"""
    findings = diff_list_entries(
        "owners", [{"id": "admin"}, "stray"], [{"id": "admin"}], volatile=_volatile()
    )

    assert len(findings) == 1
    assert findings[0]["field"] == "owners[1]"
    assert findings[0]["expected"] == "stray"
    assert findings[0]["actual"] is None


def test_volatile_keys_inside_a_child_are_not_compared() -> None:
    """DX-DSC-007：嵌套层里的易变字段同样不参与内容比对。"""
    findings = diff_list_entries(
        "translations",
        [{"id": "t1", "name": "Weight", "createdBy": {"name": "admin"}}],
        [{"id": "t1", "name": "Weight", "createdBy": {"name": "someone else"}}],
        volatile=_volatile(),
    )

    assert findings == []


# --- 引用与共享 --------------------------------------------------------------


def test_references_that_the_instance_did_not_resolve_are_reported() -> None:
    """DX-EXE-040：引用没指向载荷所指的那个 UID，就是部署没生效。"""
    findings = check_references(
        {"categoryCombo": {"id": CATEGORY_UID}, "optionSet": {"id": OPTION_SET_UID}},
        {"categoryCombo": {"id": CATEGORY_UID, "name": "default"}, "optionSet": {"id": "zz000000001"}},
        volatile=_volatile(),
    )

    assert [item["field"] for item in findings] == ["optionSet"]
    assert findings[0]["kind"] == KIND_REFERENCE_MISSING
    assert findings[0]["expected"] == OPTION_SET_UID
    assert findings[0]["actual"] == "zz000000001"
    # 这一层根本没回读出来时交给 MISSING_FIELD 那条路径，这里不重复报一遍引用
    assert check_references({"optionSet": {"id": OPTION_SET_UID}}, {}, volatile=_volatile()) == []


def test_a_reference_collection_is_checked_as_a_collection() -> None:
    """DX-EXE-040：引用集合少了哪几个 UID 要逐列出来，而不是只说「不一致」。"""
    findings = check_references(
        {"dataElementGroups": [{"id": "g1"}, {"id": "g2"}]},
        {"dataElementGroups": [{"id": "g1"}]},
        volatile=_volatile(),
    )

    assert findings[0]["kind"] == KIND_REFERENCE_MISSING
    assert findings[0]["expected"] == ["g1", "g2"]
    assert "g2" in findings[0]["detail"]


def test_an_empty_reference_collection_is_not_compared_as_a_missing_one() -> None:
    """DX-EXE-040：空的引用集合没什么可核对的（实例本来就不回读空集合）。"""
    assert check_references({"groups": []}, {"groups": []}, volatile=_volatile()) == []


def test_sharing_members_are_compared_by_identifier_and_only_in_one_direction() -> None:
    """DX-EXE-040：共享对象少了一个就是没生效；环境里多出来的共享不是本次部署的差异。"""
    findings = check_sharing(
        {"sharing": {"objects": {"u1": "rw", "u2": "rw"}, "public": "read"}},
        {"sharing": {"objects": [{"id": "u1"}], "public": "read"}},
    )

    assert [item["field"] for item in findings] == ["sharing.objects"]
    assert findings[0]["expected"] == ["u1", "u2"]
    assert findings[0]["actual"] == ["u1"]
    assert findings[0]["kind"] == KIND_SHARING_MISMATCH


def test_public_access_that_the_instance_ate_is_reported() -> None:
    """DX-EXE-040：``publicAccess`` 在内容哈希的排除表里，不单独核对就永远无人发现。"""
    findings = check_sharing({"publicAccess": "rw"}, {"publicAccess": "none"})

    assert findings[0]["kind"] == KIND_SHARING_MISMATCH
    assert findings[0]["field"] == "sharing.publicAccess"


def test_a_permission_that_is_not_read_back_at_all_is_reported() -> None:
    """DX-EXE-040：实例压根没回读这一项，也要按「没生效」报出来而不是当成一致。"""
    findings = check_sharing({"access": {"manage": True}}, {})

    assert findings[0]["field"] == "sharing.access.manage"
    assert findings[0]["expected"] is True
    assert findings[0]["actual"] is None


def test_an_empty_permission_the_instance_omits_is_not_a_difference() -> None:
    """DX-EXE-040：空值被省掉是常态——只有非空值缺失才是差异。"""
    assert check_sharing({"externalAccess": ""}, {}) == []


def test_the_sharing_view_normalises_payload_and_instance_shapes() -> None:
    """DX-EXE-040：载荷写 ``access:{read,write}``，实例也可能写 ``access:"rw"``，归一成一个视图。"""
    assert sharing_view({"sharing": {"public": "rw"}, "externalAccess": False}) == {
        "public": "rw",
        "externalAccess": False,
    }
    assert sharing_view({"access": {"read": True, "write": False}}) == {"access.read": True, "access.write": False}
    assert sharing_view({"access": "rw"}) == {"access": "rw"}
    assert sharing_view({"sharing": {"public": "rw"}, "publicAccess": "none"}) == {
        "public": "rw",
        "publicAccess": "none",
    }


def test_member_ids_accepts_the_shapes_dhis2_actually_returns() -> None:
    """DX-EXE-040：共享成员可能是映射、对象列表或裸 ID 列表，判定只有一种。"""
    assert member_ids({"u1": "rw", "u2": "ro"}) == {"u1", "u2"}
    assert member_ids([{"id": "u1"}, {"name": "Two"}, "u3", None]) == {"u1", "Two", "u3"}
    assert member_ids("not a collection") == set()


# --- 受控字段与清单（DX-EXE-040）--------------------------------------------


def test_update_only_compares_the_controlled_fields_of_that_object() -> None:
    """DX-EXE-040：UPDATE 比的是 manifest 的受控字段，且排除易变/禁用字段。"""
    wanted = fields_to_compare(
        "UPDATE",
        "dataElement",
        {"id": DATA_ELEMENT_UID, "name": "Weight", "lastUpdated": "2026-01-01", "href": "x"},
        {"bk.weight": ["name", "lastUpdated", "href", "valueType"]},
        "bk.weight",
        _profile(),
    )

    assert wanted == {"valueType", "name"}


def test_an_empty_controlled_list_claims_no_field_changes() -> None:
    """DX-EXE-040：受控字段清单为空就是「本次不声称改变任何字段」，不编造比较范围。"""
    assert fields_to_compare(
        "UPDATE", "dataElement", {"id": DATA_ELEMENT_UID, "name": "Weight"}, {}, "bk.weight", _profile()
    ) == set()


def test_create_compares_every_field_the_payload_states() -> None:
    """DX-EXE-040：CREATE 对象比载荷里出现的全部字段（id 除外，它由实例持有）。"""
    wanted = fields_to_compare(
        "CREATE",
        "dataElement",
        {"id": DATA_ELEMENT_UID, "name": "Weight", "lastUpdated": "2026-01-01"},
        {"bk.weight": ["name"]},
        "bk.weight",
        _profile(),
    )

    assert wanted == {"name"}


def compare_object_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {"id": DATA_ELEMENT_UID, "name": "Weight", "optionSet": {"id": OPTION_SET_UID}},
        {"id": DATA_ELEMENT_UID, "name": "Weight", "optionSet": {"id": OPTION_SET_UID, "name": "YesNo"}},
    )


def test_compare_object_counts_only_fields_it_actually_compared() -> None:
    """DX-EXE-042：报告里的「比了几个字段」必须是真比了的数量。"""
    expected, actual = compare_object_fixture()
    findings, compared = compare_object(expected, actual, {"name", "optionSet", "formName"}, _profile())

    assert compared == 2
    assert findings == []


def test_compare_object_skips_keys_the_payload_does_not_have() -> None:
    """DX-EXE-040：wanted 里出现但载荷没写的键，不参与比对也不算差异。"""
    findings, compared = compare_object(
        {"name": "Weight"}, {"name": "Weight", "description": "extra"}, {"name", "description"}, _profile()
    )

    assert compared == 1
    assert findings == []


def test_the_object_list_prefers_the_manifest_and_drops_unaddressable_rows() -> None:
    """DX-EXE-040：待验证清单只收 CREATE / UPDATE 且必须按 UID 寻址；REUSE 不是本次改变的。"""
    package = package_of(
        build_manifest(
            objects=[
                {"businessKey": "bk.a", "dhis2Type": "dataElement", "uid": DATA_ELEMENT_UID, "disposition": "CREATE"},
                {"businessKey": "bk.a", "dhis2Type": "dataElement", "uid": DATA_ELEMENT_UID, "disposition": "CREATE"},
                {"businessKey": "bk.b", "dhis2Type": "dataElement", "uid": None, "disposition": "UPDATE"},
                {"businessKey": "bk.c", "dhis2Type": None, "uid": OPTION_SET_UID, "disposition": "UPDATE"},
                {"businessKey": "bk.d", "dhis2Type": "dataElement", "uid": "de000000009", "disposition": "REUSE"},
            ],
            uid_map=[{"businessKey": "bk.a", "uid": DATA_ELEMENT_UID, "dhis2Type": "dataElement"}],
            dispositions={},
            controlled_fields={},
        ),
        {"dataElements": [{"id": DATA_ELEMENT_UID}]},
    )

    listed, _unaddressable = objects_to_verify(package)

    assert [item["uid"] for item in listed] == [DATA_ELEMENT_UID]


def test_the_object_list_falls_back_to_the_uid_map_when_the_manifest_is_silent() -> None:
    """DX-EXE-040：manifest 的 objects 段没给全时退回 uidToBusinessKey + 载荷，口径不变。"""
    package = package_of(
        build_manifest(
            objects=[],
            uid_map=[
                {"businessKey": "bk.a", "uid": DATA_ELEMENT_UID, "dhis2Type": "dataElement", "disposition": "CREATE"},
                {"businessKey": "bk.gone", "uid": "de999999999", "dhis2Type": "dataElement", "disposition": "UPDATE"},
                {"businessKey": "bk.reuse", "uid": OPTION_SET_UID, "dhis2Type": "optionSet", "disposition": "REUSE"},
            ],
            dispositions={},
            controlled_fields={},
        ),
        {"dataElements": [{"id": DATA_ELEMENT_UID, "name": "Weight"}]},
    )

    listed, _unaddressable = objects_to_verify(package)

    assert [(item["uid"], item["disposition"]) for item in listed] == [(DATA_ELEMENT_UID, "CREATE")]


def test_a_controlled_object_without_a_uid_is_reported_not_skipped() -> None:
    """DX-EXE-040：要验证的是**全部** CREATE/UPDATE，寻址不到的必须进报告。

    静默跳过的后果是一份"全部一致"的验证报告，而其中某个声明要创建的对象其实从来没被读回
    核对过——那正是部署后验证存在的理由被掏空的方式。
    """
    package = package_of(
        build_manifest(
            objects=[
                {"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": DATA_ELEMENT_UID, "disposition": "CREATE"},
                {"businessKey": "bk.ghost", "dhis2Type": "dataElement", "disposition": "CREATE"},
            ],
            uid_map=[{"businessKey": "bk.weight", "uid": DATA_ELEMENT_UID, "dhis2Type": "dataElement"}],
            dispositions={"bk.weight": "CREATE", "bk.ghost": "CREATE"},
            controlled_fields={},
        ),
        {"dataElements": [{"id": DATA_ELEMENT_UID}]},
    )

    listed, unaddressable = objects_to_verify(package)
    assert [item["uid"] for item in listed] == [DATA_ELEMENT_UID]
    assert len(unaddressable) == 1, unaddressable
    assert unaddressable[0].rule_id == "DX-EXE-040"
    assert unaddressable[0].is_blocking
    assert unaddressable[0].object_key == "bk.ghost"


# --- 读回与延后表达式 --------------------------------------------------------


def _manifest(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "objects": [
            {"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": DATA_ELEMENT_UID, "disposition": "UPDATE"},
        ],
        "uid_map": [
            {"businessKey": "bk.weight", "uid": DATA_ELEMENT_UID, "dhis2Type": "dataElement", "disposition": "UPDATE"},
        ],
        "dispositions": {"bk.weight": "UPDATE"},
        "controlled_fields": {"bk.weight": ["name"]},
    }
    base.update(changes)
    return build_manifest(**base)


def _payload() -> dict[str, Any]:
    return {"dataElements": [{"id": DATA_ELEMENT_UID, "name": "Weight", "shortName": "Wt"}]}


def _deferred_rule() -> list[dict[str, Any]]:
    return [
        {
            "businessKey": "bk.rule",
            "field": "condition",
            "validation": "POST_VERIFY",
            "compiled": "V{weight} > 90",
            "references": [],
        }
    ]


def test_every_object_is_read_back_by_uid_with_the_full_field_set() -> None:
    """DX-EXE-040：验证是「按 UID 读回」，字段集是要能核对内容的那一份，而不是列表页的薄读。"""
    context = make_run_context(
        package_of(_manifest(), _payload()),
        objects={("dataElement", DATA_ELEMENT_UID): {"id": DATA_ELEMENT_UID, "name": "Weight", "shortName": "Wt"}},
    )

    result = verify_payload(context)

    reads = [item for item in context.client.calls if item.logical_name == "metadata.read.one"]
    assert [(item.dhis2_type, item.path_args["uid"]) for item in reads] == [("dataElement", DATA_ELEMENT_UID)]
    assert reads[0].params["fields"] == "id,code,name,created,lastUpdated,:all"
    assert result.consistent
    assert result.objects[0].present is True
    assert result.objects[0].content["name"] == "Weight"


def test_the_deferred_expression_failure_never_flips_the_commit_result(
    harness: Any,
) -> None:
    """D-05 / AC-EXE-06：延后到此刻的表达式失败只进报告；那次提交是否生效是 COMMIT 自己的事实。"""
    del harness
    manifest = _manifest(expressions=_deferred_rule())
    manifest["objects"].append(
        {"businessKey": "bk.rule", "dhis2Type": "programRule", "uid": "pr000000001", "disposition": "CREATE"}
    )
    manifest["uidToBusinessKey"].append(
        {"businessKey": "bk.rule", "uid": "pr000000001", "dhis2Type": "programRule", "disposition": "CREATE"}
    )
    artifact_id = register_package_artifact(_payload(), manifest)
    package = package_from_artifact(artifact_id)
    client = FakeClient(
        make_target(),
        objects={
            ("dataElement", DATA_ELEMENT_UID): {"id": DATA_ELEMENT_UID, "name": "Weight", "shortName": "Wt"},
            ("programRule", "pr000000001"): {"id": "pr000000001", "name": "rule"},
        },
        expression_verdicts={"programRules.condition.validate": {"result": False, "message": "unknown variable"}},
    )
    context = make_run_context(package, client=client)

    # 被验证的那次 COMMIT 已经给出「已生效」的结论
    with unit_of_work() as session:
        committed = steps.create_execution(
            session,
            environment_key="env-unit",
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            operator="ops-1",
        )
        steps.set_status(session, committed, ExecutionStatus.COMPLETED, applied=True)
        committed_id = committed.id

    monkey = pytest.MonkeyPatch()
    monkey.setattr(post_verify, "load_context", lambda *_args, **_kwargs: context)
    try:
        outcome = post_verify_call(
            "env-unit", PROJECT, artifact_id, committed_id, operator="ops-1", idempotency_key="idem-pv-linked"
        )
    finally:
        monkey.undo()

    assert outcome.consistent is False
    assert [issue.rule_id for issue in outcome.issues] == ["DX-EXE-041"]
    assert client.write_calls == []

    with unit_of_work() as session:
        committed_row = steps.require(session, committed_id)
        assert committed_row.status == ExecutionStatus.COMPLETED.value
        assert committed_row.applied is True, "部署后验证失败不能改写 COMMIT 自己的结论"
        verified_step = next(
            item for item in steps.steps_as_info(session, committed_id) if item.step == "POST_VERIFY"
        )
        assert verified_step.status == StepStatus.FAILED.value
        assert verified_step.detail["consistent"] is False

    report_document = read_json(str(outcome.report_artifact_id))
    assert report_document["verifiedExecutionId"] == committed_id
    assert report_document["expressions"]["checked"] == 1
    assert report_document["artifacts"]["postCommitSnapshot"], "POST_COMMIT Snapshot 是下一次 Diff 的基准"


def test_the_verification_result_document_is_the_diff_list_the_report_shows() -> None:
    """DX-EXE-042：差异清单按类型 + UID + 字段稳定排序，两次跑给的是同一份可读清单。"""
    first = ObjectVerification(
        dhis2_type="optionSet", uid=OPTION_SET_UID, business_key="bk.yesno", disposition="CREATE",
        findings=[{"kind": KIND_FIELD_MISMATCH, "field": "version", "expected": 1, "actual": 2}],
    )
    second = ObjectVerification(
        dhis2_type="dataElement", uid=DATA_ELEMENT_UID, business_key="bk.weight", disposition="UPDATE",
        findings=[
            {"kind": KIND_SHARING_MISMATCH, "field": "sharing.public", "expected": "rw", "actual": "none"},
            {"kind": KIND_MISSING_OBJECT, "field": None, "expected": "对象存在", "actual": None},
        ],
    )
    result = VerificationResult(objects=[second, first])
    # 差异本身还不等于结论：问题清单由 verify_payload 按同一口径生成（DX-EXE-040）
    assert result.consistent is True

    result.issues = [
        post_verify.issue_for(item, finding, artifact_id=None)
        for item in result.objects
        for finding in item.findings
    ]
    document = result.as_document(environment_key="env-unit", phase="POST_VERIFY")

    assert [(row["dhis2Type"], row["field"]) for row in document["diffList"]] == [
        ("dataElement", None),
        ("dataElement", "sharing.public"),
        ("optionSet", "version"),
    ]
    assert document["consistent"] is False
    assert document["comparedObjects"] == 2
    assert document["objects"][0]["businessKey"] == "bk.weight"
    assert "expressions" in document


def test_an_absent_object_is_a_missing_object_finding() -> None:
    """DX-EXE-040：按 UID 读回为空就是「部署后环境里读不到」，这是最硬的一条差异。"""
    context = make_run_context(package_of(_manifest(), _payload()), objects={})

    result = verify_payload(context)

    assert [item["kind"] for item in result.diff_list] == [KIND_MISSING_OBJECT]
    assert result.objects[0].present is False
    assert result.issues[0].description
    assert "部署后在环境中读不到" in result.issues[0].description


def test_cancellation_stops_the_read_back_between_objects() -> None:
    """DX-EXE-040：部署后验证也要能被取消，一千个对象的读回不能停不下来。"""
    from factory_dhis2.errors import Dhis2TimeoutError

    manifest = _manifest()
    for index in range(3):
        uid = f"de0000000{index}"
        manifest["objects"].append(
            {"businessKey": f"bk.{uid}", "dhis2Type": "dataElement", "uid": uid, "disposition": "CREATE"}
        )
        manifest["uidToBusinessKey"].append(
            {"businessKey": f"bk.{uid}", "uid": uid, "dhis2Type": "dataElement", "disposition": "CREATE"}
        )
    package = package_of(
        manifest,
        {"dataElements": [{"id": f"de0000000{index}", "name": "Weight"} for index in range(3)]},
    )
    context = make_run_context(package, objects={})

    class _Cancel:
        def __init__(self) -> None:
            self.seen = 0

        def raise_if_cancelled(self) -> None:
            self.seen += 1
            if self.seen > 1:
                raise Dhis2TimeoutError("cancelled", details={})

    with pytest.raises(Dhis2TimeoutError):
        verify_payload(context, cancel=_Cancel())

    assert len([item for item in context.client.calls if item.logical_name == "metadata.read.one"]) == 1


def test_progress_is_reported_for_each_object_and_for_the_deferred_batch() -> None:
    """DX-EXE-042：验证过程的每一步都能报进度，包括「有多少延后表达式要问实例」。"""
    manifest = _manifest(expressions=_deferred_rule())
    manifest["objects"].append(
        {"businessKey": "bk.rule", "dhis2Type": "programRule", "uid": "pr000000001", "disposition": "CREATE"}
    )
    manifest["uidToBusinessKey"].append(
        {"businessKey": "bk.rule", "uid": "pr000000001", "dhis2Type": "programRule", "disposition": "CREATE"}
    )
    context = make_run_context(
        package_of(manifest, _payload()),
        objects={
            ("dataElement", DATA_ELEMENT_UID): {"id": DATA_ELEMENT_UID, "name": "Weight", "shortName": "Wt"},
            ("programRule", "pr000000001"): {"id": "pr000000001", "name": "rule"},
        },
    )
    seen: list[str] = []

    class _Progress:
        def report(self, *, step: str, message: str | None = None, completed_fraction: float | None = None) -> None:
            del completed_fraction
            seen.append(f"{step}:{message}")

    verify_payload(context, progress=_Progress())

    assert "POST_VERIFY:verified dataElement/de000000001" in seen
    assert any("延后表达式" in item for item in seen)
