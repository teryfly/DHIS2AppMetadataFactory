"""S2 start-up (00 doc 6.10): ``initialize()`` and ``MIGRATION_CONFIG``.

``initialize()`` registers the artifact types S2 owns, the 13 audit events of
S2 SRS appendix C, S2's error codes and its ID prefixes, all through the
foundation's public registration functions.  It is idempotent: repeated
registration merges owner lists and skips existing rows, and a code S0 owns is
never restamped with S2 as its owner.
"""

from __future__ import annotations

from pathlib import Path

from factory_foundation.api import (
    MigrationConfig,
    current_session,
    error_catalog,
    has_active_uow,
    register_artifact_types,
    register_audit_event_types,
    register_error_codes,
    register_id_prefixes,
    unit_of_work,
)
from sqlalchemy.orm import Session

from factory_blueprint.registries import (
    BP_ARTIFACT_TYPES,
    BP_AUDIT_EVENT_TYPES,
    BP_ERROR_CODES,
    BP_ID_PREFIXES,
    S0_PENDING_ERROR_CODES,
)

BP_MIGRATION_SCRIPT_LOCATION = str(Path(__file__).resolve().parent / "migrations")

#: Passed to S0's migration runner, which executes ``core → dx → bp → ai → cp``.
MIGRATION_CONFIG = MigrationConfig(
    schema="bp",
    script_location=BP_MIGRATION_SCRIPT_LOCATION,
    version_table="bp_alembic_version",
)

__all__ = ["initialize", "MIGRATION_CONFIG", "BP_MIGRATION_SCRIPT_LOCATION"]


def _pending_error_codes() -> dict[str, tuple[str, bool]]:
    """The S0-owned codes this deployment's S0 has not registered yet.

    S0 owns these per FD-COM-001a and registers them in ``initialize_foundation()``, which
    runs before S2's ``initialize()`` (00 doc 6.12), so an S0 that has them needs nothing from
    S2 and S2 must not restamp their owner or retryability.  Until such an S0 is everywhere,
    S2 fills only the gap it can actually see — see ``registries.S0_PENDING_ERROR_CODES``.
    """
    return {
        code: owner_retryable
        for code, owner_retryable in sorted(S0_PENDING_ERROR_CODES.items())
        if not error_catalog.is_registered(code)
    }


def _register_all(session: Session) -> None:
    register_artifact_types(BP_ARTIFACT_TYPES, session=session)
    register_audit_event_types(BP_AUDIT_EVENT_TYPES, session=session)
    register_error_codes(BP_ERROR_CODES)
    missing = _pending_error_codes()
    if missing:
        register_error_codes(missing)
    register_id_prefixes(BP_ID_PREFIXES)
    session.flush()


def initialize(*, session: Session | None = None) -> None:
    """Register S2's catalog entries (FD-ASM-01)."""
    if session is not None or has_active_uow():
        _register_all(session if session is not None else current_session())
        return
    with unit_of_work() as sess:
        _register_all(sess)
