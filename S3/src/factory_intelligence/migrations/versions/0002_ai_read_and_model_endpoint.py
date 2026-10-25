"""ai: parse read projections and per-model endpoint

Revision ID: 0002_ai_read_and_model_endpoint
Revises: 0001_ai_initial
Create Date: 2026-09-29

Three additions, each of which SRS V1.1.0 made necessary:

* ``ai.nrr_entry`` and ``ai.input_parse.mapping_proposal`` — AI-INP-013/014/015 make S3
  answer "what is the latest parse of this input", "what column mapping did you propose"
  and "give me the NRR entries of this category".  Those are 只读 functions, and S1 calls
  them with its own transaction open, so they may not read artifact content (00 doc 6.2):
  the parse therefore projects what a query needs into ``ai`` while it writes its record.
  AI-INP-015 says so itself — an NRR's category and parsing detail live in "S3's NRR
  artifact and the ``ai`` schema".
* ``ai.agent_run.last_progress`` — AI-RT-019 asks a run query for the most recent progress,
  which a still-running row cannot otherwise answer.
* ``ai.model.endpoint`` / ``api_key_secret_ref`` — AI-LLM-003 lets *each model
  registration* configure its interface address and API Key, so one provider can front two
  servers (vLLM and Ollama, or two regions) without pretending they are one endpoint.

``0001`` builds its tables from the ORM metadata with ``checkfirst``, so a fresh database
already has these by the time this revision runs; every statement below is therefore
guarded by an inspection of what is actually there rather than issued blind.  That is the
same rule ``0001`` states for a deployment that created ``ai`` before a model change —
except now the change is in the migration where it belongs (AI-DATA-01).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine.reflection import Inspector

revision = "0002_ai_read_and_model_endpoint"
down_revision = "0001_ai_initial"
branch_labels = None
depends_on = None


#: AI-LLM-003: both are nullable with no default, so an existing registration keeps
#: meaning "use the provider's address and Key".
_MODEL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("endpoint", "VARCHAR(512)"),
    ("api_key_secret_ref", "VARCHAR(255)"),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = inspect(bind)

    for name, sql_type in _MODEL_COLUMNS:
        if name not in _column_names(existing, "model"):
            op.execute(
                f"ALTER TABLE ai.model ADD COLUMN IF NOT EXISTS {name} {sql_type}"
            )

    if "last_progress" not in _column_names(existing, "agent_run"):
        op.execute("ALTER TABLE ai.agent_run ADD COLUMN IF NOT EXISTS last_progress JSONB")

    if "mapping_proposal" not in _column_names(existing, "input_parse"):
        op.execute("ALTER TABLE ai.input_parse ADD COLUMN IF NOT EXISTS mapping_proposal JSONB")
    if "result" not in _column_names(existing, "input_parse"):
        op.execute("ALTER TABLE ai.input_parse ADD COLUMN IF NOT EXISTS result JSONB")

    if "nrr_entry" not in existing.get_table_names(schema="ai"):
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS ai.nrr_entry (
                id VARCHAR(64) NOT NULL,
                project_id VARCHAR(64) NOT NULL,
                parse_id VARCHAR(64) NOT NULL,
                input_artifact_id VARCHAR(64) NOT NULL,
                nrr_artifact_id VARCHAR(64),
                business_key VARCHAR(256) NOT NULL,
                category VARCHAR(48) NOT NULL,
                statement TEXT NOT NULL,
                location VARCHAR(512),
                confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
                status VARCHAR(16) NOT NULL,
                confirmed BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT pk_ai_nrr_entry PRIMARY KEY (id),
                CONSTRAINT uq_ai_nrr_entry_per_parse
                    UNIQUE (parse_id, input_artifact_id, business_key)
            )
            """
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_nrr_entry_project_category "
        "ON ai.nrr_entry (project_id, category)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_nrr_entry_project_confidence "
        "ON ai.nrr_entry (project_id, confidence)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_nrr_entry_project_artifact "
        "ON ai.nrr_entry (project_id, input_artifact_id)"
    )


def _column_names(existing: Inspector, table: str) -> set[str]:
    if table not in existing.get_table_names(schema="ai"):
        return set()
    return {str(column["name"]) for column in existing.get_columns(table, schema="ai")}


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai.nrr_entry")
    op.execute("ALTER TABLE ai.agent_run DROP COLUMN IF EXISTS last_progress")
    op.execute("ALTER TABLE ai.input_parse DROP COLUMN IF EXISTS mapping_proposal")
    op.execute("ALTER TABLE ai.input_parse DROP COLUMN IF EXISTS result")
    op.execute("ALTER TABLE ai.model DROP COLUMN IF EXISTS api_key_secret_ref")
    op.execute("ALTER TABLE ai.model DROP COLUMN IF EXISTS endpoint")
