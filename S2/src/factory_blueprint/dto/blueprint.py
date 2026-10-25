"""Blueprint version and semantics DTOs (S2 SRS 7.1, 7.2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from factory_blueprint.enums import ChangeLevel, DeliveryMode, DesignStage, ProjectType, Section


class InitialProjectInfo(BaseModel):
    """The ``project`` and ``generationPolicy`` content of version ``0.1.0``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_mode: DeliveryMode
    project_type: ProjectType
    target_dhis2_version_constraint: str = Field(min_length=1, max_length=128)
    default_language: str = Field(min_length=2, max_length=16)
    translation_languages: list[str] = Field(default_factory=list, max_length=32)
    display_name: str | None = None
    summary: str | None = None
    code_prefix: str | None = None
    naming_convention: str | None = None
    uid_pool_batch_size: int | None = Field(default=None, ge=1, le=10000)
    uid_pool_low_water_mark: int | None = Field(default=None, ge=0, le=10000)
    decision_confidence_threshold: float | None = Field(default=None, ge=0, le=1)
    extraction_confidence_threshold: float | None = Field(default=None, ge=0, le=1)
    source_artifact_ids: list[str] = Field(default_factory=list)


class SectionInfo(BaseModel):
    """One section of the Blueprint, as the manual-authoring catalog describes it (BP-SCH-008)."""

    model_config = ConfigDict(frozen=True)

    name: str
    #: the JSON Pointers under the section that hold keyed entries; empty for a section
    #: whose whole value is one object, which the editor then edits unstructured
    entry_containers: list[str] = Field(default_factory=list)
    #: the field holding each entry's identity, parallel to ``entry_containers``;
    #: ``None`` marks a keyless whole-object section
    key_fields: list[str | None] = Field(default_factory=list)


class SectionCatalog(BaseModel):
    """The section list a manual-authoring form or an Agent asks for before section schemas."""

    model_config = ConfigDict(frozen=True)

    #: the schema version these sections belong to, which is the current one unless the
    #: caller asked for a specific version
    schema_version: int
    current_schema_version: int
    available_schema_versions: list[int] = Field(default_factory=list)
    sections: list[SectionInfo] = Field(default_factory=list)


class VersionInfo(BaseModel):
    """BP-VER-001: everything recorded about one immutable version."""

    model_config = ConfigDict(frozen=True)

    blueprint_id: str
    project_id: str
    version: str
    parent_version: str | None = None
    schema_version: int
    change_level: ChangeLevel
    content_hash: str
    artifact_id: str
    status: str
    source_artifact_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    created_by: str
    created_at: str
    promoted_from_stage: DesignStage | None = None


class CurrentVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    content_hash: str
    artifact_id: str
    change_level: ChangeLevel
    schema_version: int


class ProjectSemantics(BaseModel):
    """The project-level semantics S1 and S3 need without reading the document."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    version: str
    delivery_mode: DeliveryMode
    project_type: ProjectType
    default_language: str
    translation_languages: list[str] = Field(default_factory=list)
    target_dhis2_version_constraint: str
    has_fixture_specifications: bool
    mapping_frozen: bool
    #: the stage the current version was promoted from, for Gate reasoning
    promoted_from_stage: DesignStage | None = None


class EntrySelector(BaseModel):
    """Minimal retrieval: by businessKey, decision, page or section (FR-AG-001)."""

    model_config = ConfigDict(frozen=True)

    business_keys: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    page_keys: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    #: include entries introduced by PROPOSED patches of the open draft
    source: str = "APPROVED"


class SelectedEntries(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    version: str | None = None
    source: str
    entries: list[dict[str, Any]] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
