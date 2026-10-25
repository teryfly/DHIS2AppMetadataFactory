"""bp.build_record gains compile_parameters

Revision ID: 0003_bp_compile_parameters
Revises: 0002_bp_prepared_base
Create Date: 2026-09-30

``reproduce()`` has to replay the parameters that shaped an environment-bound package.
Two of them are not derivable from the database: the deletion candidates and the change
request that authorized them are handed in by S1 (FR-CMP-009), and the design reference
environment decides which observations a REFERENCE resolves against.  Without them a
reproduction recompiles a manifest whose ``deletionCandidates`` is empty, reports a
different hash, and the defect FR-CMP-004 asks it to catch is its own.

The guard is not decoration: ``0001_bp_initial`` builds the schema from the ORM metadata,
so a fresh database already has this column and an unconditional ``ADD COLUMN`` would
fail exactly where it is least wanted — on the first run.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_bp_compile_parameters"
down_revision = "0002_bp_prepared_base"
branch_labels = None
depends_on = None


def _has_column(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if "build_record" not in inspector.get_table_names(schema="bp"):
        return False
    return name in {column["name"] for column in inspector.get_columns("build_record", schema="bp")}


def upgrade() -> None:
    if _has_column("compile_parameters"):
        return
    op.add_column(
        "build_record",
        sa.Column(
            "compile_parameters",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="bp",
    )


def downgrade() -> None:
    if _has_column("compile_parameters"):
        op.drop_column("build_record", "compile_parameters", schema="bp")
