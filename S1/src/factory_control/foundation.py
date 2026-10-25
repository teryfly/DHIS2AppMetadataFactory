"""Single import site for the S0 foundation (00 doc 6.1, D-13).

No module inside :mod:`factory_control` other than this one may import
``factory_foundation``.  Every other layer depends on the aliases re-exported
here, so a change in the foundation surfaces in exactly one place.

S0 is developed concurrently in a sibling repository, therefore
:func:`verify_foundation_contract` runs during start-up and reports every symbol
S1 relies on that S0 no longer exports as one readable
:class:`FoundationContractError` rather than an ``ImportError`` surfacing deep
inside a request handler or a Temporal activity.
"""

from __future__ import annotations

from factory_foundation import api as _api

# --- Startup and housekeeping ----------------------------------------------
# --- Transactions and unit of work (FD-DB-003 / 004 / 005) ------------------
# --- Engine, sessions and migrations (FD-DB-001 / 007) ----------------------
# --- Schema-bound ORM bases (FD-DB-002) ------------------------------------
# --- Artifacts (FD-ART) ----------------------------------------------------
# --- Audit (FD-AUD) --------------------------------------------------------
# --- Idempotency (FD-IDEM) -------------------------------------------------
# --- Object store and secrets (FD-OBJ / FD-SEC) ----------------------------
# --- Common tools (FD-COM) -------------------------------------------------
# --- Progress and cancellation (FD-PRG) ------------------------------------
# --- Error model (FD-COM-001) ---------------------------------------------
from factory_foundation.api import (
    CORE_MIGRATION_CONFIG,
    DHIS2_EXECUTION_SCOPE,
    INITIAL_ARTIFACT_CATALOG,
    ArtifactCreate,
    ArtifactError,
    ArtifactInfo,
    ArtifactPurgedError,
    ArtifactQuery,
    ArtifactStatus,
    AuditEventInput,
    AuditQuery,
    AuditRecord,
    BeginResult,
    CancellationToken,
    CancelledError,
    Clock,
    ConcurrencyError,
    ContentStream,
    CpBase,
    CrossSchemaFk,
    DataClassification,
    DiffResult,
    ErrorMeta,
    FactoryError,
    FixedClock,
    FoundationHarness,
    HousekeepingResult,
    IdempotencyConflictError,
    IdemRecord,
    InternalError,
    LineageResult,
    ManualCancellationToken,
    MemorySecretBackend,
    MigrationConfig,
    MigrationError,
    NotFoundError,
    NullProgressReporter,
    ObjectStore,
    OperationContext,
    OperatorType,
    PackResult,
    Page,
    ProgressEvent,
    ProgressReporter,
    Schema,
    Secret,
    SecretBackend,
    SecretResolver,
    SecretUnavailableError,
    StagingHandle,
    Subsystem,
    SystemClock,
    TransactionMisuseError,
    UnpackResult,
    ValidationError,
    VerifyResult,
    VersionedAggregate,
    after_commit,
    apply_retention,
    audit,
    bind_operation_context,
    build_object_path,
    bump_version,
    canonical_json,
    check_no_cross_schema_fk,
    check_version,
    cleanup_expired_idempotency,
    cleanup_expired_staging,
    configure_engine,
    configure_logging,
    configure_object_store,
    configure_object_store_from_url,
    configure_tracing,
    content_hash,
    current_context_or_default,
    current_revision,
    current_session,
    diff,
    error_catalog,
    external_io,
    file_json,
    find_cross_schema_fk,
    get_artifact,
    get_audit,
    get_engine,
    get_logger,
    get_object_store,
    get_operation_context,
    has_active_uow,
    idempotency,
    initialize_foundation,
    install_development_backends,
    is_external_io,
    is_registered_event_type,
    lineage,
    link_pending,
    list_artifact_types,
    load_settings,
    new_correlation_id,
    new_id,
    new_session,
    normalize_hash,
    open_content,
    pack,
    purge_content,
    query_artifacts,
    query_audit,
    raise_for_code,
    read_content_bytes,
    redact_structure,
    register_artifact,
    register_artifact_types,
    register_audit_event_types,
    register_error_codes,
    register_id_prefixes,
    register_secret_backend,
    register_settings_schema,
    resolve_secret,
    resolve_test_database_url,
    run_full_chain_verify,
    run_housekeeping,
    run_migrations,
    set_status,
    stage_content,
    store_secret,
    structure_hash,
    trace_span,
    tracer,
    unit_of_work,
    unpack,
    utcnow,
    verify_chain,
    version_history,
)
from sqlalchemy.orm import Session

#: Symbols S1 imports from the foundation.  Kept as data so the check below can
#: report a readable diff when S0 moves one of them.
FOUNDATION_CONTRACT: tuple[str, ...] = (
    # startup
    "CORE_MIGRATION_CONFIG",
    "HousekeepingResult",
    "initialize_foundation",
    "run_housekeeping",
    # transactions
    "VersionedAggregate",
    "after_commit",
    "bump_version",
    "check_version",
    "current_session",
    "external_io",
    "has_active_uow",
    "is_external_io",
    "unit_of_work",
    # engine / migrations
    "CrossSchemaFk",
    "MigrationConfig",
    "check_no_cross_schema_fk",
    "configure_engine",
    "current_revision",
    "find_cross_schema_fk",
    "FoundationHarness",
    "resolve_test_database_url",
    "get_engine",
    "new_session",
    "run_migrations",
    # orm base
    "CpBase",
    # artifacts
    "ArtifactCreate",
    "ArtifactInfo",
    "ArtifactQuery",
    "ArtifactStatus",
    "ContentStream",
    "DiffResult",
    "INITIAL_ARTIFACT_CATALOG",
    "LineageResult",
    "Page",
    "PackResult",
    "StagingHandle",
    "UnpackResult",
    "apply_retention",
    "cleanup_expired_staging",
    "diff",
    "get_artifact",
    "lineage",
    "list_artifact_types",
    "open_content",
    "pack",
    "purge_content",
    "query_artifacts",
    "read_content_bytes",
    "register_artifact",
    "register_artifact_types",
    "set_status",
    "stage_content",
    "unpack",
    "version_history",
    # audit
    "AuditEventInput",
    "AuditQuery",
    "AuditRecord",
    "VerifyResult",
    "audit",
    "get_audit",
    "is_registered_event_type",
    "link_pending",
    "query_audit",
    "register_audit_event_types",
    "run_full_chain_verify",
    "verify_chain",
    # idempotency
    "DHIS2_EXECUTION_SCOPE",
    "BeginResult",
    "IdemRecord",
    "cleanup_expired_idempotency",
    "idempotency",
    # object store / secrets
    "ObjectStore",
    "Secret",
    "SecretBackend",
    "SecretResolver",
    "MemorySecretBackend",
    "build_object_path",
    "configure_object_store",
    "configure_object_store_from_url",
    "get_object_store",
    "install_development_backends",
    "register_secret_backend",
    "resolve_secret",
    "store_secret",
    # tools
    "Clock",
    "DataClassification",
    "FixedClock",
    "OperationContext",
    "OperatorType",
    "SystemClock",
    "bind_operation_context",
    "canonical_json",
    "configure_logging",
    "configure_tracing",
    "content_hash",
    "current_context_or_default",
    "file_json",
    "get_logger",
    "get_operation_context",
    "load_settings",
    "new_correlation_id",
    "new_id",
    "normalize_hash",
    "redact_structure",
    "register_settings_schema",
    "structure_hash",
    "tracer",
    "utcnow",
    # progress / cancellation
    "CancellationToken",
    "ManualCancellationToken",
    "NullProgressReporter",
    "ProgressEvent",
    "ProgressReporter",
    # errors
    "ArtifactError",
    "ArtifactPurgedError",
    "CancelledError",
    "ConcurrencyError",
    "ErrorMeta",
    "FactoryError",
    "IdempotencyConflictError",
    "InternalError",
    "MigrationError",
    "NotFoundError",
    "Schema",
    "SecretUnavailableError",
    "Subsystem",
    "TransactionMisuseError",
    "ValidationError",
    "error_catalog",
    "raise_for_code",
    "register_error_codes",
    "register_id_prefixes",
)


__all__ = [
    "FOUNDATION_CONTRACT",
    "FoundationContractError",
    "find_artifact",
    "verify_foundation_contract",
    *FOUNDATION_CONTRACT,
]


class FoundationContractError(RuntimeError):
    """S0 no longer provides a symbol S1 depends on."""


def find_artifact(artifact_id: str, *, session: Session | None = None) -> ArtifactInfo | None:
    """Look an artifact up without the exception S0 raises for a missing one.

    Gate binding re-checks and artifact projections have to distinguish "this id
    no longer resolves" from a genuine lookup failure, so the raise is converted
    into ``None`` here rather than at every call site.
    """

    try:
        return get_artifact(artifact_id, session=session)
    except NotFoundError:
        return None


def verify_foundation_contract() -> None:
    """Assert every contracted symbol is still importable from the foundation.

    Called by :func:`factory_control.bootstrap.bootstrap` before anything else
    runs, and by a test, so concurrent S0 work breaks loudly at one point.
    """
    missing = [name for name in FOUNDATION_CONTRACT if not hasattr(_api, name)]
    if missing:
        raise FoundationContractError(
            "factory_foundation.api is missing symbols required by S1: "
            + ", ".join(sorted(missing))
        )
