"""Project, target-environment and project-setting contracts (existing routes, S5IF-G01)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from factory_control.rest.schemas.common import CamelModel, ListEnvelope


class ProjectStageView(CamelModel):
    id: str
    stage_type: str
    status: str
    attempt: int = 1
    deployment_run_id: str | None = None


class TargetEnvironmentView(CamelModel):
    environment_key: str
    promotion_order: int = 100
    is_design_reference: bool = False
    environment_class: str | None = None


class ProjectView(CamelModel):
    id: str
    name: str
    description: str | None = None
    owner_name: str | None = None
    delivery_mode: str = "A"
    project_type: str | None = None
    version_constraints: str | None = None
    default_language: str | None = None
    translation_languages: list[str] = Field(default_factory=list)
    milestone_status: str | None = None
    archived: bool = False
    current_blueprint_version: str | None = None
    workflow_id: str | None = None
    workflow_status: str | None = None
    current_stage_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    target_environments: list[TargetEnvironmentView] = Field(default_factory=list)
    stages: list[ProjectStageView] = Field(default_factory=list)


class ProjectPage(ListEnvelope):
    items: list[ProjectView] = Field(default_factory=list)


class TargetEnvironmentListView(ListEnvelope):
    items: list[TargetEnvironmentView] = Field(default_factory=list)


class ProjectSettingsView(CamelModel):
    gate_reminder_hours: int | None = None
    notification_emails: list[str] = Field(default_factory=list)
    notification_webhook_ref: str | None = None


class UpdatedIdsView(CamelModel):
    """The common "what changed" answer shared by several PUT handlers."""

    project_id: str | None = None
    updated: list[str] = Field(default_factory=list)


class TargetEnvironmentReplaceView(CamelModel):
    """The design reference may have moved, and S5 has to know whether it did."""

    design_reference_changed: bool = False
    previous_design_reference: str | None = None
    design_reference: str | None = None
    #: which stages the move invalidated (CP-PRJ-003), so S5 refreshes those views rather
    #: than leaving a PASSED stage on screen that the backend has already called void
    invalidated_stages: list[str] = Field(default_factory=list)
    target_environments: list[TargetEnvironmentView] = Field(default_factory=list)


class SettingsUpdateView(CamelModel):
    updated: list[str] = Field(default_factory=list)


class ArchiveView(CamelModel):
    project_id: str
    archived: bool


class DeleteAcceptedView(CamelModel):
    """202 because the data goes with S2's own delete; ``already`` covers the replay."""

    project_id: str
    deleted: bool = False
    already: bool = False
    status: str | None = None
    deleted_at: datetime | None = None
    requires: list[str] = Field(default_factory=list)


class UpgradeModeView(CamelModel):
    project_id: str
    delivery_mode: str | None = None
    invalidation: dict[str, Any] = Field(default_factory=dict)


class UploadInputView(CamelModel):
    artifact_id: str
    filename: str | None = None
    media_type: str | None = None
    size_bytes: int = 0
    content_hash: str | None = None
    data_classification: str | None = None
    stage_id: str | None = None


__all__ = [
    "ArchiveView",
    "DeleteAcceptedView",
    "ProjectPage",
    "ProjectSettingsView",
    "ProjectView",
    "SettingsUpdateView",
    "TargetEnvironmentListView",
    "TargetEnvironmentReplaceView",
    "TargetEnvironmentView",
    "UpdatedIdsView",
    "UploadInputView",
]
