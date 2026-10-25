"""factory_foundation.api — the single public entry point (00 doc 6.1, D-13).

Other subsystems import ONLY from this module.  It re-exports the foundation's
public functions and Pydantic types grouped as in the S0 SRS section 6.
"""

from __future__ import annotations

# --- Common tools ----------------------------------------------------------
from factory_foundation.artifacts.catalog import (
    INITIAL_ARTIFACT_CATALOG,
    list_artifact_types,
    register_artifact_types,
)
from factory_foundation.artifacts.diff import diff
from factory_foundation.artifacts.dto import (
    ArtifactCreate,
    ArtifactInfo,
    ArtifactQuery,
    ContentStream,
    DiffResult,
    LineageResult,
    Page,
    StagingHandle,
)
from factory_foundation.artifacts.packing import PackResult, UnpackResult, pack, unpack
from factory_foundation.artifacts.service import (
    apply_retention,
    cleanup_expired_staging,
    get_artifact,
    lineage,
    open_content,
    purge_content,
    query_artifacts,
    read_content_bytes,
    register_artifact,
    set_status,
    stage_content,
    version_history,
)
from factory_foundation.audit.chain import VerifyResult
from factory_foundation.audit.dto import AuditEventInput, AuditQuery, AuditRecord
from factory_foundation.audit.registry import (
    BUILTIN_AUDIT_EVENT_TYPES,
    is_registered_event_type,
    list_audit_event_types,
    register_audit_event_types,
)
from factory_foundation.audit.service import (
    audit,
    get_audit,
    link_pending,
    query_audit,
    run_full_chain_verify,
    verify_chain,
)
from factory_foundation.audit.service import verify_chain as verify_audit_chain

# --- Startup & housekeeping ------------------------------------------------
from factory_foundation.bootstrap import CORE_MIGRATION_CONFIG, initialize_foundation
from factory_foundation.common.clock import Clock, FixedClock, SystemClock, utcnow
from factory_foundation.common.config import Settings, load_settings, register_settings_schema
from factory_foundation.common.context import (
    OperationContext,
    bind_operation_context,
    current_context_or_default,
    get_operation_context,
)
from factory_foundation.common.errors import (
    BUILTIN_ERROR_CODES,
    ArtifactError,
    ArtifactPurgedError,
    CancelledError,
    ConcurrencyError,
    ErrorMeta,
    FactoryError,
    IdempotencyConflictError,
    InternalError,
    MigrationError,
    NotFoundError,
    RegistryConflictError,
    Schema,
    SecretUnavailableError,
    Subsystem,
    TransactionMisuseError,
    ValidationError,
    error_catalog,
    raise_for_code,
)
from factory_foundation.common.hashing import (
    canonical_json,
    content_hash,
    file_json,
    normalize_hash,
    structure_hash,
)
from factory_foundation.common.ids import id_prefix_registry, new_correlation_id, new_id
from factory_foundation.common.logging import configure_logging, get_logger
from factory_foundation.common.redaction import redact_structure
from factory_foundation.common.tracing import configure_tracing, trace_span, tracer
from factory_foundation.db.engine import configure_engine, get_engine, new_session

# --- Transaction & UoW -----------------------------------------------------
from factory_foundation.db.external_io import external_io, is_external_io
from factory_foundation.db.fk_check import (
    CrossSchemaFk,
    check_no_cross_schema_fk,
    find_cross_schema_fk,
)
from factory_foundation.db.migrations import (
    MigrationConfig,
    current_revision,
    run_migrations,
)
from factory_foundation.db.optimistic import (
    VersionedAggregate,
    bump_version,
    check_version,
)
from factory_foundation.db.uow import (
    after_commit,
    current_session,
    has_active_uow,
    unit_of_work,
)
from factory_foundation.enums import (
    DHIS2_EXECUTION_SCOPE,
    ArtifactStatus,
    DataClassification,
    IdempotencyStatus,
    OperatorType,
)
from factory_foundation.housekeeping import HousekeepingResult, run_housekeeping
from factory_foundation.idempotency.service import (
    BeginResult,
    IdemRecord,
    cleanup_expired_idempotency,
    idempotency,
)
from factory_foundation.models.base import (
    AiBase,
    BpBase,
    CoreBase,
    CpBase,
    DxBase,
    register_schema_base,
)
from factory_foundation.objectstore.base import ObjectStore, build_object_path
from factory_foundation.objectstore.provider import (
    configure_from_url as configure_object_store_from_url,
)
from factory_foundation.objectstore.provider import (
    configure_object_store,
    get_object_store,
)
from factory_foundation.progress.protocols import (
    CancellationToken,
    ManualCancellationToken,
    NullProgressReporter,
    ProgressEvent,
    ProgressReporter,
)
from factory_foundation.secrets import (
    MemorySecretBackend,
    Secret,
    SecretBackend,
    SecretResolver,
    install_development_backends,
    register_secret_backend,
    resolve_secret,
    store_secret,
)
from factory_foundation.testing.harness import (
    FoundationHarness,
    harness,
    resolve_test_database_url,
)

# --- Registration helpers used by subsystem ``initialize()`` ---------------


def register_error_codes(codes: dict[str, tuple[str, bool]]) -> None:
    """Register subsystem error codes: ``{code: (owner_subsystem, retryable)}``."""
    for code, (owner, retryable) in codes.items():
        error_catalog.register(code, owner_subsystem=owner, retryable=retryable)


def register_id_prefixes(prefixes: dict[str, str]) -> None:
    """Register ID prefixes: ``{prefix: owner_subsystem}``."""
    for prefix, owner in prefixes.items():
        id_prefix_registry.register(prefix, owner_subsystem=owner)


__all__ = [
    # startup
    "CORE_MIGRATION_CONFIG",
    "initialize_foundation",
    "run_housekeeping",
    "HousekeepingResult",
    # transactions
    "unit_of_work",
    "after_commit",
    "current_session",
    "has_active_uow",
    "external_io",
    "is_external_io",
    "VersionedAggregate",
    "check_version",
    "bump_version",
    "configure_engine",
    "get_engine",
    "new_session",
    "run_migrations",
    "current_revision",
    "MigrationConfig",
    "check_no_cross_schema_fk",
    "find_cross_schema_fk",
    "CrossSchemaFk",
    # models/bases
    "CoreBase",
    "CpBase",
    "BpBase",
    "AiBase",
    "DxBase",
    "register_schema_base",
    # artifacts
    "register_artifact_types",
    "list_artifact_types",
    "INITIAL_ARTIFACT_CATALOG",
    "stage_content",
    "register_artifact",
    "get_artifact",
    "open_content",
    "read_content_bytes",
    "query_artifacts",
    "set_status",
    "lineage",
    "version_history",
    "purge_content",
    "apply_retention",
    "cleanup_expired_staging",
    "diff",
    "pack",
    "unpack",
    "PackResult",
    "UnpackResult",
    "StagingHandle",
    "ArtifactCreate",
    "ArtifactInfo",
    "ArtifactQuery",
    "ContentStream",
    "DiffResult",
    "LineageResult",
    "Page",
    # audit
    "register_audit_event_types",
    "list_audit_event_types",
    "is_registered_event_type",
    "BUILTIN_AUDIT_EVENT_TYPES",
    "audit",
    "get_audit",
    "query_audit",
    "verify_chain",
    "verify_audit_chain",
    "run_full_chain_verify",
    "link_pending",
    "AuditEventInput",
    "AuditQuery",
    "AuditRecord",
    "VerifyResult",
    # idempotency
    "idempotency",
    "cleanup_expired_idempotency",
    "BeginResult",
    "IdemRecord",
    "DHIS2_EXECUTION_SCOPE",
    # secrets
    "resolve_secret",
    "store_secret",
    "Secret",
    "SecretBackend",
    "SecretResolver",
    "MemorySecretBackend",
    "install_development_backends",
    "register_secret_backend",
    # object store
    "ObjectStore",
    "get_object_store",
    "configure_object_store",
    "configure_object_store_from_url",
    "build_object_path",
    # test fixtures (FD-TST-001)
    "FoundationHarness",
    "harness",
    "resolve_test_database_url",
    # tools
    "new_id",
    "new_correlation_id",
    "tracer",
    "configure_tracing",
    "trace_span",
    "canonical_json",
    "file_json",
    "content_hash",
    "structure_hash",
    "normalize_hash",
    "Clock",
    "SystemClock",
    "FixedClock",
    "utcnow",
    "OperationContext",
    "bind_operation_context",
    "get_operation_context",
    "current_context_or_default",
    "get_logger",
    "configure_logging",
    "redact_structure",
    "load_settings",
    "register_settings_schema",
    "Settings",
    # progress / cancellation
    "ProgressReporter",
    "ProgressEvent",
    "NullProgressReporter",
    "CancellationToken",
    "ManualCancellationToken",
    # errors
    "FactoryError",
    "ErrorMeta",
    "Subsystem",
    "Schema",
    "error_catalog",
    "register_error_codes",
    "register_id_prefixes",
    "raise_for_code",
    "ArtifactError",
    "ArtifactPurgedError",
    "BUILTIN_ERROR_CODES",
    "ConcurrencyError",
    "TransactionMisuseError",
    "IdempotencyConflictError",
    "SecretUnavailableError",
    "CancelledError",
    "NotFoundError",
    "ValidationError",
    "InternalError",
    "MigrationError",
    "RegistryConflictError",
    # enums
    "DataClassification",
    "ArtifactStatus",
    "OperatorType",
    "IdempotencyStatus",
]
