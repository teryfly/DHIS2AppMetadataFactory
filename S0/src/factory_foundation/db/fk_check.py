"""Cross-schema foreign-key checker (FD-DB-006).

Scans every registered table definition and reports foreign keys that point at a
different schema.  D-08 forbids cross-schema foreign keys; this makes the rule
verifiable in tests (FD-AC-09).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import MetaData
from sqlalchemy.schema import ForeignKeyConstraint

from factory_foundation.common.errors import SchemaError
from factory_foundation.models.base import ALL_METADATA


@dataclass(frozen=True)
class CrossSchemaFk:
    source_table: str
    source_schema: str
    column: str
    target_table: str
    target_schema: str

    def __str__(self) -> str:
        return (
            f"{self.source_schema}.{self.source_table}.{self.column} -> "
            f"{self.target_schema}.{self.target_table}"
        )


def _resolve_target(fk: ForeignKeyConstraint, default_schema: str | None) -> tuple[str | None, str]:
    """``(schema, table)`` a foreign key points at, read off its declaration.

    Each schema keeps its own ``MetaData`` (FD-DB-002), so a cross-schema target is never
    resolvable from the declaring table's metadata and ``fk.referred_table`` raises
    ``NoReferencedTableError`` on exactly the case this checker exists for.  The dotted
    target name is the usable source: three parts carry their own schema, two parts
    belong to the declaring schema.
    """
    if not fk.elements:
        return None, "?"
    parts = fk.elements[0].target_fullname.split(".")
    if len(parts) == 3:
        return parts[0], parts[1]
    if len(parts) == 2:
        return default_schema, parts[0]
    return None, "?"


def find_cross_schema_fk(metadata: list[MetaData] | None = None) -> list[CrossSchemaFk]:
    """Return all cross-schema foreign keys across the given metadata."""
    violations: list[CrossSchemaFk] = []
    for md in metadata if metadata is not None else ALL_METADATA:
        default_schema = md.schema
        for table in md.tables.values():
            table_schema = table.schema or default_schema
            for constraint in table.foreign_key_constraints:
                if not isinstance(constraint, ForeignKeyConstraint):
                    continue
                target_schema, target_table = _resolve_target(constraint, default_schema)
                if target_schema is None:
                    continue
                if target_schema != table_schema:
                    for fk_col in constraint.columns:
                        violations.append(
                            CrossSchemaFk(
                                source_table=table.name,
                                source_schema=table_schema or "?",
                                column=fk_col.name,
                                target_table=target_table,
                                target_schema=target_schema or "?",
                            )
                        )
    return violations


def check_no_cross_schema_fk(metadata: list[MetaData] | None = None) -> None:
    """Raise ``SCHEMA_ERROR`` if any cross-schema foreign key exists (FD-AC-09)."""
    violations = find_cross_schema_fk(metadata)
    if violations:
        detail = "; ".join(str(v) for v in violations)
        raise SchemaError(
            f"found {len(violations)} cross-schema foreign key(s), forbidden by D-08: {detail}",
            details={"violations": [str(v) for v in violations]},
        )
