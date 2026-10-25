"""Schema validation with a deterministic result order.

``jsonschema`` does not guarantee the order of ``iter_errors``, but S2 needs one:
issue IDs are derived from rule + location and must be identical for identical
input (BP-VAL-001), and schema validation feeds both Patch checks (BP-DRF-005.2)
and freeze.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory_blueprint.enums import Section
from factory_blueprint.schema import store


@dataclass(frozen=True, order=True)
class SchemaViolation:
    """A single schema failure; ordering makes derived issue IDs stable."""

    path: str
    keyword: str
    message: str
    section: str = ""

    @property
    def rule_id(self) -> str:
        return "BPV-SCH-001"


def _escape(token: object) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def _location(error: Any) -> str:
    parts = [_escape(token) for token in error.absolute_path]
    return "/" + "/".join(parts) if parts else ""


def _top_section(error: Any) -> str:
    path = list(error.absolute_path)
    return str(path[0]) if path else ""


def _to_violations(errors: Any, section: str) -> list[SchemaViolation]:
    violations = [
        SchemaViolation(
            path=_location(error),
            keyword=str(error.validator),
            message=str(error.message),
            section=section if section else _top_section(error),
        )
        for error in errors
    ]
    return sorted(violations)


def validate_section(
    section: Section | str, value: Any, schema_version: int | None = None
) -> list[SchemaViolation]:
    name = Section(section).value
    validator = store.validator_for_section(name, schema_version)
    return _to_violations(validator.iter_errors(value), name)


def validate_document(document: Any, schema_version: int | None = None) -> list[SchemaViolation]:
    validator = store.validator_for_document(schema_version)
    return _to_violations(validator.iter_errors(document), "")


def is_valid_section(
    section: Section | str, value: Any, schema_version: int | None = None
) -> bool:
    return not validate_section(section, value, schema_version)
