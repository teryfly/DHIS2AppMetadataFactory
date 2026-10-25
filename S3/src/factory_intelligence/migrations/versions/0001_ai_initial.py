"""ai initial schema

Revision ID: 0001_ai_initial
Revises:
Create Date: 2026-09-28

Tables come from the ORM metadata rather than hand-written DDL, for the same reason
S2 does it this way: a column added to a model cannot then be missing from the
database, and the schema cannot silently drift from the code that reads it.

The one piece of hand-written SQL is the guard on ``ai.model_call``.  A usage row is
evidence — AI-NFR-03 requires 100% of model calls to have one, and AI-COST-001 turns
them into a bill — so neither its amounts nor its existence may be edited afterwards.
It is a trigger rather than application discipline because
:func:`factory_intelligence.cleanup.delete_project_data` deletes that project's other
rows and must not be able to delete the ledger with them.
"""

from __future__ import annotations

from alembic import op

revision = "0001_ai_initial"
down_revision = None
branch_labels = None
depends_on = None


#: A usage row is written once by the runtime and never touched again, so the guard
#: refuses every UPDATE and DELETE outright rather than checking which column moved:
#: "no exception to this" is the requirement, and an allow-list would be the loophole.
_MODEL_CALL_GUARD_FN = """
CREATE OR REPLACE FUNCTION ai.model_call_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ai.model_call rows are immutable usage evidence (attempted %)', TG_OP
        USING ERRCODE = 'check_violation';
END;
$$;
"""


def upgrade() -> None:
    from factory_foundation.api import AiBase

    import factory_intelligence.models  # noqa: F401  (registers every ai table)

    AiBase.metadata.create_all(bind=op.get_bind(), checkfirst=True)

    op.execute(_MODEL_CALL_GUARD_FN)
    op.execute(
        """
        CREATE TRIGGER ai_model_call_guard
        BEFORE UPDATE OR DELETE ON ai.model_call
        FOR EACH ROW EXECUTE FUNCTION ai.model_call_guard()
        """
    )


def downgrade() -> None:
    from factory_foundation.api import AiBase

    import factory_intelligence.models  # noqa: F401

    op.execute("DROP TRIGGER IF EXISTS ai_model_call_guard ON ai.model_call")
    op.execute("DROP FUNCTION IF EXISTS ai.model_call_guard()")
    AiBase.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
