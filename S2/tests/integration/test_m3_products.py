"""M3 products: OpenAPI, UI Specification, fixtures, development package, handoff.

These tests drive the public API the way S1 does — promote a version, compile it, ask
for the package — because the acceptance criteria are about what a caller receives:

* AC-API-01 … 03 — resources resolved by the Profile, no legacy Tracker endpoint, a
  `baseUrl` server, security schemes and per-operation Registry references;
* AC-FE-01 … 03 — the development package, its completeness result, and the absence of
  any credential;
* BP-CMP-043 / FR-POL-006/008 / FR-DOM-001 — fixtures are synthetic, seeded and marked;
* BP-AC-04's sibling in appendix B — a delivery-mode upgrade is a Minor new version.

The scenario project is the one `test_compile` builds (a pregnancy Tracker program); a
PAGE_DESIGN draft is layered on it to add the page, API intent, UI intent and fixture
sections, since a mode B project's app is designed after the mapping is approved.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import DataClassification, unit_of_work
from sqlalchemy import select

from factory_blueprint import api, artifacts, deps
from factory_blueprint.enums import AuthorKind, DeliveryMode, DesignStage, PatchStatus
from factory_blueprint.s4.stub import StubDhis2, profile_document
from factory_blueprint.testing.harness import BlueprintHarness
from tests.integration import test_compile as scenario
from tests.unit import helpers

PROJECT = scenario.PROJECT
PAGE_KEY = "patient-list"

pytestmark = pytest.mark.usefixtures("harness", "stub")


@pytest.fixture(autouse=True)
def stub() -> Iterator[StubDhis2]:
    instance = StubDhis2()
    deps.set_dhis2(instance)
    yield instance
    deps.reset_providers()


# --- the page design on top of the approved mapping -----------------------


def _page_operations() -> list[dict[str, Any]]:
    """A page, its two API intents (one shared shape), its UI intent and fixtures."""
    return [
        {
            "op": "add",
            "path": "/pages/items/-",
            "value": {
                "pageKey": PAGE_KEY,
                "name": "Patients",
                "purpose": "list the pregnancies a midwife follows",
                "routes": ["/patients"],
                "useCases": ["pregnancy"],
                "actors": ["midwife"],
                "apiIntentRefs": ["list-patients", "list-referrals"],
                "uiIntentRef": "ui-patients",
                "registryRefs": ["pregnancy", "clinicname"],
                "navigation": {"from": [PAGE_KEY], "to": [PAGE_KEY]},
                "acceptanceCriteria": ["the list shows the clinic's pregnancies"],
                "source": {"artifactId": "art-01INPUT0001", "decisionId": "MAP-001"},
            },
        },
        {
            "op": "add",
            "path": "/apiIntents/items/-",
            "value": {
                "intentKey": "list-patients",
                "pageKey": PAGE_KEY,
                "operationKey": "listPatients",
                "purpose": "read the pregnancies at this facility",
                "resource": "metadata.program",
                "method": "GET",
                "queryParams": {"ou": {"from": "route", "businessKey": "facility"}},
                "fields": ["id", "name", "status"],
                "paging": {"strategy": "page"},
                "registryRefs": ["pregnancy", "facility"],
                "source": {"artifactId": "art-01INPUT0001", "decisionId": "MAP-001"},
            },
        },
        # the same shape under a second operationKey: AC-API-02 says one shared component
        {
            "op": "add",
            "path": "/apiIntents/items/-",
            "value": {
                "intentKey": "list-referrals",
                "pageKey": PAGE_KEY,
                "operationKey": "listReferrals",
                "purpose": "read the referrals, which the page shows in the same columns",
                "resource": "metadata.optionSet",
                "method": "GET",
                "queryParams": {"ou": {"from": "route", "businessKey": "facility"}},
                "fields": ["id", "name", "status"],
                "paging": {"strategy": "page"},
                "registryRefs": ["pregnancy"],
                "source": {"artifactId": "art-01INPUT0001", "decisionId": "MAP-001"},
            },
        },
        {
            "op": "add",
            "path": "/uiIntents/items/-",
            "value": {
                "intentKey": "ui-patients",
                "pageKey": PAGE_KEY,
                "route": "/patients",
                "purpose": "the patients table and its filter",
                "layout": ["header", "table", "filter bar"],
                "components": [
                    {
                        "name": "DataTable",
                        "use": "show the pregnancies",
                        "dataSource": {"operationKey": "listPatients"},
                        "events": ["onRowClick"],
                        "visibleWhen": "the list is ready",
                        "accessibility": "caption names the columns",
                    },
                    {
                        "name": "Input",
                        "use": "filter by clinic name",
                        "dataSource": {"businessKey": "clinicname"},
                        "validation": ["at most 50 characters"],
                    },
                ],
                "states": [
                    "INITIAL",
                    "LOADING",
                    "READY",
                    "EMPTY",
                    "SUBMITTING",
                    "SUCCESS",
                    "VALIDATION_ERROR",
                    "API_ERROR",
                    "PERMISSION_DENIED",
                    "NETWORK_ERROR",
                ],
                "interactions": ["a row opens the detail page"],
                "fieldMappings": [
                    {
                        "field": "clinicName",
                        "businessKey": "clinicname",
                        "valueType": "TEXT",
                        "label": {"default": "Clinic name", "translations": {"fr": "Nom de la clinique"}},
                        "required": True,
                        "validation": ["maxLength 50"],
                    }
                ],
                "translationKeys": [f"{PAGE_KEY}.clinicName"],
                "testScenarios": ["an empty list renders the EMPTY state"],
                "source": {"artifactId": "art-01INPUT0001", "decisionId": "MAP-001"},
            },
        },
        {
            "op": "add",
            "path": "/fixtures/specifications/-",
            "value": {
                "businessKey": "patients-fixture",
                "targetKey": "person",
                "count": 3,
                "orgUnitKeys": ["facility"],
                "attributeValues": [
                    {"attributeKey": "clinicname", "strategy": "prefix", "prefix": "clinic-"},
                ],
                "source": {"artifactId": "art-01INPUT0001", "decisionId": "MAP-001"},
            },
        },
        {
            "op": "add",
            "path": "/fixtures/demoUsers/-",
            "value": {
                "businessKey": "midwife-account",
                "username": "demo_midwife",
                "displayName": "Demo midwife",
                "roleKeys": ["midwife"],
                "orgUnitScope": ["facility"],
            },
        },
    ]


def actor_version() -> str:
    """Register the midwife actor, which the DOMAIN stage owns (appendix A)."""
    base = scenario.approved_version()
    with unit_of_work() as session:
        draft_id = api.open_draft(PROJECT, DesignStage.DOMAIN, session=session)
    api.prepare_draft_base(PROJECT, draft_id)
    operations = [
        {
            "op": "add",
            "path": "/actors/items/-",
            "value": {
                "businessKey": "midwife",
                "name": {"default": "Midwife"},
                "description": {"default": "the community midwife who follows a pregnancy"},
                "source": {"artifactId": "art-01INPUT0001", "decisionId": "MAP-001"},
            },
        }
    ]
    return _design_stage(draft_id, base, operations, DesignStage.DOMAIN, "k-actor", "k-actor-promote")


def _design_stage(
    draft_id: str, base: str, operations: list[dict[str, Any]], stage: DesignStage, key: str, promote_key: str
) -> str:
    with unit_of_work() as session:
        outcome = api.submit_patch(
            PROJECT,
            draft_id,
            api.Patch(
                patch_id=key,
                base_version=base,
                author=api.PatchAuthor(kind=AuthorKind.AGENT, ref=f"run-{stage.value}"),
                decision_ids=["MAP-001"],
                operations=operations,
                rationale=f"{stage.value} design step",
            ),
            api.PatchContext(stage=stage),
            idempotency_key=key,
            session=session,
        )
        assert outcome.status is PatchStatus.PROPOSED, [i.message for i in outcome.issues]
    frozen = api.freeze_draft(draft_id, [scenario.DESIGN_ENV, scenario.TEST_ENV])
    assert frozen.promotable, [i.message for i in frozen.issues]
    with unit_of_work() as session:
        return api.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key=promote_key, session=session
        ).version


def designed_version() -> str:
    """The approved mapping and actor version, plus its page design promoted."""
    base = actor_version()
    with unit_of_work() as session:
        draft_id = api.open_draft(PROJECT, DesignStage.PAGE_DESIGN, session=session)
    api.prepare_draft_base(PROJECT, draft_id)
    with unit_of_work() as session:
        outcome = api.submit_patch(
            PROJECT,
            draft_id,
            api.Patch(
                patch_id="ptch-pages",
                base_version=base,
                author=api.PatchAuthor(kind=AuthorKind.AGENT, ref="run-page"),
                decision_ids=["MAP-001"],
                operations=_page_operations(),
                rationale="design the patient list page and its fixtures",
            ),
            api.PatchContext(stage=DesignStage.PAGE_DESIGN),
            idempotency_key="k-pages",
            session=session,
        )
        assert outcome.status is PatchStatus.PROPOSED, [i.message for i in outcome.issues]
    return _promote_page_design(draft_id)


def _promote_page_design(draft_id: str) -> str:
    # freeze is external I/O (it stages content), so it runs outside the transaction
    frozen = api.freeze_draft(draft_id, [scenario.DESIGN_ENV, scenario.TEST_ENV])
    assert frozen.promotable, [i.message for i in frozen.issues]
    with unit_of_work() as session:
        return api.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-pages-promote", session=session
        ).version


def _files_of(artifact_id: str) -> dict[str, bytes]:
    blob, _classification = artifacts.read_bytes(artifact_id)
    return artifacts.unpack_archive(blob)


def _read_json_document(artifact_id: str) -> dict[str, Any]:
    document, _classification = artifacts.read_json(artifact_id)
    return dict(document)


def _report_issues(artifact_id: str) -> list[dict[str, Any]]:
    return list(_read_json_document(artifact_id).get("issues") or [])


# --- AC-API-01 … 03: the OpenAPI product ---------------------------------


def test_baseline_compile_emits_openapi_and_ui_specification() -> None:
    version = designed_version()
    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-baseline-m3",
    )
    assert set(result.artifact_ids) >= {
        "METADATA_BASELINE",
        "OPENAPI",
        "UI_SPECIFICATION",
        "MANIFEST",
        "VALIDATION_REPORT",
    }, result.artifact_ids

    files = _files_of(result.artifact_ids["OPENAPI"])
    assert "api/openapi.yaml" in files and "api/api-reference.md" in files
    import yaml

    document = yaml.safe_load(files["api/openapi.yaml"])
    assert str(document["openapi"]).startswith("3.1")
    # AC-API-03: a baseUrl variable, the security schemes and the Registry references
    server = document["servers"][0]
    assert "baseUrl" in server["variables"] and "default" in server["variables"]["baseUrl"]
    assert set(document["components"]["securitySchemes"]) == {"apiToken", "basic", "appPlatformSession"}
    operation = document["paths"]["/programs"]["get"]
    assert operation["x-registry-refs"], "FR-API-005 requires the Registry references"
    assert operation["operationId"] == "listPatients"
    assert document["paths"]["/optionSets"]["get"]["operationId"] == "listReferrals"


def test_two_pages_sharing_a_shape_get_one_component_not_two_copies() -> None:
    """AC-API-02: identical shapes are one `$ref`, because copies drift."""
    version = designed_version()
    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-baseline-shared",
    )
    import yaml

    document = yaml.safe_load(_files_of(result.artifact_ids["OPENAPI"])["api/openapi.yaml"])
    components = document["components"]["schemas"]
    shared = [name for name, body in components.items() if body.get("properties", {}).get("objects")]
    assert len(shared) == 1, f"expected one shared shape, got {sorted(components)}"
    referenced = [
        document["paths"][path]["get"]["responses"]["200"]["schema"].get("$ref")
        for path in ("/programs", "/optionSets")
    ]
    assert referenced == [f"#/components/schemas/{shared[0]}"] * 2


def test_every_operation_key_has_a_request_or_response_example() -> None:
    version = designed_version()
    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-baseline-examples",
    )
    files = _files_of(result.artifact_ids["OPENAPI"])
    import json

    for key in ("listPatients", "listReferrals"):
        assert f"api/examples/{key}.json" in files, sorted(files)
        example = json.loads(files[f"api/examples/{key}.json"])
        assert example["response"] is not None, "17.3 requires a response example"


def test_a_resource_the_profile_does_not_offer_is_blocking_not_a_guess(stub: StubDhis2) -> None:
    """FR-API-001: S2 resolves endpoints from the Profile and refuses to invent one."""
    version = designed_version()
    document = _current_document(version)
    intent = document["apiIntents"]["items"][0]
    intent["resource"] = "metadata.somethingAbsent"
    _rewrite_version_document(version, document)

    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-baseline-unknown-resource",
    )
    report = result.artifact_ids["VALIDATION_REPORT"]
    rules = {issue["ruleId"] for issue in _report_issues(report)}
    assert "BPV-API-001" in rules


def _current_document(version: str) -> dict[str, Any]:
    with unit_of_work() as session:
        info = api.get_version(PROJECT, version, session=session)
    return _read_json_document(info.artifact_id)


def _rewrite_version_document(version: str, document: dict[str, Any]) -> None:
    """Replace a version's artifact content to drive a failure path.

    Only a test does this: a real Blueprint change goes through a draft and a Patch.
    """
    from factory_blueprint.models.version import BlueprintVersionRow

    handle = artifacts.stage_json(document, PROJECT, "BLUEPRINT")
    with unit_of_work() as session:
        row = session.execute(
            select(BlueprintVersionRow).where(
                BlueprintVersionRow.project_id == PROJECT,
                BlueprintVersionRow.version == version,
            )
        ).scalars().one()
        info = artifacts.register(
            handle,
            project_id=PROJECT,
            artifact_type="BLUEPRINT",
            data_classification=DataClassification.INTERNAL,
            source_artifact_ids=list(row.source_artifact_ids),
            session=session,
        )
        row.artifact_id = info.id
        row.content_hash = info.content_hash
        session.flush()


def _report_issues(artifact_id: str) -> list[dict[str, Any]]:
    from factory_blueprint import artifacts

    document, _classification = artifacts.read_json(artifact_id)
    return list(document.get("issues") or [])


# --- BP-CMP-010 / AC-API-01: the Tracker rule ----------------------------


def test_a_legacy_tracker_endpoint_is_refused_even_when_the_profile_offers_it() -> None:
    """FR-API-002 has no exception, so the prohibition is not Profile-relative."""
    version = designed_version()
    document = _current_document(version)
    intent = document["apiIntents"]["items"][0]
    intent["resource"] = "metadata.trackedEntityInstances"
    _rewrite_version_document(version, document)

    profile = scenario._s4_artifact(
        PROJECT,
        "CAPABILITY_PROFILE",
        profile_document(
            version="2.41",
            resources=[
                {"logicalName": "metadata.program", "path": "programs", "method": "GET"},
                {
                    "logicalName": "metadata.trackedEntityInstances",
                    "path": "trackedEntityInstances",
                    "method": "GET",
                },
            ],
        ),
    )
    result = api.compile_baseline(PROJECT, version, profile, scenario.context(), idempotency_key="k-legacy")
    rules = {issue["ruleId"] for issue in _report_issues(result.artifact_ids["VALIDATION_REPORT"])}
    assert "BPV-API-002" in rules


def test_two_intents_on_one_endpoint_are_blocked_not_silently_dropped() -> None:
    """An OpenAPI document holds one operation per path+method; overwriting would hide one."""
    version = designed_version()
    document = _current_document(version)
    document["apiIntents"]["items"][1]["resource"] = "metadata.program"
    _rewrite_version_document(version, document)
    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-baseline-collision",
    )
    rules = {issue["ruleId"] for issue in _report_issues(result.artifact_ids["VALIDATION_REPORT"])}
    assert "BPV-API-008" in rules


# --- BP-CMP-043: synthetic fixtures --------------------------------------


def test_environment_compile_produces_a_seeded_synthetic_fixture_package(
    stub: StubDhis2,
) -> None:
    version = designed_version()
    result = api.compile_for_environment(
        PROJECT,
        version,
        scenario.TEST_ENV,
        scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(),
        None,
        scenario.context(),
        idempotency_key="k-env-fixtures",
    )
    assert "FIXTURE_PACKAGE" in result.artifact_ids, result.artifact_ids
    files = _files_of(result.artifact_ids["FIXTURE_PACKAGE"])
    import json

    payload = json.loads(files["fixtures/demo-data.json"])
    assert payload["synthetic"] is True
    entities = payload["data"]["trackedEntities"]
    assert len(entities) == 3, "the specification asked for three records"
    values = {entity["attributes"][0]["value"] for entity in entities}
    assert values == {"clinic-1", "clinic-2", "clinic-3"}, "deterministic, per position"
    assert all(entity["orgUnit"] for entity in entities), "bound to this environment's UID"


def test_the_same_build_context_reproduces_the_fixtures_byte_for_byte(
    stub: StubDhis2,
) -> None:
    """FR-CMP-001: the seed comes from the caller, so the same seed gives the same bytes."""
    version = designed_version()
    first = api.compile_for_environment(
        PROJECT, version, scenario.TEST_ENV, scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(), None, scenario.context(), idempotency_key="k-fx-a",
    )
    second = api.compile_for_environment(
        PROJECT, version, scenario.TEST_ENV, scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(), None, scenario.context(), idempotency_key="k-fx-b",
    )
    # the MANIFEST of two distinct builds legitimately differs (it records that build);
    # what must be identical is the fixture product's own bytes
    assert first.output_hashes["FIXTURE_PACKAGE"] == second.output_hashes["FIXTURE_PACKAGE"]
    assert _files_of(first.artifact_ids["FIXTURE_PACKAGE"]) == _files_of(
        second.artifact_ids["FIXTURE_PACKAGE"]
    )


def test_demo_users_are_a_specification_and_never_a_credential(
    stub: StubDhis2,
) -> None:
    version = designed_version()
    result = api.compile_for_environment(
        PROJECT, version, scenario.TEST_ENV, scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(), None, scenario.context(), idempotency_key="k-fx-users",
    )
    files = _files_of(result.artifact_ids["FIXTURE_PACKAGE"])
    # the *word* appears, in the note saying credentials are distributed elsewhere; what
    # must never appear is a credential-shaped assignment (the same test BP-HO-110 runs)
    text = b"".join(files.values()).lower()
    for marker in (b'"password":', b"apitoken ", b"bearer ", b"jsessionid=", b"begin private key"):
        assert marker not in text, f"FR-COV-008 / FR-SEC-008: {marker!r} in a fixture product"
    import json

    spec = json.loads(files["fixtures/demo-users.json"])
    assert spec["users"][0]["username"] == "demo_midwife"
    assert "credential" in spec["users"][0]


# --- BP-CMP-044 / AC-FE-01 … 03: the development package -----------------


def _handoff_inputs(version: str, stub: StubDhis2) -> tuple[str, str]:
    """Compile for TEST so the package has a real environment to describe."""
    build = api.compile_for_environment(
        PROJECT,
        version,
        scenario.TEST_ENV,
        scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(),
        None,
        scenario.context(),
        idempotency_key="k-env-for-package",
    )
    evidence_id = scenario._s4_artifact(
        PROJECT,
        "EXECUTION_REPORT",
        {
            "schemaVersion": 1,
            "kind": "EXECUTION_REPORT",
            "environmentKey": scenario.TEST_ENV,
            "environmentClass": "TEST",
            "status": "SUCCESS",
            "payload": {"blueprintVersion": version},
            "completedAt": "2026-09-01T10:00:00Z",
            "steps": [
                {"step": "post_verify", "status": "SUCCESS"},
                {"step": "import_fixtures", "status": "SUCCESS"},
            ],
        },
    )
    return build.build_id, evidence_id


def test_generate_frontend_package_lays_out_the_17_1_tree(stub: StubDhis2) -> None:
    version = designed_version()
    _build_id, evidence_id = _handoff_inputs(version, stub)
    result = api.generate_frontend_package(
        PROJECT,
        version,
        [scenario.TEST_ENV],
        evidence_id,
        scenario.context(),
        idempotency_key="k-package-1",
    )
    assert set(result.artifact_ids) >= {"FRONTEND_PACKAGE", "HANDOFF_CHECK_REPORT", "MANIFEST"}
    files = _files_of(result.artifact_ids["FRONTEND_PACKAGE"])
    required = {
        "README.md",
        "PROJECT-CONTEXT.md",
        "ARCHITECTURE.md",
        "DEVELOPMENT-GUIDE.md",
        "PERMISSIONS.md",
        "ACCEPTANCE-CRITERIA.md",
        "api/openapi.yaml",
        "api/api-reference.md",
        f"pages/{PAGE_KEY}.md",
        "ui/ui-spec.json",
        "metadata/registry.json",
        "metadata/field-mapping.json",
        "metadata/option-sets.json",
        "metadata/program-rules.md",
        "metadata/manifest.json",
        "dhis2/environment.example.json",
        "dhis2/debugging.md",
        "traceability/source-map.json",
        "fixtures/demo-data.json",
    }
    missing = sorted(name for name in required if name not in files)
    assert missing == [], f"17.1 requires {missing}"
    assert any(name.startswith("i18n/") for name in files), sorted(files)


def test_registry_json_gives_the_uid_per_environment(stub: StubDhis2) -> None:
    """FR-FE-002: a developer reads the number for the environment they point at."""
    version = designed_version()
    _, evidence_id = _handoff_inputs(version, stub)
    result = api.generate_frontend_package(
        PROJECT, version, [scenario.TEST_ENV], evidence_id, scenario.context(),
        idempotency_key="k-package-registry",
    )
    import json

    registry = json.loads(_files_of(result.artifact_ids["FRONTEND_PACKAGE"])["metadata/registry.json"])
    assert scenario.TEST_ENV in registry["environmentKeys"]
    entries = {item["businessKey"]: item for item in registry["entries"]}
    assert "pregnancy" in entries
    assert entries["pregnancy"]["uid"][scenario.TEST_ENV], "a created object has a UID here"


def test_the_package_carries_no_credential_and_no_real_personal_data(stub: StubDhis2) -> None:
    version = designed_version()
    _, evidence_id = _handoff_inputs(version, stub)
    result = api.generate_frontend_package(
        PROJECT, version, [scenario.TEST_ENV], evidence_id, scenario.context(),
        idempotency_key="k-package-nocreds",
    )
    files = _files_of(result.artifact_ids["FRONTEND_PACKAGE"])
    blob = b"".join(files.values()).lower()
    # the package must *describe* `Authorization: ApiToken <token>` (FR-API-005), so what
    # is forbidden is a credential-shaped value, which is what BP-HO-110 tests for
    for marker in (b'"password":', b"jsessionid=", b"begin private key", b"bearer abcdefgh"):
        assert marker not in blob, f"AC-FE-03: {marker!r} must not be in a development package"
    assert b"apitoken <token>" in blob, "the authentication scheme must still be documented"


def test_handoff_report_marks_pages_ready_only_when_every_check_passes(stub: StubDhis2) -> None:
    """BP-HO-003 / AC-FE-01: FRONTEND_READY is earned per page, not asserted."""
    from factory_blueprint.validators import handoff

    version = designed_version()
    _, evidence_id = _handoff_inputs(version, stub)
    result = api.generate_frontend_package(
        PROJECT, version, [scenario.TEST_ENV], evidence_id, scenario.context(),
        idempotency_key="k-package-ready",
    )
    report = _read_json_document(result.artifact_ids["HANDOFF_CHECK_REPORT"])
    page0 = next(item for item in report["pages"] if item["pageKey"] == PAGE_KEY)
    assert page0["failedRules"] == [], [(i["ruleId"], i["message"][:140]) for i in page0["issues"]]
    assert result.handoff_pages.get(PAGE_KEY) == handoff.FRONTEND_READY, result.handoff_pages
    page = next(item for item in report["pages"] if item["pageKey"] == PAGE_KEY)
    assert page["failedRules"] == []
    assert report["counts"]["FRONTEND_READY"] == 1


def test_evidence_for_another_blueprint_version_is_refused(stub: StubDhis2) -> None:
    """BP-HO-002: deployment evidence certifies a version, and a mismatch is BLOCKING."""
    version = designed_version()
    _, _ = _handoff_inputs(version, stub)
    stale = scenario._s4_artifact(
        PROJECT,
        "EXECUTION_REPORT",
        {
            "kind": "EXECUTION_REPORT",
            "environmentKey": scenario.TEST_ENV,
            "environmentClass": "TEST",
            "status": "SUCCESS",
            "payload": {"blueprintVersion": "9.9.9"},
            "steps": [{"step": "post_verify", "status": "SUCCESS"}],
        },
    )
    result = api.generate_frontend_package(
        PROJECT, version, [scenario.TEST_ENV], stale, scenario.context(),
        idempotency_key="k-package-stale",
    )
    rules = {issue["ruleId"] for issue in _report_issues(result.artifact_ids["VALIDATION_REPORT"])}
    assert "BPV-HO-001" in rules
    assert result.handoff_pages == {} or all(
        state == "NOT_READY" for state in result.handoff_pages.values()
    )


def test_a_page_without_deployment_evidence_is_not_ready(stub: StubDhis2) -> None:
    from factory_blueprint.validators import handoff

    version = designed_version()
    # compile the environment so the package has real content, but hand over no evidence
    api.compile_for_environment(
        PROJECT, version, scenario.TEST_ENV, scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(), None, scenario.context(), idempotency_key="k-env-no-ev",
    )
    result = api.generate_frontend_package(
        PROJECT, version, [scenario.TEST_ENV], None, scenario.context(),
        idempotency_key="k-package-no-evidence",
    )
    assert result.handoff_pages.get(PAGE_KEY) == handoff.NOT_READY


def test_the_package_is_reproducible_from_the_same_build_context(stub: StubDhis2) -> None:
    version = designed_version()
    _, evidence_id = _handoff_inputs(version, stub)
    first = api.generate_frontend_package(
        PROJECT, version, [scenario.TEST_ENV], evidence_id, scenario.context(),
        idempotency_key="k-package-repro-a",
    )
    second = api.generate_frontend_package(
        PROJECT, version, [scenario.TEST_ENV], evidence_id, scenario.context(),
        idempotency_key="k-package-repro-b",
    )
    assert first.package_hash == second.package_hash, (first.package_hash, second.package_hash)


# --- UI Specification (BP-CMP-042) ---------------------------------------


def test_ui_specification_carries_every_10_3_element(stub: StubDhis2) -> None:
    version = designed_version()
    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-baseline-ui",
    )
    import json

    spec = json.loads(_files_of(result.artifact_ids["UI_SPECIFICATION"])["ui/ui-spec.json"])
    page = next(item for item in spec["pages"] if item["pageKey"] == PAGE_KEY)
    for element in (
        "route",
        "purpose",
        "layout",
        "components",
        "states",
        "interactions",
        "navigation",
        "fieldMappings",
        "translationKeys",
        "testScenarios",
        "acceptanceCriteria",
    ):
        assert element in page, f"10.3 requires {element}"
    # FR-UII-001: the design system is named where the component belongs to it
    libraries = {item["name"]: item["library"] for item in page["components"]}
    assert libraries["DataTable"] == "@dhis2/ui"
    # the field mapping names the Registry object and its value type (FR-FE-001)
    mapping = page["fieldMappings"][0]
    assert mapping["businessKey"] == "clinicname"
    assert mapping["dhis2Type"] == "dataElement"
    assert mapping["uid"] == "deClinic001", "an UPDATE entry carries the observed UID"


def test_a_state_the_ui_intent_omits_is_reported_not_invented(stub: StubDhis2) -> None:
    version = designed_version()
    document = _current_document(version)
    document["uiIntents"]["items"][0]["states"] = ["INITIAL", "READY"]
    _rewrite_version_document(version, document)
    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-ui-partial-states",
    )
    import json

    spec = json.loads(_files_of(result.artifact_ids["UI_SPECIFICATION"])["ui/ui-spec.json"])
    page = next(item for item in spec["pages"] if item["pageKey"] == PAGE_KEY)
    assert page["states"] == ["INITIAL", "READY"]
    assert "EMPTY" in page["missingStates"], "17.3 checks the state list against what is declared"


# --- delivery mode upgrade (SRS 7.3, appendix B) --------------------------


def test_mode_upgrade_a_to_b_creates_a_minor_new_version(harness: BlueprintHarness) -> None:
    from factory_blueprint import draft as draft_module

    handle = draft_module.prepare_initial_blueprint(PROJECT, helpers.project_info(DeliveryMode.A))
    with unit_of_work() as session:
        api.register_initial_blueprint(PROJECT, handle, session=session)
    before = _current_version()
    handle = api.prepare_mode_upgrade(PROJECT)
    with unit_of_work() as session:
        result = api.commit_mode_upgrade(PROJECT, handle, "k-mode-upgrade", session=session)
    assert result.change_level == "Minor", "appendix B: A -> B is a Minor change"
    assert result.version != before
    with unit_of_work() as session:
        semantics = api.get_project_semantics(PROJECT, session=session)
    assert semantics.delivery_mode.value == "B"
    assert DesignStage.PAGE_DESIGN in [DesignStage(item) for item in result.affected_stages]


def test_mode_upgrade_is_refused_while_a_draft_is_open(harness: BlueprintHarness) -> None:
    from factory_blueprint import draft as draft_module

    handle = draft_module.prepare_initial_blueprint(PROJECT, helpers.project_info(DeliveryMode.A))
    with unit_of_work() as session:
        api.register_initial_blueprint(PROJECT, handle, session=session)
    with unit_of_work() as session:
        api.open_draft(PROJECT, DesignStage.DOMAIN, session=session)
    with pytest.raises(api.BlueprintError):
        api.prepare_mode_upgrade(PROJECT)


def _current_version() -> str:
    with unit_of_work() as session:
        return api.get_current_version(PROJECT, session=session).version


def test_ui_specification_is_absent_for_a_mode_a_project(stub: StubDhis2) -> None:
    """Mode A ships no app, so baseline produces neither API nor UI product (10.1)."""
    from factory_blueprint import draft as draft_module

    handle = draft_module.prepare_initial_blueprint(PROJECT, helpers.project_info(DeliveryMode.A))
    with unit_of_work() as session:
        api.register_initial_blueprint(PROJECT, handle, session=session)
        version = api.get_current_version(PROJECT, session=session).version
    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-baseline-mode-a",
    )
    assert "OPENAPI" not in result.artifact_ids
    assert "UI_SPECIFICATION" not in result.artifact_ids


def test_package_manifest_lists_the_files_a_build_produced() -> None:
    """BP-CMP-021 through ``api``: UI-REV-003's by-file view reads the build record.

    The answer is the file list, its hashes and how many objects each file carries.  Object identity
    stays the Registry's answer, so the by-file and by-registry views of one build cannot drift
    apart, and no artifact bytes are read — which keeps the read inside S1's transaction.
    """
    version = designed_version()
    result = api.compile_baseline(
        PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-manifest-read",
    )
    with unit_of_work() as session:
        manifest = api.get_package_manifest(PROJECT, version, session=session)
        registry = api.get_registry(PROJECT, version, session=session)

    assert manifest.build_id == result.build_id
    assert manifest.kind == "BASELINE" and manifest.package_hash == result.package_hash
    paths = {item.path for item in manifest.files}
    assert {"metadata-baseline.json", "dependency-graph.json", "README.md"} <= paths, paths

    # the hash of every file is the one the build recorded for those very bytes
    recorded = {
        key.split("/", 1)[1]: value
        for key, value in result.output_hashes.items()
        if key.startswith("METADATA_BASELINE/")
    }
    assert {item.path: item.content_hash for item in manifest.files} == recorded

    payload = next(item for item in manifest.files if item.role == "payload")
    assert payload.object_count == len(registry), "the payload carries every object of the version"
    # a document holds no object: None, not zero, which would read as an empty package
    assert next(item for item in manifest.files if item.path == "README.md").object_count is None
    assert all(item.role for item in manifest.files)


def test_package_manifest_of_an_unbuilt_version_is_empty_not_invented() -> None:
    """Nothing was compiled, so nothing may be listed: a plausible layout would show files that
    do not exist and a hash the gate could not reproduce."""
    version = designed_version()
    with unit_of_work() as session:
        manifest = api.get_package_manifest(PROJECT, version, session=session)

    assert manifest.files == []
    assert manifest.build_id == "" and manifest.kind == "NONE"
    assert manifest.blueprint_version == version
