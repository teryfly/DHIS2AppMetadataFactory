"""Decision completeness (BP-DEC-002/003/004, BP-VAL-011).

S2 cannot create a Question, so it enforces the data rule that an Agent may not
adopt a low-confidence or high-impact decision on its own: such a decision must
carry a confirmation record, and freeze reports it as BLOCKING when it does not
(BP-DEC-004, overall SRS FR-MAP-003).
"""

from __future__ import annotations

from typing import Any

from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import (
    ImpactLevel,
    IssueLevel,
    Section,
)
from factory_blueprint.impact.stage_map import issue_source_stage
from factory_blueprint.schema import store

RULE_DECISION_REQUIRED = "BPV-DEC-001"
RULE_CONFIRMATION_REQUIRED = "BPV-DEC-002"
RULE_DISPOSITION_DECIDED = "BPV-DEC-003"
RULE_ALTERNATIVES_MISSING = "BPV-DEC-004"

DEFAULT_DECISION_THRESHOLD = 0.7
MAPPING_CONTAINER = CONTAINER_BY_PATH["/mapping/entries"]
REGISTRY_CONTAINER = CONTAINER_BY_PATH["/registry/entries"]
ARCHITECTURE_CONTAINER = CONTAINER_BY_PATH["/architecture/decisions"]


def decision_confidence_threshold(document: dict[str, Any]) -> float:
    policy = document.get(Section.GENERATION_POLICY.value) or {}
    value = policy.get("decisionConfidenceThreshold")
    return float(value) if isinstance(value, (int, float)) else DEFAULT_DECISION_THRESHOLD


def _issue(rule_id: str, message: str, business_key: str | None, **kwargs: Any) -> Issue:
    return make_issue(
        rule_id,
        IssueLevel.BLOCKING,
        message,
        section=Section.MAPPING,
        business_key=business_key,
        source_stage=issue_source_stage(Section.MAPPING),
        **kwargs,
    )


def decision_issues(document: dict[str, Any]) -> list[Issue]:
    """Every completeness rule about mapping and architecture decisions."""
    view = BlueprintDocument(document)
    known_decisions = {
        str(entry.get("decisionId"))
        for entry in view.container_entries(ARCHITECTURE_CONTAINER)
        if isinstance(entry.get("decisionId"), str)
    }
    threshold = decision_confidence_threshold(document)
    issues: list[Issue] = []

    for entry in view.container_entries(MAPPING_CONTAINER):
        business_key = str(entry.get("businessKey") or "")
        if entry.get("notMapped") is True:
            continue
        decision_id = entry.get("decisionId")
        alternatives = entry.get("alternatives") or []
        risks = entry.get("risks") or []

        if not isinstance(decision_id, str) or not decision_id:
            issues.append(
                _issue(
                    RULE_DECISION_REQUIRED,
                    f"mapping of {business_key!r} references no decision (BP-DEC-001)",
                    business_key,
                    remediation="record an architecture or mapping decision and link it",
                )
            )
        elif decision_id not in known_decisions:
            issues.append(
                _issue(
                    RULE_DECISION_REQUIRED,
                    f"decision {decision_id} of mapping {business_key!r} is not recorded "
                    "in the architecture section",
                    business_key,
                )
            )

        if alternatives:
            missing_reason = [
                str(item.get("option"))
                for item in alternatives
                if isinstance(item, dict) and not item.get("reasonRejected")
            ]
            if missing_reason:
                issues.append(
                    _issue(
                        RULE_ALTERNATIVES_MISSING,
                        f"alternative(s) {', '.join(missing_reason)} for {business_key!r} "
                        "have no rejection reason (BP-DEC-001)",
                        business_key,
                    )
                )

        confidence = entry.get("confidence")
        impact = _highest_impact(risks)
        needs_confirmation = (
            isinstance(confidence, (int, float)) and confidence < threshold
        ) or impact == ImpactLevel.HIGH.value
        if needs_confirmation and not _has_confirmation(entry):
            reason = (
                f"confidence {confidence} is below the project threshold {threshold}"
                if isinstance(confidence, (int, float)) and confidence < threshold
                else "impact level is HIGH"
            )
            issues.append(
                _issue(
                    RULE_CONFIRMATION_REQUIRED,
                    f"mapping of {business_key!r} needs a user confirmation because {reason} "
                    "(BP-DEC-004, FR-MAP-003)",
                    business_key,
                    remediation="have the user confirm the decision; S2 records the "
                    "Question ID and answerer, or accepts a human-authored patch",
                )
            )

    for entry in view.container_entries(REGISTRY_CONTAINER):
        business_key = str(entry.get("businessKey") or "")
        disposition = entry.get("disposition")
        if disposition in {"CREATE", "UPDATE"} and not entry.get("decisionId"):
            issues.append(
                _issue(
                    RULE_DISPOSITION_DECIDED,
                    f"registry entry {business_key!r} has disposition {disposition} without a "
                    "decision, so its disposition was not decided in the mapping stage "
                    "(BP-DEC-003)",
                    business_key,
                )
            )
    return sort_issues(issues)


def _highest_impact(risks: list[Any]) -> str | None:
    order = {ImpactLevel.LOW.value: 0, ImpactLevel.MEDIUM.value: 1, ImpactLevel.HIGH.value: 2}
    levels = [
        str(risk.get("level"))
        for risk in risks
        if isinstance(risk, dict) and risk.get("level") in order
    ]
    if not levels:
        return None
    return max(levels, key=lambda level: order[level])


def _has_confirmation(entry: dict[str, Any]) -> bool:
    """A Question ID with an answerer, or a human-authored patch reference."""
    confirmation = entry.get("confirmation")
    if isinstance(confirmation, dict):
        if confirmation.get("questionId") and confirmation.get("answerer"):
            return True
        if confirmation.get("operator"):
            return True
    return bool(entry.get("approvalRef"))


def unknown_schema_version(version: int) -> bool:
    return version not in store.available_versions()
