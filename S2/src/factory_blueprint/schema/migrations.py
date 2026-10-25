"""Schema version migration (BP-SCH-007).

A long-lived project crosses schema versions.  When a draft is opened on a version
whose ``blueprintSchemaVersion`` is behind the current one, S2 migrates each
section with a registered pure function and injects the result into the draft as a
SYSTEM-authored patch, which is then approved with that stage's Gate.

Migrators are per section and per single step; a document migrates one step at a
time (n → n+1 → …), so adding version 3 never has to know about version 1.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import FactoryError

from factory_blueprint.enums import Section
from factory_blueprint.schema import store

SectionMigrator = Callable[[dict[str, Any]], dict[str, Any]]

#: (section, from_version, to_version) -> migrator
MIGRATIONS: dict[tuple[str, int, int], SectionMigrator] = {}


def register_migration(section: Section | str, frm: int, to: int) -> Callable[[SectionMigrator], SectionMigrator]:
    """Decorator registering a single-step section migrator."""
    name = Section(section).value

    if to != frm + 1:
        raise FactoryError(
            f"migrators must step one version at a time, got {frm} -> {to}",
            code="BP_UNSUPPORTED_SCHEMA_VERSION",
        )

    def decorate(func: SectionMigrator) -> SectionMigrator:
        key = (name, frm, to)
        if key in MIGRATIONS:
            raise FactoryError(f"duplicate migrator for {key}", code="INTERNAL_ERROR")
        MIGRATIONS[key] = func
        return func

    return decorate


def has_migration_path(frm: int, to: int) -> bool:
    if frm == to:
        return True
    if frm > to:
        return False
    for step in range(frm, to):
        has_migrator = any((s.value, step, step + 1) in MIGRATIONS for s in Section)
        if not has_migrator and not _step_is_noop(step, step + 1):
            # a version step with no migrators is only safe when the section
            # schemas are identical; anything else needs an explicit function
            return False
    return True


def _step_is_noop(frm: int, to: int) -> bool:
    """True when two schema versions declare the identical section set."""
    try:
        old_ids = {s.value for s in Section if store.section_schema(s, frm)}
        new_ids = {s.value for s in Section if store.section_schema(s, to)}
    except ValueError:
        return False
    return old_ids == new_ids


@dataclass(frozen=True)
class MigratedDocument:
    document: dict[str, Any]
    #: sections whose content this migration changed, in section order
    changed_sections: list[str] = field(default_factory=list)
    source_version: int = 0
    target_version: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.changed_sections) or self.source_version != self.target_version


def migrate_document(document: dict[str, Any], frm: int, to: int) -> MigratedDocument:
    """Migrate a whole Blueprint document from one schema version to another."""
    if frm == to:
        return MigratedDocument(dict(document), [], frm, to)
    if frm > to:
        raise FactoryError(
            f"cannot downgrade blueprintSchemaVersion {frm} -> {to}",
            code="BP_UNSUPPORTED_SCHEMA_VERSION",
        )
    current = dict(document)
    changed: list[str] = []
    for step in range(frm, to):
        for section in Section:
            migrator = MIGRATIONS.get((section.value, step, step + 1))
            if migrator is None:
                continue
            value = current.get(section.value)
            if not isinstance(value, dict):
                continue
            migrated = migrator(dict(value))
            if migrated != value:
                current[section.value] = migrated
                if section.value not in changed:
                    changed.append(section.value)
        current[store.SCHEMA_VERSION_FIELD] = step + 1
    return MigratedDocument(current, changed, frm, to)
