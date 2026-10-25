"""ai: provider vendor preset and suggested model names

Revision ID: 0003_ai_provider_preset
Revises: 0002_ai_read_and_model_endpoint
Create Date: 2026-10-23

Two columns that make the console's provider form start from a vendor preset instead of
an empty address box:

* ``ai.provider.vendor`` — the catalogue key the registration was started from
  (``openai``, ``deepseek``, ``ollama``, … or ``custom``).  It is a label for the
  console, never a capability switch: the runtime still dispatches on nothing but the
  one OpenAI-compatible adapter, and ``endpoint`` remains what a call dials.
* ``ai.provider.suggested_models`` — model names worth offering when a model is
  registered under this provider.  Suggestions only: ``ai.model`` is the registry, and
  a private gateway, fine-tune or renamed deployment is still entered by hand.

``0001`` builds its tables from the ORM metadata with ``checkfirst``, so a fresh
database already has both columns by the time this revision runs.  Each statement is
guarded by an inspection of the actual table for the same reason ``0002`` is: a
deployment that created ``ai`` before this change must be migrated, and one created
after it must not be altered twice.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine.reflection import Inspector

revision = "0003_ai_provider_preset"
down_revision = "0002_ai_read_and_model_endpoint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = inspect(bind)
    if "provider" not in existing.get_table_names(schema="ai"):
        return

    columns = _column_names(existing, "provider")
    if "vendor" not in columns:
        op.execute(
            "ALTER TABLE ai.provider "
            "ADD COLUMN IF NOT EXISTS vendor VARCHAR(64) NOT NULL DEFAULT 'custom'"
        )
    if "suggested_models" not in columns:
        op.execute(
            "ALTER TABLE ai.provider "
            "ADD COLUMN IF NOT EXISTS suggested_models JSONB NOT NULL DEFAULT '[]'::jsonb"
        )


def _column_names(existing: Inspector, table: str) -> set[str]:
    return {str(column["name"]) for column in existing.get_columns(table, schema="ai")}


def downgrade() -> None:
    op.execute("ALTER TABLE ai.provider DROP COLUMN IF EXISTS suggested_models")
    op.execute("ALTER TABLE ai.provider DROP COLUMN IF EXISTS vendor")
