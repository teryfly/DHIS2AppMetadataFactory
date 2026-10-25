"""Impact analysis (BP-IMP-003/004/005).

The impact set tells S1 which stages to invalidate and which artifacts to rebuild;
it never includes deployed-environment facts, which S1 adds itself (BP-IMP-005).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import ENTRY_CONTAINERS
from factory_blueprint.enums import ChangeLevel, DeliveryMode, DesignStage, Section
from factory_blueprint.impact.changelvl import Change
from factory_blueprint.impact.stage_map import affected_stage

#: artifact types a section change forces to be regenerated
SECTION_ARTIFACTS: dict[Section, tuple[str, ...]] = {
    Section.REQUIREMENTS: ("VALIDATION_REPORT",),
    Section.DOMAIN: ("VALIDATION_REPORT", "METADATA_PACKAGE"),
    Section.USE_CASES: ("VALIDATION_REPORT", "UI_SPECIFICATION"),
    Section.ACTORS: ("VALIDATION_REPORT", "PERMISSIONS"),
    Section.ORGANIZATION_MODEL: ("METADATA_PACKAGE", "VALIDATION_REPORT"),
    Section.ACCESS_MODEL: ("METADATA_PACKAGE", "VALIDATION_REPORT"),
    Section.ARCHITECTURE: ("METADATA_PACKAGE", "VALIDATION_REPORT"),
    Section.MAPPING: ("METADATA_PACKAGE", "MANIFEST", "DIFF_REPORT", "VALIDATION_REPORT"),
    Section.REGISTRY: (
        "METADATA_PACKAGE",
        "MANIFEST",
        "DIFF_REPORT",
        "OPENAPI",
        "UI_SPECIFICATION",
        "FRONTEND_PACKAGE",
        "VALIDATION_REPORT",
    ),
    Section.PAGES: ("OPENAPI", "UI_SPECIFICATION", "FRONTEND_PACKAGE", "VALIDATION_REPORT"),
    Section.API_INTENTS: ("OPENAPI", "FRONTEND_PACKAGE", "VALIDATION_REPORT"),
    Section.UI_INTENTS: ("UI_SPECIFICATION", "FRONTEND_PACKAGE", "VALIDATION_REPORT"),
    Section.RULES: ("METADATA_PACKAGE", "UI_SPECIFICATION", "VALIDATION_REPORT"),
    Section.FIXTURES: ("FIXTURE_PACKAGE", "FRONTEND_PACKAGE", "VALIDATION_REPORT"),
    Section.ENVIRONMENT_CONSTRAINTS: ("METADATA_PACKAGE", "VALIDATION_REPORT"),
    Section.GENERATION_POLICY: (
        "METADATA_PACKAGE",
        "MANIFEST",
        "VALIDATION_REPORT",
    ),
    Section.PROJECT: (
        "METADATA_PACKAGE",
        "OPENAPI",
        "UI_SPECIFICATION",
        "FRONTEND_PACKAGE",
        "VALIDATION_REPORT",
    ),
}

_STAGES_WITHOUT_PAGES = (DesignStage.PAGE_DESIGN,)


def _stage_order_key(stage: DesignStage) -> int:
    return list(DesignStage).index(stage)


def affected_stages(
    sections: Iterable[Section],
    change_level: ChangeLevel,
    delivery_mode: DeliveryMode | str = DeliveryMode.B,
) -> list[DesignStage]:
    """The stage of each touched section plus every later stage (BP-IMP-003)."""
    earliest: set[DesignStage] = set()
    for section in sections:
        stage = affected_stage(section, delivery_mode)
        if stage is not None:
            earliest.add(stage)
    if change_level is ChangeLevel.MAJOR:
        earliest.add(DesignStage.MAPPING)
    if not earliest:
        return []
    affected: set[DesignStage] = set()
    for stage in earliest:
        rank = _stage_order_key(stage)
        affected.update(s for s in DesignStage if _stage_order_key(s) >= rank)
    return sorted(affected, key=_stage_order_key)


def artifact_types(
    sections: Iterable[Section], delivery_mode: DeliveryMode
) -> list[str]:
    wanted: set[str] = set()
    mode = DeliveryMode(delivery_mode)
    for section in sections:
        for artifact in SECTION_ARTIFACTS[section]:
            if mode is DeliveryMode.A and artifact in {
                "OPENAPI",
                "UI_SPECIFICATION",
                "FRONTEND_PACKAGE",
                "PERMISSIONS",
            }:
                continue
            wanted.add(artifact)
    return sorted(wanted)


def pages_using_keys(
    document: BlueprintDocument, business_keys: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Pages (and their operationKeys) that reference any of these entries."""
    wanted = set(business_keys)
    page_container = next(c for c in ENTRY_CONTAINERS if c.path == "/pages/items")
    intent_container = next(c for c in ENTRY_CONTAINERS if c.path == "/apiIntents/items")
    ui_container = next(c for c in ENTRY_CONTAINERS if c.path == "/uiIntents/items")

    page_keys: set[str] = set()
    for page in document.container_entries(page_container):
        refs = set(_strings(page.get("registryRefs")))
        refs |= set(_strings(page.get("apiIntentRefs")))
        refs |= set(_strings(page.get("useCases")))
        if refs & wanted or page.get("pageKey") in wanted:
            key = page.get("pageKey")
            if isinstance(key, str):
                page_keys.add(key)

    operation_keys: set[str] = set()
    for intent in document.container_entries(intent_container):
        refs = set(_strings(intent.get("registryRefs")))
        page_key = intent.get("pageKey")
        if refs & wanted or (isinstance(page_key, str) and page_key in wanted):
            operation_keys.update(_strings(intent.get("operationKey")))
    for intent in document.container_entries(ui_container):
        for component in intent.get("components", []) if isinstance(intent.get("components"), list) else []:
            source = component.get("dataSource") if isinstance(component, dict) else None
            if isinstance(source, dict) and source.get("businessKey") in wanted:
                page_key = intent.get("pageKey")
                if isinstance(page_key, str):
                    page_keys.add(page_key)
    return sorted(page_keys), sorted(operation_keys)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def compute_impact(
    document: BlueprintDocument,
    changes: list[Change],
    change_level: ChangeLevel,
    delivery_mode: DeliveryMode,
    downstream_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """Build the impact set of a promoted version (returned as plain JSON data)."""
    sections = {Section(name) for name in {change.section for change in changes}}
    touched_keys = {change.business_key for change in changes if change.business_key}
    expanded = set(touched_keys) | set(downstream_keys)
    page_keys, operation_keys = pages_using_keys(document, expanded)
    stages = affected_stages(sections, change_level, delivery_mode)
    return {
        "sections": sorted(section.value for section in sections),
        "stages": [stage.value for stage in stages],
        "businessKeys": sorted(expanded),
        "pageKeys": page_keys,
        "operationKeys": operation_keys,
        "artifactTypes": artifact_types(sections, delivery_mode),
        "changeLevel": change_level.value,
    }


def impact_from_stages(stages: list[DesignStage]) -> list[DesignStage]:
    """Stages to invalidate: used by S1 when it needs the ordered list alone."""
    if not stages:
        return []
    rank = min(_stage_order_key(stage) for stage in stages)
    return sorted(
        (stage for stage in DesignStage if _stage_order_key(stage) >= rank),
        key=_stage_order_key,
    )


#: stages whose products are page-level and therefore skipped in mode A
PAGE_STAGES: tuple[DesignStage, ...] = _STAGES_WITHOUT_PAGES
