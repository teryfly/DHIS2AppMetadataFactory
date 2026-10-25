"""版本 Profile ⊕ 探测结果 → 产物 → 运行期 Profile 的往返（DX-CLI-005、DX-CAP-003）。

产物是 S2 与运行期**唯一**该读的那一份，所以"存进去再取出来必须等价"不是洁癖：字段在
落盘/装载过程中丢掉一个，表现是端点解析退回猜测或某类对象突然"不支持"，而那要到一次
真实导入才暴露。
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_dhis2.contract.capability_profile import (
    DiscoveredSchema,
    build_capability_profile_document,
    profile_from_document,
)
from factory_dhis2.profiles import registry


@pytest.fixture(scope="module")
def profile() -> Any:
    registry.load_builtin_profiles()
    return registry.profile_for_version("2.41.10")


def test_round_trip_keeps_everything_the_runtime_needs(profile: Any) -> None:
    document = build_capability_profile_document(
        profile,
        environment_key="rt",
        dhis2_version="2.41.10",
        probed_at="2026-01-01T00:00:00+00:00",
    )
    restored = profile_from_document(document.model_dump(mode="json", by_alias=True), base=profile)

    assert {item.logical_name for item in restored.resources} == {
        item.logical_name for item in profile.resources
    }
    assert restored.profile_version == profile.profile_version
    assert restored.expression_grammar_version == profile.expression_grammar_version
    assert restored.import_parameters == profile.import_parameters
    assert restored.volatile_fields == profile.volatile_fields
    assert restored.api_prefix == profile.api_prefix
    assert [item.name for item in restored.deprecated] == [item.name for item in profile.deprecated]
    # 项目/版本决定，不是实例事实：产物里没有，仍取随包 Profile
    assert restored.associated_data == profile.associated_data
    assert restored.property_defaults == profile.property_defaults
    assert restored.read_fields == profile.read_fields
    assert restored.version_constraint == profile.version_constraint


def test_the_probed_negative_facts_are_removed_from_the_catalogue(profile: Any) -> None:
    """``probed.unavailableResources`` 里的端点必须从运行期目录里消失。"""
    victim = next(
        item.logical_name
        for item in profile.resources
        if item.method == "GET" and item.logical_name != "system.info"
    )
    document = build_capability_profile_document(
        profile,
        environment_key="rt",
        dhis2_version="2.41.10",
        probed={"unavailableResources": [victim, "schemas:dataElement"]},
    )
    restored = profile_from_document(document, base=profile)

    names = {item.logical_name for item in restored.resources}
    assert victim not in names
    # ``schemas:<类型>`` 不是逻辑资源名，不能顺手把 schemas 端点也删掉
    assert "schemas" in names
    with pytest.raises(Exception) as refused:
        restored.require_resource(victim)
    assert refused.value.code == "DHIS2_VERSION_UNSUPPORTED"


def test_synced_schemas_override_the_offline_baseline(profile: Any) -> None:
    """实例 Schema 是权威（0.2 权威顺序）：同步到的部分覆盖离线基线，未同步的保持原样。"""
    synced = [
        DiscoveredSchema(
            dhis2_type="dataElement",
            required=["name"],
            properties={"name": {"type": "TEXT", "readOnly": False}},
            forbidden=["aggregateData"],
            controlable_fields=["name"],
        )
    ]
    document = build_capability_profile_document(
        profile,
        environment_key="rt",
        dhis2_version="2.41.10",
        schemas=synced,
    )
    restored = profile_from_document(document, base=profile)

    by_type = {item.dhis2_type: item for item in restored.schemas}
    assert by_type["dataElement"].properties["name"]["readOnly"] is False
    assert list(by_type["dataElement"].controlable_fields) == ["name"], (
        "controlableFields 是项目决定，不被实例答案覆盖"
    )
    assert restored.object_schema("program") == profile.object_schema("program"), (
        "未同步的类型不能从产物里消失"
    )
