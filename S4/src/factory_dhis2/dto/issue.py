"""标准化问题（DX-EXE-027、总体 SRS FR-VAL-002）。

S1 把这些行存进阶段载荷并据 ``source_stage`` 决定失败回退到哪个阶段，所以 ``issue_id``
与 ``source_stage`` 是承重的：同一问题在同一载荷上必须得到同一 ID，否则每次重跑都会
“新出现”一批问题。ID 由规则 + 对象 + 消息派生，不来自计数器。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from factory_dhis2.common.hashing_helper import short_digest
from factory_dhis2.enums import IssueLevel, SourceStage

#: DHIS2 导入错误的归类（DX-EXE-027）；键是实例的 errorClass/klass 词汇
DEFAULT_STAGE_BY_ERROR_CLASS: dict[str, str] = {
    "REFERENCE_ERROR": "MAPPING",
    "TYPE_ERROR": "MAPPING",
    "SCHEMA_ERROR": "MAPPING",
    "FIELD_ERROR": "MAPPING",
    "IDENTIFIER_ERROR": "MAPPING",
    "PARSE_ERROR": "MAPPING",
    "PAGE_ERROR": "PAGE_DESIGN",
    "UI_ERROR": "PAGE_DESIGN",
    "UNKNOWN": "MAPPING",
}


class Issue(BaseModel):
    """一条标准化问题（00 文档 6.10、BP-VAL-001）。

    级别字段一律叫 **``level``**：00 文档 6.10 明确"不得使用 ``severity`` 等别名"，S2 的
    :mod:`factory_blueprint.dto.issue` 是这个结构的唯一定义方，S3 与 S4 都遵循它
    （DX-EXE-027、DX-AC-14）。同一系统里两套名字并存的代价是"哪个字段说了算"要到运行期
    才暴露，而 BLOCKING 被读成 WARNING 会让一次不通过的部署看起来通过。
    """

    model_config = ConfigDict(populate_by_name=True)

    issue_id: str
    rule_id: str = "DX-EXE-027"
    level: IssueLevel = IssueLevel.WARNING
    object_key: str | None = None
    object_uid: str | None = None
    description: str = ""
    source_stage: SourceStage = SourceStage.MAPPING
    artifact_id: str | None = None
    suggested_action: str | None = None
    #: BP-VAL-001 结构的其余字段：S2 在编译期就有它们，S4 只在拿得到时填
    dhis2_type: str | None = None
    location: str | None = None
    section: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.level is IssueLevel.BLOCKING


def make_issue(
    rule_id: str,
    level: IssueLevel,
    description: str,
    *,
    object_key: str | None = None,
    object_uid: str | None = None,
    stage: SourceStage | None = None,
    artifact_id: str | None = None,
    suggested_action: str | None = None,
    dhis2_type: str | None = None,
    location: str | None = None,
    section: str | None = None,
    details: dict[str, Any] | None = None,
) -> Issue:
    issue_id = "dxi-" + short_digest(f"{rule_id}|{object_key or ''}|{object_uid or ''}|{description}")
    return Issue(
        issue_id=issue_id,
        rule_id=rule_id,
        level=level,
        description=description,
        object_key=object_key,
        object_uid=object_uid,
        source_stage=stage or SourceStage.MAPPING,
        artifact_id=artifact_id,
        suggested_action=suggested_action,
        dhis2_type=dhis2_type,
        location=location,
        section=section,
        details=dict(details or {}),
    )


def stage_for_error_class(error_class: str | None) -> SourceStage:
    """无法判定时归 MAPPING（DX-EXE-027 的兜底方向）。"""
    value = DEFAULT_STAGE_BY_ERROR_CLASS.get((error_class or "UNKNOWN").upper(), "MAPPING")
    return SourceStage(value)


def blocking(issues: list[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.is_blocking]


def as_dicts(issues: list[Issue]) -> list[dict[str, Any]]:
    """写进产物/审计时用别名形态（camelCase），与 S4 的其他文档一致。"""
    return [issue.model_dump(mode="json", by_alias=False) for issue in issues]


__all__ = [
    "DEFAULT_STAGE_BY_ERROR_CLASS",
    "Issue",
    "as_dicts",
    "blocking",
    "make_issue",
    "stage_for_error_class",
]
