"""Change request contracts (existing routes, S5IF-G01)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from factory_control.rest.schemas.common import CamelModel


class ChangeRequestView(CamelModel):
    id: str
    project_id: str | None = None
    reason: str | None = None
    affected_business_keys: list[str] = Field(default_factory=list)
    deletion_candidates: list[str] = Field(default_factory=list)
    requested_by: str | None = None
    status: str
    impact_report_artifact_id: str | None = None
    affected_stages: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    rejected_reason: str | None = None
    result_blueprint_version: str | None = None
    created_at: datetime | None = None


class ChangeRequestList(CamelModel):
    items: list[ChangeRequestView] = Field(default_factory=list)


class ImpactView(CamelModel):
    change_request_id: str | None = None
    affected_stages: list[str] = Field(default_factory=list)
    affected_object_keys: list[str] = Field(default_factory=list)
    affected_pages: list[str] = Field(default_factory=list)
    affected_artifact_types: list[str] = Field(default_factory=list)
    deletion_candidates: list[str] = Field(default_factory=list)
    impact_report_artifact_id: str | None = None
    invalidation: dict[str, Any] = Field(default_factory=dict)


class ChangeRequestImpactView(ChangeRequestView):
    """The change request, plus what the analysis found.

    ``analyze`` answers with the row it just updated *and* the impact: the stage list and
    the report artifact are on the request, the affected objects come from S2's impact read,
    and S5's CR panel (UI-OPS-001) shows all of it in one screen.
    """

    deployed_environments: list[str] = Field(default_factory=list)
    impact: ImpactView = Field(default_factory=ImpactView)


__all__ = ["ChangeRequestImpactView", "ChangeRequestList", "ChangeRequestView", "ImpactView"]
