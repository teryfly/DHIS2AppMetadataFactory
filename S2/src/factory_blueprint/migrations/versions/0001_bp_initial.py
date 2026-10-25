"""bp initial schema

Revision ID: 0001_bp_initial
Revises:
Create Date: 2026-09-28

Creates every ``bp`` table from the ORM metadata so model and migration cannot
drift, then installs the UID ledger guard: a UID is never reused (FR-REG-004,
BP-REG-006), so ``bp.uid_allocation`` rows may be updated (status) but never
deleted, not even by project data cleanup (BP-EXP-003).
"""

from __future__ import annotations

from alembic import op

revision = "0001_bp_initial"
down_revision = None
branch_labels = None
depends_on = None


_UID_LEDGER_GUARD_FN = """
CREATE OR REPLACE FUNCTION bp.uid_allocation_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        RAISE EXCEPTION 'uid allocation % is retained forever to prevent reuse', OLD.uid;
    END IF;
    -- identity columns are immutable: retirement changes status only
    IF NEW.uid            IS DISTINCT FROM OLD.uid
       OR NEW.project_id   IS DISTINCT FROM OLD.project_id
       OR NEW.business_key IS DISTINCT FROM OLD.business_key
       OR NEW.allocated_at IS DISTINCT FROM OLD.allocated_at THEN
        RAISE EXCEPTION 'uid allocation % identity is immutable', OLD.uid;
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    # importing the models registers every bp table on BpBase.metadata
    from factory_foundation.api import BpBase

    import factory_blueprint.models  # noqa: F401

    BpBase.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    op.execute(_UID_LEDGER_GUARD_FN)
    op.execute(
        "CREATE TRIGGER uid_allocation_guard "
        "BEFORE UPDATE OR DELETE ON bp.uid_allocation "
        "FOR EACH ROW EXECUTE FUNCTION bp.uid_allocation_guard()"
    )


def downgrade() -> None:
    from factory_foundation.api import BpBase

    import factory_blueprint.models  # noqa: F401

    op.execute("DROP TRIGGER IF EXISTS uid_allocation_guard ON bp.uid_allocation")
    op.execute("DROP FUNCTION IF EXISTS bp.uid_allocation_guard()")
    BpBase.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
