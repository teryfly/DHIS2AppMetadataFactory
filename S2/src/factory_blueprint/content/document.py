"""Normalized access to a Blueprint document.

The document is a plain JSON object; this wrapper guarantees the two properties
everything else in S2 relies on:

* **normalized** — keys sorted and non-JSON types rejected, so a document that
  round-trips through PostgreSQL ``jsonb`` hashes identically (a drifting draft
  hash would make every promotion fail);
* **section-addressed** — sections and keyed entries are read through the layout
  table instead of hand-written paths.
"""

from __future__ import annotations

import json
from typing import Any, Self

from factory_foundation.api import canonical_json, structure_hash

from factory_blueprint.content.layout import EntryContainer, containers_for
from factory_blueprint.enums import ALL_SECTIONS, Section

SCHEMA_VERSION_FIELD = "blueprintSchemaVersion"


def descend(document: Any, path: str) -> Any:
    """Follow a JSON Pointer path (``/registry/entries``) without raising."""
    node: Any = document
    for token in path.split("/")[1:]:
        if not isinstance(node, dict):
            return None
        node = node.get(unescape_token(token))
    return node


def entries_of(document: Any, container: EntryContainer) -> list[dict[str, Any]]:
    """The keyed entries of one container, ignoring malformed members."""
    value = descend(document, container.path)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def entry_of(document: Any, container: EntryContainer, key: str) -> dict[str, Any] | None:
    for entry in entries_of(document, container):
        if entry.get(container.key_field) == key:
            return entry
    return None


def normalize(value: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe, key-sorted copy of a Blueprint document.

    ``canonical_json`` refuses NaN/Infinity and non-serializable objects, which is
    exactly the guarantee the draft hash needs.
    """
    loaded: dict[str, Any] = json.loads(canonical_json(value))
    return loaded


class BlueprintDocument:
    """A read-only view of a Blueprint document."""

    __slots__ = ("_data", "_hash")

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = normalize(data)
        self._hash: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(data)

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @property
    def schema_version(self) -> int:
        version = self._data.get(SCHEMA_VERSION_FIELD)
        if not isinstance(version, int):
            raise ValueError(f"{SCHEMA_VERSION_FIELD} must be an integer, got {version!r}")
        return version

    @property
    def content_hash(self) -> str:
        if self._hash is None:
            self._hash = structure_hash(self._data)
        return self._hash

    def section(self, name: Section | str) -> dict[str, Any]:
        section = Section(name)
        value = self._data.get(section.value)
        if not isinstance(value, dict):
            raise KeyError(f"section {section.value!r} is absent or not an object")
        return value

    def has_section(self, name: Section | str) -> bool:
        return isinstance(self._data.get(Section(name).value), dict)

    def missing_sections(self) -> list[str]:
        return [s.value for s in ALL_SECTIONS if not self.has_section(s)]

    def container_entries(self, container: EntryContainer) -> list[dict[str, Any]]:
        return entries_of(self._data, container)

    def entry(self, container: EntryContainer, key: str) -> dict[str, Any] | None:
        for candidate in self.container_entries(container):
            if candidate.get(container.key_field) == key:
                return candidate
        return None

    def find_entry(self, key: str, section: Section | None = None) -> dict[str, Any] | None:
        """Locate a keyed entry by its identity, optionally within one section."""
        sections = [section] if section is not None else list(ALL_SECTIONS)
        for candidate_section in sections:
            for container in containers_for(candidate_section):
                for entry in self.container_entries(container):
                    if entry.get(container.key_field) == key:
                        return entry
        return None

    def keys_of(self, container: EntryContainer) -> list[str]:
        return sorted(
            str(entry.get(container.key_field, ""))
            for entry in self.container_entries(container)
            if entry.get(container.key_field) is not None
        )

    def subset(self, sections: list[Section] | None = None) -> dict[str, Any]:
        """A projection of the document used for diffing and change level."""
        wanted = sections if sections is not None else list(ALL_SECTIONS)
        projected = {SCHEMA_VERSION_FIELD: self._data.get(SCHEMA_VERSION_FIELD)}
        for section in wanted:
            if section.value in self._data:
                projected[section.value] = self._data[section.value]
        return projected

    def replace_section(self, name: Section | str, value: dict[str, Any]) -> BlueprintDocument:
        merged = dict(self._data)
        merged[Section(name).value] = value
        return BlueprintDocument(merged)


def unescape_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")
