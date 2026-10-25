"""Blueprint document layout: where each section's entries live in the document.

This table is the single definition of "which section does a JSON Pointer touch"
(BP-AC-02 stage-scope check), of how indexes are rebuilt from a document
(BP-VER-002), and of how entries are compared for change level and Diff.  A
section that owns no keyed entries (``project``, ``generationPolicy``) is a plain
object edited as a whole.
"""

from __future__ import annotations

from dataclasses import dataclass

from factory_blueprint.enums import Section


@dataclass(frozen=True)
class EntryContainer:
    """A keyed list inside a section, e.g. ``registry.entries`` keyed by businessKey."""

    #: path of the list relative to the document root, e.g. ``/registry/entries``
    path: str
    #: field inside an entry holding its identity
    key_field: str
    #: human-readable kind used in the business_key_index
    key_kind: str = "businessKey"
    #: extra fields the index answers by, one row each (``bp.business_key_index`` is keyed by
    #: (project, version, section, key_kind, entry_key)).  Declared here rather than known to the
    #: indexer alone: a secondary key nothing validates was insertable at promote time as a
    #: primary-key violation — a 500 on the Gate approval, far from the entry that caused it.
    secondary_keys: tuple[str, ...] = ()

    @property
    def section(self) -> Section:
        return Section(self.path.split("/")[1])

    @property
    def indexed_fields(self) -> tuple[str, ...]:
        """Every field of an entry this container puts a unique index row on."""
        return (self.key_field, *self.secondary_keys)

    def pointer(self, key: str) -> str:
        return f"{self.path}/{escape(key)}"


def escape(token: str) -> str:
    """RFC 6901 escaping of a single reference token."""
    return token.replace("~", "~0").replace("/", "~1")


def unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


#: The layout, one entry per section that carries keyed entries.
ENTRY_CONTAINERS: tuple[EntryContainer, ...] = (
    EntryContainer("/requirements/items", "businessKey"),
    EntryContainer("/domain/entities", "businessKey"),
    EntryContainer("/domain/relationships", "businessKey"),
    EntryContainer("/useCases/items", "businessKey"),
    EntryContainer("/actors/items", "businessKey"),
    EntryContainer("/organizationModel/levels", "businessKey"),
    EntryContainer("/organizationModel/orgUnitRefs", "businessKey"),
    EntryContainer("/accessModel/roles", "businessKey"),
    EntryContainer("/accessModel/userGroups", "businessKey"),
    EntryContainer("/architecture/decisions", "decisionId", key_kind="decisionId"),
    EntryContainer("/mapping/entries", "businessKey"),
    EntryContainer("/registry/entries", "businessKey"),
    EntryContainer("/pages/items", "pageKey", key_kind="pageKey"),
    EntryContainer("/apiIntents/items", "intentKey", key_kind="intentKey", secondary_keys=("operationKey",)),
    EntryContainer("/uiIntents/items", "intentKey", key_kind="intentKey"),
    EntryContainer("/rules/validationRules", "businessKey"),
    EntryContainer("/rules/programRules", "businessKey"),
    EntryContainer("/rules/programRuleVariables", "businessKey"),
    EntryContainer("/rules/programIndicators", "businessKey"),
    EntryContainer("/rules/others", "businessKey"),
    EntryContainer("/fixtures/specifications", "businessKey"),
    EntryContainer("/fixtures/demoUsers", "businessKey"),
    EntryContainer("/environmentConstraints/items", "businessKey"),
)

#: Sections whose whole value is an object with no keyed entries.
WHOLE_OBJECT_SECTIONS: frozenset[Section] = frozenset(
    {Section.PROJECT, Section.GENERATION_POLICY}
)

CONTAINERS_BY_SECTION: dict[Section, tuple[EntryContainer, ...]] = {
    section: tuple(c for c in ENTRY_CONTAINERS if c.section is section)
    for section in Section
}

#: container lookup by document path, e.g. ``/registry/entries``
CONTAINER_BY_PATH: dict[str, EntryContainer] = {c.path: c for c in ENTRY_CONTAINERS}

#: Sections whose entries reference Registry objects and therefore must never
#: carry a raw UID (BP-DRF-005.4, P-04): UIDs are written by S2 only.
UID_SENSITIVE_SECTIONS: frozenset[Section] = frozenset(
    {
        Section.REGISTRY,
        Section.MAPPING,
        Section.PAGES,
        Section.API_INTENTS,
        Section.UI_INTENTS,
        Section.RULES,
        Section.FIXTURES,
    }
)


def containers_for(section: Section) -> tuple[EntryContainer, ...]:
    return CONTAINERS_BY_SECTION.get(section, ())
