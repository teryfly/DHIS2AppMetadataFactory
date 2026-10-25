"""The rule that only S2 writes UIDs (BP-DRF-005.4, P-04, BP-AC-01).

UIDs are allocated from the project's pool by S2 the moment an entry first enters
a draft.  A patch that carries one would let an agent or a user invent identity,
which breaks the "one concept one UID" guarantee, so it is rejected outright.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from factory_blueprint.content.layout import escape
from factory_blueprint.content.pointer import PointerError, locate, parse_pointer
from factory_blueprint.enums import Section
from factory_blueprint.patch.rfc6902 import Operation

#: DHIS2 UID shape: 11 characters, first is a letter (BP-REG-007).
UID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{10}$")
UID_FIELD_NAME = "uid"


@dataclass(frozen=True)
class UidWrite:
    """A UID the patch tried to write, and where."""

    path: str
    value: str
    reason: str


def _iter_strings(value: Any, prefix: str = "", parent_key: str = "") -> Iterator[tuple[str, str, str]]:
    """Yield (path, key, value) for every string in a JSON tree, lists included."""
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}/{escape(key)}"
            if isinstance(item, str):
                yield path, key, item
            else:
                yield from _iter_strings(item, path, key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}/{index}"
            if isinstance(item, str):
                yield path, parent_key, item
            else:
                yield from _iter_strings(item, path, parent_key)


def targets_uid_field(pointer: str) -> bool:
    """True when the pointer's last token is the ``uid`` member of an entry."""
    try:
        tokens = parse_pointer(pointer)
    except PointerError:
        return False
    return bool(tokens) and tokens[-1] == UID_FIELD_NAME


def find_uid_writes(operation: Operation, section: Section) -> list[UidWrite]:
    """Detect any UID the operation tries to introduce."""
    writes: list[UidWrite] = []

    if targets_uid_field(operation.path):
        writes.append(
            UidWrite(
                path=operation.path,
                value=str(operation.value) if operation.has_value else "",
                reason="the path addresses a uid member",
            )
        )

    if operation.has_value:
        for path, key, value in _iter_strings(operation.value):
            if key == UID_FIELD_NAME and value:
                writes.append(
                    UidWrite(
                        path=f"{operation.path}{path}",
                        value=value,
                        reason="the value carries a uid member",
                    )
                )
    return sorted(writes, key=lambda w: (w.path, w.value))


def find_uid_shaped_literals(operation: Operation, section: Section) -> list[UidWrite]:
    """Flag UID-shaped strings in identity-bearing fields of UID-sensitive sections.

    Only string values of keys that mean "an object reference" are inspected, so a
    businessKey that happens to be 11 lowercase characters is not mistaken for one.
    """
    if section not in _UID_REFERENCE_FIELDS:
        return []
    if not operation.has_value:
        return []
    wanted = _UID_REFERENCE_FIELDS[section]
    found: list[UidWrite] = []
    for path, key, value in _iter_strings(operation.value):
        if key in wanted and UID_PATTERN.match(value):
            found.append(
                UidWrite(
                    path=f"{operation.path}{path}",
                    value=value,
                    reason=f"{key} must reference a businessKey, not a UID",
                )
            )
    return sorted(found, key=lambda w: (w.path, w.value))


#: sections whose object references must be businessKeys (FR-PG-002, FR-CMP-010).
_UID_REFERENCE_FIELDS: dict[Section, frozenset[str]] = {
    Section.REGISTRY: frozenset({"uid"}),
    Section.PAGES: frozenset({"registryRefs"}),
    Section.API_INTENTS: frozenset({"registryRefs"}),
    Section.UI_INTENTS: frozenset({"businessKey", "optionSetKey", "sourceField"}),
    Section.RULES: frozenset({"programKey", "programStageKey", "target", "sourceField"}),
    Section.FIXTURES: frozenset({"targetKey", "attributeKey"}),
    Section.MAPPING: frozenset({"uid"}),
}


def check_operations(operations: list[Operation]) -> list[UidWrite]:
    """All UID writes across a patch, ordered by path."""
    writes: list[UidWrite] = []
    for operation in operations:
        try:
            section = locate(operation.path).section
        except PointerError:
            continue
        writes.extend(find_uid_writes(operation, section))
        writes.extend(find_uid_shaped_literals(operation, section))
    unique = {(w.path, w.value, w.reason): w for w in writes}
    return [unique[key] for key in sorted(unique)]
