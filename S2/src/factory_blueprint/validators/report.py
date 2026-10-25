"""``VALIDATION_REPORT`` rendering (BP-VAL-001 … 004)."""

from __future__ import annotations

from typing import Any

from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.dto.issue import Issue
from factory_blueprint.enums import IssueLevel


def document(
    inputs: CompileInputs, issues: list[Issue], *, kind: str
) -> dict[str, Any]:
    """The report artifact: ordered issues, counts and the provenance to trace back."""
    ordered = sorted(issues, key=Issue.sort_key)
    return {
        "schemaVersion": 1,
        "kind": kind,
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "blueprintHash": inputs.blueprint_hash,
        "environmentKey": inputs.environment_key,
        "compiler": inputs.compatibility(),
        "buildContext": inputs.build_context.as_record(),
        "counts": {
            "BLOCKING": sum(1 for issue in ordered if issue.level is IssueLevel.BLOCKING),
            "WARNING": sum(1 for issue in ordered if issue.level is IssueLevel.WARNING),
            "INFO": sum(1 for issue in ordered if issue.level is IssueLevel.INFO),
        },
        "issues": [_as_document(issue) for issue in ordered],
        "note": "validators only report; nothing here is auto-repaired (overall SRS C-07)",
    }


def _as_document(issue: Issue) -> dict[str, Any]:
    """The artifact form uses the SRS's camelCase field names (00 doc 6.7)."""
    reduced = issue.model_dump(mode="json")
    return {
        "issueId": reduced["issue_id"],
        "ruleId": reduced["rule_id"],
        "level": reduced["level"],
        "message": reduced["message"],
        "businessKey": reduced["business_key"],
        "uid": reduced["uid"],
        "dhis2Type": reduced["dhis2_type"],
        "artifactId": reduced["artifact_id"],
        "location": reduced["location"],
        "section": reduced["section"],
        "sourceStage": reduced["source_stage"],
        "remediation": reduced["remediation"],
        "details": reduced["details"],
    }


def summary(issues: list[Issue]) -> dict[str, int]:
    return {
        "BLOCKING": sum(1 for issue in issues if issue.level is IssueLevel.BLOCKING),
        "WARNING": sum(1 for issue in issues if issue.level is IssueLevel.WARNING),
        "INFO": sum(1 for issue in issues if issue.level is IssueLevel.INFO),
    }
