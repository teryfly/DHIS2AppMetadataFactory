"""The stage's rejection and failure record (S5IF-FLD02 【裁决】).

``feedback`` is what UI-WB-007 shows at the top of a stage: why it was refused, and
where to go to fix it.  It is S1's own column, but until now each writer shaped it
differently — one with a ``kind``, one without, one carrying snake_case issues — so S5
could not render it without guessing.  One builder, one shape.

``kind`` is deliberately an *open* value set: the ruling names six, S1 may add more, and
S5 renders anything it has not seen as a title plus the reason text plus the jumpable
references.  A client-side whitelist would turn an S1 addition into a broken screen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

GATE_REJECTED = "GATE_REJECTED"
PATCH_REJECTED = "PATCH_REJECTED"
VALIDATION = "VALIDATION"
POLICY_DENIED = "POLICY_DENIED"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
EXECUTION_FAILED = "EXECUTION_FAILED"

#: Published as the known set; not a closed enum, and that is the requirement.
FEEDBACK_KINDS: tuple[str, ...] = (
    GATE_REJECTED,
    PATCH_REJECTED,
    VALIDATION,
    POLICY_DENIED,
    BUDGET_EXCEEDED,
    EXECUTION_FAILED,
)


def stage_feedback(
    kind: str,
    reason: str,
    *,
    source_stage: str | None = None,
    gate_id: str | None = None,
    issues: list[Any] | None = None,
    created_at: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One feedback record, with ``issues`` in the camelCase S5 reads.

    ``extra`` is for what a particular verdict adds on top — a Gate rejection names its
    approver, a freeze names the blocking issues' report — because dropping it would hide
    the one detail that explains the refusal.
    """
    out: dict[str, Any] = {
        "kind": kind,
        "reason": reason,
        "sourceStage": source_stage,
        "gateId": gate_id,
        "issues": [_issue_view(issue) for issue in issues or []],
        "createdAt": created_at.isoformat() if created_at is not None else None,
    }
    out.update(dict(extra or {}))
    return out


def _issue_view(issue: Any) -> dict[str, Any]:
    if isinstance(issue, dict):
        return dict(issue)
    dump = getattr(issue, "model_dump", None)
    if callable(dump):
        return dict(dump(by_alias=True))
    return {"description": str(issue)}


__all__ = [
    "BUDGET_EXCEEDED",
    "EXECUTION_FAILED",
    "FEEDBACK_KINDS",
    "GATE_REJECTED",
    "PATCH_REJECTED",
    "POLICY_DENIED",
    "VALIDATION",
    "stage_feedback",
]
