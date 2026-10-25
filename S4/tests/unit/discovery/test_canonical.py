"""内容哈希的排除清单（DX-DSC-007、AC-EXE-07 / DX-AC-07）。

哈希是漂移检查、Snapshot 与 Diff 的唯一基准，所以"排除什么"是要害：
少排除一个 ``lastUpdated``，任何人碰一下对象（哪怕什么都没改）都报漂移，漂移检查很快
就被当成噪音；多排除一个 ``id``，把引用换成另一个同名对象就不算变化，于是**真的**漂移
被漏掉——后者比误报危险得多。
"""

from __future__ import annotations

from factory_dhis2.discovery.canonical import changed_fields, normalize_object, object_hash
from factory_dhis2.discovery.service import _references_of
from factory_dhis2.profiles import registry


def profile():
    registry.load_builtin_profiles()
    return registry.profile_for_version("2.41.10")


VOLATILE = ("id", "href", "created", "lastUpdated", "access", "publicAccess")


def test_only_last_updated_changing_does_not_change_the_hash() -> None:
    """AC-EXE-07：仅 lastUpdated 变化的对象不算漂移。"""
    before = {"id": "de1", "code": "W", "name": "体重", "lastUpdated": "2026-01-01T00:00:00.000"}
    after = dict(before, lastUpdated="2026-02-02T10:00:00.000")
    assert object_hash(before, VOLATILE) == object_hash(after, VOLATILE)


def test_a_controlled_field_change_does_change_the_hash() -> None:
    before = {"id": "de1", "code": "W", "name": "体重"}
    after = dict(before, name="体重（公斤）")
    assert object_hash(before, VOLATILE) != object_hash(after, VOLATILE)


def test_a_nested_reference_id_is_part_of_the_content() -> None:
    """DX-DSC-007：``id`` 只在**顶层**排除。

    嵌套层的 ``id`` 是"引用了谁"。把它一并抹掉，"categoryCombo 从 A 换成 B 但名字相同"
    这种改写就不会改变哈希，漂移检查于是漏报——而漏报的后果是按旧认知提交。
    """
    before = {
        "id": "pg1",
        "name": "ANC",
        "categoryCombo": {"id": "cc1", "name": "default"},
        "lastUpdated": "2026-01-01T00:00:00.000",
    }
    after = dict(before, categoryCombo={"id": "cc2", "name": "default"})
    assert object_hash(before, VOLATILE) != object_hash(after, VOLATILE), (
        "引用被换成了另一个对象，哈希必须变"
    )
    normalized = normalize_object(before, VOLATILE)
    assert "id" not in normalized and normalized["categoryCombo"]["id"] == "cc1"


def test_nested_volatile_fields_are_still_dropped_at_every_level() -> None:
    document = {
        "id": "pg1",
        "name": "ANC",
        "programStages": [
            {"id": "ps1", "name": "Visit", "lastUpdated": "2026-01-01T00:00:00.000"},
        ],
    }
    normalized = normalize_object(document, VOLATILE)
    stage = normalized["programStages"][0]
    assert stage == {"id": "ps1", "name": "Visit"}, "lastUpdated 在嵌套层也要排除，id 要保留"


def test_changed_fields_reports_the_field_that_actually_moved() -> None:
    before = {"id": "de1", "name": "A", "lastUpdated": "2026-01-01"}
    after = {"id": "de1", "name": "B", "lastUpdated": "2026-02-02"}
    diff = changed_fields(before, after, VOLATILE)
    assert [item["field"] for item in diff] == ["name"]


def test_changed_fields_can_be_narrowed_to_controlled_fields() -> None:
    """DX-EXE-040：UPDATE 对象只比受控字段，别的属性本来就不归本次部署管。"""
    before = {"id": "de1", "name": "A", "comment": "old"}
    after = {"id": "de1", "name": "A", "comment": "new"}
    assert changed_fields(before, after, VOLATILE, only=["name"]) == []
    assert [item["field"] for item in changed_fields(before, after, VOLATILE)] == ["comment"]


# --- DX-DSC-001：关键引用关系 -------------------------------------------------


def test_references_include_single_objects_arrays_and_member_wrappers() -> None:
    """引用数组与 ``{"members": [...]}`` 包装都要收进来。

    只收顶层 ``{"id": ...}`` 时，``programStages``、``categories`` 这类引用在发现结果里
    根本不存在，S2 要想知道"这个 Program 引用了谁"就只能再读一次实例（DX-DSC-001 明说
    不该如此）。
    """
    row = {
        "id": "pg1",
        "name": "ANC",
        "categoryCombo": {"id": "cc1", "name": "default"},
        "programStages": [{"id": "ps2"}, {"id": "ps1"}, {"id": "ps2"}],
        "dataElementGroupSetSet": {"members": [{"id": "gs1"}]},
        "access": {"read": "user"},
    }
    references = _references_of(row)
    assert references["categoryCombo"] == "cc1"
    assert references["programStages"] == ["ps1", "ps2"]
    assert references["dataElementGroupSetSet"] == ["gs1"]
    assert "access" not in references, "没有 id 的普通嵌套对象不是引用"
