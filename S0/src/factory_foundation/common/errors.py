"""Error model and error-code catalog (FD-COM-001).

`FactoryError` is the base class all public APIs raise.  Its fields mirror the
overall SRS 19.2.1 shape: ``errorCode``, ``message``, ``retryable``, ``stage``,
``artifactId``, ``details`` (``correlationId`` is read from the operation
context).  Retryability follows 19.2.2.

The catalog contains every error code from overall SRS 19.2.1 plus the codes S0
itself raises, and lets subsystems register their own codes at start-up
(FD-ASM-01).  ``BUILTIN_ERROR_CODES`` is the authoritative table for the codes
S0 owns (FD-COM-001a): owner ``S0``, available from process start and persisted
into ``core.error_code`` by ``initialize_foundation()``.
"""

from __future__ import annotations

import enum
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, NoReturn

# --- Schemas and subsystem codes ------------------------------------------


class Schema(str, enum.Enum):
    """Database schemas, one per backend subsystem (00 doc 4.1)."""

    CORE = "core"
    CP = "cp"
    BP = "bp"
    AI = "ai"
    DX = "dx"


# subsystem label -> owning schema
_SUBSYSTEM_SCHEMA: dict[str, Schema] = {
    "S0": Schema.CORE,
    "S1": Schema.CP,
    "S2": Schema.BP,
    "S3": Schema.AI,
    "S4": Schema.DX,
}

# audit event-type namespace (schema key) -> subsystem label
_SCHEMA_SUBSYSTEM: dict[str, str] = {s.value: k for k, s in _SUBSYSTEM_SCHEMA.items()}


@enum.unique
class Subsystem(str, enum.Enum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"

    @property
    def schema(self) -> Schema:
        return _SUBSYSTEM_SCHEMA[self.value]

    @classmethod
    def coerce(cls, value: str | Subsystem) -> Subsystem:
        if isinstance(value, cls):
            return value
        up = value.upper()
        if up not in cls._value2member_map_:
            raise ValueError(f"unknown subsystem code: {value!r}")
        return cls(up)


# --- Error codes -----------------------------------------------------------


# S0's own built-in codes (FD-COM-001a): ``{code: retryable}``.  Every entry is
# owned by S0; retryability is exactly what the clause marks -- only
# ``CONCURRENCY_ERROR`` and ``ARTIFACT_READBACK_MISMATCH`` are retryable, every
# other code is not.  Subsystems must not re-register these; see
# ``ErrorCatalog.register`` (REGISTRY_CONFLICT).
BUILTIN_ERROR_CODES: dict[str, bool] = {
    "TRANSACTION_MISUSE": False,  # FD-DB-004
    "CONCURRENCY_ERROR": True,  # FD-DB-005 / NFR-CON-002: back off and retry
    "ARTIFACT_ERROR": False,
    "ARTIFACT_NOT_FOUND": False,
    "ARTIFACT_PURGED": False,  # FD-ART-010
    "ARTIFACT_HASH_MISMATCH": False,
    "ARTIFACT_READBACK_MISMATCH": True,  # FD-ART-004: re-stage and re-read the object
    "ARTIFACT_TOO_LARGE": False,
    "ARTIFACT_CLASSIFICATION_DOWNGRADE": False,
    "ARTIFACT_OWNERSHIP_VIOLATION": False,
    "ARTIFACT_TYPE_UNKNOWN": False,
    # artifact exists but is not the type the call asked for; any subsystem may
    # raise it and defines its own `details` (FD-COM-001a)
    "ARTIFACT_TYPE_MISMATCH": False,
    "ARTIFACT_NO_CONTENT": False,
    "ARTIFACT_NOT_PURGEABLE": False,
    "ARTIFACT_VERSION_CONFLICT": False,
    "ARTIFACT_STAGING_NOT_FOUND": False,
    "ARTIFACT_STAGING_MISMATCH": False,
    "INVALID_STATUS_TRANSITION": False,
    "IDEMPOTENCY_CONFLICT": False,  # FD-IDEM-002
    "SECRET_UNAVAILABLE": False,  # FD-SEC-003
    "SECRET_STORE_UNSUPPORTED": False,  # FD-SEC-004
    "SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION": False,  # FD-SEC-001
    "CANCELLED": False,  # FD-PRG-002
    "CHAIN_INTEGRITY_ERROR": False,  # FD-AUD-005
    "PACK_INTEGRITY_ERROR": False,  # FD-ART-015
    "OBJECT_NOT_FOUND": False,  # FD-OBJ-001
    "CONFIGURATION_ERROR": False,  # FD-COM-008
    "MIGRATION_ERROR": False,  # FD-DB-007
    "AUDIT_OUTSIDE_TRANSACTION": False,  # FD-AUD-003
    "AUDIT_EVENT_TYPE_UNKNOWN": False,  # FD-AUD-010
    "REGISTRY_CONFLICT": False,  # FD-COM-001a: duplicate registration in a catalog
}

# Overall SRS 19.2.1 categories raised by one upstream subsystem.  S0 pre-registers
# them so cross-subsystem callers can map retryability, but ownership stays there.
_UPSTREAM_OWNED: dict[str, tuple[str, bool]] = {
    "LLM_ERROR": ("S3", False),
    "LLM_TIMEOUT": ("S3", True),
    "POLICY_DENIED": ("S3", False),
    "SCHEMA_ERROR": ("S2", False),
    "REGISTRY_ERROR": ("S2", False),
    "GATE_NOT_APPROVED": ("S1", False),
    "APPROVAL_INVALIDATED": ("S1", False),
    "DHIS2_AUTH_ERROR": ("S4", False),
    "DHIS2_PERMISSION_ERROR": ("S4", False),
    "DHIS2_NOT_FOUND": ("S4", False),
    "DHIS2_IMPORT_ERROR": ("S4", False),
    "DHIS2_TIMEOUT": ("S4", True),
    "DRIFT_DETECTED": ("S4", False),
}

# 19.2.1 categories with no single upstream owner, owned by S0.
_SHARED_CODES: dict[str, bool] = {
    "VALIDATION_ERROR": False,
    "NOT_FOUND": False,
    "INTERNAL_ERROR": False,
}

_BUILTIN_CODES: dict[str, bool] = {
    **BUILTIN_ERROR_CODES,
    **{code: retryable for code, (_owner, retryable) in _UPSTREAM_OWNED.items()},
    **_SHARED_CODES,
}

_BUILTIN_OWNER: dict[str, str] = {code: "S0" for code in _BUILTIN_CODES}
_BUILTIN_OWNER.update({code: owner for code, (owner, _r) in _UPSTREAM_OWNED.items()})


@dataclass(frozen=True)
class ErrorMeta:
    code: str
    owner_subsystem: str
    retryable: bool


class ErrorCatalog:
    """Process-wide registry of known error codes and their retryability."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._codes: dict[str, ErrorMeta] = {}
        for code, retryable in _BUILTIN_CODES.items():
            self._codes[code] = ErrorMeta(code, _BUILTIN_OWNER.get(code, "S0"), retryable)

    def register(self, code: str, *, owner_subsystem: str, retryable: bool) -> ErrorMeta:
        """Register ``code``; re-registering under a different owner is a conflict.

        The same owner may register again (start-up is idempotent and merges), but a
        second owner would give one code two semantic owners (FD-COM-001a).
        """
        with self._lock:
            existing = self._codes.get(code)
            if existing is not None and existing.owner_subsystem != owner_subsystem:
                raise RegistryConflictError(
                    f"error code {code!r} is already owned by "
                    f"{existing.owner_subsystem}, cannot re-register as {owner_subsystem}",
                    details={
                        "code": code,
                        "existingOwner": existing.owner_subsystem,
                        "requestedOwner": owner_subsystem,
                    },
                )
            meta = ErrorMeta(code, owner_subsystem, retryable)
            self._codes[code] = meta
            return meta

    def register_many(self, metas: Iterable[ErrorMeta]) -> None:
        for m in metas:
            self.register(m.code, owner_subsystem=m.owner_subsystem, retryable=m.retryable)

    def get(self, code: str) -> ErrorMeta | None:
        with self._lock:
            return self._codes.get(code)

    def is_registered(self, code: str) -> bool:
        return self.get(code) is not None

    def is_retryable(self, code: str) -> bool:
        meta = self.get(code)
        # Unknown codes default to non-retryable (fail-safe: never auto-retry blindly).
        return meta.retryable if meta else False

    def all(self) -> dict[str, ErrorMeta]:
        with self._lock:
            return dict(self._codes)


error_catalog = ErrorCatalog()


# --- The exception itself --------------------------------------------------


class FactoryError(Exception):
    """Base class for every error surfaced through the public API (FD-COM-001)."""

    #: subclasses set this to their canonical error code
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        stage: str | None = None,
        artifact_id: str | None = None,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.error_code = code or self.code
        self.message = message or self.error_code
        self.retryable = (
            error_catalog.is_retryable(self.error_code) if retryable is None else retryable
        )
        self.stage = stage
        self.artifact_id = artifact_id
        self.details: dict[str, Any] = dict(details or {})
        if correlation_id is None:
            from factory_foundation.common.context import current_correlation_id

            correlation_id = current_correlation_id()
        self.correlation_id = correlation_id
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Serialised shape for REST error mapping (overall SRS 19.2.1)."""
        return {
            "errorCode": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
            "stage": self.stage,
            "artifactId": self.artifact_id,
            "correlationId": self.correlation_id,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({self.error_code}: {self.message})"


# Convenience subclasses for codes S0 itself raises.


class ArtifactError(FactoryError):
    code = "ARTIFACT_ERROR"


class ArtifactPurgedError(FactoryError):
    code = "ARTIFACT_PURGED"


class ConcurrencyError(FactoryError):
    code = "CONCURRENCY_ERROR"


class TransactionMisuseError(FactoryError):
    code = "TRANSACTION_MISUSE"


class IdempotencyConflictError(FactoryError):
    code = "IDEMPOTENCY_CONFLICT"


class SecretUnavailableError(FactoryError):
    code = "SECRET_UNAVAILABLE"


class CancelledError(FactoryError):
    code = "CANCELLED"


class NotFoundError(FactoryError):
    code = "NOT_FOUND"


class ValidationError(FactoryError):
    code = "VALIDATION_ERROR"


class SchemaError(FactoryError):
    code = "SCHEMA_ERROR"


class InternalError(FactoryError):
    code = "INTERNAL_ERROR"


class MigrationError(FactoryError):
    code = "MIGRATION_ERROR"


class RegistryConflictError(FactoryError):
    code = "REGISTRY_CONFLICT"


class SecretStoreUnsupportedError(FactoryError):
    code = "SECRET_STORE_UNSUPPORTED"


class SecretEnvBackendForbiddenInProductionError(FactoryError):
    code = "SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION"


_CODE_TO_CLASS: dict[str, type[FactoryError]] = {
    cls.code: cls
    for cls in (
        ArtifactError,
        ArtifactPurgedError,
        ConcurrencyError,
        TransactionMisuseError,
        IdempotencyConflictError,
        SecretUnavailableError,
        CancelledError,
        NotFoundError,
        ValidationError,
        SchemaError,
        InternalError,
        MigrationError,
        RegistryConflictError,
        SecretStoreUnsupportedError,
        SecretEnvBackendForbiddenInProductionError,
    )
}


def raise_for_code(code: str, message: str, **kw: Any) -> NoReturn:
    cls = _CODE_TO_CLASS.get(code, FactoryError)
    if cls is FactoryError and not error_catalog.is_registered(code):
        raise InternalError(f"unknown error code: {code}")
    raise cls(message, code=code, **kw)
