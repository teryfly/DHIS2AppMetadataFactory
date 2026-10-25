"""Patch and draft DTOs (BP-DRF-002, S2 SRS 7.3)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from factory_blueprint.dto.issue import Issue
from factory_blueprint.enums import AuthorKind, DesignStage, DraftStatus, PatchStatus, Section


class PatchAuthor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AuthorKind
    #: agentRunId for AGENT, operator name for USER and SYSTEM
    ref: str = Field(min_length=1, max_length=128)


class Patch(BaseModel):
    """A structured Blueprint change proposal: RFC 6902 operations plus intent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patch_id: str = Field(min_length=1, max_length=64)
    base_version: str = Field(min_length=1, max_length=32)
    author: PatchAuthor
    decision_ids: list[str] = Field(default_factory=list)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    rationale: str | None = Field(default=None, max_length=4096)
    #: advisory only, never authoritative (BP-DRF-002)
    declared_impact: dict[str, Any] | None = None


class PatchContext(BaseModel):
    """What the caller knows about why this patch is being submitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: DesignStage
    change_request_id: str | None = None


class StageScope(BaseModel):
    """Which sections a draft may modify (appendix A)."""

    model_config = ConfigDict(frozen=True)

    stage: DesignStage
    sections: list[Section]


class MergeQuestionData(BaseModel):
    """The data for a "manual merge required" Question (BP-DRF-003).

    S2 does not create Questions; it returns this payload and the caller hands it
    to S1, which owns Question storage.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = "BLUEPRINT_MERGE_REQUIRED"
    project_id: str
    draft_id: str
    patch_id: str
    base_version: str
    draft_version: str
    draft_hash: str
    rationale: str | None = None
    question: str
    options: list[dict[str, Any]] = Field(default_factory=list)


class PatchOutcome(BaseModel):
    """Result of submitting a patch (S2 SRS 7.3)."""

    model_config = ConfigDict(frozen=True)

    patch_id: str
    status: PatchStatus
    draft_id: str
    draft_hash: str | None = None
    issues: list[Issue] = Field(default_factory=list)
    merge_question: MergeQuestionData | None = None
    #: UIDs allocated for entries this patch created (BP-REG-006)
    assigned_uids: dict[str, str] = Field(default_factory=dict)
    blocking_count: int = 0


class DraftInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_id: str
    project_id: str
    base_version: str
    stage: DesignStage
    stage_scope: list[Section]
    status: DraftStatus
    content_hash: str
    change_request_id: str | None = None
    frozen_hash: str | None = None
    promotable: bool | None = None
    open_patch_count: int = 0


class FreezeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_id: str
    draft_hash: str
    staging_handle: str
    promotable: bool
    issues: list[Issue] = Field(default_factory=list)
    blocking_count: int = 0
    warnings: list[Issue] = Field(default_factory=list)


class ImpactSet(BaseModel):
    """What a change reaches (BP-IMP-003/004)."""

    model_config = ConfigDict(frozen=True)

    sections: list[Section] = Field(default_factory=list)
    stages: list[DesignStage] = Field(default_factory=list)
    business_keys: list[str] = Field(default_factory=list)
    page_keys: list[str] = Field(default_factory=list)
    operation_keys: list[str] = Field(default_factory=list)
    artifact_types: list[str] = Field(default_factory=list)


class PromoteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    change_level: str
    artifact_id: str
    content_hash: str
    affected_stages: list[DesignStage] = Field(default_factory=list)
    impact: ImpactSet
    applied_patch_ids: list[str] = Field(default_factory=list)
