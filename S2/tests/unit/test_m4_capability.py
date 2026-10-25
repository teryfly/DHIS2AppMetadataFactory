"""The Capability Profile layer, read as the pure function it must be (FR-CAP-001/003).

These tests call :class:`ProfileView` directly rather than through a compile, because
FR-CAP-001 is a claim about *this* code: a new version is a different document, and no
branch here may look like ``if version == "2.42"``.  The compile-side behaviour that
depends on it is in ``tests/integration/test_m4_environment.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_blueprint.capability import (
    ADDRESSING_FIELDS,
    DEPRECATED,
    FORBIDDEN,
    REMOVED,
    SUPPORTED,
    ProfileView,
    series,
    view_of,
)
from factory_blueprint.capability.view import (
    RULE_IMPORT_CONSTRAINT,
    RULE_TYPE_OUT_OF_RANGE,
)
from factory_blueprint.descriptors import registry as descriptors
from factory_blueprint.enums import IssueLevel
from factory_blueprint.s4.contract import CapabilityProfile, profile_from_json
from factory_blueprint.s4.stub import profile_document

PROGRAM = "program"


def view(**overrides: Any) -> ProfileView:
    return ProfileView(profile_from_json(profile_document(**overrides)))


def descriptor(dhis2_type: str = PROGRAM) -> Any:
    found = descriptors.descriptor(dhis2_type)
    assert found is not None
    return found


@pytest.mark.parametrize(
    "stated,expected",
    [
        ("2.41.10", "2.41"),
        ("2.41", "2.41"),
        ("40.1.2", "40.1"),
        ("", ""),
        ("2", "2"),
    ],
)
def test_a_series_is_the_first_two_components(stated: str, expected: str) -> None:
    """S4 identifies a version series this way, and descriptor ranges are written per
    series: a patch release moves no endpoint and no schema (DX-CAP-005)."""
    assert series(stated) == expected


def test_a_version_inside_the_descriptor_range_is_supported() -> None:
    assert view(version="2.41.7").supports(descriptor())
    assert view(version="2.42.0").supports(descriptor())


def test_a_version_outside_it_is_refused_with_the_range_named() -> None:
    issues = view(version="2.40.6").type_issues(descriptor())
    assert [(issue.rule_id, issue.level) for issue in issues] == [
        (RULE_TYPE_OUT_OF_RANGE, IssueLevel.BLOCKING)
    ]
    assert "2.40" in issues[0].message and "2.41" in issues[0].message


def test_the_profile_answers_for_one_property() -> None:
    profile = view(
        version="2.42",
        removed=[{"kind": "field", "name": "program.accessLevel", "removedIn": "2.42"}],
        deprecated=[{"kind": "field", "name": "program.executionDateLabel"}],
        schemas=[
            {
                "dhis2Type": PROGRAM,
                "required": [],
                "controlableFields": [],
                "forbidden": ["href", "lastUpdated"],
                "properties": {},
            }
        ],
    )
    assert profile.field(PROGRAM, "accessLevel").verdict == REMOVED
    assert profile.field(PROGRAM, "executionDateLabel").verdict == DEPRECATED
    assert profile.field(PROGRAM, "lastUpdated").verdict == FORBIDDEN
    assert profile.field(PROGRAM, "name").verdict == SUPPORTED
    assert not profile.field(PROGRAM, "accessLevel").writable
    assert profile.field(PROGRAM, "executionDateLabel").writable, "deprecated still imports"


def test_a_property_named_bare_is_matched_too() -> None:
    """S4's field entries name either ``<dhis2Type>.<property>`` or the property alone,
    and reading only one of the two would silently un-report the other."""
    assert view(
        version="2.42", removed=[{"kind": "field", "name": "accessLevel"}]
    ).field(PROGRAM, "accessLevel").verdict == REMOVED


def test_an_endpoint_entry_is_not_read_as_a_field() -> None:
    """The vocabulary is S4's (endpoint, field, parameter): a removed endpoint says
    nothing about a metadata property, and mixing them up would refuse valid objects."""
    profile = view(version="2.42", removed=[{"kind": "endpoint", "name": "api/programs"}])
    assert profile.field(PROGRAM, "accessLevel").verdict == SUPPORTED


def test_every_disagreement_with_the_descriptor_baseline_is_recorded() -> None:
    profile = view(
        version="2.42",
        schemas=[
            {
                "dhis2Type": PROGRAM,
                "required": ["name", "shortName", "programType", "accessLevel"],
                "controlableFields": ["name", "accessLevel"],
                "forbidden": ["href", "id", "lastUpdated", "access", "favorite"],
                "properties": {},
            }
        ],
    )
    effects = {(item["effect"], item["name"]) for item in profile.differences(descriptor())}
    assert ("REQUIRED_ADDED", "accessLevel") in effects
    assert ("REQUIRED_DROPPED", "categoryCombo") in effects
    assert ("FIELD_UNCONTROLABLE", "formName") in effects
    assert ("FIELD_CONTROLABLE", "accessLevel") in effects
    # favorite is forbidden by this version and not in the shipped descriptor at all
    assert ("FIELD_FORBIDDEN", "favorite") in effects
    assert all(item["dhis2_type"] == PROGRAM for item in profile.differences(descriptor()))


def test_a_type_the_profile_says_nothing_about_produces_no_differences() -> None:
    """Silence is coverage, not disagreement: listing every type the Profile does not
    publish would bury the few entries that say this version changed something."""
    # trackedEntityType is in the descriptor set and in no schema of this Profile
    assert view(version="2.42").differences(descriptor("trackedEntityType")) == []


def test_a_stored_format_that_differs_from_the_baseline_is_a_difference() -> None:
    profile = view(version="2.42", translation_format="i18n", sharing_format="auth")
    effects = {(item["effect"], item["name"]) for item in profile.format_differences()}
    assert effects == {("TRANSLATION_FORMAT", "i18n"), ("SHARING_FORMAT", "auth")}
    assert profile.translation_property == "i18n"
    assert profile.sharing_property == "auth"


def test_the_baseline_formats_produce_no_differences() -> None:
    assert view(version="2.42").format_differences() == []


def test_volatile_and_forbidden_fields_are_left_out_of_a_comparison() -> None:
    """The set S4 excludes from its own field-level Diff (DX-DSC-007), because two
    subsystems answering "what changed" with two different lists is worse than neither."""
    profile = view(
        version="2.42",
        volatile_fields=["lastUpdated", "created"],
        schemas=[
            {
                "dhis2Type": PROGRAM,
                "required": [],
                "controlableFields": [],
                "forbidden": ["access"],
                "properties": {},
            }
        ],
    )
    assert profile.uncomparable_fields(PROGRAM) >= {"lastUpdated", "created", "access"}
    assert "name" not in profile.uncomparable_fields(PROGRAM)


def test_the_import_rules_the_profile_states_are_evaluated_at_its_own_severity() -> None:
    profile = view(
        version="2.42",
        import_parameters={
            "metadata": {"defaults": {"identifier": "EXISTING", "atomicMode": "NONE"}}
        },
    )
    issues = profile.constraint_issues()
    assert {issue.rule_id for issue in issues} == {RULE_IMPORT_CONSTRAINT}
    assert {issue.level for issue in issues} == {IssueLevel.BLOCKING, IssueLevel.WARNING}
    assert all(issue.details["profileVersion"] for issue in issues)


def test_an_import_that_matches_the_baseline_reports_nothing() -> None:
    assert view(version="2.42").constraint_issues() == []


def test_a_rule_about_something_else_is_left_to_its_owner() -> None:
    """A Profile states rules for the subsystem that can check them; S2 does not claim a
    verdict on an execution rule it has no inputs for."""
    profile = view(
        version="2.42",
        validation_rules={
            "rules": {
                "tracker.import.forbidsProduction": {
                    "severity": "BLOCKING",
                    "description": "DX-TRK-002：Tracker 数据导入禁止 PRODUCTION 类环境",
                }
            }
        },
    )
    assert profile.constraint_issues() == []


def test_a_profile_that_states_no_severity_warns_rather_than_refusing() -> None:
    """A typo in a data file must not block a compile, and must not pass unnoticed."""
    profile = view(
        version="2.42",
        import_parameters={"metadata": {"defaults": {"identifier": "AUTO"}}},
        validation_rules={
            "rules": {"metadata.import.requiresUidIdentifier": {"description": "no severity"}}
        },
    )
    assert [issue.level for issue in profile.constraint_issues()] == [IssueLevel.WARNING]


def test_a_profile_with_no_rules_section_says_nothing() -> None:
    assert view(version="2.42", validation_rules={}).constraint_issues() == []
    assert view(version="2.42", validation_rules={"rules": []}).constraint_issues() == []


def test_the_addressing_fields_are_never_dropped_from_a_payload() -> None:
    """A Profile lists ``id`` among the properties a client may not write, meaning the
    server maintains it; S2's payload still has to name the object it means."""
    assert frozenset({"id", "code"}) == ADDRESSING_FIELDS
    assert view(version="2.42").field(PROGRAM, "id").verdict in {SUPPORTED, FORBIDDEN}


def test_no_profile_means_no_constraint_at_all() -> None:
    """A baseline compile has no target version, so nothing here may constrain it
    (D-01, AC-ENV-01): the products have to stay environment-independent."""
    assert view_of(None) is None
    assert isinstance(profile_from_json(profile_document()), CapabilityProfile)
