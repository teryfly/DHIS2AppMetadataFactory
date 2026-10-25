"""core initial schema

Revision ID: 0001_core_initial
Revises:
Create Date: 2026-01-01

Creates every ``core`` table directly from the ORM metadata so the migration and
the model definitions can never drift, then installs the append-only guard on
``core.audit_event`` (FD-AUD-003).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_core_initial"
down_revision = None
branch_labels = None
depends_on = None


_AUDIT_GUARD_FN = """
CREATE OR REPLACE FUNCTION core.audit_event_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        RAISE EXCEPTION 'audit events are append-only: DELETE is forbidden';
    ELSIF (TG_OP = 'UPDATE') THEN
        -- already-linked records are fully immutable
        IF OLD.seq IS NOT NULL THEN
            RAISE EXCEPTION 'audit event % is already linked and immutable', OLD.id;
        END IF;
        -- content columns must not change; only first-time linking (seq/prev_hash/hash) is allowed
        IF NEW.id                    IS DISTINCT FROM OLD.id
           OR NEW.occurred_at          IS DISTINCT FROM OLD.occurred_at
           OR NEW.operator_type        IS DISTINCT FROM OLD.operator_type
           OR NEW.operator_name        IS DISTINCT FROM OLD.operator_name
           OR NEW.source_subsystem     IS DISTINCT FROM OLD.source_subsystem
           OR NEW.event_type           IS DISTINCT FROM OLD.event_type
           OR NEW.object_type          IS DISTINCT FROM OLD.object_type
           OR NEW.object_id            IS DISTINCT FROM OLD.object_id
           OR NEW.project_id           IS DISTINCT FROM OLD.project_id
           OR NEW.before_summary       IS DISTINCT FROM OLD.before_summary
           OR NEW.after_summary        IS DISTINCT FROM OLD.after_summary
           OR NEW.correlation_id       IS DISTINCT FROM OLD.correlation_id
           OR NEW.redacted             IS DISTINCT FROM OLD.redacted
        THEN
            RAISE EXCEPTION 'audit event % content is immutable', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""


_AUDIT_NO_TRUNCATE_FN = """
CREATE OR REPLACE FUNCTION core.audit_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit events are append-only: TRUNCATE is forbidden';
END;
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    from factory_foundation.models.base import CoreBase

    # metadata.create_all emits DDL for every core table straight from the models.
    CoreBase.metadata.create_all(bind=bind, checkfirst=True)

    op.execute(_AUDIT_GUARD_FN)
    op.execute(
        "CREATE TRIGGER audit_event_guard "
        "BEFORE UPDATE OR DELETE ON core.audit_event "
        "FOR EACH ROW EXECUTE FUNCTION core.audit_event_guard()"
    )
    # FD-AUD-003 also forbids bulk removal
    op.execute(_AUDIT_NO_TRUNCATE_FN)
    op.execute(
        "CREATE TRIGGER audit_event_no_truncate "
        "BEFORE TRUNCATE ON core.audit_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION core.audit_no_truncate()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_truncate ON core.audit_event")
    op.execute("DROP FUNCTION IF EXISTS core.audit_no_truncate()")
    op.execute("DROP TRIGGER IF EXISTS audit_event_guard ON core.audit_event")
    op.execute("DROP FUNCTION IF EXISTS core.audit_event_guard()")
    from factory_foundation.models.base import CoreBase

    CoreBase.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
