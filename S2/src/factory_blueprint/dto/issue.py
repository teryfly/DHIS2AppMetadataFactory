"""Validation issues (BP-VAL-001).

An issue ID must be identical for identical input, so it is derived from the rule,
the object and the location rather than from a sequence number.  Validators only
report; nothing here can repair a product (C-07).
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import structure_hash
from pydantic import BaseModel, ConfigDict, Field

from factory_blueprint.enums import DesignStage, IssueLevel, Section

ISSUE_ID_PREFIX = "bpis-"


def derive_issue_id(
    rule_id: str, business_key: str | None, uid: str | None, location: str | None, message: str
) -> str:
    digest = structure_hash(
        {
            "ruleId": rule_id,
            "businessKey": business_key,
            "uid": uid,
            "location": location,
            "message": message,
        }
    )
    return f"{ISSUE_ID_PREFIX}{digest.split(':', 1)[1][:26]}"


class Issue(BaseModel):
    """One validation result (overall SRS 13.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_id: str
    rule_id: str
    level: IssueLevel
    message: str
    source_stage: DesignStage | None = None
    business_key: str | None = None
    uid: str | None = None
    dhis2_type: str | None = None
    artifact_id: str | None = None
    location: str | None = None
    section: Section | None = None
    remediation: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.level.value,
            self.rule_id,
            self.business_key or "",
            self.location or "",
            self.issue_id,
        )

    def summary(self) -> dict[str, Any]:
        """The reduced form S1 needs when creating a Gate (CP-GATE-004)."""
        return {
            "issueId": self.issue_id,
            "ruleId": self.rule_id,
            "level": self.level.value,
            "businessKey": self.business_key,
            "message": self.message,
            "sourceStage": self.source_stage.value if self.source_stage else None,
            "remediation": self.remediation,
        }


def make_issue(
    rule_id: str,
    level: IssueLevel,
    message: str,
    *,
    business_key: str | None = None,
    uid: str | None = None,
    dhis2_type: str | None = None,
    location: str | None = None,
    section: Section | None = None,
    source_stage: DesignStage | None = None,
    remediation: str | None = None,
    artifact_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> Issue:
    return Issue(
        issue_id=derive_issue_id(rule_id, business_key, uid, location, message),
        rule_id=rule_id,
        level=level,
        message=message,
        business_key=business_key,
        uid=uid,
        dhis2_type=dhis2_type,
        location=location,
        section=section,
        source_stage=source_stage,
        remediation=remediation,
        artifact_id=artifact_id,
        details=details or {},
    )


def sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=Issue.sort_key)
