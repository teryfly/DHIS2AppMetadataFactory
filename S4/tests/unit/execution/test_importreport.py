"""ImportReport 的保存与标准化（DX-EXE-027、AC-VAL-02、FR-VAL-002）。

实例返回的报告必须**完整保存**（它是追溯链上的一环），在此之上才标准化成统一问题格式：
UID 经 manifest 的 ``uidToBusinessKey`` 映射回 businessKey，来源阶段按错误类别映射（引用
与类型、Schema 与字段 → MAPPING，页面相关 → PAGE_DESIGN，无法判定 → MAPPING）。映射不上
的 UID 不丢弃，而是单独列出“载荷之外被点名的对象”。
"""

from __future__ import annotations

from typing import Any

from factory_dhis2.artifacts import read_json, read_metadata
from factory_dhis2.dto.issue import Issue
from factory_dhis2.enums import IssueLevel, SourceStage
from factory_dhis2.execution.importreport import parse_report, save_report
from factory_dhis2.payload.package import MetadataPackage
from factory_dhis2.profiles import registry
from factory_dhis2.profiles.base import VersionProfile
from factory_dhis2.testing.import_report import (
    ROW_STATUS_IGNORED,
    ROW_STATUS_OK,
    ImportOptions,
    ReportRow,
    build_report,
    klass_for_type,
)

UID_DE = "FTRrcoaog83"
UID_PAGE = "BpxO5WSDhXJ"
UID_OUTSIDE = "tMkKl0zMZMJp"

OPTIONS = ImportOptions(import_mode="VALIDATE")


def profile() -> VersionProfile:
    registry.load_builtin_profiles()
    return registry.profile_for_version("2.41.10")


def package() -> MetadataPackage:
    return MetadataPackage(
        artifact_id="art-package",
        artifact_hash="sha256:" + "a" * 64,
        payload={"dataElements": [{"id": UID_DE}], "eventCharts": [{"id": UID_PAGE}]},
        manifest={
            "uidToBusinessKey": [
                {"uid": UID_DE, "businessKey": "de.weight", "dhis2Type": "dataElement"},
                {"uid": UID_PAGE, "businessKey": "page.followup", "dhis2Type": "eventChart"},
            ],
            "objects": [
                {"businessKey": "de.weight", "dhis2Type": "dataElement", "uid": UID_DE}
            ],
        },
    )


def failing_report() -> dict[str, Any]:
    """一份含引用错误、Schema 错误、页面相关错误与一条 IGNORED 的 ImportReport。"""
    return build_report(
        [
            ReportRow(
                klass=klass_for_type("dataElement"),
                name="体重",
                status="ERROR",
                err_code="REFERENCE_ERROR",
                message="Cannot find CategoryOptionCombo with id: missingUid",
                uid=UID_DE,
            ),
            ReportRow(
                klass=klass_for_type("optionSet"),
                name="性别",
                status="ERROR",
                err_code="SCHEMA_ERROR",
                message="Property 'valueType' is required but not provided",
                uid="v2MybRD5Tmz",
            ),
            ReportRow(
                klass=klass_for_type("eventChart"),
                name="随访页面",
                status="ERROR",
                err_code="PAGE_ERROR",
                message="Section references a data element that is not in the program",
                uid=UID_PAGE,
            ),
            ReportRow(
                klass=klass_for_type("dataElement"),
                name="已存在的对象",
                status=ROW_STATUS_IGNORED,
                err_code="OK",
                message="Object already exists and is unchanged",
                uid=UID_OUTSIDE,
            ),
        ],
        OPTIONS,
        rolled_back=True,
    )


def issue_for(summary: Any, uid: str) -> Any:
    matches = [issue for issue in summary.issues if issue.object_uid == uid]
    assert matches, f"没有为 {uid} 生成问题：{summary.issues}"
    return matches[0]


# --- DX-EXE-027：来源阶段与级别 ----------------------------------------------


def test_standardized_issues_use_the_unified_level_field() -> None:
    """DX-AC-14：标准化后的问题使用统一 Issue 结构，级别字段是 ``level``。

    这是 00 文档 6.10 的硬约束（"级别字段一律称为 ``level``，不得使用 ``severity`` 等别名"）。
    序列化成产物/回给调用方时也要成立：字段名不对，S1 的镜像就会把 BLOCKING 读成默认值。
    """
    summary = parse_report(failing_report(), package=package(), profile=profile())
    assert summary.issues, "这份报告应该有问题可标准化"
    for issue in summary.issues:
        assert isinstance(issue, Issue)
        dumped = issue.model_dump(mode="json")
        assert "level" in dumped and "severity" not in dumped
        assert dumped["level"] in {"BLOCKING", "WARNING", "INFO"}
        assert set(dumped) >= {
            "issue_id",
            "rule_id",
            "level",
            "description",
            "source_stage",
            "object_uid",
            "object_key",
            "dhis2_type",
            "location",
            "details",
        }
    blocking = [issue for issue in summary.issues if issue.level is IssueLevel.BLOCKING]
    assert blocking and all(issue.is_blocking for issue in blocking)
    # 实例给的类型与错误码进了详情，事后不必回头翻原始 ImportReport
    assert blocking[0].dhis2_type == klass_for_type("dataElement")
    assert blocking[0].details.get("status") == "ERROR"


def test_a_reference_error_maps_back_to_the_mapping_stage() -> None:
    """DX-EXE-027：引用错误归 MAPPING。"""
    summary = parse_report(failing_report(), package=package(), profile=profile())
    issue = issue_for(summary, UID_DE)
    assert issue.source_stage is SourceStage.MAPPING
    assert issue.level is IssueLevel.BLOCKING


def test_a_schema_error_maps_back_to_the_mapping_stage() -> None:
    summary = parse_report(failing_report(), package=package(), profile=profile())
    issue = issue_for(summary, "v2MybRD5Tmz")
    assert issue.source_stage is SourceStage.MAPPING
    assert issue.rule_id == "DXIS-SCHEMA_ERROR"


def test_a_page_related_error_maps_back_to_the_page_design_stage() -> None:
    """DX-EXE-027：页面相关的错误要退回页面设计阶段，而不是映射。"""
    summary = parse_report(failing_report(), package=package(), profile=profile())
    issue = issue_for(summary, UID_PAGE)
    assert issue.source_stage is SourceStage.PAGE_DESIGN


def test_an_unclassifiable_error_defaults_to_the_mapping_stage() -> None:
    """DX-EXE-027：“无法判定”的兜底方向是 MAPPING。"""
    document = build_report(
        [
            ReportRow(
                klass=klass_for_type("dataElement"),
                name="体重",
                status="ERROR",
                err_code="E7004",
                message="Something the vocabulary does not know",
                uid=UID_DE,
            )
        ],
        OPTIONS,
    )
    summary = parse_report(document, package=package(), profile=profile())
    assert summary.issues[0].source_stage is SourceStage.MAPPING
    assert summary.issues[0].rule_id == "DXIS-UNKNOWN"


def test_every_problem_carries_the_message_the_instance_gave() -> None:
    """说明不能只剩“导入失败”：原文是排查的依据。"""
    summary = parse_report(failing_report(), package=package(), profile=profile())
    assert issue_for(summary, UID_DE).description.startswith("Cannot find CategoryOptionCombo")


def test_an_ignored_entry_is_a_warning_not_a_blocking_problem() -> None:
    """IGNORED 表示“什么都没发生”，它不该阻断执行。"""
    summary = parse_report(failing_report(), package=package(), profile=profile())
    issue = issue_for(summary, UID_OUTSIDE)
    assert issue.level is IssueLevel.WARNING
    assert issue.is_blocking is False


def test_the_uid_is_mapped_back_to_the_business_key_through_the_manifest() -> None:
    """DX-EXE-027：S1 据 businessKey 把问题落回设计对象。"""
    summary = parse_report(failing_report(), package=package(), profile=profile())
    assert issue_for(summary, UID_DE).object_key == "de.weight"
    assert issue_for(summary, UID_PAGE).object_key == "page.followup"


def test_a_uid_that_is_not_in_the_payload_is_collected_instead_of_dropped() -> None:
    """“载荷之外被点名的对象”本身就是一个值得看见的事实。"""
    summary = parse_report(failing_report(), package=package(), profile=profile())
    assert UID_OUTSIDE in summary.outside_payload_uids
    assert issue_for(summary, UID_OUTSIDE).object_key is None


def test_a_report_with_errors_does_not_pass() -> None:
    """AC-VAL-02：错误清单存在时结论就是没通过。"""
    summary = parse_report(failing_report(), package=package(), profile=profile())
    assert summary.passed is False
    assert len(summary.blocking) == 3
    assert summary.status == "ERROR"


def test_the_stats_of_the_instance_are_kept_verbatim() -> None:
    """原子模式回滚后 created 归零：报告里的数字要原样传下去。"""
    summary = parse_report(failing_report(), package=package(), profile=profile())
    assert summary.stats["invalid"] == 3
    assert summary.stats["created"] == 0


def test_a_fully_ok_report_passes_with_no_issues() -> None:
    """DX-EXE-027 的正面对照：全 OK 的报告没有问题。"""
    document = build_report(
        [
            ReportRow(
                klass=klass_for_type("dataElement"),
                name="体重",
                status=ROW_STATUS_OK,
                err_code="OK",
                change="CREATED",
                uid=UID_DE,
            ),
            ReportRow(
                klass=klass_for_type("eventChart"),
                name="随访页面",
                status=ROW_STATUS_OK,
                err_code="OK",
                change="UPDATED",
                uid=UID_PAGE,
            ),
        ],
        ImportOptions(import_mode="COMMIT"),
    )
    summary = parse_report(document, package=package(), profile=profile())
    assert summary.passed is True
    assert summary.issues == []
    assert summary.outside_payload_uids == []
    assert summary.stats["created"] == 1
    assert summary.stats["updated"] == 1


def test_parsing_without_a_package_still_reports_the_uids() -> None:
    """没有 manifest 时问题照样列出，只是没有 businessKey（S4 不猜）。"""
    summary = parse_report(failing_report(), profile=profile())
    assert summary.issues
    assert all(issue.object_key is None for issue in summary.issues)
    assert summary.outside_payload_uids == []


def test_issue_ids_are_stable_for_the_same_problem() -> None:
    """S1 按 issueId 去重：同一问题每次重跑都必须是同一个 ID。"""
    first = parse_report(failing_report(), package=package(), profile=profile())
    second = parse_report(failing_report(), package=package(), profile=profile())
    assert [issue.issue_id for issue in first.issues] == [
        issue.issue_id for issue in second.issues
    ]


# --- DX-EXE-027：完整保存 ----------------------------------------------------


def test_save_report_registers_a_dhis2_import_report_artifact(harness: object) -> None:
    document = failing_report()
    artifact_id = save_report(
        project_id="prj-1",
        document=document,
        mode="VALIDATE",
        environment_key="staging",
    )
    info = read_metadata(artifact_id)
    assert info.type == "DHIS2_IMPORT_REPORT"
    stored = read_json(artifact_id)
    assert stored["kind"] == "DHIS2_IMPORT_REPORT"
    assert stored["mode"] == "VALIDATE"
    assert stored["environmentKey"] == "staging"


def test_the_raw_report_is_preserved_verbatim_in_the_artifact(harness: object) -> None:
    """完整保存：原文一个字段都不能被“整理”掉（FR-SEC-014 的证据链）。"""
    document = failing_report()
    artifact_id = save_report(
        project_id=None, document=document, mode="COMMIT", environment_key="prod"
    )
    stored = read_json(artifact_id)
    assert stored["report"] == document
    assert stored["report"]["response"]["objects"][0]["errCode"] == "REFERENCE_ERROR"


def test_an_import_report_of_a_project_free_execution_still_has_an_owner(harness: object) -> None:
    """维护动作没有项目归属：产物仍要落在一个稳定归属上。"""
    artifact_id = save_report(
        project_id=None,
        document=build_report([], ImportOptions(import_mode="COMMIT")),
        mode="COMMIT",
        environment_key="prod",
    )
    assert read_metadata(artifact_id).project_id == "env-prod"


def test_the_saved_report_can_be_read_back_and_parsed_again(harness: object) -> None:
    """人工核实与对账都从产物重读原文，而不是依赖内存里的对象。"""
    artifact_id = save_report(
        project_id="prj-1",
        document=failing_report(),
        mode="VALIDATE",
        environment_key="staging",
    )
    summary = parse_report(read_json(artifact_id)["report"], package=package(), profile=profile())
    assert summary.passed is False
    assert len(summary.blocking) == 3


def test_the_saved_report_declares_the_payload_as_its_source(harness: object) -> None:
    """血缘（FD-ART-008）：报告是这份载荷产生的。"""
    artifact_id = save_report(
        project_id="prj-1",
        document=failing_report(),
        mode="VALIDATE",
        environment_key="staging",
        source_artifact_ids=[],
    )
    assert artifact_id.startswith("art")
