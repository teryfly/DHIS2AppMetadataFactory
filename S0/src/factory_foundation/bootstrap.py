"""Foundation bootstrap (API: ``initialize_foundation``, ``CORE_MIGRATION_CONFIG``).

Per 00 doc 6.10 the backend process runs migrations first (S1's job), then calls
``initialize_foundation()``, then each subsystem's ``initialize()``.  This loads
S0's built-in registries into the ``core`` catalog tables: the artifact type
catalog, audit event types, the error-code catalog and ID-prefix ownership.
It is idempotent — safe to call repeatedly (re-registrations merge).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from factory_foundation.artifacts.catalog import (
    INITIAL_ARTIFACT_CATALOG,
    register_artifact_types,
)
from factory_foundation.audit.registry import (
    BUILTIN_AUDIT_EVENT_TYPES,
    register_audit_event_types,
)
from factory_foundation.common.errors import Subsystem, error_catalog
from factory_foundation.common.ids import id_prefix_registry
from factory_foundation.db.migrations import CORE_MIGRATION_CONFIG  # noqa: F401 (re-export)
from factory_foundation.db.uow import has_active_uow, unit_of_work
from factory_foundation.models.registry import ErrorCodeRow, IdPrefixRow

__all__ = ["initialize_foundation", "CORE_MIGRATION_CONFIG"]


def _persist_error_codes(session: Session) -> None:
    for code, meta in error_catalog.all().items():
        row = session.get(ErrorCodeRow, code)
        if row is None:
            session.add(
                ErrorCodeRow(
                    code=code, owner_subsystem=meta.owner_subsystem, retryable=meta.retryable
                )
            )
        else:
            row.owner_subsystem = meta.owner_subsystem
            row.retryable = meta.retryable


def _persist_id_prefixes(session: Session) -> None:
    for prefix, owner in id_prefix_registry.all().items():
        if session.get(IdPrefixRow, prefix) is None:
            session.add(IdPrefixRow(prefix=prefix, owner_subsystem=owner))


def initialize_foundation(*, owner: Subsystem = Subsystem.S0) -> None:
    """Register S0 built-ins into ``core`` and load all catalog registries."""
    _ = owner
    if has_active_uow():
        _do_initialize()
    else:
        with unit_of_work():
            _do_initialize()


def _do_initialize() -> None:
    from factory_foundation.db.uow import current_session

    session = current_session()
    register_artifact_types(INITIAL_ARTIFACT_CATALOG, session=session)
    register_audit_event_types(BUILTIN_AUDIT_EVENT_TYPES, session=session)
    _persist_error_codes(session)
    _persist_id_prefixes(session)
    session.flush()
