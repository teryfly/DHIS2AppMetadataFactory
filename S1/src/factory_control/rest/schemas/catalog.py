"""Artifact, audit, notification and system-setting contracts.

S5IF-FLD10 gives each of the seven settings its own type so S5 can generate the
administration form instead of guessing that ``max_upload_bytes`` is a number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel

from factory_control.rest.schemas.common import CamelModel, ListEnvelope
from factory_control.rest.schemas.runs import ExecutionView

#: The seven keys, as the enum S5IF-FLD10 requires.  Spelled out because a Literal
#: built from ``SETTING_KEYS`` at runtime is not a type mypy can check;
#: ``test_setting_keys_are_the_seven_the_settings_module_owns`` fails if the two drift.
SettingKey = Literal[
    "idempotency_retention_hours",
    "event_retention_days",
    "default_gate_reminder_hours",
    "max_upload_bytes",
    "allowed_upload_types",
    "archived_project_retention_days",
    "platform_notification_channels",
    "stage_auto_continue",
]


class ArtifactView(CamelModel):
    id: str
    project_id: str | None = None
    workflow_id: str | None = None
    type: str | None = None
    version: int | str | None = None
    status: str | None = None
    content_hash: str | None = None
    size: int = 0
    media_type: str | None = None
    data_classification: str | None = None
    creator_subsystem: str | None = None
    creator_operator: str | None = None
    source_artifact_ids: list[str] = Field(default_factory=list)
    prev_version_id: str | None = None
    content_purged: bool = False
    created_at: datetime | None = None


class ArtifactPage(ListEnvelope):
    items: list[ArtifactView] = Field(default_factory=list)


class ArtifactDiffView(CamelModel):
    """S0's diff of two artifacts (FD-ART-013): a unified line list, or JSON paths."""

    kind: str | None = None
    a_id: str | None = None
    b_id: str | None = None
    changed: bool = False
    # A line is ``{type, text, aLine, bLine}`` in S0's own shape — line-based for text
    # artifacts, path-based for JSON, which is why it is not a bare string.
    lines: list[dict[str, Any]] = Field(default_factory=list)


class LineageView(CamelModel):
    artifact_id: str
    sources: list[str] = Field(default_factory=list)
    derived: list[str] = Field(default_factory=list)


class ReproduceView(CamelModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow", from_attributes=True)

    matches: bool | None = None
    hash: str | None = None


class ArtifactPurgeView(CamelModel):
    """FD-ART-010 through S1: the bytes are gone and the index says so."""

    artifact_id: str
    content_purged: bool


class AuditRecordView(CamelModel):
    id: str
    sequence: int | None = None
    occurred_at: datetime | None = None
    operator_type: str | None = None
    operator_name: str | None = None
    source_subsystem: str | None = None
    event_type: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    project_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    correlation_id: str | None = None
    redacted: bool = False
    hash: str | None = None
    prev_hash: str | None = None


class AuditPage(ListEnvelope):
    items: list[AuditRecordView] = Field(default_factory=list)


class AuditVerifyView(CamelModel):
    verified: bool
    first_sequence: int | None = None
    last_sequence: int | None = None
    record_count: int = 0
    broken_at: int | None = None
    message: str | None = None


class TraceApprovalView(CamelModel):
    """One approval on the COMMIT trace — who, and what they decided (AC-AUD-01)."""

    approver_name: str | None = None
    decision: str | None = None


class CommitTraceView(CamelModel):
    """S5IF-G03: the ``execution`` member used to be a raw snake_case dump."""

    execution_id: str | None = None
    environment_key: str | None = None
    blueprint_version: str | None = None
    package_artifact_id: str | None = None
    payload_artifact_id: str | None = None
    payload_hash: str | None = None
    # Who ran the two steps: the trace has to name them, AC-AUD-01.
    commit_operator: str | None = None
    validate_operator: str | None = None
    gate_id: str | None = None
    gate_status: str | None = None
    approvals: list[TraceApprovalView] = Field(default_factory=list)
    execution: ExecutionView | None = None
    import_report_artifact_id: str | None = None
    audit_trail: list[AuditRecordView] = Field(default_factory=list)


class NotificationView(CamelModel):
    id: str
    project_id: str | None = None
    type: str | None = None
    object_ref: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    read: bool = False
    created_at: datetime | None = None


class NotificationPage(ListEnvelope):
    items: list[NotificationView] = Field(default_factory=list)


class MarkReadView(CamelModel):
    """The notification, as it now stands: the same view the list answers with."""

    id: str
    project_id: str | None = None
    type: str | None = None
    object_ref: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    read: bool


class NotificationChannels(CamelModel):
    """One platform channel (CP-UI-017): ``{type, target, secretRef}``.

    ``target`` is where the notice goes; ``secretRef`` is a reference to whatever
    credential the send needs.  They are separate fields so no URL can ever carry a
    password in place of a reference — a value, a mask, a length and a prefix are all
    forbidden on this wire (S5IF-G09 / CP-API-015).
    """

    type: str | None = None
    target: str | None = None
    secret_ref: str | None = None


class SystemSettingsView(CamelModel):
    """The seven keys of ``settings.py`` with their value types stated (S5IF-FLD10).

    S5 builds the administration form from this component: a generated client has to
    know that ``max_upload_bytes`` is an integer and
    ``platform_notification_channels`` is a list of references, not ``unknown``.
    """

    idempotency_retention_hours: int = 24
    event_retention_days: int = 30
    default_gate_reminder_hours: int = 24
    max_upload_bytes: int = 104_857_600
    allowed_upload_types: list[str] = Field(default_factory=list)
    archived_project_retention_days: int = 90
    platform_notification_channels: list[NotificationChannels] = Field(default_factory=list)
    #: Stage types that start on their own once READY.  The approval Gate on each stage still
    #: needs a person; this only removes the "press Run" step.
    stage_auto_continue: list[str] = Field(default_factory=list)


class SystemSettingItem(CamelModel):
    """One setting as a key/value pair.

    The key is *data*, not a wire field name, so it keeps its ``snake_case`` spelling
    (S5IF-G03 exception) and is declared as the enum S5IF-FLD10 asks for — a client can
    switch on it.  ``values`` beside this list carries the same seven facts with one
    type per key, which is what a form generator needs.
    """

    key: SettingKey
    value: Any = None


class SystemSettingList(ListEnvelope):
    items: list[SystemSettingItem] = Field(default_factory=list)
    values: SystemSettingsView = Field(default_factory=SystemSettingsView)


class UploadLimitsView(CamelModel):
    """S5IF-FLD10: readable by the browser so its pre-check matches S1's."""

    max_upload_bytes: int
    allowed_upload_types: list[str] = Field(default_factory=list)


__all__ = [
    "ArtifactDiffView",
    "ArtifactPage",
    "ArtifactView",
    "AuditPage",
    "ArtifactPurgeView",
    "AuditRecordView",
    "AuditVerifyView",
    "CommitTraceView",
    "LineageView",
    "MarkReadView",
    "NotificationChannels",
    "NotificationPage",
    "NotificationView",
    "ReproduceView",
    "SystemSettingItem",
    "SystemSettingList",
    "SystemSettingsView",
    "TraceApprovalView",
    "UploadLimitsView",
]
