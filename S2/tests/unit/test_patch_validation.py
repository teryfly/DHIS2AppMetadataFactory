"""P1: the five Patch validation rules (BP-DRF-005)."""

from __future__ import annotations

from typing import Any

from factory_blueprint.enums import DesignStage, IssueLevel
from factory_blueprint.impact.stage_map import allowed_sections
from factory_blueprint.patch import validate as pv
from factory_blueprint.schema import store
from tests.unit import helpers


def submit(
    base: dict[str, Any],
    operations: list[dict[str, Any]],
    *,
    stage: DesignStage = DesignStage.MAPPING,
    mapping_frozen: bool = False,
    change_request_id: str | None = None,
) -> pv.PatchValidation:
    return pv.validate_patch(
        base,
        operations,
        allowed_sections=allowed_sections(stage),
        schema_version=store.current_schema_version(),
        mapping_frozen=mapping_frozen,
        change_request_id=change_request_id,
        stage=stage,
    )


def rules(result: pv.PatchValidation) -> set[str]:
    return {issue.rule_id for issue in result.issues}


def test_valid_new_registry_entry_is_accepted() -> None:
    entry = helpers.registry_entry("blood_pressure", "trackedEntityAttribute", "CREATE", code="MC_TE")
    result = submit(helpers.seeded_document(), [{"op": "add", "path": "/registry/entries/-", "value": entry}])
    assert result.accepted, [issue.message for issue in result.issues]
    assert result.new_create_keys == ["blood_pressure"]
    assert result.document is not None
    assert result.touched_entries == [("/registry/entries", "blood_pressure")]


def test_patch_outside_stage_scope_is_rejected() -> None:
    """BP-AC-02."""
    page = {"pageKey": "patient-detail", "name": "Detail", "purpose": "x", "routes": ["/p"],
            "useCases": ["uc"], "actors": ["midwife"], "registryRefs": [], "source": {"note": "n"}}
    result = submit(helpers.seeded_document(), [{"op": "add", "path": "/pages/items/-", "value": page}])
    assert not result.accepted
    assert pv.RULE_STAGE_SCOPE in rules(result)
    assert result.document is None


def test_whole_object_section_respects_stage_scope() -> None:
    result = submit(
        helpers.seeded_document(),
        [{"op": "replace", "path": "/project/deliveryMode", "value": "A"}],
    )
    assert not result.accepted
    assert pv.RULE_STAGE_SCOPE in rules(result)


def test_move_source_outside_scope_is_rejected() -> None:
    result = submit(
        helpers.seeded_document(),
        [{"op": "move", "from": "/pages/items/0", "path": "/registry/entries/-"}],
        stage=DesignStage.MAPPING,
    )
    assert not result.accepted
    assert pv.RULE_STAGE_SCOPE in rules(result)


def test_patch_writing_uid_is_rejected() -> None:
    """BP-AC-01 / BP-DRF-005.4."""
    base = helpers.seeded_document()
    entry = helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_WEIGHT", uid="aB3cD4eF5gH")
    result = submit(base, [{"op": "add", "path": "/registry/entries/-", "value": entry}])
    assert not result.accepted
    assert pv.RULE_UID in rules(result)

    direct = submit(base, [{"op": "replace", "path": "/registry/entries/0/uid", "value": "zZ9yY8xX7wV"}])
    assert not direct.accepted
    assert pv.RULE_UID in rules(direct)


def test_uid_shaped_literal_in_page_reference_is_rejected() -> None:
    """FR-PG-002: pages must reference the Registry by businessKey."""
    result = submit(
        helpers.seeded_document(),
        [{"op": "add", "path": "/pages/items/-", "value": {
            "pageKey": "patient-detail", "name": "D", "purpose": "p", "routes": ["/x"],
            "useCases": ["uc"], "actors": ["a"], "registryRefs": ["aB3cD4eF5gH"],
            "source": {"note": "n"}}}],
        stage=DesignStage.PAGE_DESIGN,
    )
    assert not result.accepted
    assert pv.RULE_UID in rules(result)


def test_schema_violation_is_reported() -> None:
    """BP-DRF-005.2."""
    entry = helpers.registry_entry("broken", "optionSet", "NOT_A_DISPOSITION")
    result = submit(helpers.seeded_document(), [{"op": "add", "path": "/registry/entries/-", "value": entry}])
    assert not result.accepted
    assert pv.RULE_SECTION_SCHEMA in rules(result)


def test_duplicate_business_key_is_reported() -> None:
    """BP-DRF-005.3."""
    base = helpers.seeded_document()
    duplicate = helpers.registry_entry("pregnancy", "programStage", "CREATE", code="MC_PS_DUP")
    result = submit(base, [{"op": "add", "path": "/registry/entries/-", "value": duplicate}])
    assert not result.accepted
    assert pv.RULE_KEYS in rules(result)


def test_unresolved_reference_is_reported() -> None:
    base = helpers.seeded_document()
    entry = helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_W", decision_id="MAP-404")
    result = submit(base, [{"op": "add", "path": "/registry/entries/-", "value": entry}])
    assert not result.accepted
    assert pv.RULE_KEYS in rules(result)
    assert any("MAP-404" in issue.message for issue in result.issues)


def test_frozen_mapping_without_change_request_is_rejected() -> None:
    """AC-BP-02, BP-BR-07."""
    entry = helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_W")
    for path in ("/registry/entries/-", "/mapping/entries/-", "/architecture/decisions/-"):
        result = submit(
            helpers.seeded_document(),
            [{"op": "add", "path": path, "value": entry}],
            mapping_frozen=True,
        )
        assert not result.accepted, path
        assert pv.RULE_FROZEN_MAPPING in rules(result)


def test_frozen_mapping_with_change_request_is_accepted() -> None:
    entry = helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_W")
    result = submit(
        helpers.seeded_document(),
        [{"op": "add", "path": "/registry/entries/-", "value": entry}],
        mapping_frozen=True,
        change_request_id="cr-000123",
    )
    assert result.accepted, [issue.message for issue in result.issues]


def test_unrelated_section_is_not_blocked_by_frozen_mapping() -> None:
    """RULES sit in the MAPPING scope but are not part of the frozen mapping set."""
    result = submit(
        helpers.seeded_document(),
        [{"op": "add", "path": "/rules/programRules/-", "value": {
            "businessKey": "rule.due.date", "name": {"default": "Due date"},
            "expression": "D2:VALUE('dueDate') != null", "source": {"note": "n"}}}],
        mapping_frozen=True,
    )
    assert result.accepted, [issue.message for issue in result.issues]
    assert pv.RULE_FROZEN_MAPPING not in rules(result)


def test_malformed_patch_reports_an_issue_instead_of_raising() -> None:
    result = submit(helpers.seeded_document(), [{"op": "nope", "path": "/registry/entries"}])
    assert not result.accepted
    assert rules(result) == {pv.RULE_MALFORMED}


def test_all_issues_are_blocking_and_have_a_source_stage() -> None:
    result = submit(
        helpers.seeded_document(),
        [{"op": "add", "path": "/registry/entries/-", "value": {"businessKey": "x"}}],
    )
    assert result.issues
    for issue in result.issues:
        assert issue.level is IssueLevel.BLOCKING
        assert issue.source_stage in {DesignStage.MAPPING, DesignStage.REQUIREMENTS}, issue
        assert issue.issue_id.startswith("bpis-")
