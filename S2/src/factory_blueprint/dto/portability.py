"""Export, import and project-cleanup DTOs (S2 SRS 7.6)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from factory_blueprint.dto.issue import Issue


class ExportResult(BaseModel):
    """What one export produced: the artifact and the inputs it refers to."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    artifact_id: str
    content_hash: str
    files: dict[str, str] = Field(default_factory=dict)
    versions: list[str] = Field(default_factory=list)
    current_version: str | None = None
    #: every artifact ID the export refers to, so the caller can unpack them and
    #: hand back the old→new map that ``prepare_import`` needs (BP-EXP-002)
    referenced_artifact_ids: list[str] = Field(default_factory=list)


class ImportPreview(BaseModel):
    """The outcome of ``prepare_import``: what would be restored, and whether it can be."""

    model_config = ConfigDict(frozen=True)

    batch_id: str
    project_id: str
    source_project_id: str
    versions: list[str] = Field(default_factory=list)
    #: artifact IDs the export referenced but the caller's id_map did not cover
    unmapped_artifact_ids: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        from factory_blueprint.enums import IssueLevel

        return any(issue.level is IssueLevel.BLOCKING for issue in self.issues)

    @property
    def committable(self) -> bool:
        return not self.blocking and bool(self.versions)


class ImportResult(BaseModel):
    """The restored state, as the importing deployment now holds it."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    batch_id: str
    versions: list[str] = Field(default_factory=list)
    current_version: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    #: bindings are restored as pending: the environments of this deployment differ
    pending_reparse: int = 0
    rebuilt_from_content: bool = True

    def as_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ProjectCleanupResult(BaseModel):
    """BP-EXP-003: what was deleted, and the UID ledger that stayed behind."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    deleted: dict[str, int] = Field(default_factory=dict)
    retired_uids: int = 0
    retained_uids: int = 0
