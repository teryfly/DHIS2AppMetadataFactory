"""The section catalog (BP-SCH-008).

A manual-authoring form (overall SRS FR-UI-011) and S3's constrained Agent output both
start by asking what sections exist and how their entries are keyed, then fetch one
section schema at a time.  This is that first answer, read from the same layout table a
patch is checked against, so the catalog cannot drift from the validation.

A section whose whole value is an object — ``project``, ``generationPolicy`` — has no
keyed entries and is reported with no containers, which is what tells the editor to offer
it as one document instead of a row grid.
"""

from __future__ import annotations

from factory_blueprint.content.layout import CONTAINERS_BY_SECTION
from factory_blueprint.dto.blueprint import SectionCatalog, SectionInfo
from factory_blueprint.enums import Section
from factory_blueprint.schema import store


def list_sections(schema_version: int | None = None) -> SectionCatalog:
    """Current and available Schema versions, plus every section's entry containers."""
    version = store.resolve_version(schema_version)
    return SectionCatalog(
        schema_version=version,
        current_schema_version=store.current_schema_version(),
        available_schema_versions=list(store.available_versions()),
        sections=[_section_info(section) for section in Section],
    )


def _section_info(section: Section) -> SectionInfo:
    containers = CONTAINERS_BY_SECTION[section]
    if not containers:
        return SectionInfo(name=section.value, key_fields=[None])
    return SectionInfo(
        name=section.value,
        entry_containers=[container.path for container in containers],
        key_fields=[container.key_field for container in containers],
    )
