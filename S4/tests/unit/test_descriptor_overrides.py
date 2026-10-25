"""类型描述器覆盖（DX-CAP-001、总体 SRS 14.3）：它必须**真的改变**生效的 Schema。

覆盖是"某些对象类型在该版本中的字段差异"。这个机制的失败方式很安静：数据文件被读进
Profile、被写进产物，但没有任何一处按它行事——于是差异照样发生，只是没人看得见。这里
逐条锁住「按下它」的那几步：``object_schema`` 的视图、产物的内容、以及运行期从产物重建
Profile 之后仍然是它。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from factory_dhis2.contract.capability_profile import (
    DiscoveredSchema,
    build_capability_profile_document,
    profile_from_document,
)
from factory_dhis2.profiles import registry
from factory_dhis2.profiles.base import VersionProfile


@pytest.fixture(scope="module")
def base_profile() -> VersionProfile:
    registry.load_builtin_profiles()
    return registry.profile_for_version("2.41.10")


def overridden(profile: VersionProfile, dhis2_type: str, **fields: Any) -> VersionProfile:
    """加一条覆盖，其它部分原样（覆盖的键名与产物一致，是 camelCase）。"""
    return replace(
        profile,
        descriptor_overrides=(*profile.descriptor_overrides, {"dhis2Type": dhis2_type, **fields}),
    )


def test_the_shipped_profile_has_no_overrides_but_the_mechanism_is_wired(
    base_profile: VersionProfile,
) -> None:
    """随包 2.41 没有需要覆盖的字段，但**读得到**：空数据文件不等于机制不存在。"""
    assert base_profile.descriptor_overrides == ()
    assert base_profile.descriptor_override("dataElement") == {}
    assert base_profile.schema_view_types() == tuple(
        sorted(schema.dhis2_type for schema in base_profile.schemas)
    )


def test_an_override_replaces_the_lists_and_merges_properties(
    base_profile: VersionProfile,
) -> None:
    """``required``/``forbidden``/``controlableFields`` 整项替换，``properties`` 按键合并。

    清单整项替换是因为覆盖表达的就是"这个版本这个类型的那份清单"；属性按键合并是因为包
    里那一份常常来自实例同步，一刀切替换会把同步结果丢掉。
    """
    pack = base_profile.object_schema("dataElement")
    assert pack is not None
    profile = overridden(
        base_profile,
        "dataElement",
        required=["name", "shortName"],
        forbidden=["href"],
        properties={"zAuth": {"type": "TEXT", "readOnly": True}},
    )

    schema = profile.object_schema("dataElement")
    assert schema is not None
    assert schema.required == ("name", "shortName")
    assert schema.forbidden == ("href",)
    assert schema.properties["zAuth"]["readOnly"] is True
    assert set(pack.properties) <= set(schema.properties), "原有属性不丢"


def test_an_empty_list_in_an_override_is_an_explicit_replacement(
    base_profile: VersionProfile,
) -> None:
    """覆盖里写 ``forbidden: []`` 是「这个版本不禁这些字段」，不是「没写」。"""
    profile = overridden(base_profile, "program", forbidden=[])
    schema = profile.object_schema("program")
    assert schema is not None
    assert schema.forbidden == ()
    assert profile.forbidden_fields("program") == ()


def test_a_type_with_only_an_override_gets_a_schema(base_profile: VersionProfile) -> None:
    """包里没有该片段的类型，覆盖也能让它出现（新增类型只改数据的另一半）。"""
    assert base_profile.object_schema("programIndicator") is None

    profile = overridden(
        base_profile, "programIndicator", required=["expression"], controlableFields=["expression"]
    )

    assert profile.object_schema("programIndicator") is not None
    assert profile.controlable_fields("programIndicator") == ("expression",)
    assert "programIndicator" in profile.schema_view_types()


def test_the_published_document_carries_the_effective_schema(
    base_profile: VersionProfile,
) -> None:
    """S2 只读产物，所以覆盖必须在产物里生效，而不是留在 S4 的代码里。"""
    profile = overridden(base_profile, "dataElement", controlableFields=["name", "code"])
    synced = [
        DiscoveredSchema(
            dhis2_type="dataElement",
            required=["name"],
            properties={"name": {"type": "TEXT", "readOnly": False}},
            forbidden=[],
            controlable_fields=["name"],
        )
    ]

    document = build_capability_profile_document(
        profile,
        environment_key="ov",
        dhis2_version="2.41.10",
        schemas=synced,
    )

    by_type = {item.dhis2_type: item for item in document.schemas}
    assert by_type["dataElement"].controlable_fields == ["name", "code"], (
        "覆盖压过实例同步：可受控字段是版本与项目的决定"
    )
    assert by_type["dataElement"].properties["name"]["readOnly"] is False, (
        "实例同步的属性清单不被覆盖丢掉"
    )
    assert {"dhis2Type": "dataElement", "controlableFields": ["name", "code"]} in [
        dict(item) for item in document.descriptor_overrides
    ], "覆盖本身也要发布出去，S2 才看得到版本差异是从哪来的"


def test_the_override_survives_the_round_trip_and_is_idempotent(
    base_profile: VersionProfile,
) -> None:
    """运行期从产物重建 Profile 之后仍是覆盖过的那一份，而且再重建一次结果不变。

    产物的 ``schemas`` 已经是"生效视图"，而重建出来的 Profile 还带着 ``descriptor_overrides``，
    于是 ``object_schema`` 会再压一次——只有覆盖是幂等的，这一步才不会把结论越改越歪。
    """
    profile = overridden(
        base_profile, "program", required=["name"], controlableFields=["name", "shortName"]
    )
    document = build_capability_profile_document(
        profile, environment_key="ov", dhis2_version="2.41.10"
    )

    first = profile_from_document(document, base=profile)
    dumped = document.model_dump(mode="json", by_alias=True)
    second = profile_from_document(dumped, base=first)

    for resolved in (first, second):
        schema = resolved.object_schema("program")
        assert schema is not None
        assert schema.required == ("name",)
        assert schema.controlable_fields == ("name", "shortName")
    assert first.object_schema("program") == second.object_schema("program")


def test_synced_types_outside_the_pack_keep_their_override(
    base_profile: VersionProfile,
) -> None:
    """覆盖加在一个「包里没有、但实例答得出 Schema」的类型上，同步回来也要在。"""
    profile = overridden(base_profile, "dataSet", forbidden=["organisationUnits"])
    synced = [
        DiscoveredSchema(
            dhis2_type="dataSet",
            required=["name"],
            properties={"name": {"type": "TEXT"}},
        )
    ]

    document = build_capability_profile_document(
        profile, environment_key="ov", dhis2_version="2.41.10", schemas=synced
    )

    by_type = {item.dhis2_type: item for item in document.schemas}
    assert by_type["dataSet"].forbidden == ["organisationUnits"]
    assert by_type["dataSet"].required == ["name"], "覆盖没写的那一项保持实例答案"
