"""Deployment Run and execution contracts (existing routes, S5IF-FLD06 / G03)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from factory_control.rest.schemas.common import CamelModel, IssueView, ListEnvelope
from factory_control.rest.schemas.gates import GateView


class RunView(CamelModel):
    id: str
    workflow_id: str | None = None
    environment_key: str | None = None
    blueprint_version: str | None = None
    status: str
    outdated: bool = False
    validate_payload_hash: str | None = None
    validate_operator: str | None = None
    commit_operator: str | None = None
    execution_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # S5IF-FLD06: UI-DEP-001 shows the last VERIFIED time and the environment class.
    project_id: str | None = None
    project_name: str | None = None
    environment_class: str | None = None
    last_verified_at: datetime | None = None
    post_verify_artifact_id: str | None = None


class RunList(ListEnvelope):
    items: list[RunView] = Field(default_factory=list)


class RunPage(ListEnvelope):
    """The cross-project Run list (S5IF-FLD04), ordered by ``updatedAt`` descending."""

    items: list[RunView] = Field(default_factory=list)


class RunStages(CamelModel):
    id: str
    stage_type: str
    status: str
    attempt: int = 1


class RunDetailView(CamelModel):
    run: RunView
    stages: list[RunStages] = Field(default_factory=list)
    gates: list[GateView] = Field(default_factory=list)


class CreateRunView(CamelModel):
    """``POST /workflows/{id}/deployment-runs`` answers with the Run *object*, not its id:
    the caller needs the stage list R1…R6 that was just created alongside it."""

    run: RunView
    stages: list[str] = Field(default_factory=list)


class CancelRunView(CamelModel):
    deployment_run_id: str
    status: str


class ExecutionView(CamelModel):
    """S5IF-G03: this whole projection used to be a raw snake_case ``model_dump()``."""

    execution_id: str | None = None
    status: str
    report_artifact_id: str | None = None
    import_report_artifact_id: str | None = None
    applied: bool | None = None
    payload_hash: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[IssueView] = Field(default_factory=list)


class ExecutionReportView(CamelModel):
    execution_id: str
    report_artifact_id: str | None = None
    import_report_artifact_id: str | None = None


class ReconcileView(CamelModel):
    outcome: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class CommitWindowSlot(CamelModel):
    """The environment's own commit schedule, read live from S4 (CP-WF-027)."""

    within_window: bool = True
    windows: list[dict[str, Any]] = Field(default_factory=list)
    timezone: str = "UTC"
    emergency_locked: bool = False


class ArtifactSlot(CamelModel):
    """One artifact a view points at, with what the console needs to decide about the link.

    ``contentPurged`` is the fact that decides it: FD-ART-010 keeps the metadata and hash and
    flips a boolean when the bytes go, and reading such an artifact raises ``ArtifactPurgedError``
    (S0 ``artifacts/service.py:313``) — so a download offered without this flag is a click that
    fails.  ``status`` is the lifecycle (DRAFT/APPROVED/SUPERSEDED) and says nothing about purging;
    it used to be the only thing here, which is why ``_artifact_slot``'s comment claimed a promise
    the shape could not keep.
    """

    status: str | None = None
    artifact_id: str | None = None
    content_purged: bool = False


class DriftSlot(CamelModel):
    """The last recorded drift verdict — never a fresh check (S5IF-FLD05)."""

    drifted: bool = False
    checked_at: str | None = None
    objects: list[dict[str, Any]] = Field(default_factory=list)
    run_id: str | None = None


class ConnectionSlot(CamelModel):
    ok: bool = False
    result: str | None = None
    missing_authorities: dict[str, list[str]] = Field(default_factory=dict)
    last_tested_at: datetime | None = None


class FreshnessSlot(CamelModel):
    state: str | None = None
    probed_at: str | None = None
    artifact_id: str | None = None


class ValidationSlot(CamelModel):
    static_passed: bool = False
    dhis2_validate_passed: bool = False
    blocking_count: int = 0
    warning_count: int = 0
    report_artifact_id: str | None = None


class ApprovalSlot(CamelModel):
    package_gate: str | None = None
    deploy_gate: str | None = None
    required_approvals: int = 1
    approvals: int = 0


class VerificationSlot(CamelModel):
    last_verified_at: datetime | None = None
    post_verify_artifact_id: str | None = None
    consistent: bool | None = None


class ExecutionSlot(CamelModel):
    """The COMMIT's own record, with each report's artifact slot beside it.

    ``ArtifactSlot`` rather than a bare ``str`` because the producer already answers that
    (``services/runs.py``'s ``_artifact_slot``), and the slot carries ``contentPurged``: a report
    whose bytes are gone under the retention rule must not be offered as a download that fails on
    click.  Declaring the id alone made ``GET /deployment-runs/{runId}/summary`` a 500 for every
    identity the moment a real execution report existed — which no test had, because no fixture
    gave the COMMIT stage an ``EXECUTION_REPORT`` output (S5IF-FLD05, CP-UI-012, FD-ART-010).
    """

    execution_id: str | None = None
    status: str | None = None
    report_artifact_id: ArtifactSlot | None = None
    import_report_artifact_id: ArtifactSlot | None = None


class PayloadSlot(CamelModel):
    hash: str | None = None
    package_artifact_id: str | None = None


class ChangeStatsView(CamelModel):
    """How much this Run changes, and how much of it sits outside the managed scope."""

    create: int = 0
    update: int = 0
    delete: int = 0
    external_references: int = 0


class RunSummaryView(CamelModel):
    """The deployment console in one reply (S5IF-FLD05), built with zero DHIS2 calls.

    ``changeStats`` is empty until S2 publishes the DIFF_REPORT read (S5IF-UP02); the
    artifact id it would summarise is already here.  ``drift``, ``capabilityFreshness`` and
    ``connection`` are *recorded* verdicts, so asking for a fresh one is its own action
    (``POST /deployment-runs/{runId}/drift-check``), which is how the panel stays inside
    CP-NFR-03 and off the target instance.
    """

    run_id: str
    workflow_id: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    environment_key: str | None = None
    environment_class: str | None = None
    blueprint_version: str | None = None
    outdated: bool = False
    status: str
    dhis2_version: str | None = None
    profile_version: str | None = None
    capability_freshness: FreshnessSlot | None = None
    connection: ConnectionSlot | None = None
    drift: DriftSlot | None = None
    snapshot: ArtifactSlot | None = None
    validation: ValidationSlot = Field(default_factory=ValidationSlot)
    approval: ApprovalSlot = Field(default_factory=ApprovalSlot)
    change_stats: ChangeStatsView = Field(default_factory=ChangeStatsView)
    diff_report_artifact_id: str | None = None
    maintenance_actions: list[dict[str, Any]] = Field(default_factory=list)
    payload: PayloadSlot = Field(default_factory=PayloadSlot)
    commit_window: CommitWindowSlot = Field(default_factory=CommitWindowSlot)
    verification: VerificationSlot = Field(default_factory=VerificationSlot)
    execution: ExecutionSlot = Field(default_factory=ExecutionSlot)
    gates: list[GateView] = Field(default_factory=list)
    stages: list[RunStages] = Field(default_factory=list)
    evaluated_at: str | None = None


__all__ = [
    "CancelRunView",
    "ChangeStatsView",
    "CreateRunView",
    "ExecutionReportView",
    "ExecutionView",
    "ReconcileView",
    "RunDetailView",
    "RunList",
    "RunPage",
    "RunSummaryView",
    "RunView",
]
