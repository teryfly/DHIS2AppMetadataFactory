"""BP-DIFF-001 / BP-CMP-029 (overall SRS 15.5): the object-level Diff, as pure functions.

Three comparisons belong to S2 — Blueprint version N vs N+1, Registry N vs N+1 and the
environment's current objects vs this compile — each reported per object as added,
updated (field level), unchanged, removed or reference-only.
"""

from __future__ import annotations

import copy
from typing import Any

from factory_blueprint.diff.object_diff import (
    ADDED,
    IGNORED_FIELDS,
    REFERENCE_ONLY,
    REMOVED,
    UNCHANGED,
    UPDATED,
    compare_documents,
    compare_registry,
    environment_diff,
    field_changes,
)
from factory_blueprint.dto.diff import DiffReport
from tests.unit import helpers

PROJECT = "p1"
FROM, TO = "0.2.0", "0.3.0"


def _pair() -> tuple[dict[str, Any], dict[str, Any]]:
    """A version pair: one object added, one updated, one unchanged, one removed."""
    before = helpers.seeded_document()
    before["registry"]["entries"].append(
        helpers.registry_entry("legacy", "program", "CREATE", uid="Leg1acy00001")
    )
    before["mapping"]["entries"].append(helpers.mapping_entry("legacy", "program"))
    before["domain"]["entities"].append(helpers.domain_entity("legacy", name="Legacy"))
    after = copy.deepcopy(before)
    after["registry"]["entries"].pop()
    after["mapping"]["entries"].pop()
    after["domain"]["entities"].pop()
    after["registry"]["entries"].append(
        helpers.registry_entry("delivery", "program", "CREATE", uid="Del1ivery0001")
    )
    after["mapping"]["entries"].append(helpers.mapping_entry("delivery", "program"))
    for entry in after["mapping"]["entries"]:
        if entry["businessKey"] == "pregnancy":
            entry["fields"]["name"] = {"default": "Pregnancy care"}
    return before, after


def _state_of(report: DiffReport, section: str, business_key: str) -> str:
    matches = [
        item
        for item in report.objects
        if item.section == section and item.business_key == business_key
    ]
    assert len(matches) == 1, (section, business_key, report.objects)
    return matches[0].state


def test_version_diff_reports_every_object_state() -> None:
    before, after = _pair()
    report = compare_documents(PROJECT, FROM, TO, before, after)
    assert report.kind == "BLUEPRINT_VERSIONS"
    assert (report.project_id, report.from_version, report.to_version) == (PROJECT, FROM, TO)
    assert _state_of(report, "registry", "delivery") == ADDED
    assert _state_of(report, "registry", "legacy") == REMOVED
    assert _state_of(report, "registry", "pregnancy") == UNCHANGED
    assert _state_of(report, "mapping", "pregnancy") == UPDATED
    assert report.counts[ADDED] == 2
    assert report.counts[REMOVED] == 3
    assert report.counts[UPDATED] == 1


def test_updated_object_carries_field_level_changes() -> None:
    before, after = _pair()
    report = compare_documents(PROJECT, FROM, TO, before, after)
    changed = next(
        item for item in report.objects if item.section == "mapping" and item.state == UPDATED
    )
    assert [(change.field, change.before, change.after) for change in changed.changes] == [
        ("fields/name/default", "pregnancy", "Pregnancy care")
    ]


def test_a_disposition_change_is_visible_on_the_object_and_its_fields() -> None:
    before, after = _pair()
    for entry in after["registry"]["entries"]:
        if entry["businessKey"] == "pregnancy":
            entry["disposition"] = "UPDATE"
            entry["controlledFields"] = ["name"]
    report = compare_documents(PROJECT, FROM, TO, before, after)
    pregnancy = next(item for item in report.objects if item.business_key == "pregnancy"
                     and item.section == "registry")
    assert pregnancy.state == UPDATED
    assert pregnancy.disposition == "UPDATE"
    assert "disposition" in [change.field for change in pregnancy.changes]


def test_a_translation_change_is_a_field_change() -> None:
    before, after = _pair()
    for document in (before, after):
        for entry in document["mapping"]["entries"]:
            if entry["businessKey"] == "pregnancy":
                entry["fields"]["name"] = {"default": "Pregnancy"}
    before["mapping"]["entries"][0]["fields"]["name"]["translations"] = {"fr": "Grossesse"}
    after["mapping"]["entries"][0]["fields"]["name"]["translations"] = {"fr": "Soins prénataux"}
    report = compare_documents(PROJECT, FROM, TO, before, after)
    changes = [
        change
        for item in report.objects
        if item.section == "mapping"
        for change in item.changes
    ]
    assert [(change.field, change.after) for change in changes] == [
        ("fields/name/translations/fr", "Soins prénataux")
    ]


def test_cosmetic_fields_never_appear_in_a_version_diff() -> None:
    before, after = _pair()
    for entry in after["domain"]["entities"]:
        entry["source"] = {"artifactId": "art-02INPUT0002", "location": "chap 9"}
        entry["notes"] = "reworded only"
    report = compare_documents(PROJECT, FROM, TO, before, after)
    assert _state_of(report, "domain", "pregnancy") == UNCHANGED
    assert "source" in IGNORED_FIELDS and "notes" in IGNORED_FIELDS
    assert not [
        change
        for item in report.objects
        for change in item.changes
        if change.field.split("/")[0] in IGNORED_FIELDS
    ]


def test_removed_object_keeps_the_identity_it_had_in_the_older_version() -> None:
    before, after = _pair()
    report = compare_documents(PROJECT, FROM, TO, before, after)
    legacy = next(item for item in report.objects if item.business_key == "legacy"
                  and item.section == "registry")
    assert (legacy.state, legacy.dhis2_type, legacy.uid, legacy.disposition) == (
        REMOVED,
        "program",
        "Leg1acy00001",
        "CREATE",
    )
    assert legacy.changes == []


def test_architecture_decisions_are_out_of_the_object_diff() -> None:
    before, after = _pair()
    after["architecture"]["decisions"].append(
        {
            "decisionId": "MAP-009",
            "title": "t",
            "context": "c",
            "decision": "d",
            "status": "ACCEPTED",
        }
    )
    report = compare_documents(PROJECT, FROM, TO, before, after)
    assert "architecture" not in {item.section for item in report.objects}


def test_compare_registry_is_the_registry_only_projection() -> None:
    before, after = _pair()
    full = compare_documents(PROJECT, FROM, TO, before, after)
    registry = compare_registry(PROJECT, FROM, TO, before, after)
    assert {item.section for item in registry.objects} == {"registry"}
    assert [item.business_key for item in registry.objects] == ["delivery", "legacy", "pregnancy"]
    assert [item.business_key for item in full.objects if item.section == "registry"] == [
        item.business_key for item in registry.objects
    ]


def test_environment_diff_compares_current_objects_with_this_compile() -> None:
    discovered = {
        "Progra00001": {
            "dhis2Type": "program",
            "uid": "Progra00001",
            "name": "Antenatal",
            "translations": [{"field": "name", "locale": "fr", "text": "Ancien"}],
        },
        "Outgo000001": {"dhis2Type": "program", "uid": "Outgo000001", "name": "Withdrawn"},
    }
    compiled = {
        "Progra00001": {
            "dhis2Type": "program",
            "uid": "Progra00001",
            "name": "Antenatal",
            "translations": [{"field": "name", "locale": "fr", "text": "Nouveau"}],
        },
        "Newpr000001": {
            "dhis2Type": "program",
            "uid": "Newpr000001",
            "name": "New program",
            "disposition": "CREATE",
        },
        "SexOp000001": {
            "dhis2Type": "optionSet",
            "uid": "SexOp000001",
            "name": "Sex",
            "disposition": "REFERENCE",
        },
    }
    report = environment_diff(
        PROJECT, TO, "TEST", discovered, compiled, reference_only={"SexOp000001"}
    )
    states = {item.business_key: item.state for item in report.objects}
    assert states == {
        "Progra00001": UPDATED,
        "Outgo000001": REMOVED,
        "Newpr000001": ADDED,
        "SexOp000001": REFERENCE_ONLY,
    }
    assert (report.kind, report.environment_key, report.to_version) == (
        "ENVIRONMENT_VS_COMPILE",
        "TEST",
        TO,
    )
    assert report.counts == {ADDED: 1, REFERENCE_ONLY: 1, REMOVED: 1, UPDATED: 1}
    assert report.by_state()[UPDATED][0].changes[0].before == [
        {"field": "name", "locale": "fr", "text": "Ancien"}
    ]


def test_field_changes_are_leaf_level_and_ordered_by_path() -> None:
    changes = field_changes(
        {"b": 1, "nested": {"x": 1, "y": 2}, "gone": True, "list": [1, 2]},
        {"b": 2, "nested": {"x": 1, "y": 3}, "fresh": "text", "list": [1, 3]},
    )
    assert [change.field for change in changes] == ["b", "fresh", "gone", "list", "nested/y"]
    assert changes[3].before == [1, 2] and changes[3].after == [1, 3]


def test_the_diff_is_deterministic_and_independent_of_entry_order() -> None:
    before, after = _pair()
    first = compare_documents(PROJECT, FROM, TO, before, after)
    again = compare_documents(PROJECT, FROM, TO, copy.deepcopy(before), copy.deepcopy(after))
    assert again.model_dump(mode="json") == first.model_dump(mode="json")
    shuffled = copy.deepcopy(after)
    for container in ("registry", "mapping"):
        shuffled[container]["entries"].reverse()
    reordered = compare_documents(PROJECT, FROM, TO, before, shuffled)
    assert reordered.model_dump(mode="json") == first.model_dump(mode="json")
    sections = {item.section for item in first.objects}
    assert sections == {"domain", "mapping", "registry"}
    for section in sections:
        keys = [item.business_key for item in first.objects if item.section == section]
        assert keys == sorted(keys)
