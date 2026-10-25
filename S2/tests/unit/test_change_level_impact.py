"""P1: change level determination (appendix B) and impact analysis (BP-IMP)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from factory_blueprint.enums import ChangeLevel, DeliveryMode, DesignStage, Section
from factory_blueprint.impact import changelvl, impact
from factory_blueprint.impact.stage_map import allowed_sections, issue_source_stage
from tests.unit import helpers


def with_value(document: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """Deep-copy a document and set one value addressed by a path of tokens."""
    result = copy.deepcopy(document)
    node: Any = result
    for token in path[:-1]:
        node = node[int(token)] if isinstance(node, list) else node[token]
    last = path[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return result


def level_for(base: dict[str, Any], draft: dict[str, Any]) -> ChangeLevel:
    return changelvl.determine(base, draft)[0]


def test_no_change_is_cosmetic() -> None:
    document = helpers.seeded_document()
    assert level_for(document, copy.deepcopy(document)) is ChangeLevel.COSMETIC


def test_display_only_change_is_cosmetic() -> None:
    """BP-AC-04: description, display name, short name and translations only."""
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["registry"]["entries"][0]["notes"] = "renamed for the client"
    draft["mapping"]["entries"][0]["fields"]["description"] = {"default": "antenatal program"}
    draft["mapping"]["entries"][0]["fields"]["shortName"] = {"default": "ANC"}
    assert level_for(base, draft) is ChangeLevel.COSMETIC


def test_disposition_change_is_major() -> None:
    """BP-AC-05."""
    base = helpers.seeded_document()
    draft = with_value(base, ("registry", "entries", "0", "disposition"), "REUSE")
    assert level_for(base, draft) is ChangeLevel.MAJOR


def test_dhis2_type_change_is_major() -> None:
    base = helpers.seeded_document()
    draft = with_value(base, ("registry", "entries", "0", "dhis2Type"), "programStage")
    assert level_for(base, draft) is ChangeLevel.MAJOR


def test_controlled_fields_change_is_major() -> None:
    base = helpers.seeded_document()
    entry = dict(base["registry"]["entries"][0])
    entry["disposition"] = "UPDATE"
    entry["controlledFields"] = ["name"]
    draft = with_value(base, ("registry", "entries", "0"), entry)
    assert level_for(base, draft) is ChangeLevel.MAJOR


def test_removing_an_entry_is_major() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["registry"]["entries"] = []
    assert level_for(base, draft) is ChangeLevel.MAJOR


def test_adding_a_registry_entry_is_minor() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["registry"]["entries"].append(
        helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_W")
    )
    assert level_for(base, draft) is ChangeLevel.MINOR


def test_adding_a_deprecated_entry_is_major() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["registry"]["entries"].append(
        helpers.registry_entry("old", "dataElement", "DEPRECATED", code="MC_OLD")
    )
    assert level_for(base, draft) is ChangeLevel.MAJOR


def test_access_model_change_is_major() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["accessModel"]["sharingDefaults"]["publicAccess"] = "view"
    assert level_for(base, draft) is ChangeLevel.MAJOR


def test_api_intent_semantics_change_is_major() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["apiIntents"]["items"] = [
        {
            "intentKey": "api-patient",
            "pageKey": "patient",
            "operationKey": "loadPatient",
            "purpose": "load",
            "resource": "tracker.trackedEntities.get",
            "method": "GET",
            "source": {"note": "n"},
        }
    ]
    assert level_for(base, draft) is ChangeLevel.MINOR  # added
    changed = copy.deepcopy(draft)
    changed["apiIntents"]["items"][0]["method"] = "POST"
    assert level_for(draft, changed) is ChangeLevel.MAJOR


def test_expression_change_in_rules_is_major() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    rule = {
        "businessKey": "rule.a",
        "name": {"default": "A"},
        "expression": "#{weight} > 0",
        "source": {"note": "n"},
    }
    draft["rules"]["programRules"].append(rule)
    assert level_for(base, draft) is ChangeLevel.MINOR
    changed = copy.deepcopy(draft)
    changed["rules"]["programRules"][0]["expression"] = "#{weight} > 5"
    assert level_for(draft, changed) is ChangeLevel.MAJOR


def test_delivery_mode_upgrade_is_minor_and_downgrade_is_major() -> None:
    mode_a = helpers.seeded_document()
    mode_a["project"]["deliveryMode"] = "A"
    mode_b = copy.deepcopy(mode_a)
    mode_b["project"]["deliveryMode"] = "B"
    assert level_for(mode_a, mode_b) is ChangeLevel.MINOR
    assert level_for(mode_b, mode_a) is ChangeLevel.MAJOR


def test_target_version_change_is_major() -> None:
    base = helpers.seeded_document()
    draft = with_value(
        base, ("project", "targetDhis2"), {"versionConstraint": ">=2.42"}
    )
    assert level_for(base, draft) is ChangeLevel.MAJOR


def test_highest_level_wins() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["registry"]["entries"].append(
        helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_W")
    )
    draft["registry"]["entries"][0]["disposition"] = "UPDATE"
    level, changes = changelvl.determine(base, draft)
    assert level is ChangeLevel.MAJOR
    assert {change.level for change in changes} == {ChangeLevel.MINOR, ChangeLevel.MAJOR}


def test_unknown_section_change_defaults_to_minor_not_cosmetic() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["environmentConstraints"]["items"].append(
        {"businessKey": "ec.1", "constraint": "no offline", "source": {"note": "n"}}
    )
    assert level_for(base, draft) is ChangeLevel.MINOR


def test_affected_stages_include_every_later_stage() -> None:
    """BP-IMP-003."""
    stages = impact.affected_stages([Section.REGISTRY], ChangeLevel.MINOR)
    assert DesignStage.MAPPING in stages
    assert DesignStage.HANDOFF in stages
    assert DesignStage.REQUIREMENTS not in stages


def test_major_change_reaches_at_least_mapping() -> None:
    stages = impact.affected_stages([Section.REQUIREMENTS], ChangeLevel.MAJOR)
    assert DesignStage.MAPPING in stages


def test_artifact_types_depend_on_delivery_mode() -> None:
    """BP-IMP-004: which products must be rebuilt."""
    mode_b = impact.artifact_types([Section.REGISTRY], DeliveryMode.B)
    assert "OPENAPI" in mode_b and "FRONTEND_PACKAGE" in mode_b
    mode_a = impact.artifact_types([Section.REGISTRY], DeliveryMode.A)
    assert "OPENAPI" not in mode_a


def test_pages_using_keys_propagates_to_operation_keys() -> None:
    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["pages"]["items"].append(
        {
            "pageKey": "patient-detail",
            "name": "Detail",
            "purpose": "view",
            "routes": ["/patients/:te"],
            "useCases": ["uc.pregnancy"],
            "actors": ["midwife"],
            "registryRefs": ["pregnancy"],
            "source": {"note": "n"},
        }
    )
    draft["apiIntents"]["items"].append(
        {
            "intentKey": "api-patient-detail",
            "pageKey": "patient-detail",
            "operationKey": "loadPatient",
            "purpose": "load",
            "resource": "tracker.trackedEntities.get",
            "method": "GET",
            "registryRefs": ["pregnancy"],
            "source": {"note": "n"},
        }
    )
    from factory_blueprint.content.document import BlueprintDocument

    pages, operations = impact.pages_using_keys(BlueprintDocument(draft), ["pregnancy"])
    assert pages == ["patient-detail"]
    assert operations == ["loadPatient"]


def test_compute_impact_shape() -> None:
    from factory_blueprint.content.document import BlueprintDocument

    base = helpers.seeded_document()
    draft = copy.deepcopy(base)
    draft["registry"]["entries"].append(
        helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_W")
    )
    level, changes = changelvl.determine(base, draft)
    result = impact.compute_impact(
        BlueprintDocument(draft), changes, level, DeliveryMode.B, downstream_keys=["pregnancy"]
    )
    assert set(result) == {
        "sections", "stages", "businessKeys", "pageKeys", "operationKeys",
        "artifactTypes", "changeLevel",
    }
    assert result["changeLevel"] == "Minor"
    assert "weight" in result["businessKeys"]
    assert "pregnancy" in result["businessKeys"]
    assert "METADATA_PACKAGE" in result["artifactTypes"]


@pytest.mark.parametrize(
    ("section", "stage"),
    [
        (Section.REQUIREMENTS, DesignStage.REQUIREMENTS),
        (Section.DOMAIN, DesignStage.DOMAIN),
        (Section.USE_CASES, DesignStage.DOMAIN),
        (Section.RULES, DesignStage.MAPPING),
        (Section.UI_INTENTS, DesignStage.PAGE_DESIGN),
        (Section.PROJECT, DesignStage.REQUIREMENTS),
    ],
)
def test_issue_source_stage_follows_appendix_a(section: Section, stage: DesignStage) -> None:
    assert issue_source_stage(section) is stage


def test_stage_scope_in_mode_a_includes_fixtures_at_mapping() -> None:
    assert Section.FIXTURES in allowed_sections(DesignStage.MAPPING, DeliveryMode.A)
    assert Section.FIXTURES not in allowed_sections(DesignStage.MAPPING, DeliveryMode.B)
    assert Section.FIXTURES in allowed_sections(DesignStage.PAGE_DESIGN, DeliveryMode.B)


def test_change_request_scope_extends_to_later_stages() -> None:
    """Appendix A, last paragraph: CR scope is the earliest stage and everything after."""
    sections = allowed_sections(
        DesignStage.PAGE_DESIGN,
        DeliveryMode.B,
        change_request_stages=[DesignStage.DOMAIN],
    )
    assert {Section.DOMAIN, Section.REGISTRY, Section.PAGES} <= set(sections)
