"""ImportReport 的保存与标准化（DX-EXE-027、AC-VAL-02、总体 SRS FR-VAL-002）。

实例返回的 ImportReport **完整保存**为 DHIS2_IMPORT_REPORT 产物——它是追溯链上的一环
（FR-SEC-014），也是人工核实时唯一能看的原始证据。在此基础上再标准化成统一问题格式：

* UID 经 manifest 的 ``uidToBusinessKey`` 映射回 businessKey；
* 来源阶段按错误类别映射（引用/类型/Schema/字段 → MAPPING，页面相关 → PAGE_DESIGN，
  无法判定 → MAPPING）。

映射不上的 UID 不丢弃：它照样是一条问题，只是没有 businessKey，报告里标出“载荷之外的
对象”，这本身就是一个值得看见的事实。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import unit_of_work

from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.dto.issue import Issue, make_issue
from factory_dhis2.enums import IssueLevel, SourceStage
from factory_dhis2.payload.package import MetadataPackage
from factory_dhis2.profiles.base import VersionProfile

#: 一条对象级消息里可能出现的状态
ERROR_STATUSES: frozenset[str] = frozenset({"ERROR", "FAILURE", "IGNORED"})
OK_STATUSES: frozenset[str] = frozenset({"OK", "SUCCESS"})

#: errCode → 错误类别的兜底词表（实例的 klass 是对象类型，不是错误类别）
ERROR_CLASS_HINTS: tuple[tuple[str, str], ...] = (
    ("reference", "REFERENCE_ERROR"),
    ("not found", "REFERENCE_ERROR"),
    ("cannot find", "REFERENCE_ERROR"),
    ("property", "SCHEMA_ERROR"),
    ("schema", "SCHEMA_ERROR"),
    ("required", "SCHEMA_ERROR"),
    ("invalid value", "FIELD_ERROR"),
    ("type", "TYPE_ERROR"),
    ("uid", "IDENTIFIER_ERROR"),
    ("identifier", "IDENTIFIER_ERROR"),
    ("page", "PAGE_ERROR"),
    ("design", "PAGE_ERROR"),
)


@dataclass
class ImportReportSummary:
    """一次导入结果的标准化视图。"""

    passed: bool = True
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    artifact_id: str | None = None
    #: 载荷之外被点名的对象（DX-EXE-027 的“映射不上”情形）
    outside_payload_uids: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.is_blocking]


def parse_report(
    document: dict[str, Any],
    *,
    package: MetadataPackage | None = None,
    profile: VersionProfile | None = None,
) -> ImportReportSummary:
    """把一份 DHIS2 ImportReport 转成统一问题格式。"""
    inner = document.get("response")
    response: dict[str, Any] = inner if isinstance(inner, dict) else document
    status = str(document.get("status") or response.get("status") or "")
    summary = ImportReportSummary(
        status=status or None,
        stats=dict(response.get("stats") or {}),
    )
    by_uid = package.uid_to_business_key() if package else {}
    stage_map = (profile.validation_rules.get("importReportErrorClasses") if profile else None) or {}

    rows: list[dict[str, Any]] = []
    for key in ("objects", "detailedMessages", "messages", "imports"):
        value = response.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            # tracker 的 imports 形态：按集合名分组
            for group in value.values():
                if isinstance(group, list):
                    rows.extend(item for item in group if isinstance(item, dict))
    # 2.41 同步导入的实际形态：逐对象错误在 ``typeReports[].objectReports[]`` 里，而且对象
    # 本身**没有 status 字段**——有 errorReports 就是被拒。不展开这一层，真实例的错误清单
    # 会一条都映射不出来（在线契约测试实测到的形状）。
    rows.extend(_type_report_rows(response.get("typeReports")))

    for row in rows:
        row_status = str(row.get("status") or "").upper()
        if row_status in OK_STATUSES or not row_status:
            continue
        uid = _uid_of(row)
        message = _message_of(row)
        error_class = _error_class_of(row, stage_map)
        level = IssueLevel.BLOCKING if row_status != "IGNORED" else IssueLevel.WARNING
        stage_value = stage_map.get(error_class) or "MAPPING"
        summary.issues.append(
            make_issue(
                f"DXIS-{error_class}",
                level,
                message or f"{row.get('klass') or row.get('type') or 'object'} was rejected",
                object_key=by_uid.get(uid or "", None),
                object_uid=uid,
                stage=SourceStage(stage_value),
                artifact_id=summary.artifact_id,
                suggested_action=_action_for(error_class),
                # BP-VAL-001 的其余字段：实例给了就带上，S2/S1 不必回头再读原始报告
                dhis2_type=str(row["klass"]) if row.get("klass") else (
                    str(row["type"]) if row.get("type") else None
                ),
                location=str(row["errcode"]) if row.get("errcode") else None,
                details={
                    key: row[key]
                    for key in ("status", "klass", "errcode", "uid", "code")
                    if row.get(key) is not None
                },
            )
        )
        if uid and package is not None and uid not in by_uid:
            summary.outside_payload_uids.append(uid)

    failure_flags = [status.upper(), str(response.get("status") or "").upper()]
    summary.passed = not any(flag in {"ERROR", "FAILURE"} for flag in failure_flags) and not any(
        issue.is_blocking for issue in summary.issues
    )
    return summary


def save_report(
    *,
    project_id: str | None,
    document: dict[str, Any],
    mode: str,
    environment_key: str,
    source_artifact_ids: list[str] | None = None,
) -> str:
    """完整保存 ImportReport 为 DHIS2_IMPORT_REPORT 产物（DX-EXE-027）。

    暂存是外部 I/O，因此这里自成一事务：先在事务外写对象存储，再在短事务里登记。
    """
    envelope = {
        "schemaVersion": 1,
        "kind": "DHIS2_IMPORT_REPORT",
        "mode": mode,
        "environmentKey": environment_key,
        "report": document,
    }
    owner = project_id or f"env-{environment_key}"
    handle = stage_document(envelope, project_id=owner, artifact_type="DHIS2_IMPORT_REPORT")
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=owner,
            artifact_type="DHIS2_IMPORT_REPORT",
            source_artifact_ids=list(source_artifact_ids or []),
            session=session,
        )
    return info.id


def _type_report_rows(value: Any) -> list[dict[str, Any]]:
    """把 2.41 的 ``typeReports`` 展平成逐对象行。

    ``klass`` 是 Java 全限定名（``org.hisp.dhis.dataelement.DataElement``），版本与包名都可能
    变，所以这里只按 ``uid`` 映射回 businessKey，绝不依赖 klass 的拼写——这正是 DX-EXE-027 与
    S2 manifest 用 UID 而不是类型名对齐的原因。
    """
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for report in value:
        if not isinstance(report, dict):
            continue
        objects = report.get("objectReports")
        if not isinstance(objects, list):
            continue
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            errors = [item for item in (obj.get("errorReports") or []) if isinstance(item, dict)]
            if not errors:
                continue
            messages = [str(item.get("message") or "") for item in errors if item.get("message")]
            codes = [str(item.get("errorCode") or "") for item in errors if item.get("errorCode")]
            rows.append(
                {
                    # 实例把“没有生效的对象”一律计进 stats.ignored，但带 errorReports 的对象是
                    # 明确的失败：把它降级成 WARNING 会让一次引用坏掉的部署看起来可以接受。
                    "status": "ERROR",
                    "klass": obj.get("klass") or report.get("klass"),
                    "uid": obj.get("uid") or obj.get("mainId"),
                    "name": obj.get("displayName") or obj.get("name"),
                    "message": "; ".join(message for message in messages if message),
                    "errCode": codes[0] if codes else None,
                    "params": {"id": obj.get("uid")} if obj.get("uid") else {},
                }
            )
    return rows


def _uid_of(row: dict[str, Any]) -> str | None:
    params = row.get("params")
    if isinstance(params, dict):
        for key in ("id", "uid", "code"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("uid", "id", "object"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    if isinstance(row.get("object"), dict):
        nested = row["object"]
        if isinstance(nested, dict):
            return _uid_of(nested)
    return None


def _message_of(row: dict[str, Any]) -> str:
    for key in ("message", "error message", "errorMessage", "description", "errCode"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    klass = row.get("klass") or row.get("type")
    return str(klass) if klass else ""


def _error_class_of(row: dict[str, Any], stage_map: dict[str, str]) -> str:
    explicit = row.get("errorClass") or row.get("errCode")
    if isinstance(explicit, str) and explicit.upper() in stage_map:
        return explicit.upper()
    haystack = _message_of(row).lower()
    for marker, error_class in ERROR_CLASS_HINTS:
        if marker in haystack:
            return error_class
    return "UNKNOWN"


def _action_for(error_class: str) -> str:
    if error_class in {"REFERENCE_ERROR", "IDENTIFIER_ERROR"}:
        return "检查映射：引用对象是否已在该环境存在，或应否进入载荷"
    if error_class in {"SCHEMA_ERROR", "FIELD_ERROR", "TYPE_ERROR"}:
        return "检查编译：字段与类型是否符合该版本的 Schema"
    if error_class == "PAGE_ERROR":
        return "回到页面设计阶段修正"
    return "查看 ImportReport 原文"


__all__ = [
    "ERROR_CLASS_HINTS",
    "ImportReportSummary",
    "parse_report",
    "save_report",
]
