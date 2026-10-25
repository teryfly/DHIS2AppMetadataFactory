"""businessKey uniqueness and reference resolution (BP-DRF-005.3, BP-VAL-010).

Uniqueness is *per collection*: the same concept keeps one businessKey and may
appear as a domain entity, a mapping entry and a Registry entry at once.
References are resolved against the flat key index of the document, so a dangling
reference is reported instead of silently compiled (P-02).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.content.document import descend, entries_of
from factory_blueprint.content.layout import ENTRY_CONTAINERS, EntryContainer

BUSINESS_KEY = "businessKey"
PAGE_KEY = "pageKey"
INTENT_KEY = "intentKey"
OPERATION_KEY = "operationKey"
DECISION_ID = "decisionId"

#: which key field each container indexes
KEY_FIELD_BY_CONTAINER: dict[str, str] = {c.path: c.key_field for c in ENTRY_CONTAINERS}


@dataclass(frozen=True)
class DuplicateKey:
    container_path: str
    key: str
    count: int
    #: which field of the entry holds this key — the container's primary key or a secondary one
    field: str = "businessKey"


@dataclass(frozen=True)
class UnresolvedReference:
    from_container: str
    from_key: str
    field: str
    target: str
    target_kind: str


@dataclass
class KeyIndex:
    business_keys: set[str] = field(default_factory=set)
    page_keys: set[str] = field(default_factory=set)
    intent_keys: set[str] = field(default_factory=set)
    operation_keys: set[str] = field(default_factory=set)
    decision_ids: set[str] = field(default_factory=set)

    def contains(self, key: str, kind: str) -> bool:
        return key in self.set_for(kind)

    def set_for(self, kind: str) -> set[str]:
        if kind == PAGE_KEY:
            return self.page_keys
        if kind == INTENT_KEY:
            return self.intent_keys
        if kind == OPERATION_KEY:
            return self.operation_keys
        if kind == DECISION_ID:
            return self.decision_ids
        return self.business_keys


#: (container path, field path inside an entry, expected target kind).
#: A field path supports ``a.b`` and ``list[].child``.
REFERENCE_RULES: tuple[tuple[str, str, str], ...] = (
    ("/requirements/items", "actors[]", BUSINESS_KEY),
    ("/domain/entities", "attributes[].optionSetKey", BUSINESS_KEY),
    ("/domain/entities", "useCases[]", BUSINESS_KEY),
    ("/domain/relationships", "from", BUSINESS_KEY),
    ("/domain/relationships", "to", BUSINESS_KEY),
    ("/useCases/items", "actors[]", BUSINESS_KEY),
    ("/useCases/items", "requirements[]", BUSINESS_KEY),
    ("/actors/items", "roleKeys[]", BUSINESS_KEY),
    ("/organizationModel/orgUnitRefs", "levelKey", BUSINESS_KEY),
    ("/accessModel/roles", "userGroupKeys[]", BUSINESS_KEY),
    ("/accessModel/userGroups", "roleKeys[]", BUSINESS_KEY),
    ("/accessModel", "sharingOverrides[].businessKey", BUSINESS_KEY),
    ("/mapping/entries", "businessKey", BUSINESS_KEY),
    ("/mapping/entries", "decisionId", DECISION_ID),
    ("/registry/entries", "decisionId", DECISION_ID),
    ("/pages/items", "useCases[]", BUSINESS_KEY),
    ("/pages/items", "actors[]", BUSINESS_KEY),
    ("/pages/items", "registryRefs[]", BUSINESS_KEY),
    ("/pages/items", "apiIntentRefs[]", INTENT_KEY),
    ("/pages/items", "uiIntentRef", INTENT_KEY),
    ("/pages/items", "navigation.from[]", PAGE_KEY),
    ("/pages/items", "navigation.to[]", PAGE_KEY),
    ("/apiIntents/items", "pageKey", PAGE_KEY),
    ("/apiIntents/items", "registryRefs[]", BUSINESS_KEY),
    ("/apiIntents/items", "requestBodyShape[].businessKey", BUSINESS_KEY),
    ("/uiIntents/items", "pageKey", PAGE_KEY),
    ("/uiIntents/items", "fieldMappings[].businessKey", BUSINESS_KEY),
    ("/uiIntents/items", "fieldMappings[].optionSetKey", BUSINESS_KEY),
    ("/uiIntents/items", "programRules[]", BUSINESS_KEY),
    ("/rules/validationRules", "programKey", BUSINESS_KEY),
    ("/rules/validationRules", "programStageKey", BUSINESS_KEY),
    ("/rules/programRules", "programKey", BUSINESS_KEY),
    ("/rules/programRules", "programStageKey", BUSINESS_KEY),
    ("/rules/programRules", "actions[].target", BUSINESS_KEY),
    ("/rules/programRules", "variables[].sourceField", BUSINESS_KEY),
    ("/rules/programRuleVariables", "programKey", BUSINESS_KEY),
    ("/rules/programIndicators", "programKey", BUSINESS_KEY),
    ("/fixtures/specifications", "targetKey", BUSINESS_KEY),
    ("/fixtures/specifications", "attributeValues[].attributeKey", BUSINESS_KEY),
    ("/fixtures/specifications", "orgUnitKeys[]", BUSINESS_KEY),
    ("/fixtures/demoUsers", "roleKeys[]", BUSINESS_KEY),
    ("/fixtures/demoUsers", "userGroupKeys[]", BUSINESS_KEY),
    ("/fixtures/demoUsers", "orgUnitScope[]", BUSINESS_KEY),
)


def containers(document: dict[str, Any]) -> Iterator[tuple[EntryContainer, list[dict[str, Any]]]]:
    for container in ENTRY_CONTAINERS:
        yield container, entries_of(document, container)


def build_key_index(document: dict[str, Any]) -> KeyIndex:
    index = KeyIndex()
    for container, entries in containers(document):
        for key_field in container.indexed_fields:
            bucket = index.set_for(key_field)
            for entry in entries:
                key = entry.get(key_field)
                if isinstance(key, str):
                    bucket.add(key)
    return index


def duplicate_keys(document: dict[str, Any]) -> list[DuplicateKey]:
    """Every entry key the promoted index would have to put on two rows.

    ``bp.business_key_index`` is keyed by (project, version, section, key_kind, entry_key), so
    each field in ``container.indexed_fields`` — the primary key and every secondary key — is a
    uniqueness obligation, not just the primary one.  Checking only the primary key let a
    duplicate ``operationKey`` pass validation and surface as a primary-key violation while the
    version was being promoted.
    """
    duplicates: list[DuplicateKey] = []
    for container, entries in containers(document):
        for key_field in container.indexed_fields:
            counts: dict[str, int] = {}
            for entry in entries:
                key = entry.get(key_field)
                if isinstance(key, str):
                    counts[key] = counts.get(key, 0) + 1
            duplicates.extend(
                DuplicateKey(container.path, key, count, field=key_field)
                for key, count in sorted(counts.items())
                if count > 1
            )
    return sorted(duplicates, key=lambda d: (d.container_path, d.field, d.key))


def _walk_field(entry: dict[str, Any], path: str) -> Iterator[tuple[str, Any]]:
    """Yield (leaf_field_name, value) for a field path with list expansion."""
    tokens = path.split(".")
    current: list[Any] = [entry]
    walked: list[str] = []
    for token in tokens:
        if token.endswith("[]"):
            name = token[:-2]
            walked.append(name)
            next_values: list[Any] = []
            for node in current:
                if isinstance(node, dict):
                    value = node.get(name)
                    if isinstance(value, list):
                        next_values.extend(value)
            current = next_values
        else:
            walked.append(token)
            nested: list[Any] = []
            for node in current:
                if isinstance(node, dict) and token in node:
                    nested.append(node[token])
            current = nested
    leaf = walked[-1] if walked else path
    for value in current:
        yield leaf, value


def _entry_id(container_path: str, entry: dict[str, Any]) -> str:
    key_field = KEY_FIELD_BY_CONTAINER.get(container_path, BUSINESS_KEY)
    value = entry.get(key_field)
    return value if isinstance(value, str) else "<anonymous>"


def unresolved_references(
    document: dict[str, Any], only_entries: Iterable[tuple[str, str]] | None = None
) -> list[UnresolvedReference]:
    """Find references that do not resolve, optionally limited to touched entries.

    ``only_entries`` is a set of (container path, entry key) pairs, which keeps a
    Patch submission cheap on a 5,000-object project (BP-NFR-03).
    """
    index = build_key_index(document)
    wanted: set[tuple[str, str]] | None = (
        {(path, key) for path, key in only_entries} if only_entries is not None else None
    )
    unresolved: list[UnresolvedReference] = []
    for container_path, field_path, kind in REFERENCE_RULES:
        if container_path not in KEY_FIELD_BY_CONTAINER:
            # whole-section rule (e.g. /accessModel), handled by its own lookup
            node = descend(document, container_path)
            if not isinstance(node, dict):
                continue
            for leaf, value in _walk_field(node, field_path):
                unresolved.extend(_check(container_path, "<section>", leaf, value, kind, index))
            continue
        for container, entries in containers(document):
            if container.path != container_path:
                continue
            for entry in entries:
                entry_key = _entry_id(container_path, entry)
                if wanted is not None and (container_path, entry_key) not in wanted:
                    continue
                for leaf, value in _walk_field(entry, field_path):
                    unresolved.extend(
                        _check(container_path, entry_key, leaf, value, kind, index)
                    )
    return sorted(unresolved, key=lambda u: (u.from_container, u.from_key, u.field, u.target))


def _check(
    container: str, entry_key: str, field_name: str, value: Any, kind: str, index: KeyIndex
) -> list[UnresolvedReference]:
    if not isinstance(value, str) or not value:
        return []
    if index.contains(value, kind):
        return []
    return [
        UnresolvedReference(
            from_container=container,
            from_key=entry_key,
            field=field_name,
            target=value,
            target_kind=kind,
        )
    ]
