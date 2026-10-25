"""Cross-product consistency validation (BP-VAL-030/031, overall SRS 13.4).

The dimensions S2 owns: mapping ↔ Registry, Registry ↔ payload, pages ↔ API
Intent, API Intent ↔ UI Intent, API Intent ↔ Registry, pages ↔ the permission
model, and fixtures ↔ metadata.  "Capability ↔ execution plan" belongs to S4 and
is deliberately not checked here.
"""

from __future__ import annotations

from typing import Any

from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.metadata import CompileOutput
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import Disposition, IssueLevel, RegistryEntryStatus, Section

RULE_MAPPING_REGISTRY = "BPV-XA-001"
RULE_PAGE_INTENTS = "BPV-XA-002"
RULE_UI_DATA_SOURCE = "BPV-XA-003"
RULE_INTENT_REGISTRY = "BPV-XA-004"
RULE_PAGE_PERMISSIONS = "BPV-XA-005"
RULE_FIXTURE_METADATA = "BPV-XA-006"
RULE_PAYLOAD_SCOPE = "BPV-XA-007"
RULE_REQUIREMENT_COVERAGE = "BPV-XA-008"
RULE_PAGE_USE_CASE = "BPV-XA-009"

_CONTAINERS = {
    "registry": "/registry/entries",
    "mapping": "/mapping/entries",
    "requirements": "/requirements/items",
    "useCases": "/useCases/items",
    "pages": "/pages/items",
    "apiIntents": "/apiIntents/items",
    "uiIntents": "/uiIntents/items",
    "roles": "/accessModel/roles",
    "userGroups": "/accessModel/userGroups",
    "actors": "/actors/items",
    "fixtureSpecs": "/fixtures/specifications",
}


def _issue(rule_id: str, message: str, *, business_key: str, section: Section) -> Issue:
    return make_issue(
        rule_id, IssueLevel.BLOCKING, message, business_key=business_key, section=section
    )


def _requirements_covered_by_use_cases(
    sections: dict[str, list[dict[str, Any]]],
) -> list[Issue]:
    """BP-VAL-030, 13.4 需求 ↔ 领域: every confirmed requirement is taken up.

    A requirement only reaches a deliverable through a Use Case — that is the chain the
    traceability map is built from — so a confirmed one no Use Case names is work the
    project agreed to and nothing will produce.  A CANDIDATE is not: it has not been
    accepted yet, and requiring coverage of it would block the stage that is meant to
    decide it.
    """
    covered = {
        str(reference)
        for use_case in sections["useCases"]
        for reference in use_case.get("requirements") or []
    }
    issues: list[Issue] = []
    for requirement in sorted(
        sections["requirements"], key=lambda item: str(item.get("businessKey"))
    ):
        key = str(requirement.get("businessKey") or "")
        if str(requirement.get("status")) != "CONFIRMED":
            continue
        if key not in covered:
            issues.append(
                _issue(
                    RULE_REQUIREMENT_COVERAGE,
                    f"confirmed requirement {key!r} is covered by no Use Case, so no page, "
                    "API or metadata object will deliver it (13.4 需求 ↔ 领域)",
                    business_key=key,
                    section=Section.REQUIREMENTS,
                )
            )
    return issues


def validate(
    document: dict[str, Any], output: CompileOutput, inputs: CompileInputs
) -> list[Issue]:
    """Every consistency dimension S2 is responsible for, in a stable order."""
    view = BlueprintDocument(document)
    sections = {
        name: view.container_entries(CONTAINER_BY_PATH[path])
        for name, path in _CONTAINERS.items()
    }
    registry = {str(entry.get("businessKey")): entry for entry in sections["registry"]}
    mapping = {str(entry.get("businessKey")): entry for entry in sections["mapping"]}
    operations = {
        (str(intent.get("pageKey")), str(intent.get("operationKey")))
        for intent in sections["apiIntents"]
    }
    intent_keys = {str(intent.get("intentKey")) for intent in sections["apiIntents"]}
    issues: list[Issue] = []

    issues.extend(_requirements_covered_by_use_cases(sections))
    for key, entry in sorted(mapping.items()):
        if entry.get("notMapped") is True:
            continue
        if key not in registry:
            issues.append(
                _issue(
                    RULE_MAPPING_REGISTRY,
                    f"mapping of {key!r} lands on no Registry entry",
                    business_key=key,
                    section=Section.REGISTRY,
                )
            )
    for page in sorted(sections["pages"], key=lambda item: str(item.get("pageKey"))):
        page_key = str(page.get("pageKey") or "")
        use_cases = [str(item) for item in page.get("useCases") or []]
        if not use_cases:
            issues.append(
                _issue(
                    RULE_PAGE_USE_CASE,
                    f"page {page_key!r} is associated with no Use Case, so nothing states "
                    "why it exists and no actor's work it serves can be checked against "
                    "the permission model (FR-PG-001)",
                    business_key=page_key,
                    section=Section.PAGES,
                )
            )
        for reference in page.get("apiIntentRefs") or []:
            if str(reference) not in intent_keys:
                issues.append(
                    _issue(
                        RULE_PAGE_INTENTS,
                        f"page {page_key!r} references the API intent {reference!r}, "
                        "which does not exist",
                        business_key=page_key,
                        section=Section.API_INTENTS,
                    )
                )
        if not page.get("apiIntentRefs"):
            issues.append(
                _issue(
                    RULE_PAGE_INTENTS,
                    f"page {page_key!r} declares no API intent, so its data need is unspecified",
                    business_key=page_key,
                    section=Section.PAGES,
                )
            )
        for reference in page.get("registryRefs") or []:
            if str(reference) not in registry:
                issues.append(
                    _issue(
                        RULE_INTENT_REGISTRY,
                        f"page {page_key!r} references unknown Registry entry {reference!r}",
                        business_key=page_key,
                        section=Section.PAGES,
                    )
                )

    for intent in sections["apiIntents"]:
        key = str(intent.get("intentKey") or intent.get("operationKey") or "")
        for reference in intent.get("registryRefs") or []:
            referenced = registry.get(str(reference))
            if referenced is None:
                issues.append(
                    _issue(
                        RULE_INTENT_REGISTRY,
                        f"API intent {key!r} references unknown Registry entry {reference!r}",
                        business_key=key,
                        section=Section.API_INTENTS,
                    )
                )
            elif str(referenced.get("status")) == RegistryEntryStatus.PROPOSED.value:
                issues.append(
                    _issue(
                        RULE_INTENT_REGISTRY,
                        f"API intent {key!r} references {reference!r} while it is still "
                        "PROPOSED (13.4 example)",
                        business_key=key,
                        section=Section.API_INTENTS,
                    )
                )

    for ui_intent in sections["uiIntents"]:
        page_key = str(ui_intent.get("pageKey") or "")
        for component in ui_intent.get("components") or []:
            if not isinstance(component, dict):
                continue
            source = component.get("dataSource")
            if not isinstance(source, dict):
                continue
            name = str(component.get("name") or "?")
            if source.get("operationKey"):
                pair = (page_key, str(source["operationKey"]))
                if pair not in operations:
                    issues.append(
                        _issue(
                            RULE_UI_DATA_SOURCE,
                            f"component {name!r} on page {page_key!r} uses operationKey "
                            f"{source['operationKey']!r}, which no API Intent of that page "
                            "declares (FR-UII-002)",
                            business_key=page_key,
                            section=Section.UI_INTENTS,
                        )
                    )
            elif source.get("businessKey"):
                if str(source["businessKey"]) not in registry:
                    issues.append(
                        _issue(
                            RULE_UI_DATA_SOURCE,
                            f"component {name!r} on page {page_key!r} reads unknown object "
                            f"{source['businessKey']!r}",
                            business_key=page_key,
                            section=Section.UI_INTENTS,
                        )
                    )

    role_keys = {str(item.get("businessKey")) for item in sections["roles"]} | {
        str(item.get("businessKey")) for item in sections["userGroups"]
    }
    actors = {str(item.get("businessKey")): item for item in sections["actors"]}
    for page in sections["pages"]:
        page_key = str(page.get("pageKey") or "")
        for actor in page.get("actors") or []:
            actor_key = str(actor)
            if actor_key not in actors:
                issues.append(
                    _issue(
                        RULE_PAGE_PERMISSIONS,
                        f"page {page_key!r} names the unknown actor {actor_key!r}",
                        business_key=page_key,
                        section=Section.PAGES,
                    )
                )
                continue
            mapped = {str(item) for item in actors[actor_key].get("roleKeys") or []}
            if not (mapped & role_keys) and actor_key not in role_keys:
                issues.append(
                    _issue(
                        RULE_PAGE_PERMISSIONS,
                        f"actor {actor_key!r} on page {page_key!r} maps to no role or user "
                        "group (FR-PG-001)",
                        business_key=page_key,
                        section=Section.ACCESS_MODEL,
                    )
                )

    compiled = set(output.objects)
    for spec in sections["fixtureSpecs"]:
        key = str(spec.get("businessKey") or "")
        target = str(spec.get("targetKey") or "")
        if target not in registry:
            issues.append(
                _issue(
                    RULE_FIXTURE_METADATA,
                    f"fixture specification {key!r} targets unknown object {target!r}",
                    business_key=key,
                    section=Section.FIXTURES,
                )
            )
        elif str(registry[target].get("disposition")) in (
            Disposition.CREATE.value,
            Disposition.UPDATE.value,
        ) and target not in compiled:
            issues.append(
                _issue(
                    RULE_FIXTURE_METADATA,
                    f"fixture specification {key!r} targets {target!r}, which produced no "
                    "payload object",
                    business_key=key,
                    section=Section.FIXTURES,
                )
            )
        for attribute in spec.get("attributeValues") or []:
            attribute_key = str(attribute.get("attributeKey") or "") if isinstance(attribute, dict) else ""
            if attribute_key and attribute_key not in registry:
                issues.append(
                    _issue(
                        RULE_FIXTURE_METADATA,
                        f"fixture specification {key!r} sets unknown attribute {attribute_key!r}",
                        business_key=key,
                        section=Section.FIXTURES,
                    )
                )

    for key in sorted(compiled):
        entry = registry.get(key) or {}
        if str(entry.get("disposition")) == Disposition.REFERENCE.value:
            issues.append(
                _issue(
                    RULE_PAYLOAD_SCOPE,
                    f"payload contains REFERENCE entry {key!r}, which must never be imported",
                    business_key=key,
                    section=Section.REGISTRY,
                )
            )
    return sort_issues(issues)
