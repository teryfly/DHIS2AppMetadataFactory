"""bp.prepared_content gains base_content

Revision ID: 0002_bp_prepared_base
Revises: 0001_bp_initial
Create Date: 2026-09-29

A delivery-mode upgrade stages its result outside the transaction and registers it
inside one. Deciding the change level needs the document it departs from, and reading
an artifact inside a unit of work is forbidden (00 doc 6.2), so the base travels in the
staging row — the same reason ``blueprint_draft.base_content`` exists (BP-IMP-001).

The guard is not decoration: ``0001_bp_initial`` builds the schema from the ORM
metadata, so a fresh database already has this column and an unconditional
``ADD COLUMN`` would fail exactly where it is least wanted — on the first run.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_bp_prepared_base"
down_revision = "0001_bp_initial"
branch_labels = None
depends_on = None


def _has_column(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if "prepared_content" not in inspector.get_table_names(schema="bp"):
        return False
    return name in {column["name"] for column in inspector.get_columns("prepared_content", schema="bp")}


def upgrade() -> None:
    if _has_column("base_content"):
        return
    op.add_column(
        "prepared_content",
        sa.Column(
            "base_content",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="bp",
    )


def downgrade() -> None:
    if _has_column("base_content"):
        op.drop_column("prepared_content", "base_content", schema="bp")
