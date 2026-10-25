"""S2 Blueprint schema access: versioned section schemas and migrations."""

from factory_blueprint.schema.migrations import (
    MIGRATIONS,
    MigratedDocument,
    has_migration_path,
    migrate_document,
    register_migration,
)
from factory_blueprint.schema.store import (
    available_versions,
    current_schema_version,
    document_schema,
    registry_for,
    resolve_version,
    section_schema,
)
from factory_blueprint.schema.validate import (
    SchemaViolation,
    is_valid_section,
    validate_document,
    validate_section,
)

__all__ = [
    "MIGRATIONS",
    "MigratedDocument",
    "SchemaViolation",
    "available_versions",
    "current_schema_version",
    "document_schema",
    "has_migration_path",
    "is_valid_section",
    "migrate_document",
    "register_migration",
    "registry_for",
    "resolve_version",
    "section_schema",
    "validate_document",
    "validate_section",
]
