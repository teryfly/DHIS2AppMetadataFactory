"""``cp`` schema tables (SRS 5).

All tables are declared on :class:`factory_control.foundation.CpBase`, which binds
them to schema ``cp`` (D-08).  Every foreign key stays inside ``cp``; references
to other subsystems are stored as bare IDs, plus a content hash where the SRS
requires a frozen state (D-14).

Three constraints encode business rules directly in the schema:

* ``uq_cp_stage_single_running`` — CP-WF-006 / CP-BR-02: at most one RUNNING stage
  per workflow, across the design line *and* every Deployment Run of it.
* ``uq_cp_question_source`` — CP-Q-004: dedupe by ``(agentRunId, source-local id)``
  so an activity retry cannot create the same question twice.
* ``uq_cp_design_stage`` / ``uq_cp_run_stage`` — one instance of each stage type
  in the design line and inside each Run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_control.credentials.crypto import ALGORITHM as _CREDENTIAL_ALGO
from factory_control.foundation import CpBase, VersionedAggregate, utcnow


class _Timestamps:
    """``created_at`` / ``updated_at`` on every entity (SRS 5 preamble)."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


#: What :class:`SecretRecord.ciphertext` is sealed with, recorded per row so an algorithm change
#: is a new value rather than a silent reinterpretation of old ones. The one true spelling lives in
#: :mod:`factory_control.credentials.crypto`, next to the code that performs it.


class Project(_Timestamps, CpBase):
    """CP-PRJ: management and flow attributes only — semantics live in Blueprint (D-15)."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    milestone_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    delivery_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="A")
    project_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    version_constraints: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    default_language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh")
    translation_languages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    current_blueprint_version: Mapped[str | None] = mapped_column(String(32))
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectTargetEnvironment(_Timestamps, CpBase):
    """CP-PRJ-002/003/004: registered S4 environmentKey, promotion order, design reference."""

    __tablename__ = "project_target_environments"
    __table_args__ = (
        UniqueConstraint("project_id", "environment_key", name="uq_cp_target_env_key"),
        Index("ix_cp_target_env_project", "project_id", "promotion_order"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("cp.projects.id", ondelete="CASCADE"), nullable=False
    )
    environment_key: Mapped[str] = mapped_column(String(128), nullable=False)
    promotion_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_design_reference: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ProjectSetting(_Timestamps, CpBase):
    """CP-NTF-002 / CP-GATE-016 per-project knobs. The webhook is a Secret *reference*."""

    __tablename__ = "project_settings"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("cp.projects.id", ondelete="CASCADE"), primary_key=True
    )
    gate_reminder_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    notification_emails: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    notification_webhook_ref: Mapped[str | None] = mapped_column(String(512))


class Workflow(_Timestamps, VersionedAggregate, CpBase):
    """CP-WF-001: exactly one per project. ``last_event_seq`` bumps under a row lock."""

    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("project_id", name="uq_cp_workflow_project"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("cp.projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_stage_id: Mapped[str | None] = mapped_column(String(64))
    last_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DeploymentRun(_Timestamps, VersionedAggregate, CpBase):
    """CP-WF-018/019/020: one Blueprint version onto one target environment."""

    __tablename__ = "deployment_runs"
    __table_args__ = (
        Index("ix_cp_run_workflow_env", "workflow_id", "environment_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("cp.workflows.id", ondelete="CASCADE"), nullable=False
    )
    environment_key: Mapped[str] = mapped_column(String(128), nullable=False)
    blueprint_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    outdated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    change_request_id: Mapped[str | None] = mapped_column(String(64))
    validate_payload_hash: Mapped[str | None] = mapped_column(String(80))
    validate_operator: Mapped[str | None] = mapped_column(String(255))
    commit_operator: Mapped[str | None] = mapped_column(String(255))
    execution_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Stage(_Timestamps, CpBase):
    """CP-WF-003: a step of the design line (``deployment_run_id`` NULL) or of a Run."""

    __tablename__ = "stages"
    __table_args__ = (
        Index(
            "uq_cp_design_stage",
            "workflow_id",
            "stage_type",
            unique=True,
            postgresql_where=text("deployment_run_id IS NULL"),
        ),
        Index(
            "uq_cp_run_stage",
            "deployment_run_id",
            "stage_type",
            unique=True,
            postgresql_where=text("deployment_run_id IS NOT NULL"),
        ),
        Index(
            "uq_cp_stage_single_running",
            "workflow_id",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
        Index("ix_cp_stage_workflow_status", "workflow_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("cp.workflows.id", ondelete="CASCADE"), nullable=False
    )
    deployment_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("cp.deployment_runs.id", ondelete="CASCADE")
    )
    stage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="LOCKED")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_blueprint_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    failure_source_stage: Mapped[str | None] = mapped_column(String(32))
    feedback: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    page_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StageRun(_Timestamps, CpBase):
    """CP-WF-013: one record per execution of a stage, with its idempotency key."""

    __tablename__ = "stage_runs"
    __table_args__ = (
        UniqueConstraint("stage_id", "attempt", name="uq_cp_stage_run_attempt"),
        Index("ix_cp_stage_run_idem", "idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stage_id: Mapped[str] = mapped_column(ForeignKey("cp.stages.id", ondelete="CASCADE"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    agent_run_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    execution_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    build_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    input_blueprint_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    freeze_handle: Mapped[str | None] = mapped_column(String(128))
    draft_hash: Mapped[str | None] = mapped_column(String(80))
    payload_hash: Mapped[str | None] = mapped_column(String(80))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InputParseRun(_Timestamps, CpBase):
    """One parse attempt of a business input, from the click to its outcome.

    The parse runs on a background thread after the REST call has answered, so this row is
    the durable answer to "is it still running".  It is separate from :class:`StageRun`
    because one requirements stage can parse several inputs, and the stage row's ``result``
    has room for only the last of them.
    """

    __tablename__ = "input_parse_runs"
    __table_args__ = (
        Index("ix_cp_input_parse_run_artifact", "input_artifact_id", "created_at"),
        Index("ix_cp_input_parse_run_project", "project_id", "created_at"),
        # CP-BR-02's shape for a parse: one running attempt per input, enforced by the
        # database rather than by a check-then-insert a second request can race.
        Index(
            "uq_cp_input_parse_run_active",
            "input_artifact_id",
            unique=True,
            postgresql_where=text("status = 'PARSING'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("cp.projects.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("cp.workflows.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[str] = mapped_column(
        ForeignKey("cp.stages.id", ondelete="CASCADE"), nullable=False
    )
    input_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: PARSING | PARSED | NEEDS_REVIEW | FAILED | POLICY_DENIED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PARSING")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    operator: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    #: S3's ``ai.input_parse`` id and the step run the outcome is recorded on
    parse_id: Mapped[str | None] = mapped_column(String(64))
    step_run_id: Mapped[str | None] = mapped_column(String(64))
    nrr_artifact_id: Mapped[str | None] = mapped_column(String(64))
    parser_type: Mapped[str | None] = mapped_column(String(32))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    used_model: Mapped[bool | None] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Question(_Timestamps, CpBase):
    """CP-Q-002: S1 is the only owner of questions."""

    __tablename__ = "questions"
    __table_args__ = (
        Index(
            "uq_cp_question_source",
            "agent_run_id",
            "source_local_id",
            unique=True,
            postgresql_where=text("agent_run_id IS NOT NULL AND source_local_id IS NOT NULL"),
        ),
        Index("ix_cp_question_stage", "stage_id", "status"),
        Index("ix_cp_question_workflow", "workflow_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("cp.projects.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("cp.workflows.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[str | None] = mapped_column(ForeignKey("cp.stages.id", ondelete="CASCADE"))
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="AGENT")
    agent_run_id: Mapped[str | None] = mapped_column(String(64))
    source_local_id: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="MEDIUM")
    #: SINGLE | MULTIPLE — how the review questionnaire renders this question's choices
    answer_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="SINGLE")
    affects: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    suggestions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    answered_by: Mapped[str | None] = mapped_column(String(255))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Gate(_Timestamps, CpBase):
    """CP-GATE-001/003: a persisted human approval point bound to object hashes."""

    __tablename__ = "gates"
    __table_args__ = (
        Index("ix_cp_gate_workflow", "workflow_id", "status"),
        Index("ix_cp_gate_run", "deployment_run_id", "status"),
        Index("ix_cp_gate_pending", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("cp.workflows.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[str | None] = mapped_column(ForeignKey("cp.stages.id", ondelete="CASCADE"))
    deployment_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("cp.deployment_runs.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    environment_key: Mapped[str | None] = mapped_column(String(128))
    blueprint_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    bound_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    risk_summary_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confirmation_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="WAITING")
    invalidated_reason: Mapped[str | None] = mapped_column(String(64))
    reminder_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Approval(_Timestamps, CpBase):
    """CP-GATE-003/007/008: one recorded decision by one operator."""

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_cp_approval_gate", "gate_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    gate_id: Mapped[str] = mapped_column(ForeignKey("cp.gates.id", ondelete="CASCADE"), nullable=False)
    approver_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: the authenticated identity behind that name, NULL while ``FACTORY_AUTH_MODE=off``
    #: (CP-IAM-001: the name is the identity until M4 replaces the policy, and the audit trail
    #: has to say which regime recorded the decision)
    approver_user_id: Mapped[str | None] = mapped_column(String(64))
    #: the roles held at the moment of the decision, recorded rather than re-derived: a role
    #: granted later must not make an old approval look compliant (CP-GATE-007)
    approver_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    warning_dispositions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    confirmations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    seen_bound_hashes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class ChangeRequest(_Timestamps, CpBase):
    """CP-CR-001..007: controlled change to an approved or deployed project."""

    __tablename__ = "change_requests"
    __table_args__ = (
        Index("ix_cp_cr_project_status", "project_id", "status"),
        Index(
            "uq_cp_cr_single_in_progress",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("cp.projects.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    affected_business_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    deletion_candidates: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    impact_report_artifact_id: Mapped[str | None] = mapped_column(String(64))
    affected_stages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    result_blueprint_version: Mapped[str | None] = mapped_column(String(32))


class EventStreamRecord(_Timestamps, CpBase):
    """CP-EVT-001/004: one row per state change, sequenced per workflow, retained by days."""

    __tablename__ = "event_stream_records"
    __table_args__ = (
        UniqueConstraint("workflow_id", "sequence", name="uq_cp_event_workflow_seq"),
        Index("ix_cp_event_workflow_seq", "workflow_id", "sequence"),
        Index("ix_cp_event_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("cp.workflows.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(_Timestamps, CpBase):
    """CP-NTF-001: project-level in MVP; ``project_id`` NULL is a platform notification."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_cp_notification_project_read", "project_id", "read", "created_at"),
        # a personal inbox is read by recipient, not by project (CP-NTF-001's M4 half)
        Index("ix_cp_notification_recipient_read", "recipient_user_id", "read", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("cp.projects.id", ondelete="CASCADE"))
    #: set only on a personal notification (budget and credential reminders, "awaiting your
    #: approval"); a project-level one leaves it NULL and every member sees it (CP-NTF-001)
    recipient_user_id: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class NotificationDelivery(_Timestamps, CpBase):
    """CP-NTF-002: per-channel attempt bookkeeping with exponential backoff."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (Index("ix_cp_delivery_status", "status", "updated_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("cp.notifications.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemSetting(_Timestamps, CpBase):
    """CP-SET-001: runtime tunables; every change is audited."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")


class IdempotentResponse(_Timestamps, CpBase):
    """CP-API-003: S0 keeps only a ``resultRef``; the replayable response is S1's."""

    __tablename__ = "idempotent_responses"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key", name="uq_cp_idem_response"),
        Index("ix_cp_idem_response_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/json")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


#: Every ``cp`` table, in dependency order, for migrations and the FK checker.

# --- Identity and permissions (CP-IAM-002, FR-SEC-001~005) -------------------------


class User(_Timestamps, CpBase):
    """One account: a local one, or the subject an OIDC provider asserted.

    ``password_hash`` is NULL for an OIDC account and never holds a password — S0's secret
    rules apply to credentials too (FR-SEC-006/008): the column stores a salted scrypt digest
    with its parameters, which is not reversible and is never logged.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("source", "subject", name="uq_cp_user_subject"),
        Index("ix_cp_user_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: the login name for a local account, the ``sub`` claim for an OIDC one
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    password_hash: Mapped[str | None] = mapped_column(String(512))
    #: the digest scheme and its parameters, so they can be raised without guessing
    password_algo: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    #: lock-out against a password guesser (FR-SEC-005's sibling in 21.1)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: a locally created account whose password was set by an administrator
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class UserRole(_Timestamps, CpBase):
    """A role held by a user, org-wide or inside one project (FR-SEC-002).

    ``project_id`` NULL is the organisation-wide grant, which is what a Platform Administrator
    holds; a non-NULL value is the same role limited to that project.  Both exist because
    "权限粒度至少到项目 × 动作" and because an organisation-wide architect may approve in any
    project while a project-scoped one may not approve outside it.
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role", "project_id", name="uq_cp_user_role_scope"),
        Index("ix_cp_user_role_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("cp.users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("cp.projects.id", ondelete="CASCADE"))
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProjectMember(_Timestamps, CpBase):
    """Membership, which is what limits ``project.view`` and artifact reads (FR-SEC-003/004).

    A single-organisation deployment (FR-SEC-004) still needs this: projects are isolated by
    membership, not by tenancy, so a member of one project sees nothing of another.
    """

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_cp_project_member"),
        Index("ix_cp_project_member_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("cp.projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("cp.users.id", ondelete="CASCADE"), nullable=False)
    #: the role the person acts under *in this project*; NULL means they may look only
    role: Mapped[str | None] = mapped_column(String(64))
    added_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")


class Session(_Timestamps, CpBase):
    """A server-side session: the token is stored hashed, so a database read cannot hijack it.

    Idle timeout and absolute lifetime are both enforced here (FR-SEC-005: idle 30 minutes by
    default); ``csrf_token_hash`` is why a cross-site form cannot ride an authenticated cookie.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_cp_session_token"),
        Index("ix_cp_session_user_seen", "user_id", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: SHA-256 of the opaque token the client holds; the token itself is never stored
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("cp.users.id", ondelete="CASCADE"), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    auth_source: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    #: the idle deadline, slid on every request (FR-SEC-005's 30 minutes by default)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: the absolute deadline, never slid: two clocks because they defend different things
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RestrictedGrant(_Timestamps, CpBase):
    """FR-SEC-003: reading a PHI_PII or RESTRICTED artifact needs its own grant.

    ``project.view`` is not enough, and a grant is not a role: it is recorded per person and
    project with a reason and an expiry, and every read that used it is audited.
    """

    __tablename__ = "restricted_grants"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", "classification", name="uq_cp_restricted_grant"),
        Index("ix_cp_restricted_grant_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("cp.projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("cp.users.id", ondelete="CASCADE"), nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductionExemption(_Timestamps, CpBase):
    """CP-WF-020's exemption, which the SRS says arrives with M4's permissions.

    The rule it waives is "this Blueprint version was verified in a non-production
    environment first".  That is a safety property, so an exemption is named, reasoned,
    time-boxed and revocable rather than a checkbox, and it is reported to whoever asks what
    the protection said.
    """

    __tablename__ = "production_exemptions"
    __table_args__ = (
        Index("ix_cp_production_exemption_project", "project_id", "environment_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("cp.projects.id", ondelete="CASCADE"), nullable=False)
    environment_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    #: which precondition is waived (``NON_PRODUCTION_VERIFIED`` today; CP-WF-020 names no other)
    waiver: Mapped[str] = mapped_column(String(64), nullable=False, default="NON_PRODUCTION_VERIFIED")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecretRecord(_Timestamps, CpBase):
    """CP-SEC-001: one operator-supplied credential, sealed.

    The value is never a column.  ``ciphertext`` holds ``v1$salt$nonce$sealed$fingerprint`` as
    produced by :mod:`factory_control.credentials.crypto`, and the only way to read it back is
    through the deployment's master key, which lives in the environment file beside
    ``DATABASE_URL`` and deliberately not in this table.

    That is the whole protection story, stated plainly: encryption here defends the **business
    database** — its backups, replicas and anyone who can ``SELECT`` on ``cp`` — not the host that
    holds both the ciphertext and the key.  A deployment that needs the stronger boundary keeps
    using ``env://`` and a real secret manager; this table exists so a lab installation can be
    configured from a browser without pasting tokens into a database column.

    ``name`` is unique and is what a reference looks like (``db://dhis2-dev-discovery``), so it is
    listed by ``GET /secrets`` and must never carry a username or any other person-identifying
    value.
    """

    __tablename__ = "secrets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    algo: Mapped[str] = mapped_column(String(64), nullable=False, default=_CREDENTIAL_ALGO)
    key_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: set when a later write replaced the value under the same name; ``created_at`` stays the
    #: first one, because "how long has this deployment had a credential here" is the question
    #: an operator actually asks when deciding whether to rotate.
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


#: Every ``cp`` table, in dependency order for ``create_all`` and for the harness that
#: rebuilds the schema between tests.  The identity tables come last because they reference
#: projects and users by foreign key, and the tuple is what the migration's metadata reads.
CP_MODELS: tuple[type[object], ...] = (
    Project,
    ProjectTargetEnvironment,
    ProjectSetting,
    Workflow,
    DeploymentRun,
    Stage,
    StageRun,
    InputParseRun,
    Question,
    Gate,
    Approval,
    ChangeRequest,
    EventStreamRecord,
    Notification,
    NotificationDelivery,
    SystemSetting,
    IdempotentResponse,
    User,
    UserRole,
    ProjectMember,
    Session,
    RestrictedGrant,
    ProductionExemption,
    SecretRecord,
)


__all__ = [
    "Approval",
    "CP_MODELS",
    "ChangeRequest",
    "DeploymentRun",
    "EventStreamRecord",
    "Gate",
    "IdempotentResponse",
    "InputParseRun",
    "Notification",
    "NotificationDelivery",
    "ProductionExemption",
    "Project",
    "ProjectMember",
    "ProjectSetting",
    "ProjectTargetEnvironment",
    "RestrictedGrant",
    "SecretRecord",
    "Session",
    "Stage",
    "StageRun",
    "SystemSetting",
    "User",
    "UserRole",
    "Workflow",
]
