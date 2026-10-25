"""Section ↔ design stage mapping (S2 SRS appendix A).

The draft's stage scope decides which sections a patch may touch (BP-DRF-005.1),
and the section a problem is in decides its source stage (BP-VAL-004).
"""

from __future__ import annotations

from factory_blueprint.enums import DeliveryMode, DesignStage, Section

#: sections modifiable in each draft stage
STAGE_SCOPE: dict[DesignStage, frozenset[Section]] = {
    DesignStage.REQUIREMENTS: frozenset({Section.REQUIREMENTS}),
    DesignStage.DOMAIN: frozenset({Section.DOMAIN, Section.USE_CASES, Section.ACTORS}),
    DesignStage.MAPPING: frozenset(
        {
            Section.ORGANIZATION_MODEL,
            Section.ACCESS_MODEL,
            Section.ARCHITECTURE,
            Section.MAPPING,
            Section.REGISTRY,
            Section.RULES,
            Section.ENVIRONMENT_CONSTRAINTS,
            Section.GENERATION_POLICY,
        }
    ),
    DesignStage.PAGE_DESIGN: frozenset(
        {Section.PAGES, Section.API_INTENTS, Section.UI_INTENTS}
    ),
}

#: fixtures are designed with the mapping in mode A and with the pages in mode B
FIXTURES_STAGE: dict[DeliveryMode, DesignStage] = {
    DeliveryMode.A: DesignStage.MAPPING,
    DeliveryMode.B: DesignStage.PAGE_DESIGN,
}

#: sections no draft may edit: only initialization and the delivery-mode upgrade
#: write them, both as SYSTEM-authored changes
SYSTEM_ONLY: frozenset[Section] = frozenset({Section.PROJECT})

#: the stage a validation issue in this section came from (appendix A, right column)
SOURCE_STAGE: dict[Section, DesignStage] = {
    Section.PROJECT: DesignStage.REQUIREMENTS,
    Section.REQUIREMENTS: DesignStage.REQUIREMENTS,
    Section.DOMAIN: DesignStage.DOMAIN,
    Section.USE_CASES: DesignStage.DOMAIN,
    Section.ACTORS: DesignStage.DOMAIN,
    Section.ORGANIZATION_MODEL: DesignStage.MAPPING,
    Section.ACCESS_MODEL: DesignStage.MAPPING,
    Section.ARCHITECTURE: DesignStage.MAPPING,
    Section.MAPPING: DesignStage.MAPPING,
    Section.REGISTRY: DesignStage.MAPPING,
    Section.RULES: DesignStage.MAPPING,
    Section.ENVIRONMENT_CONSTRAINTS: DesignStage.MAPPING,
    Section.GENERATION_POLICY: DesignStage.MAPPING,
    Section.PAGES: DesignStage.PAGE_DESIGN,
    Section.API_INTENTS: DesignStage.PAGE_DESIGN,
    Section.UI_INTENTS: DesignStage.PAGE_DESIGN,
}


def allowed_sections(
    stage: DesignStage | str,
    delivery_mode: DeliveryMode | str = DeliveryMode.B,
    *,
    change_request_stages: list[DesignStage] | None = None,
) -> list[Section]:
    """Sections a draft at this stage may modify.

    When the draft executes an approved change request, its scope is the sections
    of the CR's earliest affected stage *and every later stage* (appendix A, last
    paragraph).
    """
    design_stage = DesignStage(stage)
    mode = DeliveryMode(delivery_mode)
    if change_request_stages:
        stages = set(change_request_stages)
        for rank, stage_item in enumerate(_ordered_stages()):
            if stage_item in stages:
                stages.update(_ordered_stages()[rank:])
    else:
        stages = {design_stage} if design_stage in STAGE_SCOPE else set()

    allowed: set[Section] = set()
    for stage_item in stages:
        allowed |= STAGE_SCOPE.get(stage_item, frozenset())
    fixtures_stage = FIXTURES_STAGE[mode]
    if fixtures_stage in stages:
        allowed.add(Section.FIXTURES)
    return sorted(allowed, key=lambda s: list(Section).index(s))


def affected_stage(
    section: Section | str, delivery_mode: DeliveryMode | str = DeliveryMode.B
) -> DesignStage | None:
    """The stage whose design work this section belongs to (appendix A).

    `fixtures` is the one section whose stage depends on the delivery mode — designed
    with the mapping in mode A and with the pages in mode B — so it is resolved rather
    than stored.
    """
    try:
        named = Section(section)
    except ValueError:
        return None
    if named is Section.FIXTURES:
        return FIXTURES_STAGE[DeliveryMode(delivery_mode)]
    return SOURCE_STAGE.get(named)


def issue_source_stage(
    section: Section | str,
    fallback: DesignStage | None = None,
    delivery_mode: DeliveryMode | str = DeliveryMode.B,
) -> DesignStage:
    """BP-VAL-004: derive the source stage from the section, else the earliest one."""
    resolved = affected_stage(section, delivery_mode)
    if resolved is not None:
        return resolved
    if fallback is not None:
        return fallback
    return DesignStage.REQUIREMENTS


def is_writable(section: Section | str, allowed: list[Section] | frozenset[Section]) -> bool:
    return Section(section) in set(allowed)


def _ordered_stages() -> list[DesignStage]:
    return [
        DesignStage.REQUIREMENTS,
        DesignStage.DOMAIN,
        DesignStage.DISCOVERY,
        DesignStage.MAPPING,
        DesignStage.PAGE_DESIGN,
        DesignStage.BASELINE_COMPILE,
    ]
