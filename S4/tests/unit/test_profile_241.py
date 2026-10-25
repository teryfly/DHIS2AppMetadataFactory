"""Dhis241Profile（DX-CAP-001/002/004、DX-CLI-005、DX-QRY-001）。

资源目录是 S4 唯一允许出现 DHIS2 端点的地方；这些测试同时保证 **S2 读得到的字段名**
（DX-CAP-008 的契约）与**逻辑名词汇表与总体 SRS 19.3 一致**。
"""

from __future__ import annotations

import pytest

from factory_dhis2.errors import VersionUnsupportedError
from factory_dhis2.profiles import registry
from factory_dhis2.profiles.base import TYPE_RESOURCE_PREFIX, VersionProfile


@pytest.fixture(scope="module")
def profile() -> VersionProfile:
    registry.load_builtin_profiles()
    return registry.profile_for_version("2.41.10")


def test_dhis241_profile_is_shipped(profile: VersionProfile) -> None:
    """FR-CAP-001：MVP 必须提供 Dhis241Profile。"""
    assert profile.profile_version == "Dhis241Profile"
    assert profile.dhis2_series == "2.41"
    assert profile.matches_version("2.41.10")
    assert not profile.matches_version("2.42.0")


def test_expression_grammar_version_is_published(profile: VersionProfile) -> None:
    """DX-CAP-001【新增】：供 S2 选择解析器。"""
    assert profile.expression_grammar_version == "dhis2-2.41"


def test_logical_resource_vocabulary_matches_overall_srs_19_3(
    profile: VersionProfile,
) -> None:
    """总体 SRS 19.3 的每一个逻辑名都必须能在目录里解析。"""
    required = [
        "system.info",
        "schemas",
        "metadata.read",
        "system.id",
        "identifiableObjects",
        "metadata.import",
        "system.tasks",
        "programRules.condition.validate",
        "programIndicators.expression.validate",
        "validationRules.expression.validate",
        "tracker.import",
        "tracker.trackedEntities",
        "tracker.enrollments",
        "tracker.events",
        "tracker.relationships",
        "dataStore",
        "apps",
        "maintenance",
    ]
    missing = [name for name in required if profile.resource(name) is None]
    assert not missing, f"资源目录缺少：{missing}"


def test_tracker_resources_resolve_to_the_new_tracker_api(
    profile: VersionProfile,
) -> None:
    """DX-CAP-004：Tracker 必须解析到 /api/tracker 及其子资源。"""
    for name in ("tracker.trackedEntities", "tracker.enrollments", "tracker.events"):
        entry = profile.require_resource(name)
        assert entry.path.startswith("tracker/")
        assert entry.tracker_api is True
    assert profile.require_resource("tracker.import").path == "tracker"


def test_legacy_tracker_endpoints_are_deprecated(profile: VersionProfile) -> None:
    """DX-CAP-004：旧 Tracker 端点必须登记在弃用清单里。"""
    for legacy in ("api/trackedEntityInstances", "api/programs/{id}/events", "api/enrollments"):
        assert profile.is_deprecated("endpoint", legacy), legacy


def test_removed_endpoint_is_reported_as_removed(profile: VersionProfile) -> None:
    assert profile.is_removed("endpoint", "api/dataElementOptions")
    assert not profile.is_removed("endpoint", "api/trackedEntityInstances")


def test_no_version_specific_path_is_hardcoded_outside_data(
    profile: VersionProfile,
) -> None:
    """DX-CLI-005：路径一律带 api 前缀之外的相对形式，不写死 ``/api/``。"""
    for entry in profile.resources:
        assert not entry.path.startswith("/api"), entry.logical_name
        assert not entry.path.startswith("api/"), entry.logical_name


def test_type_endpoints_cover_the_s2_descriptor_types(profile: VersionProfile) -> None:
    """S2 的 24 个类型描述器都必须有对应资源，否则发现与读回比对无法进行。"""
    for dhis2_type in (
        "dataElement",
        "optionSet",
        "categoryCombo",
        "categoryOption",
        "program",
        "programStage",
        "programRule",
        "trackedEntityAttribute",
        "trackedEntityType",
        "organisationUnit",
        "validationRule",
        "userRole",
        "userGroup",
    ):
        assert profile.has_type(dhis2_type), dhis2_type
        entry = profile.type_resource(dhis2_type)
        assert entry.method == "POST"
        assert entry.logical_name.startswith(TYPE_RESOURCE_PREFIX)


def test_default_object_reads_are_addressable(profile: VersionProfile) -> None:
    """DX-DSC-003：Greenfield 发现要读的 default 四件套。"""
    for dhis2_type in ("categoryCombo", "category", "categoryOption", "categoryOptionCombo"):
        assert profile.has_type(dhis2_type), dhis2_type


def test_uid_generation_resource_is_the_official_one(profile: VersionProfile) -> None:
    """DX-QRY-001：逻辑资源 ``system.id``；实测 ``reserved/ids`` 在 2.41 不存在。"""
    entry = profile.require_resource("system.id")
    assert entry.path == "system/id"
    assert entry.method == "GET"
    assert entry.parameters.get("limit") == "limit"
    assert profile.resource("reserved/ids") is None


def test_volatile_fields_exclude_everything_unstable(profile: VersionProfile) -> None:
    """DX-DSC-007 / AC-EXE-07：仅 lastUpdated 变化不得报漂移。"""
    for field_name in ("lastUpdated", "created", "href", "access", "id"):
        assert field_name in profile.volatile_fields, field_name
    # 受控字段绝不能被排除，否则真的改了也看不见
    for controlled in ("name", "code", "shortName", "description"):
        assert controlled not in profile.volatile_fields, controlled


def test_metadata_import_defaults_are_the_required_ones(profile: VersionProfile) -> None:
    """DX-EXE-011：UID 作标识符、创建并更新、全部成功或全部失败。"""
    defaults = profile.import_defaults("metadata")
    assert defaults["identifier"] == "ASSIGNED"
    assert defaults["importStrategy"] == "CREATE_AND_UPDATE"
    assert defaults["atomicMode"] == "ALL"
    assert defaults["importMode"] == "COMMIT"


def test_non_default_parameters_require_explicit_confirmation(
    profile: VersionProfile,
) -> None:
    """DX-EXE-011：调用方选择的非默认参数必须在计划里标记为需显式确认。"""
    block = profile.import_parameters["metadata"]
    assert "atomicMode" in block["nonDefaultRequiresConfirmation"]
    assert "importStrategy" in block["nonDefaultRequiresConfirmation"]


def test_async_import_mechanism_is_described(profile: VersionProfile) -> None:
    """DX-EXE-033：Profile 说明异步任务的提交与查询方式。"""
    block = profile.import_parameters["metadata"]["async"]
    assert block["taskResource"] == "system.tasks"
    assert "COMPLETED" in block["terminalStatuses"]


def test_associated_data_covers_the_five_mvp_types(profile: VersionProfile) -> None:
    """DX-QRY-004：MVP 覆盖五类，其余类型没有登记项。"""
    for dhis2_type in (
        "dataElement",
        "trackedEntityAttribute",
        "program",
        "programStage",
        "optionSet",
    ):
        assert profile.associated_data_check(dhis2_type) is not None, dhis2_type
    assert profile.associated_data_check("organisationUnit") is None


def test_import_report_error_classes_map_to_stages(profile: VersionProfile) -> None:
    """DX-EXE-027：引用/类型/Schema/字段错误归 MAPPING，页面相关归 PAGE_DESIGN。"""
    mapping = profile.validation_rules["importReportErrorClasses"]
    assert mapping["REFERENCE_ERROR"] == "MAPPING"
    assert mapping["SCHEMA_ERROR"] == "MAPPING"
    assert mapping["PAGE_ERROR"] == "PAGE_DESIGN"
    assert mapping["UNKNOWN"] == "MAPPING"


def test_unknown_series_is_refused_not_guessed() -> None:
    """DX-CON-001：没有对应版本 Profile 就是一个明确错误。"""
    with pytest.raises(VersionUnsupportedError):
        registry.profile_for_version("2.42.0")


def test_environment_constraint_matching(profile: VersionProfile) -> None:
    assert profile.matches_constraint("2.41.x")
    assert profile.matches_constraint(None)
    assert not profile.matches_constraint("2.42.x")


def test_document_shape_matches_what_s2_parses(profile: VersionProfile) -> None:
    """DX-CAP-008：S2 的读取契约（``factory_blueprint/s4/contract.py``）要求的字段名。

    S4 不能导入 S2（依赖方向），所以这里逐字断言发布形状的键名，改动必然先在这里失败。
    """
    entry = profile.require_resource("system.id")
    document = entry.as_document()
    assert set(document) == {
        "logicalName",
        "path",
        "method",
        "parameters",
        "paging",
        "trackerApi",
        "credentialScope",
    }
    schema = profile.object_schema("dataElement")
    assert schema is not None
    schema_doc = schema.as_document()
    assert set(schema_doc) == {
        "dhis2Type",
        "required",
        "properties",
        "forbidden",
        "controlableFields",
    }
    assert set(profile.deprecated[0].as_document()) <= {"kind", "name", "removedIn", "note"}
