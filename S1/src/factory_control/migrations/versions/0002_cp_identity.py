"""cp identity and permissions (CP-IAM-002, FR-SEC-001~005)

Adds the tables M4's authorization hook is keyed on, and the two columns that record *who*
decided rather than only what name was typed: ``cp.approvals.approver_user_id`` with the roles
held at that moment, and ``cp.notifications.recipient_user_id`` for a personal notification.

Both new columns are nullable with no default, so a deployment that keeps
``FACTORY_AUTH_MODE=off`` (CP-IAM-001's MVP behaviour) writes exactly what it wrote before and
nothing in this migration changes a row it did not create.

Revision ID: 0002_cp_identity
Revises: 0001_cp_initial
Create Date: 2026-09-30 15:40:12.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_cp_identity"
down_revision: str | None = "0001_cp_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        # a salted scrypt digest with its parameters, never a password (FR-SEC-006/008)
        sa.Column("password_hash", sa.String(length=512), nullable=True),
        sa.Column("password_algo", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("source", "subject", name="uq_cp_user_subject"),
        schema="cp",
    )
    op.create_index("ix_cp_user_status", "users", ["status"], unique=False, schema="cp")

    op.create_table(
        "user_roles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        # NULL is the organisation-wide grant (FR-SEC-002: 项目 × 动作, with org-wide above it)
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("granted_by", sa.String(length=255), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["cp.projects.id"], name=op.f("fk_user_roles_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["cp.users.id"], name=op.f("fk_user_roles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_roles")),
        sa.UniqueConstraint("user_id", "role", "project_id", name="uq_cp_user_role_scope"),
        schema="cp",
    )
    op.create_index("ix_cp_user_role_user", "user_roles", ["user_id"], unique=False, schema="cp")

    op.create_table(
        "project_members",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("added_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["cp.projects.id"], name=op.f("fk_project_members_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["cp.users.id"], name=op.f("fk_project_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_members")),
        sa.UniqueConstraint("project_id", "user_id", name="uq_cp_project_member"),
        schema="cp",
    )
    op.create_index("ix_cp_project_member_user", "project_members", ["user_id"], unique=False, schema="cp")

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        # the SHA-256 of the client's opaque token: a read of this table cannot hijack a session
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=128), nullable=False),
        sa.Column("auth_source", sa.String(length=16), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # the absolute deadline is a column, not something derived: a sliding idle clock must
        # never be able to extend a session past it (FR-SEC-005)
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["cp.users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name="uq_cp_session_token"),
        schema="cp",
    )
    op.create_index(
        "ix_cp_session_user_seen", "sessions", ["user_id", "last_seen_at"], unique=False, schema="cp"
    )

    op.create_table(
        "restricted_grants",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("granted_by", sa.String(length=255), nullable=False),
        # FR-SEC-003: a PHI_PII or RESTRICTED read needs its own reason, not just a role
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["cp.projects.id"],
            name=op.f("fk_restricted_grants_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["cp.users.id"], name=op.f("fk_restricted_grants_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restricted_grants")),
        sa.UniqueConstraint("project_id", "user_id", "classification", name="uq_cp_restricted_grant"),
        schema="cp",
    )
    op.create_index(
        "ix_cp_restricted_grant_user", "restricted_grants", ["user_id"], unique=False, schema="cp"
    )

    op.create_table(
        "production_exemptions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("environment_key", sa.String(length=128), nullable=False),
        sa.Column("waiver", sa.String(length=64), nullable=False),
        # CP-WF-020's exemption is a named, reasoned, time-boxed waiver of a safety precondition
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("granted_by", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["cp.projects.id"],
            name=op.f("fk_production_exemptions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_production_exemptions")),
        schema="cp",
    )
    op.create_index(
        "ix_cp_production_exemption_project",
        "production_exemptions",
        ["project_id", "environment_key"],
        unique=False,
        schema="cp",
    )

    # --- what a decision was made *by*, and who a notification is for ---------------
    op.add_column(
        "approvals", sa.Column("approver_user_id", sa.String(length=64), nullable=True), schema="cp"
    )
    # the JSONB default is what the model declares, so an old row reads as "no roles recorded"
    # rather than as a row the policy would refuse to interpret
    op.add_column(
        "approvals",
        sa.Column(
            "approver_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="cp",
    )
    op.add_column(
        "notifications", sa.Column("recipient_user_id", sa.String(length=64), nullable=True), schema="cp"
    )
    op.create_index(
        "ix_cp_notification_recipient_read",
        "notifications",
        ["recipient_user_id", "read", "created_at"],
        unique=False,
        schema="cp",
    )


def downgrade() -> None:
    op.drop_index("ix_cp_notification_recipient_read", table_name="notifications", schema="cp")
    op.drop_column("notifications", "recipient_user_id", schema="cp")
    op.drop_column("approvals", "approver_roles", schema="cp")
    op.drop_column("approvals", "approver_user_id", schema="cp")
    op.drop_index("ix_cp_production_exemption_project", table_name="production_exemptions", schema="cp")
    op.drop_table("production_exemptions", schema="cp")
    op.drop_index("ix_cp_restricted_grant_user", table_name="restricted_grants", schema="cp")
    op.drop_table("restricted_grants", schema="cp")
    op.drop_index("ix_cp_session_user_seen", table_name="sessions", schema="cp")
    op.drop_table("sessions", schema="cp")
    op.drop_index("ix_cp_project_member_user", table_name="project_members", schema="cp")
    op.drop_table("project_members", schema="cp")
    op.drop_index("ix_cp_user_role_user", table_name="user_roles", schema="cp")
    op.drop_table("user_roles", schema="cp")
    op.drop_index("ix_cp_user_status", table_name="users", schema="cp")
    op.drop_table("users", schema="cp")
