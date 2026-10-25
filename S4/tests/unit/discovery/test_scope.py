"""发现范围解析与"一次请求一个过滤器"的形状（DX-DSC-002、DX-DSC-003、DX-ASM-03、AC-ENV-01）。

这一层全是纯函数，但它决定的是一次真实发现会发出什么请求，所以这里盯的是**过滤器字符串
本身**：明确清单里 UID 与 code 必须走不同的字段，code 前缀必须一次一个请求（条件之间是
OR），Greenfield 必须收窄到只读 default。写错一处，发现就会静默读出空结果或拖垮实例。
"""

from __future__ import annotations

import pytest

from factory_dhis2.discovery import scope as scope_module
from factory_dhis2.errors import DiscoveryScopeInvalidError
from factory_dhis2.profiles import registry


@pytest.fixture(scope="module")
def profile() -> registry.VersionProfile:
    registry.load_builtin_profiles()
    return registry.profile_for_version("2.41.10")


# --- DX-DSC-002【新增】：明确清单 ---------------------------------------------


def test_explicit_uids_become_an_id_in_filter(profile: registry.VersionProfile) -> None:
    resolved = scope_module.resolve(
        {"kind": "RUN_TARGET"},
        [{"dhis2Type": "dataElement", "uid": "de1"}, {"dhis2Type": "dataElement", "uid": "de2"}],
        profile,
    )
    assert resolved.explicit_uids == {"dataElement": ["de1", "de2"]}
    assert resolved.explicit_codes == {}
    assert resolved.selection_for("dataElement") == ["id:in:[de1,de2]"]


def test_explicit_codes_become_a_code_in_filter(profile: registry.VersionProfile) -> None:
    """只给 code 的清单必须按 code 过滤。

    以前 uid 与 code 落进同一个桶、一律按 ``id:in:`` 发问，于是"按 Registry 里的 code 清单
    发现"（AC-ENV-01、DX-ASM-03 的核心用法）永远返回空对象，还不报错。
    """
    resolved = scope_module.resolve(
        {"kind": "RUN_TARGET"},
        [{"dhis2Type": "dataElement", "code": "WEIGHT_KG"}, {"dhis2Type": "optionSet", "code": "YES_NO"}],
        profile,
    )
    assert resolved.explicit_codes == {"dataElement": ["WEIGHT_KG"], "optionSet": ["YES_NO"]}
    assert resolved.explicit_uids == {}
    assert resolved.selection_for("dataElement") == ["code:in:[WEIGHT_KG]"]


def test_one_reference_may_give_both_uid_and_code(profile: registry.VersionProfile) -> None:
    resolved = scope_module.resolve(
        None,
        [{"dhis2Type": "dataElement", "uid": "de1", "code": "WEIGHT_KG"}],
        profile,
    )
    assert resolved.selection_for("dataElement") == ["id:in:[de1]", "code:in:[WEIGHT_KG]"]


def test_a_reference_without_any_address_is_refused(profile: registry.VersionProfile) -> None:
    with pytest.raises(DiscoveryScopeInvalidError):
        scope_module.resolve(None, [{"dhis2Type": "dataElement"}], profile)


def test_a_reference_to_an_unsupported_type_is_reported_not_dropped(
    profile: registry.VersionProfile,
) -> None:
    """不在目录里的类型要报出来，而不是静默跳过：静默=发现结果看起来完整。"""
    resolved = scope_module.resolve(
        {"kind": "RUN_TARGET", "dhis2Types": ["dataElement"]},
        [{"dhis2Type": "notAThing", "uid": "x"}],
        profile,
    )
    assert "notAThing" in resolved.unsupported_types
    assert resolved.types_to_read() == ["dataElement"]


def test_an_unsupported_type_alone_selects_nothing_and_is_refused(
    profile: registry.VersionProfile,
) -> None:
    with pytest.raises(DiscoveryScopeInvalidError):
        scope_module.resolve(None, [{"dhis2Type": "notAThing", "uid": "x"}], profile)


# --- DX-DSC-002：code 前缀 ----------------------------------------------------


def test_each_code_prefix_is_its_own_request(profile: registry.VersionProfile) -> None:
    """前缀之间是 OR：拼进一个请求会变成 AND，条件越加越少而不是越多。"""
    resolved = scope_module.resolve(
        {"kind": "RUN_TARGET", "dhis2Types": ["dataElement"], "codePrefixes": ["ANC", "PMT"]},
        None,
        profile,
    )
    assert resolved.code_prefixes == ["ANC", "PMT"]
    assert resolved.selection_for("dataElement") == ["code:ilike:ANC", "code:ilike:PMT"]


def test_a_type_with_no_selection_still_reads_unfiltered(profile: registry.VersionProfile) -> None:
    resolved = scope_module.resolve(
        {"kind": "RUN_TARGET", "dhis2Types": ["dataElement"]}, None, profile
    )
    assert resolved.selection_for("dataElement") == [""]


def test_explicit_refs_supersede_prefixes_for_that_type(profile: registry.VersionProfile) -> None:
    """明确清单比前缀窄，两者同时给出时按清单读，不发多余请求。"""
    resolved = scope_module.resolve(
        {"kind": "RUN_TARGET", "codePrefixes": ["ANC"]},
        [{"dhis2Type": "dataElement", "uid": "de1"}],
        profile,
    )
    assert resolved.selection_for("dataElement") == ["id:in:[de1]"]


# --- DX-DSC-003：Greenfield ---------------------------------------------------


def test_greenfield_clears_every_selection(profile: registry.VersionProfile) -> None:
    resolved = scope_module.resolve(
        {
            "kind": "GREENFIELD",
            "dhis2Types": ["dataElement"],
            "codePrefixes": ["ANC"],
            "orgUnitDepth": 2,
        },
        [{"dhis2Type": "dataElement", "uid": "de1"}],
        profile,
    )
    assert resolved.greenfield is True
    assert resolved.dhis2_types == []
    assert resolved.explicit_uids == {} and resolved.explicit_codes == {}
    assert resolved.code_prefixes == []
    assert resolved.with_content is False


def test_greenfield_still_reads_the_org_skeleton(profile: registry.VersionProfile) -> None:
    """DX-DSC-006 的深度是按配置读的：Greenfield 也要能拿到 OrgUnit 骨架做映射。"""
    resolved = scope_module.resolve({"kind": "GREENFIELD", "orgUnitDepth": 1}, None, profile)
    assert resolved.org_unit_depth == 1


# --- 范围不合法时拒绝，而不是退化成全量 --------------------------------------


def test_an_unknown_kind_is_refused(profile: registry.VersionProfile) -> None:
    with pytest.raises(DiscoveryScopeInvalidError) as excinfo:
        scope_module.resolve({"kind": "WHOLE_INSTANCE"}, None, profile)
    assert "RUN_TARGET" in excinfo.value.details["known"]


def test_a_scope_that_selects_nothing_is_refused(profile: registry.VersionProfile) -> None:
    with pytest.raises(DiscoveryScopeInvalidError):
        scope_module.resolve({"kind": "RUN_TARGET"}, [], profile)
