"""How a question is answered: one choice or a set (CP-Q-005).

The model already proposes structured suggestions; the review screen needs to know whether
they are mutually exclusive alternatives (SINGLE, a radio group) or a set of options that can
hold together (MULTIPLE, checkboxes).  The value is stored beside the suggestions it governs,
so a resumed run and a historical question both render the same way.

Existing rows are single-select: that is what the console did before this column existed, and
a defaulted value keeps their answers valid.

Revision ID: 0005_cp_question_answer_mode
Revises: 0004_cp_input_parse_runs
Create Date: 2026-10-25 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_cp_question_answer_mode"
down_revision: str | None = "0004_cp_input_parse_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "questions",
        sa.Column(
            "answer_mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'SINGLE'"),
        ),
        schema="cp",
    )


def downgrade() -> None:
    op.drop_column("questions", "answer_mode", schema="cp")
