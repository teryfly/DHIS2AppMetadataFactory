"""ai: the parse-time LLM exchange log

Adds ``ai.llm_exchange`` — the request messages and raw answer of every model call a
business-input parse makes, including each transport retry.  It is deliberately separate
from ``ai.model_call``, which AI-LLM-004 / AI-DEL-001 keep as content-free accounting.

``0001`` builds its tables from the ORM metadata with ``checkfirst``, so a fresh database
already has this table by the time this revision runs; the statements are guarded for the
deployments that created ``ai`` earlier.

Revision ID: 0004_ai_llm_exchange
Revises: 0003_ai_provider_preset
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_ai_llm_exchange"
down_revision: str | None = "0003_ai_provider_preset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai.llm_exchange (
            id VARCHAR(64) NOT NULL,
            project_id VARCHAR(64) NOT NULL,
            input_artifact_id VARCHAR(64) NOT NULL,
            workflow_id VARCHAR(64),
            sequence INTEGER NOT NULL DEFAULT 1,
            call_role VARCHAR(24) NOT NULL DEFAULT 'extraction',
            provider VARCHAR(64) NOT NULL,
            provider_class VARCHAR(32),
            model VARCHAR(128) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'IN_FLIGHT',
            request_id VARCHAR(128),
            request JSONB NOT NULL,
            response_text TEXT,
            response_payload JSONB,
            response_meta JSONB,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER,
            error_code VARCHAR(64),
            error TEXT,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT pk_ai_llm_exchange PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_llm_exchange_artifact "
        "ON ai.llm_exchange (project_id, input_artifact_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_llm_exchange_expires "
        "ON ai.llm_exchange (expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_llm_exchange_status "
        "ON ai.llm_exchange (status, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai.llm_exchange")
