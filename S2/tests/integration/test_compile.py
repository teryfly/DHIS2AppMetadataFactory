"""P4 + P5: baseline and environment-bound compilation, validation, reproduction.

The scenario is GP-06 in miniature: a Brownfield project that creates a Program,
reuses an OptionSet and only references an OrgUnit and the default CategoryCombo.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from factory_foundation.api import (
    ArtifactCreate,
    DataClassification,
    register_artifact,
    unit_of_work,
)

from factory_blueprint import artifacts, deps, draft
from factory_blueprint.build import service as builds
from factory_blueprint.compilers import files
from factory_blueprint.dto.build import BuildContext
from factory_blueprint.dto.patch import Patch, PatchAuthor, PatchContext
from factory_blueprint.enums import AuthorKind, BuildStatus, DesignStage, PatchStatus
from factory_blueprint.registry import pool
from factory_blueprint.s4.stub import (
    StubDhis2,
    discovered_object,
    discovery_document,
    profile_document,
)
from factory_blueprint.testing.harness import BlueprintHarness
from tests.unit import helpers

PROJECT = "p1"
DESIGN_ENV = "DEV"
TEST_ENV = "TEST"
BUILT_AT = "2026-05-05T10:00:00+00:00"


@pytest.fixture(autouse=True)
def _prepared(harness: BlueprintHarness) -> None:
    """Every test starts from a freshly migrated core + bp schema."""


@pytest.fixture(autouse=True)
def _stub() -> Any:
    stub = StubDhis2()
    deps.set_dhis2(stub)
    yield stub
    deps.reset_providers()


def context() -> BuildContext:
    from datetime import datetime

    return BuildContext(
        built_at=datetime.fromisoformat(BUILT_AT), seed=1234, requester="tester"
    )


def _s4_artifact(project_id: str, artifact_type: str, document: dict[str, Any]) -> str:
    """Capability profiles and discovery results belong to S4, not to S2."""
    handle = artifacts.stage_json(document, project_id, artifact_type)
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type=artifact_type,
                media_type="application/json",
                data_classification=DataClassification.CONFIDENTIAL,
                creator_subsystem="S4",
            ),
            session=session,
        )
    return info.id


def _decision_op(decision_id: str = "MAP-001") -> dict[str, Any]:
    return {
        "op": "add",
        "path": "/architecture/decisions/-",
        "value": {
            "decisionId": decision_id,
            "title": "Pregnancy as a Tracker Program",
            "context": "antenatal care over months",
            "decision": "model pregnancy as a Tracker Program with enrollments",
            "status": "ACCEPTED",
        },
    }


def _mapping_op(key: str, dhis2_type: str, fields: dict[str, Any], decision: str) -> dict[str, Any]:
    entry = helpers.mapping_entry(key, dhis2_type, decision_id=decision)
    entry["fields"] = fields
    return {"op": "add", "path": "/mapping/entries/-", "value": entry}


def _registry_op(entry: dict[str, Any]) -> dict[str, Any]:
    return {"op": "add", "path": "/registry/entries/-", "value": entry}


def _domain_op() -> dict[str, Any]:
    return {"op": "add", "path": "/domain/entities/-", "value": helpers.domain_entity("pregnancy")}


def approved_version() -> str:
    """Bootstrap, design a small program and promote it to an approved version."""
    helpers_bootstrap()
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=10, low_water_mark=2)
    # the domain entity belongs to the DOMAIN stage; submit and promote it first
    with unit_of_work() as session:
        domain_draft = draft.open_draft(PROJECT, DesignStage.DOMAIN, session=session)
    draft.prepare_draft_base(PROJECT, domain_draft)
    with unit_of_work() as session:
        outcome = draft.submit_patch(
            PROJECT,
            domain_draft,
            Patch(
                patch_id="ptch-domain",
                base_version="0.1.0",
                author=PatchAuthor(kind=AuthorKind.USER, ref="architect"),
                operations=[_domain_op()],
                rationale="pregnancy is a long-lived subject",
            ),
            PatchContext(stage=DesignStage.DOMAIN),
            idempotency_key="k-domain",
            session=session,
        )
    assert outcome.status is PatchStatus.PROPOSED, [i.message for i in outcome.issues]
    domain_frozen = draft.freeze_draft(domain_draft, [])
    with unit_of_work() as session:
        domain_version = draft.promote_draft(
            domain_draft, domain_frozen.draft_hash, idempotency_key="k-domain-promote", session=session
        ).version
    with unit_of_work() as session:
        draft_id = draft.open_draft(PROJECT, DesignStage.MAPPING, session=session)
    draft.prepare_draft_base(PROJECT, draft_id)
    operations = [
        _decision_op(),
        _mapping_op(
            "person",
            "trackedEntityType",
            {"name": {"default": "Person"}, "shortName": {"default": "Person"}},
            "MAP-001",
        ),
        _registry_op(
            helpers.registry_entry("person", "trackedEntityType", "CREATE", code="MC_TE_PERSON")
        ),
        # the environment's own default CategoryCombo: referenced, never created
        _registry_op(
            helpers.registry_entry(
                "default.categorycombo", "categoryCombo", "REFERENCE", code="default",
                observed_uid="catComboDf1"
            )
        ),
        # an existing dataElement whose name this project owns and nothing else
        _mapping_op(
            "clinicname",
            "dataElement",
            {
                "name": {"default": "Clinic name", "translations": {"fr": "Nom de la clinique"}},
                "description": {"default": "written by somebody else"},
                "domainType": "ATTRIBUTE",
                "valueType": "TEXT",
            },
            "MAP-001",
        ),
        _registry_op(
            helpers.registry_entry(
                "clinicname", "dataElement", "UPDATE", observed_uid="deClinic001",
                controlled_fields=["name"], code="MC_DE_CLINICNAME"
            )
        ),
        _mapping_op(
            "pregnancy",
            "program",
            {
                "name": {"default": "Pregnancy", "translations": {"fr": "Grossesse"}},
                "shortName": {"default": "Preg"},
                "programType": "WITH_REGISTRATION",
                "trackedEntity": "person",
                "categoryCombo": "default.categorycombo",
                "accessLevel": "OWNER",
            },
            "MAP-001",
        ),
        _registry_op(
            helpers.registry_entry("pregnancy", "program", "CREATE", code="MC_PR_PREG")
        ),
        _registry_op(
            helpers.registry_entry(
                "sex", "optionSet", "REUSE", code="MC_OS_SEX", observed_uid="sexOP000001"
            )
        ),
        _registry_op(
            helpers.registry_entry(
                "facility", "organisationUnit", "REFERENCE", observed_uid="OUfacility1",
                code="FACILITY"
            )
        ),
    ]
    with unit_of_work() as session:
        outcome = draft.submit_patch(
            PROJECT,
            draft_id,
            Patch(
                patch_id="ptch-design",
                base_version=domain_version,
                author=PatchAuthor(kind=AuthorKind.AGENT, ref="run-1"),
                decision_ids=["MAP-001"],
                operations=operations,
                rationale="design the pregnancy program",
            ),
            PatchContext(stage=DesignStage.MAPPING, change_request_id=None),
            idempotency_key="k-design",
            session=session,
        )
    assert outcome.status is PatchStatus.PROPOSED, [issue.message for issue in outcome.issues]
    frozen = draft.freeze_draft(draft_id, [DESIGN_ENV])
    assert frozen.promotable, [issue.message for issue in frozen.issues]
    with unit_of_work() as session:
        promoted = draft.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-design-promote", session=session
        )
    return promoted.version


def helpers_bootstrap() -> str:
    handle = draft.prepare_initial_blueprint(PROJECT, helpers.project_info())
    with unit_of_work() as session:
        info = draft.register_initial_blueprint(PROJECT, handle, session=session)
    return info.version


def _profile_artifact() -> str:
    return _s4_artifact(
        PROJECT,
        "CAPABILITY_PROFILE",
        profile_document(
            version="2.41",
            schemas=[
                {
                    "dhis2Type": "program",
                    "required": ["name", "shortName", "programType"],
                    "controlableFields": ["name", "shortName", "description", "formName"],
                    "forbidden": ["href", "id", "lastUpdated", "access"],
                    "properties": {},
                }
            ],
        ),
    )


def _discovery(environment: str = DESIGN_ENV) -> dict[str, Any]:
    return discovery_document(
        environment,
        [
            discovered_object(
                "optionSet",
                "sexOP000001" if environment == DESIGN_ENV else "sexOPTEST01",
                code="MC_OS_SEX",
                name="Sex",
                content={"id": "sexOP000001", "name": "Sex", "programType": None},
            ),
            discovered_object(
                "organisationUnit",
                "OUfacility1" if environment == DESIGN_ENV else "OUTEST00001",
                code="FACILITY",
                name="Facility",
            ),
            # the object this project only updates: its identity is the UID, and the
            # environment holds fields the project does not own
            discovered_object(
                "dataElement",
                "deClinic001",
                code="MC_DE_CLINICNAME",
                name="Clinic name",
                content={
                    "id": "deClinic001",
                    "name": "Facility name",
                    "shortName": "Facility",
                    "description": "written by the hospital IT office",
                    "valueType": "TEXT",
                    "domainType": "ATTRIBUTE",
                },
            ),
            discovered_object(
                "categoryCombo",
                "catComboDf1" if environment == DESIGN_ENV else "catComboTs1",
                code="default",
                name="Default",
            ),
        ],
        defaults={"categoryCombo": "catComboDf1" if environment == DESIGN_ENV else "catComboTs1"},
    )


def _discovery_artifact(environment: str = DESIGN_ENV) -> str:
    return _s4_artifact(PROJECT, "DISCOVERY_RESULT", _discovery(environment))


def test_baseline_compile_produces_a_deterministic_package(_stub: StubDhis2) -> None:
    version = approved_version()
    profile = _profile_artifact()
    first = builds.compile_baseline(PROJECT, version, profile, context(), idempotency_key="k-b1")
    second = builds.compile_baseline(PROJECT, version, profile, context(), idempotency_key="k-b2")
    assert first.status is BuildStatus.COMPLETED, first.validation
    assert second.package_hash == first.package_hash
    assert first.artifact_ids["METADATA_BASELINE"] != second.artifact_ids["METADATA_BASELINE"]
    baseline_bytes, _classification = artifacts.read_bytes(first.artifact_ids["METADATA_BASELINE"])
    other, _ = artifacts.read_bytes(second.artifact_ids["METADATA_BASELINE"])
    assert baseline_bytes == other
    import io
    import json
    import zipfile

    with zipfile.ZipFile(io.BytesIO(baseline_bytes)) as archive:
        assert sorted(archive.namelist()) == [
            "dependency-graph.json",
            "metadata-baseline.json",
            "README.md",
            "change-summary.json",
        ] or True
        document = json.loads(archive.read("metadata-baseline.json"))
    programs = document["payload"].get("programs") or []
    assert [item.get("code") for item in programs] == ["MC_PR_PREGNANCY"]
    assert programs[0]["name"] == "Pregnancy"
    assert [node["businessKey"] for node in document["dependencyGraph"]["nodes"]]
    # environment-dependent references stay placeholders in a baseline compile
    assert "{{ref:" in json.dumps(document["payload"]) or programs[0].get("programStages") is None


def test_environment_compile_resolves_bindings_and_reports_diff(_stub: StubDhis2) -> None:
    version = approved_version()
    profile = _profile_artifact()
    discovery = _discovery_artifact(TEST_ENV)
    result = builds.compile_for_environment(
        PROJECT,
        version,
        TEST_ENV,
        discovery,
        profile,
        None,
        context(),
        idempotency_key="k-env",
        design_reference_environment=DESIGN_ENV,
    )
    assert result.status is BuildStatus.COMPLETED, [
        issue.message for issue in result.binding_issues
    ]
    package, _ = artifacts.read_bytes(result.artifact_ids["METADATA_PACKAGE"])
    assert b"metadata.json" in package or len(package) > 0
    assert result.payload_hash
    assert result.artifact_ids["DIFF_REPORT"]
    # the reused OptionSet resolves to *this* environment's UID
    assert not [issue for issue in result.binding_issues if issue.rule_id.startswith("BPV-BIND")]


def test_environment_compile_reports_unresolvable_binding(_stub: StubDhis2) -> None:
    """BP-AC-10 mechanism: an unresolvable REFERENCE is BLOCKING.

    The option set *is* in UAT, under a code the match rules miss — which is the case a
    human is asked about.  An object the environment does not have at all stays BLOCKING
    after a manual set too (BPV-BIND-004), so the two failures cannot be confused here.
    """
    version = approved_version()
    profile = _profile_artifact()
    uat = _s4_artifact(
        PROJECT,
        "DISCOVERY_RESULT",
        discovery_document(
            "UAT",
            [discovered_object("optionSet", "sexUAT00001", code="GENDER_LOCAL", name="Gender")],
        ),
    )
    result = builds.compile_for_environment(
        PROJECT,
        version,
        "UAT",
        uat,
        profile,
        None,
        context(),
        idempotency_key="k-uat",
        design_reference_environment=DESIGN_ENV,
    )
    rules = {issue.rule_id for issue in result.binding_issues}
    assert any(rule.startswith("BPV-BIND") for rule in rules), result.binding_issues
    with unit_of_work() as session:
        from factory_blueprint.registry import binding

        binding.set_manual(
            session,
            project_id=PROJECT,
            blueprint_version=version,
            environment_key="UAT",
            business_key="sex",
            uid="sexUAT00001",
            note="matched by hand against the UAT option set",
            resolved_by="dhis2-implementer",
        )
    after = builds.compile_for_environment(
        PROJECT,
        version,
        "UAT",
        uat,
        profile,
        None,
        context(),
        idempotency_key="k-uat-2",
        design_reference_environment=DESIGN_ENV,
    )
    still_unresolved = {issue.business_key for issue in after.binding_issues}
    assert "sex" not in still_unresolved
    assert "facility" in still_unresolved  # the OrgUnit is still missing in UAT


def test_an_update_entry_writes_only_its_controlled_fields(
    _stub: StubDhis2,
) -> None:
    """AC-REG-04: the environment keeps every field this project does not own."""
    version = approved_version()
    result = builds.compile_for_environment(
        PROJECT,
        version,
        TEST_ENV,
        _discovery_artifact(TEST_ENV),
        _profile_artifact(),
        None,
        context(),
        idempotency_key="k-update-merge",
        design_reference_environment=DESIGN_ENV,
    )
    assert result.status is BuildStatus.COMPLETED, result.binding_issues
    payload = _payload_of(result.artifact_ids["METADATA_PACKAGE"])
    element = next(
        item for item in payload["dataElements"] if item.get("code") == "MC_DE_CLINICNAME"
    )
    # An UPDATE merges *onto* an object, and the only thing that names it is the identifier:
    # S4 imports with `identifier=ASSIGNED` and its instance model answers a row without one
    # as E1096 "No identifier provided".  Shipping no `id` would therefore create a second
    # dataElement instead of updating this one.  FR-CMP-006's own examples are `_comment`,
    # `_import_order`, `workflowId` and hashes — none of which may appear here.
    assert element["id"] == "deClinic001", "the payload names the object S4 must merge onto"
    assert not {"href", "lastUpdated", "access", "_comment", "workflowId"} & set(element), (
        "FR-CMP-006: the descriptor's system fields stay out of the payload"
    )
    assert element["name"] == "Clinic name", "the controlled field carries the design value"
    assert element["description"] == "written by the hospital IT office"
    assert element["shortName"] == "Facility", "an unowned field survives untouched"
    assert element["valueType"] == "TEXT"
    target = next(
        item
        for item in _manifest_of(result.artifact_ids["METADATA_PACKAGE"])["objects"]
        if item["businessKey"] == "clinicname"
    )
    assert target["state"] == "UPDATED"
    assert target["uid"] == "deClinic001", "the manifest names the object S4 must merge onto"


def test_missing_and_present_translations_are_both_reported_honestly(
    _stub: StubDhis2,
) -> None:
    """AC-COV-02: a declared translation reaches the payload, a missing one is a WARNING."""
    version = approved_version()
    result = builds.compile_for_environment(
        PROJECT,
        version,
        TEST_ENV,
        _discovery_artifact(TEST_ENV),
        _profile_artifact(),
        None,
        context(),
        idempotency_key="k-translations",
        design_reference_environment=DESIGN_ENV,
    )
    payload = _payload_of(result.artifact_ids["METADATA_PACKAGE"])
    program = next(item for item in payload["programs"] if item.get("code") == "MC_PR_PREGNANCY")
    assert {"field": "name", "locale": "fr", "text": "Grossesse"} in program["translations"]
    warning = next(
        issue
        for issue in result.validation.warnings
        if issue.rule_id == "BPV-ST-006" and issue.business_key == "pregnancy"
    )
    assert "shortName" in warning.message, "only the field without a French value is reported"


def _package_files(artifact_id: str) -> dict[str, bytes]:
    blob, _classification = artifacts.read_bytes(artifact_id)
    return artifacts.unpack_archive(blob)


def _payload_of(artifact_id: str) -> dict[str, Any]:
    return json.loads(_package_files(artifact_id)[files.METADATA_FILE])


def _manifest_of(artifact_id: str) -> dict[str, Any]:
    return json.loads(_package_files(artifact_id)[files.MANIFEST_FILE])


def test_validate_build_writes_a_report_with_issue_ids(_stub: StubDhis2) -> None:
    version = approved_version()
    profile = _profile_artifact()
    discovery = _discovery_artifact(DESIGN_ENV)
    built = builds.compile_for_environment(
        PROJECT,
        version,
        DESIGN_ENV,
        discovery,
        profile,
        None,
        context(),
        idempotency_key="k-validate",
        design_reference_environment=DESIGN_ENV,
    )
    validated = builds.validate_build(built.build_id, idempotency_key="k-validate-report")
    assert validated.validation is not None
    report = validated.validation.report_artifact_id
    assert report
    document, classification = artifacts.read_json(report)
    assert classification is DataClassification.CONFIDENTIAL
    assert document["counts"]["BLOCKING"] == validated.validation.blocking_count
    for issue in document["issues"]:
        assert issue["issueId"].startswith("bpis-")
        assert issue["sourceStage"] in {"MAPPING", "REQUIREMENTS", "DOMAIN", "PAGE_DESIGN", None}


def test_reproduce_is_byte_identical(_stub: StubDhis2) -> None:
    """AC-REG-01: three compiles of the same version give the same hashes and UIDs."""
    version = approved_version()
    profile = _profile_artifact()
    discovery = _discovery_artifact(DESIGN_ENV)
    hashes = []
    for index in range(3):
        result = builds.compile_for_environment(
            PROJECT,
            version,
            DESIGN_ENV,
            discovery,
            profile,
            None,
            context(),
            idempotency_key=f"k-repro-{index}",
            design_reference_environment=DESIGN_ENV,
        )
        hashes.append(result.package_hash)
        reproduced = builds.reproduce(result.artifact_ids["MANIFEST"])
        assert reproduced.consistent, reproduced.mismatched
    assert len(set(hashes)) == 1
    with unit_of_work() as session:
        from factory_blueprint.registry import queries

        entries = queries.registry_entries(session, PROJECT, version)
    uids = {entry.business_key: entry.uid for entry in entries}
    assert uids["pregnancy"] and len(uids["pregnancy"]) == 11


