"""S2 error classes over the foundation's error model (00 doc 6.5)."""

from __future__ import annotations

from factory_foundation.api import FactoryError


class BlueprintError(FactoryError):
    """Base for S2 errors that need no dedicated catalogue code."""

    code = "REGISTRY_ERROR"


class RegistryError(BlueprintError):
    code = "REGISTRY_ERROR"


class UidPoolExhausted(BlueprintError):
    """BP-REG-008a.3: replenish the pool, then retry the submission."""

    code = "UID_POOL_EXHAUSTED"


class SchemaVersionUnsupported(BlueprintError):
    code = "BP_UNSUPPORTED_SCHEMA_VERSION"


class ExpressionGrammarMissing(BlueprintError):
    """BP-VAL-021: no expression grammar is shipped for the requested DHIS2 version."""

    code = "BP_EXPRESSION_GRAMMAR_MISSING"


class FeatureNotEnabled(BlueprintError):
    """A compiler or capability of a later milestone was requested."""

    code = "BP_FEATURE_NOT_ENABLED"


class NotPromotable(BlueprintError):
    """BP-DRF-008: the frozen draft still carries BLOCKING problems."""

    code = "BP_NOT_PROMOTABLE"


class DraftNotMaterialized(BlueprintError):
    """The draft base content must be staged outside a transaction first."""

    code = "BP_DRAFT_NOT_MATERIALIZED"


class OperationInProgress(BlueprintError):
    code = "BP_IN_PROGRESS"


class ImportConflict(BlueprintError):
    code = "BP_IMPORT_CONFLICT"


class DescriptorMissing(BlueprintError):
    """BP-TD-003 would be a BLOCKING issue; this is the API-level signal that a
    caller asked S2 about a type S2 does not know at all."""

    code = "REGISTRY_ERROR"
