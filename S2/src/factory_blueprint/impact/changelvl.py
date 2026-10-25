"""Change level determination (BP-IMP-001/002, appendix B).

The level is derived from the diff alone; no caller can state or downgrade it.  A
change that appendix B does not name is never treated as Cosmetic — the default
is Minor, and anything that removes an entry is Major.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from factory_blueprint.content.document import BlueprintDocument, entries_of
from factory_blueprint.content.layout import ENTRY_CONTAINERS as _CONTAINERS
from factory_blueprint.content.layout import EntryContainer, escape
from factory_blueprint.enums import ChangeLevel, DeliveryMode, Section

#: display-only fields: changing one cannot affect structure (overall SRS 5.2)
COSMETIC_FIELDS: frozenset[str] = frozenset(
    {
        "description",
        "displayName",
        "shortName",
        "formName",
        "label",
        "name",
        "translations",
        "note",
        "notes",
        "comment",
        "rationale",
        "summary",
        "purpose",
        "title",
    }
)

#: fields S2 maintains itself; they never influence the change level
DERIVED_FIELDS: frozenset[str] = frozenset({"status", "managed", "source"})

#: per section, fields whose change is Major (appendix B rows)
MAJOR_FIELDS: dict[Section, frozenset[str]] = {
    Section.REGISTRY: frozenset({"dhis2Type", "uid", "disposition", "controlledFields"}),
    Section.MAPPING: frozenset({"dhis2Type", "structure", "notMapped"}),
    Section.API_INTENTS: frozenset(
        {
            "resource",
            "method",
            "pathParams",
            "queryParams",
            "fields",
            "filters",
            "order",
            "paging",
            "requestBodyShape",
        }
    ),
    Section.RULES: frozenset(
        {
            "expression",
            "leftExpression",
            "rightExpression",
            "programKey",
            "programStageKey",
            "actions",
            "variables",
            "missingValueStrategy",
            "periodType",
        }
    ),
    Section.DOMAIN: frozenset(
        {"attributes", "lifecycle", "from", "to", "cardinality", "ownership"}
    ),
    Section.GENERATION_POLICY: frozenset({"codePrefix", "namingConvention", "deletionPolicy"}),
    Section.PROJECT: frozenset({"targetDhis2", "defaultLanguage", "projectType"}),
    Section.UI_INTENTS: frozenset({"components", "fieldMappings"}),
    Section.ENVIRONMENT_CONSTRAINTS: frozenset({"constraint", "requiredCapabilities"}),
}

#: sections where any non-display modification changes the permission model (Major)
ALWAYS_MAJOR_SECTIONS: frozenset[Section] = frozenset(
    {Section.ACCESS_MODEL, Section.ORGANIZATION_MODEL}
)

#: DHIS2 property names inside mapping.fields whose change is structural
SEMANTIC_PROPERTY_NAMES: frozenset[str] = frozenset(
    {
        "valueType",
        "aggregationType",
        "domainType",
        "optionSet",
        "optionSetKey",
        "categoryCombo",
        "dataElement",
        "categoryOptionCombo",
        "formType",
        "type",
        "programType",
        "trackerType",
        "kind",
    }
)


@dataclass(frozen=True)
class Change:
    """One classified difference between the base version and the draft."""

    level: ChangeLevel
    reason: str
    section: str
    path: str
    business_key: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "reason": self.reason,
            "section": self.section,
            "path": self.path,
            "businessKey": self.business_key,
        }


#: fields identifying an entry: a patch that changes one is add + remove
IDENTITY_FIELDS: frozenset[str] = frozenset({"businessKey", "pageKey", "intentKey"})


def _changed_keys(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Top-level field names whose value actually differs."""
    return sorted(
        key
        for key in set(old) | set(new)
        if key not in DERIVED_FIELDS
        and key not in IDENTITY_FIELDS
        and old.get(key, _MISSING) != new.get(key, _MISSING)
    )


_MISSING = object()


def _differing_leaf_keys(old: Any, new: Any) -> set[str]:
    """Names of the fields that differ, stopping at display fields.

    A display field counts as one change no matter what happened inside it, so a
    restructured ``translations`` object is still a Cosmetic change.
    """
    found: set[str] = set()

    def walk(left: Any, right: Any, key: str) -> None:
        if key in DERIVED_FIELDS:
            return
        if key in COSMETIC_FIELDS:
            found.add(key)
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for name in set(left) | set(right):
                before = left.get(name, _MISSING)
                after = right.get(name, _MISSING)
                if before is not after and before != after:
                    walk(before, after, name)
            return
        found.add(key)

    if isinstance(old, dict) and isinstance(new, dict):
        for name in set(old) | set(new):
            if name in DERIVED_FIELDS:
                continue
            before = old.get(name, _MISSING)
            after = new.get(name, _MISSING)
            if before is not after and before != after:
                walk(before, after, name)
    elif old != new:
        found.add("value")
    return found


def _is_display_only(old: Any, new: Any) -> bool:
    """True when every difference sits under a display-only field name."""
    if old == new:
        return True
    leaves = _differing_leaf_keys(old, new)
    return bool(leaves) and leaves <= COSMETIC_FIELDS


def _classify_modified(
    section: Section, entry: dict[str, Any], updated: dict[str, Any], path: str, key: str
) -> Change:
    changed = _changed_keys(entry, updated)
    if not changed:
        return Change(ChangeLevel.COSMETIC, "no effective change", section.value, path, key)
    major = MAJOR_FIELDS.get(section, frozenset())
    hit = [name for name in changed if name in major]
    if hit:
        return Change(
            ChangeLevel.MAJOR,
            f"changes {', '.join(hit)} of an existing entry",
            section.value,
            path,
            key,
        )
    semantic = _differing_leaf_keys(entry, updated) & SEMANTIC_PROPERTY_NAMES
    if semantic:
        return Change(
            ChangeLevel.MAJOR,
            f"changes the data semantics of an existing entry: {', '.join(sorted(semantic))}",
            section.value,
            path,
            key,
        )
    if section in ALWAYS_MAJOR_SECTIONS:
        return Change(
            ChangeLevel.MAJOR,
            f"changes the {section.value} of an existing entry",
            section.value,
            path,
            key,
        )
    if _is_display_only(entry, updated):
        return Change(
            ChangeLevel.COSMETIC,
            "display-only change",
            section.value,
            path,
            key,
        )
    return Change(
        ChangeLevel.MINOR,
        f"modifies {', '.join(changed)}",
        section.value,
        path,
        key,
    )


def _entry_key(container: EntryContainer, entry: dict[str, Any]) -> str:
    value = entry.get(container.key_field)
    return value if isinstance(value, str) else "<anonymous>"


def classify_entries(
    container: EntryContainer, old_entries: list[dict[str, Any]], new_entries: list[dict[str, Any]]
) -> list[Change]:
    section = container.section
    old_by_key = {_entry_key(container, e): e for e in old_entries}
    new_by_key = {_entry_key(container, e): e for e in new_entries}
    changes: list[Change] = []

    for key in sorted(set(old_by_key) - set(new_by_key)):
        changes.append(
            Change(
                ChangeLevel.MAJOR,
                f"removes {container.key_field} {key!r}",
                section.value,
                f"{container.path}/{escape(key)}",
                key,
            )
        )
    for key in sorted(set(new_by_key) - set(old_by_key)):
        entry = new_by_key[key]
        disposition = entry.get("disposition")
        if disposition == "DEPRECATED":
            changes.append(
                Change(
                    ChangeLevel.MAJOR,
                    f"introduces a DEPRECATED entry {key!r}",
                    section.value,
                    f"{container.path}/{escape(key)}",
                    key,
                )
            )
        else:
            changes.append(
                Change(
                    ChangeLevel.MINOR,
                    f"adds {container.key_field} {key!r}",
                    section.value,
                    f"{container.path}/{escape(key)}",
                    key,
                )
            )
    for key in sorted(set(old_by_key) & set(new_by_key)):
        entry, updated = old_by_key[key], new_by_key[key]
        if entry == updated:
            continue
        path = f"{container.path}/{escape(key)}"
        changes.append(_classify_modified(section, entry, updated, path, key))
    return changes


def _document_entries(
    document: dict[str, Any], container: EntryContainer
) -> list[dict[str, Any]]:
    return entries_of(document, container)


def _classify_project(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    if old == new:
        return []
    changes: list[Change] = []
    old_mode, new_mode = old.get("deliveryMode"), new.get("deliveryMode")
    if old_mode != new_mode:
        if old_mode == DeliveryMode.A.value and new_mode == DeliveryMode.B.value:
            changes.append(
                Change(ChangeLevel.MINOR, "delivery mode upgraded A -> B", "project", "/project")
            )
        else:
            changes.append(
                Change(ChangeLevel.MAJOR, f"delivery mode changed {old_mode} -> {new_mode}", "project", "/project")
            )
    remaining = {k for k in _changed_keys(old, new) if k != "deliveryMode"}
    if not remaining:
        return changes
    major = [k for k in remaining if k in MAJOR_FIELDS[Section.PROJECT]]
    if major:
        changes.append(
            Change(
                ChangeLevel.MAJOR,
                f"changes {', '.join(sorted(major))} of the project semantics",
                "project",
                "/project",
            )
        )
    elif _is_display_only(old, new):
        changes.append(Change(ChangeLevel.COSMETIC, "display-only project change", "project", "/project"))
    else:
        changes.append(
            Change(
                ChangeLevel.MINOR,
                f"changes {', '.join(sorted(remaining))}",
                "project",
                "/project",
            )
        )
    return changes


def classify_changes(
    base: BlueprintDocument | dict[str, Any], draft: BlueprintDocument | dict[str, Any]
) -> list[Change]:
    """All classified differences between the base version and the draft."""
    old_doc = base.data if isinstance(base, BlueprintDocument) else dict(base)
    new_doc = draft.data if isinstance(draft, BlueprintDocument) else dict(draft)
    changes: list[Change] = []
    for section in Section:
        old_value = old_doc.get(section.value)
        new_value = new_doc.get(section.value)
        if old_value == new_value:
            continue
        if section is Section.PROJECT:
            changes.extend(
                _classify_project(
                    old_value if isinstance(old_value, dict) else {},
                    new_value if isinstance(new_value, dict) else {},
                )
            )
            continue
        containers = [c for c in _CONTAINERS if c.section is section]
        if not containers:
            changes.append(
                Change(ChangeLevel.MINOR, f"changes {section.value}", section.value, f"/{section.value}")
            )
            continue
        for container in containers:
            changes.extend(
                classify_entries(
                    container,
                    _document_entries(old_doc, container),
                    _document_entries(new_doc, container),
                )
            )
        # non-entry content of the same section (e.g. accessModel.sharingDefaults)
        leftover_old = {k: v for k, v in (old_value or {}).items() if not isinstance(v, list)}
        leftover_new = {k: v for k, v in (new_value or {}).items() if not isinstance(v, list)}
        if leftover_old != leftover_new and section in ALWAYS_MAJOR_SECTIONS:
            changes.append(
                Change(
                    ChangeLevel.MAJOR,
                    f"changes {section.value} configuration",
                    section.value,
                    f"/{section.value}",
                )
            )
        elif leftover_old != leftover_new and not _is_display_only(leftover_old, leftover_new):
            changes.append(
                Change(
                    ChangeLevel.MINOR,
                    f"changes {section.value} configuration",
                    section.value,
                    f"/{section.value}",
                )
            )
    return changes


def determine_change_level(changes: list[Change]) -> ChangeLevel:
    """Highest level among all changes; no change at all is Cosmetic."""
    levels = [change.level for change in changes]
    if not levels:
        return ChangeLevel.COSMETIC
    order = [ChangeLevel.COSMETIC, ChangeLevel.MINOR, ChangeLevel.MAJOR]
    for level in reversed(order):
        if level in levels:
            return level
    return ChangeLevel.COSMETIC


def determine(
    base: BlueprintDocument | dict[str, Any], draft: BlueprintDocument | dict[str, Any]
) -> tuple[ChangeLevel, list[Change]]:
    changes = classify_changes(base, draft)
    return determine_change_level(changes), changes


def iter_sections(changes: list[Change]) -> Iterator[str]:
    seen: set[str] = set()
    for change in changes:
        if change.section not in seen:
            seen.add(change.section)
            yield change.section
