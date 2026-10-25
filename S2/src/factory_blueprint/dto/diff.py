"""Object-level diff DTOs (BP-DIFF-001, overall SRS 15.5)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FieldChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    before: Any = None
    after: Any = None


class ObjectDiff(BaseModel):
    """One object as added, updated (field level), unchanged or reference-only."""

    model_config = ConfigDict(frozen=True)

    business_key: str
    dhis2_type: str | None = None
    uid: str | None = None
    state: str = Field(description="ADDED | UPDATED | UNCHANGED | REFERENCE_ONLY | REMOVED")
    disposition: str | None = None
    changes: list[FieldChange] = Field(default_factory=list)
    section: str | None = None


class ProfileDifference(BaseModel):
    """One way this environment's Capability Profile differs from the baseline.

    AC-CAP-01 asks for the difference to be *shown*: a reviewer comparing the Diff of a
    v2.41 and a v2.42 deploy of one version must be able to tell that the change came
    from the version, not from somebody editing the Blueprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: str
    dhis2_type: str | None = None
    name: str
    detail: str


class DiffReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    kind: str
    from_version: str | None = None
    to_version: str | None = None
    environment_key: str | None = None
    objects: list[ObjectDiff] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    #: which Profile decided this compile's shape, and what it changed (BP-CMP-030)
    profile_version: str | None = None
    #: the Profile is named by its content hash, never by a mutable artifact ID
    profile_hash: str | None = None
    dhis2_version: str | None = None
    profile_differences: list[ProfileDifference] = Field(default_factory=list)

    def by_state(self) -> dict[str, list[ObjectDiff]]:
        grouped: dict[str, list[ObjectDiff]] = {}
        for item in self.objects:
            grouped.setdefault(item.state, []).append(item)
        return grouped
