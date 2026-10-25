"""Request bodies for the endpoints that existed before S5IF (S5IF-G02).

G02's reason is not decoration: with a free ``dict`` crossing the boundary, a bad
payload is discovered inside S2 or S4 and comes back as that subsystem's own
exception, which S5 cannot map to an ``errorCode`` for UI-REQ-002.  Declaring the
body here means S1 answers 422 with ``VALIDATION_ERROR`` and a ``details`` naming the
field.

Two payloads stay open on purpose, and both are open because a *provider* defines
them rather than S1:

* ``POST /projects/{id}/inputs/{artifactId}`` upload — multipart, no JSON body;
* the section patch inside ``POST /stages/{id}/patches`` — its shape is S2's
  (S1 SRS 8.1: "资源模型由提供方公共 API 定义"), so S1 wraps it rather than inventing fields.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from factory_control.errors import ValidationFailedError
from factory_control.rest.schemas.common import CamelModel

DELIVERY_MODES = ("A", "B")

#: S2 splices the prefix straight into every code it derives (``{prefix}_{TYPE}_{key}``),
#: so anything outside a readable identifier would end up in the target instance verbatim.
_CODE_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,15}")


class TargetEnvironmentBody(CamelModel):
    environment_key: str
    promotion_order: int = 100
    is_design_reference: bool = False


class CreateProjectBody(CamelModel):
    """CP-PRJ-001.  ``targets`` is accepted as the older spelling of the same list."""

    name: str
    owner_name: str
    description: str = ""
    delivery_mode: str = "A"
    project_type: str = "greenfield"
    # S2's InitialProjectInfo requires a non-empty constraint and CP-PRJ-001 lists it
    # as a creation input; S1 must not invent a DHIS2 version range for a project.
    version_constraints: str = "2.41.x"
    default_language: str = "zh"
    translation_languages: list[str] = Field(default_factory=list)
    # BP-REG-009: S2 assigns every managed object's code from the project's generation
    # policy and replaces an author-supplied one, so this prefix — not the patch — is the
    # only way to make a code traceable to the run that created it (FR-TST-002).
    code_prefix: str = ""
    target_environments: list[TargetEnvironmentBody] = Field(default_factory=list)
    targets: list[TargetEnvironmentBody] | None = None

    @model_validator(mode="after")
    def _check(self) -> CreateProjectBody:
        if self.delivery_mode not in DELIVERY_MODES:
            raise ValidationFailedError(
                "deliveryMode must be A or B",
                details={"field": "deliveryMode", "value": self.delivery_mode, "allowed": list(DELIVERY_MODES)},
            )
        if not (self.version_constraints or "").strip():
            raise ValidationFailedError(
                "versionConstraints is required to create a project",
                details={"field": "versionConstraints"},
            )
        if self.code_prefix and not _CODE_PREFIX.fullmatch(self.code_prefix):
            raise ValidationFailedError(
                "codePrefix must start with a letter and contain only letters, digits and underscores",
                details={"field": "codePrefix", "value": self.code_prefix, "pattern": _CODE_PREFIX.pattern},
            )
        if not self.target_environments and self.targets:
            self.target_environments = self.targets
        return self

    def to_payload(self) -> dict[str, Any]:
        """The shape ``PRJ.CreateProjectRequest.from_payload`` already reads."""
        return {
            "name": self.name,
            "ownerName": self.owner_name,
            "description": self.description,
            "deliveryMode": self.delivery_mode,
            "projectType": self.project_type,
            "versionConstraints": self.version_constraints,
            "defaultLanguage": self.default_language,
            "translationLanguages": list(self.translation_languages),
            "codePrefix": self.code_prefix,
            "targetEnvironments": [t.model_dump(by_alias=True) for t in self.target_environments],
        }


class UpdateProjectBody(CamelModel):
    """Mutable project fields; an omitted field means "leave it alone".

    Unknown keys are carried through rather than dropped: which fields are not
    editable, and what happens when someone tries to change one, is
    ``PRJ.update_project``'s judgement (it answers 409 ``INVALID_STATE``), not this
    model's.  Swallowing the key here would turn that refusal into a silent no-op.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow", from_attributes=True
    )

    name: str | None = None
    description: str | None = None
    owner_name: str | None = None
    default_language: str | None = None
    translation_languages: list[str] | None = None
    version_constraints: str | None = None
    project_type: str | None = None

    def to_changes(self) -> dict[str, Any]:
        changes = self.model_dump(by_alias=True, exclude_none=True)
        changes.update({k: v for k, v in (self.model_extra or {}).items() if v is not None})
        return changes


class ImportProjectBody(CamelModel):
    artifact_id: str

    @model_validator(mode="after")
    def _check(self) -> ImportProjectBody:
        if not (self.artifact_id or "").strip():
            raise ValidationFailedError("artifactId is required", details={"field": "artifactId"})
        return self


class TargetEnvironmentListBody(CamelModel):
    target_environments: list[TargetEnvironmentBody] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"targetEnvironments": [t.model_dump(by_alias=True) for t in self.target_environments]}


class ProjectSettingsBody(CamelModel):
    gate_reminder_hours: int | None = Field(default=None, ge=1, le=720)
    notification_emails: list[str] | None = None
    notification_webhook_ref: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class SystemSettingsBody(CamelModel):
    """``PUT /settings``: the seven keys S1 knows, each with an arbitrary value.

    The *shape* of a value belongs to :mod:`factory_control.settings` and to whoever
    reads it — ``platform_notification_channels`` for instance has been written as
    both ``[{type, ref}]`` and ``{webhookUrl, emails}`` — so S1 validates the key and
    not the value here.  The typed form S5 renders is on the response side
    (``SystemSettingsView``, S5IF-FLD10).  An unknown key still reaches the handler and
    answers 404 rather than being dropped by ``extra="ignore"``.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow", from_attributes=True
    )

    settings: dict[str, Any] | None = None
    idempotency_retention_hours: Any = None
    event_retention_days: Any = None
    default_gate_reminder_hours: Any = None
    max_upload_bytes: Any = None
    allowed_upload_types: Any = None
    archived_project_retention_days: Any = None
    platform_notification_channels: Any = None
    stage_auto_continue: Any = None

    def to_updates(self) -> dict[str, Any]:
        merged: dict[str, Any] = dict(self.settings or {})
        named = self.model_dump(by_alias=False, exclude_none=True)
        named.pop("settings", None)
        merged.update(named)
        # Keys a caller sent that S1 does not model still have to be refused, so pass
        # them through and let the handler answer 404 as it always has.
        merged.update({k: v for k, v in (self.model_extra or {}).items() if v is not None})
        return merged


class ManualCheckBody(CamelModel):
    """The verdict is not validated here.

    An unrecognised outcome means the stage is not in MANUAL_CHECK to be resolved at
    all, which the state machine reports as 409 ``INVALID_STATE`` (附录 A /
    CP-WF-019).  A 422 here would describe a well-formed request as malformed.
    """

    outcome: str
    note: str = ""


class StagePatchBody(CamelModel):
    """The wrapper is S1's; ``patch`` itself is S2's document-level operation set."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow", from_attributes=True
    )

    patch: dict[str, Any] | None = None

    def to_patch(self) -> dict[str, Any]:
        """``{"patch": {...}}`` or the patch inline — both have shipped, so both stay.

        S1 cannot validate the inner document: its shape is S2's to define
        (S1 SRS 8.1 "资源模型由提供方公共 API 定义").
        """
        if self.patch is not None:
            return dict(self.patch)
        return dict(self.model_extra or {})


class StageQuestionBody(CamelModel):
    local_id: str | None = None
    text: str
    severity: str = "MEDIUM"
    affects: list[str] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    blocking: bool = False


class WarningDisposition(CamelModel):
    issue_id: str | None = None
    disposition: str | None = None
    note: str | None = None


class ApprovalConfirmation(CamelModel):
    id: str | None = None
    confirmed: bool = True
    note: str | None = None


class ApproveGateBody(CamelModel):
    """S5IF-FLD07: ``seenBoundHashes`` may be hashes or ``{refId, contentHash}`` objects."""

    gate_type: str = "GATE"
    seen_bound_hashes: list[Any] = Field(default_factory=list)
    warning_dispositions: list[WarningDisposition] = Field(default_factory=list)
    confirmations: list[ApprovalConfirmation] = Field(default_factory=list)
    comment: str | None = None

    @model_validator(mode="after")
    def _check_hashes(self) -> ApproveGateBody:
        for item in self.seen_bound_hashes:
            if not isinstance(item, (str, dict)):
                raise ValidationFailedError(
                    "seenBoundHashes items must be hashes or {refId,contentHash} objects",
                    details={"field": "seenBoundHashes", "received": type(item).__name__},
                )
        return self


class RejectGateBody(CamelModel):
    gate_type: str = "GATE"
    reason: str
    warning_dispositions: list[WarningDisposition] = Field(default_factory=list)

    # ``reason`` is required as a key but not as content: an empty one is the
    # approver acting in the wrong state, and ``G.reject_gate`` answers 409.



class AnswerQuestionBody(CamelModel):
    suggestion_id: str | None = None
    #: a MULTIPLE question answers with a set; SINGLE questions may still use the singular
    #: ``suggestionId`` so existing clients keep working
    suggestion_ids: list[str] = Field(default_factory=list)
    custom_text: str | None = None

    @model_validator(mode="after")
    def _one_of(self) -> AnswerQuestionBody:
        # CP-Q-005: an answer picks a suggestion or supplies custom text, never neither.
        if not self.suggestion_id and not self.suggestion_ids and not (self.custom_text or "").strip():
            raise ValidationFailedError(
                "an answer must choose a suggestionId or give customText",
                details={"field": "suggestionId|suggestionIds|customText"},
            )
        return self


class QuestionnaireAnswerItem(CamelModel):
    """One row of the review screen's single submit."""

    question_id: str
    suggestion_ids: list[str] = Field(default_factory=list)
    custom_text: str | None = None

    @model_validator(mode="after")
    def _one_of(self) -> QuestionnaireAnswerItem:
        if not self.question_id:
            raise ValidationFailedError(
                "every questionnaire answer needs a questionId",
                details={"field": "questionId"},
            )
        if not self.suggestion_ids and not (self.custom_text or "").strip():
            raise ValidationFailedError(
                "an answer must choose a suggestion or give customText",
                details={"field": "questionId", "questionId": self.question_id},
            )
        return self


class AnswerQuestionsBody(CamelModel):
    """All answers of one questionnaire, committed together (CP-Q-005)."""

    answers: list[QuestionnaireAnswerItem] = Field(min_length=1, max_length=200)

    def as_service_answers(self) -> list[dict[str, Any]]:
        return [
            {
                "questionId": item.question_id,
                "suggestionIds": list(item.suggestion_ids),
                "customText": item.custom_text,
            }
            for item in self.answers
        ]


class CreateRunBody(CamelModel):
    environment_key: str

    @model_validator(mode="after")
    def _check(self) -> CreateRunBody:
        # DX-ENV-004 / FR-ENV-003: no default environment, ever.
        if not (self.environment_key or "").strip():
            raise ValidationFailedError(
                "environmentKey is required; S1 has no default environment",
                details={"field": "environmentKey"},
            )
        return self


class AuditVerifyBody(CamelModel):
    first_sequence: int | None = Field(default=None, ge=0)
    last_sequence: int | None = Field(default=None, ge=0)


class CreateChangeRequestBody(CamelModel):
    reason: str
    affected_business_keys: list[str] = Field(default_factory=list)
    #: 总体 SRS 15.6's directed-deletion strategy: the request itself names the DEPRECATED objects
    #: it retires.  S2's impact read answers no such list (its ``ImpactSet`` has no such field), so
    #: this is the only place a deletion can be authorized — see services/change_requests.py.
    deletion_candidates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reason_required(self) -> CreateChangeRequestBody:
        if not (self.reason or "").strip():
            raise ValidationFailedError(
                "a change request needs a reason",
                details={"fields": [{"path": "reason", "message": "a change request needs a reason"}]},
            )
        return self


class RejectChangeRequestBody(CamelModel):
    reason: str = ""


__all__ = [
    "AnswerQuestionBody",
    "ApproveGateBody",
    "AuditVerifyBody",
    "CreateChangeRequestBody",
    "CreateProjectBody",
    "CreateRunBody",
    "ImportProjectBody",
    "ManualCheckBody",
    "ProjectSettingsBody",
    "RejectChangeRequestBody",
    "RejectGateBody",
    "StagePatchBody",
    "StageQuestionBody",
    "SystemSettingsBody",
    "TargetEnvironmentBody",
    "TargetEnvironmentListBody",
    "UpdateProjectBody",
]
