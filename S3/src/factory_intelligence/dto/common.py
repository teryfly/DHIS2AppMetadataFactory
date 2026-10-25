"""Shared DTOs of S3's public API (SRS 7, AI-RT-010/015/016).

Two rules govern these shapes, and both come from S1 rather than from taste:

* **Field names are S1's.**  ``DelegatingAdapter._coerce`` calls
  ``dto.model_validate(value.model_dump())`` (``factory_control/adapters/delegating.py:53``),
  so a result whose field were renamed in S3 would validate into S1's mirror with the
  renamed entry silently dropped — ``extra="ignore"`` makes that failure invisible.  The
  names below therefore match ``factory_control/ports/dto.py`` character for character,
  and ``tests/unit/test_contract.py`` asserts it against S1's own class fields.
* **Requests are read from attributes.**  S1 hands over its *own* model instance, not a
  dict, so the request models declare ``from_attributes=True``.
"""

from __future__ import annotations

from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

from factory_intelligence.enums import QuestionSeverity

#: The severities S1's ``QuestionSeverity`` accepts.  Anything else is coerced, never
#: refused: a model that invents "CRITICAL" must still get its question in front of a
#: human, at medium priority.
_VALID_SEVERITIES = {member.value for member in QuestionSeverity}

#: 00 doc 6.10 / BP-VAL-001: the only three levels a finding may carry.
_VALID_LEVELS = frozenset({"BLOCKING", "WARNING", "INFO"})


class ApiModel(BaseModel):
    """Base for S3 DTOs: lenient on input, exact on output."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )


class Suggestion(ApiModel):
    """One proposed answer (AI-RT-010).

    ``id`` is required, and a suggestion without one is a Schema failure rather than
    something to paper over: an answer selects a suggestion *by id*, so an anonymous
    option could never be resolved on resume.  The validation aliases exist because the
    model writes the SRS's own camelCase spelling (``changesUid``).
    """

    id: str = Field(min_length=1)
    text: str = ""
    rationale: str | None = None
    #: the option the agent recommends when the question is single-select; several may be
    #: recommended for a MULTIPLE question, because each is useful for a different reason
    recommended: bool = False
    changes_uid: bool = Field(
        default=False, validation_alias=AliasChoices("changes_uid", "changesUid")
    )
    regenerates: bool = False


class Question(ApiModel):
    """A question the agent cannot answer for itself (AI-RT-010, AI-RT-015).

    ``local_id`` is assigned by S3, not by the model: S1 dedupes on
    ``(agent_run_id, source_local_id)`` and *skips* a repeat, so two model-authored
    ``"Q1"``s would lose a question outright.  ``blocking`` is recomputed on the way out
    because HIGH is blocking whatever the model claimed (AI-RT-015).
    """

    local_id: str
    text: str
    severity: str = QuestionSeverity.MEDIUM.value
    #: SINGLE chooses one suggestion (or writes another); MULTIPLE chooses a set.  The UI
    #: renders the question as a radio group or a checkbox group from this value.
    answer_mode: str = Field(
        default="SINGLE", validation_alias=AliasChoices("answer_mode", "answerMode")
    )
    affects: list[str] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    blocking: bool = False
    #: AI-INP-009: which source said what, when several inputs disagree
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("severity")
    @classmethod
    def _known_severity(cls, value: str) -> str:
        return value if value in _VALID_SEVERITIES else QuestionSeverity.MEDIUM.value

    @property
    def is_blocking(self) -> bool:
        """HIGH always blocks; the field alone is never trusted (AI-RT-015)."""
        return self.blocking or self.severity == QuestionSeverity.HIGH.value


class Answer(ApiModel):
    """One answer supplied by S1 on resume (AI-RT-016, CP-Q-005).

    ``suggestion_ids``/``suggestion_texts`` carry a MULTIPLE answer: the model has to see
    every option the human chose, not only the first one.  ``suggestion_id`` stays for a
    SINGLE answer and for confirmation records written before multi-select existed.
    """

    question_local_id: str
    suggestion_id: str | None = None
    suggestion_ids: list[str] = Field(default_factory=list)
    suggestion_texts: list[str] = Field(default_factory=list)
    custom_text: str | None = None
    answered_by: str = "unknown"


class RejectedArtifact(ApiModel):
    """AI-POL-003: a denied item, named rather than silently dropped."""

    ref: str
    classification: str | None = None
    reason: str = ""
    allowed_classes: list[str] = Field(default_factory=list)


class Issue(ApiModel):
    """A finding, in S2's unified Issue structure (AI-RT-018, BP-VAL-001, 00 doc 6.10).

    The level field is ``level`` and nothing else: 00 doc 6.10 names it so across the
    factory, and S2, S4 and S1 all read ``level``.  ``severity`` stays accepted on the way
    *in* — a model or an older caller that writes it must not lose the finding, and a
    BLOCKING one must never be silently downgraded to this field's default.

    ``description``, ``object_key`` and ``suggested_action`` are emitted as aliases on the
    way *out* only: S1's port mirror declares those names with ``extra="ignore"``, so
    dropping them here would delete the text a reviewer reads rather than rename it.  They
    are computed from the canonical fields, so the two spellings cannot disagree.
    """

    issue_id: str
    rule_id: str | None = None
    #: BLOCKING | WARNING | INFO
    level: str = Field(
        default="WARNING",
        validation_alias=AliasChoices("level", "severity"),
    )
    message: str = Field(default="", validation_alias=AliasChoices("message", "description"))
    business_key: str | None = Field(
        default=None, validation_alias=AliasChoices("business_key", "businessKey", "object_key")
    )
    uid: str | None = Field(default=None, validation_alias=AliasChoices("uid", "object_uid"))
    dhis2_type: str | None = Field(default=None, validation_alias=AliasChoices("dhis2_type"))
    artifact_id: str | None = None
    location: str | None = None
    section: str | None = None
    source_stage: str | None = None
    remediation: str | None = Field(
        default=None, validation_alias=AliasChoices("remediation", "suggested_action")
    )
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        upper = str(value or "").strip().upper()
        return upper if upper in _VALID_LEVELS else "WARNING"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def description(self) -> str:
        return self.message

    @computed_field  # type: ignore[prop-decorator]
    @property
    def object_key(self) -> str | None:
        return self.business_key

    @computed_field  # type: ignore[prop-decorator]
    @property
    def object_uid(self) -> str | None:
        return self.uid

    @computed_field  # type: ignore[prop-decorator]
    @property
    def suggested_action(self) -> str | None:
        return self.remediation

    @property
    def is_blocking(self) -> bool:
        return self.level == "BLOCKING"

    @classmethod
    def from_blueprint(cls, issue: Any) -> Issue:
        """Translate one of S2's ``Issue`` objects, field for field.

        S2 is the defining owner of this structure (BP-VAL-001), so a finding that reaches a
        human through S3 says the same thing it said in S2 — the same rules
        (``BPV-PCH-*``), the same level, the same location.
        """
        level = getattr(issue, "level", None)
        section = getattr(issue, "section", None)
        return cls(
            issue_id=str(getattr(issue, "issue_id", "")),
            rule_id=getattr(issue, "rule_id", None),
            level=getattr(level, "value", None) or str(level or "WARNING"),
            message=str(getattr(issue, "message", "") or ""),
            business_key=getattr(issue, "business_key", None),
            uid=getattr(issue, "uid", None),
            dhis2_type=getattr(issue, "dhis2_type", None),
            artifact_id=getattr(issue, "artifact_id", None),
            location=getattr(issue, "location", None),
            section=getattr(section, "value", None) or (str(section) if section else None),
            source_stage=getattr(getattr(issue, "source_stage", None), "value", None),
            remediation=getattr(issue, "remediation", None),
            details=dict(getattr(issue, "details", None) or {}),
        )

    @classmethod
    def from_finding(cls, finding: Any, *, level: str = "BLOCKING") -> Issue:
        """One rung's finding of S3's own validation ladder (AI-RT-011/012).

        A finding that stopped the run *is* blocking: the patch was not submitted, and a
        reviewer needs that stated as a level rather than inferred from a sentence in
        ``denial_reasons``.
        """
        details = dict(getattr(finding, "details", None) or {})
        return cls(
            issue_id=f"aiss-{getattr(finding, 'rule_id', 'unknown')}",
            rule_id=str(getattr(finding, "rule_id", "") or ""),
            level=level,
            message=str(getattr(finding, "message", "") or ""),
            location=getattr(finding, "location", None),
            source_stage=details.get("sourceStage"),
            artifact_id=details.get("artifactId"),
            business_key=details.get("businessKey"),
            details={**details, "ladder": getattr(finding, "ladder", None)},
        )


__all__ = [
    "ApiModel",
    "Suggestion",
    "Question",
    "Answer",
    "RejectedArtifact",
    "Issue",
]
