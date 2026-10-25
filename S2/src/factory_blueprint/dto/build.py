"""Build, validation and reproduction DTOs (S2 SRS 7.5)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from factory_blueprint.dto.issue import Issue
from factory_blueprint.enums import BuildKind, BuildStatus, ChangeLevel


class BuildContext(BaseModel):
    """External inputs a deterministic compile is allowed to know about.

    S1 supplies the same values again when it retries a build, which is what makes
    byte-level reproduction possible (BP-ASM-01, FR-CMP-001).
    """

    model_config = ConfigDict(frozen=True)

    built_at: datetime
    seed: int
    requester: str = Field(min_length=1, max_length=128)
    correlation_id: str | None = None

    def as_record(self) -> dict[str, object]:
        return {
            "builtAt": self.built_at.isoformat(),
            "seed": self.seed,
            "requester": self.requester,
        }


class BuildInput(BaseModel):
    """One external input of a build, referenced by artifact ID and hash.

    Only ``artifact_id``/``artifact_hash`` pairs are stored: an import into another
    deployment rewrites the IDs while the hashes must stay identical (BP-EXP-002).
    """

    model_config = ConfigDict(frozen=True)

    role: str
    artifact_id: str
    type: str
    artifact_hash: str


class ValidationSummary(BaseModel):
    """BP-VAL-003: the report artifact plus what a Gate needs to show."""

    model_config = ConfigDict(frozen=True)

    report_artifact_id: str | None = None
    blocking_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    warnings: list[Issue] = Field(default_factory=list)
    issues_digest: str | None = None


class BuildResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    build_id: str
    kind: BuildKind
    project_id: str
    version: str
    environment_key: str | None = None
    status: BuildStatus
    artifact_ids: dict[str, str] = Field(default_factory=dict)
    output_hashes: dict[str, str] = Field(default_factory=dict)
    package_hash: str | None = None
    validation: ValidationSummary | None = None
    binding_issues: list[Issue] = Field(default_factory=list)
    #: the hash the caller must present again at the Gate (FR-GATE-001)
    payload_hash: str | None = None
    #: pageKey -> FRONTEND_READY | NOT_READY, from the handoff completeness check.
    #: Chapter 7.5 lists "各页面状态" as an output of generate_frontend_package, and the
    #: HANDOFF_GATE decides on it, so it travels with the result rather than only inside
    #: the report artifact.
    handoff_pages: dict[str, str] = Field(default_factory=dict)


class ReproduceResult(BaseModel):
    """BP-CMP-004: recompile from a build record and compare hashes."""

    model_config = ConfigDict(frozen=True)

    build_id: str
    consistent: bool
    expected_hashes: dict[str, str] = Field(default_factory=dict)
    actual_hashes: dict[str, str] = Field(default_factory=dict)
    mismatched: list[str] = Field(default_factory=list)
    message: str | None = None


class VersionLevel(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    change_level: ChangeLevel
