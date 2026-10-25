"""cp: background parse runs

Adds ``cp.input_parse_runs``: one row per business-input parse attempt, created when the
REST call claims the work and finalized by the background thread.  It is what lets the input
list answer "解析中" after the 202 and across a reload, and it is per input rather than per
stage because one requirements stage can parse several inputs.

``cp.stage_runs.result`` still records the parse outcome for the stage view; this table is
the per-input source of truth for the status the console shows.

Revision ID: 0004_cp_input_parse_runs
Revises: 0003_cp_secrets
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_cp_input_parse_runs"
down_revision: str | None = "0003_cp_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "input_parse_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(length=64), nullable=False),
        sa.Column("stage_id", sa.String(length=64), nullable=False),
        sa.Column("input_artifact_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("operator", sa.String(length=128), nullable=False),
        sa.Column("parse_id", sa.String(length=64), nullable=True),
        sa.Column("step_run_id", sa.String(length=64), nullable=True),
        sa.Column("nrr_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("parser_type", sa.String(length=32), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("used_model", sa.Boolean(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["cp.projects.id"], name=op.f("fk_input_parse_runs_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["cp.workflows.id"], name=op.f("fk_input_parse_runs_workflow_id_workflows"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["cp.stages.id"], name=op.f("fk_input_parse_runs_stage_id_stages"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_input_parse_runs")),
        schema="cp",
    )
    op.create_index(
        "ix_cp_input_parse_run_artifact",
        "input_parse_runs",
        ["input_artifact_id", "created_at"],
        unique=False,
        schema="cp",
    )
    op.create_index(
        "ix_cp_input_parse_run_project",
        "input_parse_runs",
        ["project_id", "created_at"],
        unique=False,
        schema="cp",
    )
    op.create_index(
        "uq_cp_input_parse_run_active",
        "input_parse_runs",
        ["input_artifact_id"],
        unique=True,
        schema="cp",
        postgresql_where=sa.text("status = 'PARSING'"),
    )


def downgrade() -> None:
    op.drop_table("input_parse_runs", schema="cp")
