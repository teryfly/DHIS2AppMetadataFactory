"""M4: one version against two environments, and one version against two versions.

Three acceptance criteria live here.  AC-ENV-01 says a version is environment-independent
where it must be and environment-bound where it is not; AC-CAP-01 says a different DHIS2
version is compiled *with that version's Profile* and the difference is shown rather than
inferred; BP-CMP-030 names the mechanism.  FR-CAP-001 is the constraint they all answer to
— none of these tests asks a compiler to know a version number, they hand it a different
Profile and read back what changed.
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import unit_of_work

from factory_blueprint import api, artifacts
from factory_blueprint.enums import BuildStatus, IssueLevel
from factory_blueprint.s4.stub import (
    discovered_object,
    discovery_document,
    profile_document,
)
from factory_blueprint.testing.harness import BlueprintHarness
from tests.integration import test_compile as scenario

PROJECT = scenario.PROJECT
UAT = "UAT"

#: the same objects the design environment has, under the UIDs two other instances use
TEST_UIDS = {
    "optionSet": "sexTEST001",
    "organisationUnit": "OUTEST00001",
    "categoryCombo": "catComboTs1",
}
UAT_UIDS = {
    "optionSet": "sexUAT00001",
    "organisationUnit": "OUUAT00001",
    "categoryCombo": "catComboUa1",
}


def _discovery(environment: str, uids: dict[str, str], **extra: Any) -> str:
    """A Discovery Result for one environment.

    The dataElement keeps its design UID: an UPDATE is matched by UID alone
    (BP-REG-010.2), so the same object in a second instance is still the same object.
    """
    element_content = extra.pop("elementContent", None) or {
        "id": "deClinic001",
        "name": "Facility name",
        "shortName": "Facility",
        "description": "written by the hospital IT office",
        "valueType": "TEXT",
        "domainType": "ATTRIBUTE",
    }
    return scenario._s4_artifact(
        PROJECT,
        "DISCOVERY_RESULT",
        discovery_document(
            environment,
            [
                discovered_object(
                    "optionSet", uids["optionSet"], code="MC_OS_SEX", name="Sex",
                    content={"id": uids["optionSet"], "name": "Sex"},
                ),
                discovered_object(
                    "organisationUnit", uids["organisationUnit"], code="FACILITY", name="Facility"
                ),
                discovered_object(
                    "dataElement", "deClinic001", code="MC_DE_CLINICNAME",
                    name=str(element_content.get("name")),
                    content=element_content,
                ),
                discovered_object(
                    "categoryCombo", uids["categoryCombo"], code="default", name="Default"
                ),
            ],
            defaults={"categoryCombo": uids["categoryCombo"]},
        ),
    )


#: what the instance rewrites by itself, so a change in one of them is not a managed change
VOLATILE = {"lastUpdated", "created", "href", "access", "id"}


def _profile_242(**overrides: Any) -> str:
    """A Profile shaped like S4's 2.42 one: same grammar, different statements."""
    document = profile_document(
        version="2.42",
        profile_version="Dhis242Profile",
        expression_grammar_version="dhis2-2.42",
        **overrides,
    )
    return scenario._s4_artifact(PROJECT, "CAPABILITY_PROFILE", document)


def _compile(version: str, environment: str, discovery: str, profile: str, key: str) -> Any:
    return api.compile_for_environment(
        PROJECT,
        version,
        environment,
        discovery,
        profile,
        None,
        scenario.context(),
        idempotency_key=key,
        design_reference_environment=scenario.DESIGN_ENV,
    )


def _issues(build: Any) -> list[dict[str, Any]]:
    """Every finding of one build, read from its VALIDATION_REPORT (BP-VAL-003)."""
    report, _ = artifacts.read_json(build.artifact_ids["VALIDATION_REPORT"])
    return list(report["issues"])


def _levels(build: Any) -> dict[str, str]:
    return {issue["ruleId"]: issue["level"] for issue in _issues(build)}


def test_ac_env_01_one_version_compiled_for_two_environments(harness: BlueprintHarness) -> None:
    """AC-ENV-01: an object this version creates is the same object everywhere, and a
    reference resolves to what each environment actually holds (D-02, BP-REG-010.2)."""
    version = scenario.approved_version()
    profile = scenario._profile_artifact()
    test_build = _compile(version, scenario.TEST_ENV, _discovery(scenario.TEST_ENV, TEST_UIDS), profile, "k-m4-test")
    uat_build = _compile(version, UAT, _discovery(UAT, UAT_UIDS), profile, "k-m4-uat")
    assert test_build.status is BuildStatus.COMPLETED, test_build.binding_issues
    assert uat_build.status is BuildStatus.COMPLETED, uat_build.binding_issues

    test_objects = _manifest_objects(test_build)
    uat_objects = _manifest_objects(uat_build)
    varies = _environment_dependent(test_build)
    stable = set(test_objects) - varies
    assert stable, "the scenario creates and updates objects"
    assert all(test_objects[key]["uid"] == uat_objects[key]["uid"] for key in stable), (
        "an object this version creates, or the one an UPDATE addresses, is the same "
        "object in every environment"
    )
    assert scenario._payload_of(test_build.artifact_ids["METADATA_PACKAGE"]) != scenario._payload_of(
        uat_build.artifact_ids["METADATA_PACKAGE"]
    ), "the references that resolved per environment must show in the payload"

    with unit_of_work() as session:
        bindings = api.get_bindings(
            PROJECT, test_build.version, [scenario.TEST_ENV, UAT], session=session
        )
    facility = {item.environment_key: item.uid for item in bindings if item.business_key == "facility"}
    assert set(facility) == {scenario.TEST_ENV, UAT}
    assert len(set(facility.values())) == 2, "a reference resolves to each environment's own object"


def _manifest_objects(build: Any) -> dict[str, dict[str, Any]]:
    manifest = scenario._manifest_of(build.artifact_ids["METADATA_PACKAGE"])
    return {str(item["businessKey"]): item for item in manifest["objects"]}


def _environment_dependent(build: Any) -> set[str]:
    manifest = scenario._manifest_of(build.artifact_ids["METADATA_PACKAGE"])
    return {
        str(item["businessKey"])
        for item in manifest["uidToBusinessKey"]
        if item["variesByEnvironment"]
    }


def test_ac_env_01_the_manifest_names_what_varies_by_environment(
    harness: BlueprintHarness,
) -> None:
    """BP-CMP-027 with AC-ENV-01: the package says which entries depend on the
    environment, so a reviewer does not have to diff two packages to find out."""
    version = scenario.approved_version()
    profile = scenario._profile_artifact()
    test_build = _compile(version, scenario.TEST_ENV, _discovery(scenario.TEST_ENV, TEST_UIDS), profile, "k-m4-varies-a")
    uat_build = _compile(version, UAT, _discovery(UAT, UAT_UIDS), profile, "k-m4-varies-b")
    manifest = scenario._manifest_of(test_build.artifact_ids["METADATA_PACKAGE"])
    varies = {
        item["businessKey"]
        for item in manifest["uidToBusinessKey"]
        if item["variesByEnvironment"]
    }
    assert {"sex", "facility", "default.categorycombo"} <= varies
    assert "pregnancy" not in varies, "a CREATE object is the same object everywhere"

    differing = _differing_object_entries(test_build, uat_build)
    assert differing <= varies, (
        f"the two packages differ on {sorted(differing)}, which the manifest does not "
        "declare environment-dependent"
    )


def _differing_object_entries(first: Any, second: Any) -> set[str]:
    """businessKeys whose manifest object is not equal across two environments."""
    left = {
        item["businessKey"]: item
        for item in scenario._manifest_of(first.artifact_ids["METADATA_PACKAGE"])["objects"]
    }
    right = {
        item["businessKey"]: item
        for item in scenario._manifest_of(second.artifact_ids["METADATA_PACKAGE"])["objects"]
    }
    return {key for key in set(left) & set(right) if left[key] != right[key]}


def test_ac_cap_01_a_different_version_is_compiled_with_its_own_profile(
    harness: BlueprintHarness,
) -> None:
    """AC-CAP-01 with FR-CAP-003 and BP-CMP-030: the same Blueprint against 2.42 uses the
    2.42 Profile, and what that version changed is written into the Diff."""
    version = scenario.approved_version()
    baseline = _compile(
        version,
        scenario.TEST_ENV, _discovery(scenario.TEST_ENV, TEST_UIDS), scenario._profile_artifact(), "k-m4-241"
    )
    assert baseline.status is BuildStatus.COMPLETED, baseline.binding_issues
    build_242 = _compile(
        version,
        scenario.TEST_ENV,
        _discovery(scenario.TEST_ENV, TEST_UIDS),
        _profile_242(
            schemas=[
                {
                    "dhis2Type": "program",
                    # this version insists on a field the 2.41 baseline does not, and no
                    # longer on one it used to
                    "required": ["name", "shortName", "programType", "accessLevel"],
                    "controlableFields": ["name", "shortName", "description", "accessLevel"],
                    "forbidden": ["href", "id", "lastUpdated", "access"],
                    "properties": {},
                }
            ],
        ),
        "k-m4-242",
    )
    assert build_242.status is BuildStatus.COMPLETED, build_242.binding_issues
    assert build_242.version == version, "another version changes the product, not the Blueprint"
    assert build_242.package_hash != baseline.package_hash

    diff, _ = artifacts.read_json(build_242.artifact_ids["DIFF_REPORT"])
    assert diff["profile_version"] == "Dhis242Profile"
    assert diff["dhis2_version"] == "2.42"
    assert diff["profile_hash"].startswith("sha256:")
    effects = {
        (item["effect"], item["dhis2_type"], item["name"])
        for item in diff["profile_differences"]
    }
    assert ("REQUIRED_ADDED", "program", "accessLevel") in effects
    assert ("REQUIRED_DROPPED", "program", "categoryCombo") in effects

    recorded, _ = artifacts.read_json(baseline.artifact_ids["DIFF_REPORT"])
    assert recorded["profile_version"] == "Dhis241Profile"
    assert recorded["profile_hash"] != diff["profile_hash"]
    base_effects = {
        (item["effect"], item["dhis2_type"], item["name"])
        for item in recorded["profile_differences"]
    }
    assert ("REQUIRED_ADDED", "program", "accessLevel") not in base_effects, (
        "only the 2.42 Profile asks for this property"
    )
    assert base_effects, "the Profile the scenario names is not the descriptor baseline either"


def test_bp_cmp_030_a_property_the_target_version_removed_is_refused(
    harness: BlueprintHarness,
) -> None:
    """FR-CMP-006 with BP-CMP-030: the version's answer about one property binds.

    Dropping it quietly would ship a program that lost a value the approved Blueprint
    asked for, so it is reported and no package is produced.
    """
    version = scenario.approved_version()
    build = _compile(
        version,
        scenario.TEST_ENV,
        _discovery(scenario.TEST_ENV, TEST_UIDS),
        _profile_242(removed=[{"kind": "field", "name": "program.accessLevel", "removedIn": "2.42"}]),
        "k-m4-removed",
    )
    assert build.status is BuildStatus.FAILED
    assert _levels(build).get("BPV-CAP-002") == IssueLevel.BLOCKING.value
    assert "METADATA_PACKAGE" not in build.artifact_ids


def test_bp_cmp_030_a_deprecated_property_still_ships_with_a_warning(
    harness: BlueprintHarness,
) -> None:
    """The version accepts it today, so the object is compiled; the warning says for how long."""
    version = scenario.approved_version()
    build = _compile(
        version,
        scenario.TEST_ENV,
        _discovery(scenario.TEST_ENV, TEST_UIDS),
        _profile_242(
            deprecated=[{"kind": "field", "name": "program.accessLevel", "removedIn": "2.44"}]
        ),
        "k-m4-deprecated",
    )
    assert build.status is BuildStatus.COMPLETED, build.binding_issues
    assert _levels(build).get("BPV-CAP-005") == IssueLevel.WARNING.value


def test_bp_cap_001_a_type_the_target_series_does_not_have_is_not_compiled(
    harness: BlueprintHarness,
) -> None:
    """FR-CAP-001: a descriptor says which versions describe it, and a Profile outside
    that range stops the compile instead of producing an object that cannot import."""
    version = scenario.approved_version()
    build = _compile(
        version,
        scenario.TEST_ENV,
        _discovery(scenario.TEST_ENV, TEST_UIDS),
        scenario._s4_artifact(
            PROJECT,
            "CAPABILITY_PROFILE",
            profile_document(
                version="2.40.6",
                profile_version="Dhis240Profile",
                expression_grammar_version="dhis2-2.40",
            ),
        ),
        "k-m4-outrange",
    )
    assert build.status is BuildStatus.FAILED
    assert _levels(build).get("BPV-CAP-001") == IssueLevel.BLOCKING.value


def test_fr_cap_003_a_profile_that_addresses_objects_another_way_is_refused(
    harness: BlueprintHarness,
) -> None:
    """A Profile's rules about S2's own output are evaluated at the severity stated.

    Every UID in a payload was allocated by S2, and both the drift check and the
    post-verification address objects by it, so a version that imports by some other
    identifier invalidates the whole build rather than one field.
    """
    version = scenario.approved_version()
    build = _compile(
        version,
        scenario.TEST_ENV,
        _discovery(scenario.TEST_ENV, TEST_UIDS),
        _profile_242(
            import_parameters={
                "metadata": {
                    "defaults": {"identifier": "EXISTING", "atomicMode": "NONE"},
                    "allowed": {"identifier": ["ASSIGNED", "EXISTING", "AUTO"]},
                }
            },
        ),
        "k-m4-constraint",
    )
    assert build.status is BuildStatus.FAILED
    found = [issue for issue in _issues(build) if issue["ruleId"] == "BPV-CAP-004"]
    assert {issue["level"] for issue in found} == {"BLOCKING", "WARNING"}, found
    assert any("identifier" in issue["message"] for issue in found)


def test_ac_cap_01_the_diff_ignores_what_the_instance_rewrites_on_its_own(
    harness: BlueprintHarness,
) -> None:
    """DX-DSC-007 across the seam: S4 leaves volatile fields out of a field-level Diff, so
    S2 has to leave them out too, or one deploy answers the question "what changed" twice
    with two different lists."""
    version = scenario.approved_version()
    content = {
        "id": "deClinic001",
        "name": "Facility name",
        "shortName": "Facility",
        "description": "written by the hospital IT office",
        "valueType": "TEXT",
        "domainType": "ATTRIBUTE",
        # the instance rewrote these; nobody edited the object
        "lastUpdated": "2026-02-02T00:00:00.000",
        "created": "2020-01-01T00:00:00.000",
        "href": "https://instance/api/dataElements/deClinic001",
        "access": {"read": ["all"]},
    }
    build = _compile(
        version,
        scenario.TEST_ENV,
        _discovery(scenario.TEST_ENV, TEST_UIDS, elementContent=content),
        scenario._profile_artifact(),
        "k-m4-volatile",
    )
    assert build.status is BuildStatus.COMPLETED, build.binding_issues
    diff, _ = artifacts.read_json(build.artifact_ids["DIFF_REPORT"])
    element = next(item for item in diff["objects"] if item["business_key"] == "deClinic001")
    changed = {str(item["field"]).split("/")[0] for item in element["changes"]}
    assert "name" in changed, "the one field this project manages did change"
    assert not changed & VOLATILE, sorted(changed & VOLATILE)


@pytest.mark.parametrize(
    "profile_version,series",
    [("Dhis241Profile", "2.41"), ("Dhis242Profile", "2.42")],
)
def test_the_profile_is_recorded_by_version_and_hash_never_by_artifact_id(
    harness: BlueprintHarness, profile_version: str, series: str
) -> None:
    """AC-DATA-01 with BP-CMP-030: a product names its inputs by content hash, because an
    import into another deployment rewrites artifact IDs and the bytes must not move."""
    version = scenario.approved_version()
    build = _compile(
        version,
        scenario.TEST_ENV,
        _discovery(scenario.TEST_ENV, TEST_UIDS),
        scenario._profile_artifact() if profile_version == "Dhis241Profile" else _profile_242(),
        f"k-m4-provenance-{series}",
    )
    assert build.status is BuildStatus.COMPLETED, build.binding_issues
    diff_text = str(artifacts.read_json(build.artifact_ids["DIFF_REPORT"])[0])
    report, _ = artifacts.read_json(build.artifact_ids["VALIDATION_REPORT"])
    assert report["compiler"]["profileVersion"] == profile_version
    assert report["compiler"]["dhis2Version"].startswith(series)
    assert profile_version in diff_text
    assert build.artifact_ids["DIFF_REPORT"] not in diff_text
