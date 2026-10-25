"""Registry, UID and decision DTOs (S2 SRS 7.4)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from factory_blueprint.enums import (
    BindingResolution,
    Disposition,
    ImpactLevel,
    RegistryEntryStatus,
    UidAllocationStatus,
)


class ObservationBaseline(BaseModel):
    """The design-reference-environment observation used for drift detection."""

    model_config = ConfigDict(frozen=True)

    source_environment: str | None = None
    observed_last_updated: str | None = None
    observed_hash: str | None = None
    discovery_result_id: str | None = None


class RegistryEntry(BaseModel):
    """BP-REG-001."""

    model_config = ConfigDict(frozen=True)

    business_key: str
    dhis2_type: str
    disposition: Disposition
    status: RegistryEntryStatus
    uid: str | None = None
    code: str | None = None
    managed: bool
    controlled_fields: list[str] = Field(default_factory=list)
    decision_id: str | None = None
    observed: ObservationBaseline = ObservationBaseline()
    environment_bindings: dict[str, str] = Field(default_factory=dict)


class ManagedObject(BaseModel):
    """One entry of a project's managed scope in one environment (BP-REG-011)."""

    model_config = ConfigDict(frozen=True)

    business_key: str
    dhis2_type: str
    uid: str
    disposition: Disposition
    code: str | None = None
    controlled_fields: list[str] = Field(default_factory=list)
    observed_last_updated: str | None = None
    observed_hash: str | None = None


class BindingInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment_key: str
    business_key: str
    uid: str
    resolution: BindingResolution
    observed_last_updated: str | None = None
    observed_hash: str | None = None
    discovery_result_id: str | None = None
    resolved_by: str | None = None
    note: str | None = None
    pending_reparse: bool = False


class UidPoolStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    available: int
    low_water_mark: int
    source_environment: str | None = None
    needs_replenishment: bool


class DecisionInfo(BaseModel):
    """BP-DEC-001/002 plus the confirmation record (BP-DEC-004)."""

    model_config = ConfigDict(frozen=True)

    decision_id: str
    type: str
    subject: str | None = None
    selected: dict[str, Any] | None = None
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str | None = None
    risks: list[Any] = Field(default_factory=list)
    confidence: float | None = None
    impact_level: ImpactLevel | None = None
    confirmation: dict[str, Any] | None = None
    affected_artifacts: list[str] = Field(default_factory=list)


class OwnershipConflict(BaseModel):
    """BP-REG-012: another project manages the same UID in the same environment."""

    model_config = ConfigDict(frozen=True)

    uid: str
    environment_key: str
    business_key: str
    dhis2_type: str
    other_project_id: str
    other_disposition: Disposition


class UidAllocationInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    uid: str
    project_id: str
    business_key: str
    dhis2_type: str
    source_environment: str
    status: UidAllocationStatus
