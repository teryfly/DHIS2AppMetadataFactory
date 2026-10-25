"""P1: Blueprint section schemas and schema migration machinery."""

from __future__ import annotations

import pytest

from factory_blueprint.content.initial import empty_sections, initial_document
from factory_blueprint.enums import Section
from factory_blueprint.schema import migrations, store, validate
from tests.unit import helpers


def test_current_schema_version_is_one() -> None:
    assert store.current_schema_version() == 1
    assert store.resolve_version(None) == 1


def test_every_section_has_a_standalone_schema() -> None:
    """BP-SCH-006: S5's editors and S3's agents consume one section at a time."""
    for section in Section:
        schema = store.section_schema(section)
        assert schema["type"] == "object", section
        assert schema["$id"].endswith(f"/sections/{section.value}.json")
        # self-contained: nothing may reference another document
        text = repr(schema)
        assert "damf.invalid/bp/1/defs.json" not in text, section


def test_inlined_definitions_are_reachable() -> None:
    schema = store.section_schema(Section.REGISTRY)
    assert "definitions" in schema
    assert "businessKey" in schema["definitions"]
    assert schema["properties"]["entries"]["items"]["properties"]["businessKey"]["$ref"].startswith(
        "#/definitions/"
    )


def test_initial_document_validates_against_the_root_schema() -> None:
    document = initial_document(helpers.project_info())
    assert not validate.validate_document(document)


def test_initial_document_validates_section_by_section() -> None:
    document = initial_document(helpers.project_info())
    for section in Section:
        assert not validate.validate_section(section, document[section.value]), section


def test_seeded_document_validates() -> None:
    document = helpers.seeded_document()
    assert not validate.validate_document(document)


def test_schema_rejects_an_unknown_section_content() -> None:
    violations = validate.validate_section(Section.PROJECT, {"deliveryMode": "C"})
    assert violations
    assert violations[0].section == "project"


def test_validation_order_is_deterministic() -> None:
    bad = {"deliveryMode": "C", "projectType": "NOPE", "targetDhis2": {}, "defaultLanguage": "EN!"}
    first = validate.validate_section(Section.PROJECT, bad)
    second = validate.validate_section(Section.PROJECT, bad)
    assert [(v.path, v.keyword, v.message) for v in first] == [
        (v.path, v.keyword, v.message) for v in second
    ]
    assert len(first) > 1


def test_registry_entry_requires_observation_for_reuse() -> None:
    """BP-REG-004: REUSE/REFERENCE/UPDATE must carry the observation baseline."""
    entry = helpers.registry_entry("sex", "optionSet", "REUSE", observed=False)
    violations = validate.validate_section(
        Section.REGISTRY, {"entries": [entry, *empty_sections()[Section.REGISTRY.value]["entries"]]}
    )
    assert violations
    assert any("observedLastUpdated" in v.message or "required" in v.keyword for v in violations)


def test_registry_entry_rejects_environment_bindings_in_content() -> None:
    """BP-REG-010.4: bindings are not version content."""
    entry = helpers.registry_entry("sex", "optionSet", "REUSE")
    entry["environmentBindings"] = {"TEST": {"uid": "aB3cD4eF5gH"}}
    violations = validate.validate_section(Section.REGISTRY, {"entries": [entry]})
    assert violations


def test_generation_policy_has_no_language_fields() -> None:
    """BP-SCH-002 keeps languages in project only."""
    policy = initial_document(helpers.project_info())[Section.GENERATION_POLICY.value]
    assert "defaultLanguage" not in policy
    violations = validate.validate_section(Section.GENERATION_POLICY, {**policy, "translationLanguages": ["fr"]})
    assert violations


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ValueError):
        store.resolve_version(99)


def test_migrator_steps_one_version_at_a_time() -> None:
    with pytest.raises(Exception) as raised:
        migrations.register_migration(Section.REGISTRY, 1, 3)
    assert raised.value.error_code == "BP_UNSUPPORTED_SCHEMA_VERSION"


def test_migrate_document_chains_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """BP-SCH-007 machinery: 1 → 2 → 3 applies each registered step in order."""
    document = helpers.seeded_document()
    monkeypatch.setitem(migrations.MIGRATIONS, ("registry", 1, 2), lambda d: {**d, "marker": [1]})
    monkeypatch.setitem(
        migrations.MIGRATIONS,
        ("registry", 2, 3),
        lambda d: {**d, "marker": [*d.get("marker", []), 2]},
    )
    # declare version 3 available so the noop check is not consulted
    monkeypatch.setattr(migrations.store, "available_versions", lambda: (1, 2, 3))
    assert migrations.has_migration_path(1, 3)
    migrated = migrations.migrate_document(document, 1, 3)
    assert migrated.document[store.SCHEMA_VERSION_FIELD] == 3
    assert migrated.document["registry"]["marker"] == [1, 2]
    assert migrated.changed_sections == ["registry"]


def test_downgrade_is_unsupported() -> None:
    with pytest.raises(Exception) as raised:
        migrations.migrate_document(helpers.seeded_document(), 2, 1)
    assert raised.value.error_code == "BP_UNSUPPORTED_SCHEMA_VERSION"
