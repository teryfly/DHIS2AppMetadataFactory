"""The validation report that the ladder produces (AI-RT-011, AI-RT-012, AI-RT-013).

One type carries every failure the runtime can feed back to the model, whether it came
from the envelope, from JSON Schema, from an agent constraint check or from S2 rejecting a
patch.  That uniformity is the requirement: AI-RT-011 and AI-RT-013 both say the reason is
"作为校验错误反馈给模型", and if each rung reported in its own shape the corrective prompt
would need four formats and the model would be graded on the least consistent one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory_intelligence.dto.common import Issue

#: Which rung of the ladder produced a finding.  Recorded so a test can assert that a
#: constraint violation is *not* reported as a schema failure (they consume different
#: retry budgets — see the plan's ruling 6).
LADDER_ENVELOPE = "envelope"
LADDER_SCHEMA = "jsonSchema"
LADDER_CONSTRAINT = "constraint"
LADDER_BLUEPRINT = "blueprint"


@dataclass(frozen=True)
class Finding:
    """One reason the answer was refused."""

    rule_id: str
    message: str
    ladder: str
    location: str | None = None
    #: e.g. the offending key, so the corrective block can say more than "wrong"
    details: dict[str, Any] = field(default_factory=dict)

    def as_line(self) -> str:
        where = f" (at {self.location})" if self.location else ""
        return f"{self.rule_id}: {self.message}{where}"


@dataclass
class ValidationReport:
    """All findings of one ladder pass, in a stable order."""

    findings: list[Finding] = field(default_factory=list)
    #: the rung that stopped the pass, when one did — later rungs do not run
    stopped_at: str | None = None

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def count(self) -> int:
        return len(self.findings)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings: list[Finding]) -> None:
        self.findings.extend(findings)

    def sorted_findings(self) -> list[Finding]:
        """Deterministic order, because the corrective prompt is part of the run record."""
        return sorted(self.findings, key=lambda item: (item.ladder, item.rule_id, item.location or "", item.message))

    def as_payload(self) -> dict[str, Any]:
        """The block appended to the context for the one corrective retry (AI-RT-011)."""
        return {
            "kind": "validationErrors",
            "stoppedAt": self.stopped_at,
            "errors": [
                {
                    "rule": finding.rule_id,
                    "ladder": finding.ladder,
                    "message": finding.message,
                    "location": finding.location,
                }
                for finding in self.sorted_findings()
            ],
            # AI-RT-031: the feedback is data about the answer, not an instruction to
            # do anything else.
            "instruction": "Return one JSON object that satisfies the stated schema.",
        }

    def as_prompt_block(self) -> str:
        import json

        return json.dumps(self.as_payload(), ensure_ascii=False, indent=2, sort_keys=True)

    def messages(self) -> list[str]:
        return [finding.as_line() for finding in self.sorted_findings()]

    def to_issues(self, *, stage: str | None = None) -> list[Issue]:
        """The same findings in :class:`factory_intelligence.dto.common.Issue` form.

        Used when a run fails outright: S1's ``issues`` field is what a user reads, and a
        FAILED run with no explanation is the single most common complaint about an agent
        system (overall SRS FR-UI-010).
        """
        from factory_intelligence.dto.common import Issue

        return [
            Issue(
                issue_id=f"ai-{finding.ladder}-{index:03d}",
                rule_id=finding.rule_id,
                # 00 doc 6.10: the field is `level`, and a finding that stopped the ladder is
                # blocking whatever else it might have been called
                level="BLOCKING",
                message=finding.message,
                source_stage=stage,
                location=finding.location,
                details=dict(finding.details or {}),
            )
            for index, finding in enumerate(self.sorted_findings(), start=1)
        ]


__all__ = [
    "Finding",
    "ValidationReport",
    "LADDER_ENVELOPE",
    "LADDER_SCHEMA",
    "LADDER_CONSTRAINT",
    "LADDER_BLUEPRINT",
]
