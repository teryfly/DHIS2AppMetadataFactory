"""Each of 17.3's nine handoff checks, proven to fire (BP-HO-001 … 003).

A completeness check that never triggers is indistinguishable from one that was never
written, so each rule here is provoked by changing exactly one thing about an otherwise
passing package.  The page is only ``FRONTEND_READY`` when none of them fires — which is
what makes the second assertion in each pair meaningful rather than decoration.

These are pure: the package's files, the compiled products and the deployment evidence
the caller hands in are the whole input (BP-HO-002), no database and no instance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from factory_blueprint.compilers import fixture as fixture_mod
from factory_blueprint.compilers import openapi as openapi_mod
from factory_blueprint.compilers import package as package_mod
from factory_blueprint.compilers import ui_spec as uispec_mod
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.dto.build import BuildContext
from factory_blueprint.enums import IssueLevel
from factory_blueprint.s4 import contract
from factory_blueprint.s4.stub import profile_document
from factory_blueprint.validators import handoff
from tests.unit import helpers

PROJECT = "p1"
VERSION = "1.0.0"
ENVIRONMENT = "TEST"
PAGE = "clinic-list"


def _inputs(
    document: dict[str, Any],
    *,
    bindings: Mapping[str, str] | None = None,
    profile: contract.CapabilityProfile | None = None,
) -> CompileInputs:
    return CompileInputs(
        project_id=PROJECT,
        blueprint_version=VERSION,
        blueprint_hash=helpers.BASE_HASH,
        document=document,
        build_context=BuildContext(
            built_at=datetime(2026, 5, 5, tzinfo=UTC), seed=3, requester="tester"
        ),
        blueprint_artifact_id="art-01BLUEPRINT00000000000000",
        profile=profile if profile is not None else contract.profile_from_json(profile_document()),
        environment_key=ENVIRONMENT,
        bindings=dict(bindings or {"clinic": "deClinic001"}),
        design_environment="DEV",
    )


def _page(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "pageKey": PAGE,
        "name": "Clinics",
        "purpose": "show clinics",
        "routes": ["/clinics"],
        "useCases": ["uc-clinic"],
        "actors": ["midwife"],
        "registryRefs": ["clinic"],
        "source": {"decisionId": "MAP-001"},
    }
    entry.update(overrides)
    return entry


def _intent(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "intentKey": "read-clinic",
        "pageKey": PAGE,
        "operationKey": "readClinics",
        "purpose": "read the clinics",
        "resource": "metadata.dataElement",
        "method": "GET",
        "fields": ["id", "name"],
        "registryRefs": ["clinic"],
        "source": {"decisionId": "MAP-001"},
    }
    entry.update(overrides)
    return entry


def _ui_intent(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "intentKey": "ui-1",
        "pageKey": PAGE,
        "route": "/clinics",
        "purpose": "the clinic table",
        "components": [
            {
                "name": "DataTable",
                "use": "rows",
                "dataSource": {"operationKey": "readClinics"},
            },
            {
                "name": "Input",
                "use": "filter",
                "dataSource": {"businessKey": "clinic"},
            },
        ],
        "states": list(uispec_mod.PAGE_STATES),
        "fieldMappings": [
            {
                "field": "name",
                "businessKey": "clinic",
                "valueType": "TEXT",
                "label": {"default": "Name", "translations": {"fr": "Nom"}},
            }
        ],
        "translationKeys": [f"{PAGE}.name"],
        "source": {"decisionId": "MAP-001"},
    }
    entry.update(overrides)
    return entry


def _registry() -> list[dict[str, Any]]:
    return [
        helpers.registry_entry("clinic", "dataElement", "CREATE", uid="deClinic001"),
        helpers.registry_entry("person", "trackedEntityType", "CREATE", uid="tePerson001"),
    ]


def _document(
    *,
    pages: Sequence[dict[str, Any]] | None = None,
    intents: Sequence[dict[str, Any]] | None = None,
    ui_intents: Sequence[dict[str, Any]] | None = None,
    fixtures: Sequence[dict[str, Any]] | None = None,
    registry: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    document = helpers.empty_document()
    document["registry"] = {"entries": list(registry if registry is not None else _registry())}
    document["pages"] = {"items": list(pages if pages is not None else [_page()])}
    document["apiIntents"] = {"items": list(intents if intents is not None else [_intent()])}
    document["uiIntents"] = {"items": list(ui_intents if ui_intents is not None else [_ui_intent()])}
    document["fixtures"] = {"specifications": list(fixtures or []), "demoUsers": []}
    return document


def _evidence(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "artifactId": "art-01EVIDENCE0000000000000",
        "environmentKey": ENVIRONMENT,
        "environmentClass": "TEST",
        "blueprintVersion": VERSION,
        "deployed": True,
        "deployedAt": "2026-05-05T00:00:00Z",
        "postVerification": "SUCCESS",
    }
    entry.update(overrides)
    return entry


def _check(
    document: dict[str, Any] | None = None,
    *,
    evidence: Sequence[dict[str, Any]] = (),
    fixtures: Sequence[dict[str, Any]] | None = None,
    profile: contract.CapabilityProfile | None = None,
) -> tuple[handoff.HandoffReport, package_mod.PackageProduct]:
    inputs = _inputs(
        document if document is not None else _document(fixtures=fixtures), profile=profile
    )
    objects = {"clinic": {"valueType": "TEXT"}}
    openapi = openapi_mod.compile_openapi(inputs, objects)
    ui = uispec_mod.compile_ui_spec(
        inputs, [operation["operationKey"] for operation in openapi.operations]
    )
    fixture_product = fixture_mod.compile_fixtures(inputs, objects) if fixtures else None
    package = package_mod.compile_package(
        inputs,
        openapi,
        ui,
        fixture_product,
        [package_mod.TargetEnvironment(environment_key=ENVIRONMENT, bindings=dict(inputs.bindings))],
        list(evidence),
    )
    report = handoff.check_handoff(inputs, package, openapi, ui, fixture_product, list(evidence))
    return report, package


def _rules(report: handoff.HandoffReport) -> set[str]:
    return {issue.rule_id for issue in report.issues}


# --- the passing case ------------------------------------------------------


def test_a_complete_package_marks_its_page_frontend_ready() -> None:
    report, _package = _check(evidence=[_evidence()])
    assert report.issues == [], [i.message for i in report.issues]
    assert report.pages == {PAGE: handoff.FRONTEND_READY}
    assert report.ready_pages == [PAGE]
    assert report.document["counts"]["FRONTEND_READY"] == 1


# --- 1 … 3: page spec, examples, field mappings ----------------------------


def test_a_page_without_a_ui_intent_is_not_ready() -> None:
    document = _document(ui_intents=[])
    report, _package = _check(document, evidence=[_evidence()])
    assert handoff.RULE_PAGE_INCOMPLETE in _rules(report)
    assert report.pages[PAGE] == handoff.NOT_READY


def test_an_unresolved_operation_key_leaves_the_page_without_a_data_source() -> None:
    document = _document(intents=[_intent(resource="metadata.nope")])
    report, _package = _check(document, evidence=[_evidence()])
    assert handoff.RULE_PAGE_INCOMPLETE in _rules(report)


def test_an_input_component_without_a_field_mapping_is_blocked() -> None:
    document = _document(ui_intents=[_ui_intent(fieldMappings=[])])
    report, _package = _check(document, evidence=[_evidence()])
    assert handoff.RULE_FIELD_UNMAPPED in _rules(report)


def test_a_request_body_needs_an_example_on_both_sides() -> None:
    document = _document(intents=[_intent(requestBodyShape=[{"field": "name", "required": True}])])
    report, package = _check(document, evidence=[_evidence()])
    # the compiler emits the example, so the check passes and proves the rule is live by
    # the one case below where a file is genuinely absent
    assert handoff.RULE_EXAMPLE_MISSING not in _rules(report)
    del package.files["api/examples/readClinics.json"]
    inputs = _inputs(document)
    openapi = openapi_mod.compile_openapi(inputs, {"clinic": {"valueType": "TEXT"}})
    ui = uispec_mod.compile_ui_spec(
        inputs, [operation["operationKey"] for operation in openapi.operations]
    )
    again = handoff.check_handoff(inputs, package, openapi, ui, None, [_evidence()])
    assert handoff.RULE_EXAMPLE_MISSING in _rules(again)


# --- 4 and 7: deployment and fixture evidence (BP-HO-002) ------------------


def test_no_deployment_evidence_means_no_page_is_ready() -> None:
    report, _package = _check(evidence=[])
    assert handoff.RULE_NOT_DEPLOYED in _rules(report)
    assert report.pages[PAGE] == handoff.NOT_READY


def test_a_failed_post_verification_does_not_certify_the_page() -> None:
    report, _package = _check(evidence=[_evidence(postVerification="FAILED")])
    assert handoff.RULE_NOT_DEPLOYED in _rules(report)


def test_production_evidence_alone_does_not_count_as_deployed_for_handoff() -> None:
    """17.3 item 4 asks for a non-production target, so PROD proves nothing here."""
    report, _package = _check(evidence=[_evidence(environmentClass="PROD")])
    assert handoff.RULE_NOT_DEPLOYED in _rules(report)


def test_a_fixture_project_without_an_import_in_dev_or_test_is_blocked() -> None:
    fixtures = [
        {
            "businessKey": "people",
            "targetKey": "person",
            "count": 1,
            "attributeValues": [{"attributeKey": "clinic", "strategy": "prefix", "prefix": "c-"}],
            "source": {"decisionId": "MAP-001"},
        }
    ]
    report, _package = _check(evidence=[_evidence()], fixtures=fixtures)
    assert handoff.RULE_FIXTURES_NOT_IMPORTED in _rules(report)
    ready, _ = _check(evidence=[_evidence(fixtureImport="OK")], fixtures=fixtures)
    assert ready.pages[PAGE] == handoff.FRONTEND_READY, [i.message for i in ready.issues]


# --- 5 and 6: the document itself, and the locales -------------------------


def test_a_deprecated_endpoint_is_reported_even_though_it_resolves() -> None:
    profile = contract.profile_from_json(
        profile_document(
            resources=[{"logicalName": "metadata.dataElement", "path": "dataElements", "method": "GET"}],
            deprecated=[{"kind": "endpoint", "name": "/dataElements"}],
        )
    )
    report, _package = _check(evidence=[_evidence()], profile=profile)
    assert handoff.RULE_DEPRECATED_ENDPOINT in _rules(report)


def test_a_missing_translation_key_keeps_the_page_from_being_ready() -> None:
    """fr is a declared language; a key nobody translated is a BLOCKING handoff gap."""
    document = _document(
        ui_intents=[_ui_intent(translationKeys=[f"{PAGE}.name", f"{PAGE}. untranslated"])]
    )
    report, _package = _check(document, evidence=[_evidence()])
    assert handoff.RULE_TRANSLATION_INCOMPLETE in _rules(report)
    assert report.pages[PAGE] == handoff.NOT_READY


def test_declaring_no_translation_key_leaves_the_locales_complete() -> None:
    document = _document(ui_intents=[_ui_intent(translationKeys=[])])
    report, _package = _check(document, evidence=[_evidence()])
    assert handoff.RULE_TRANSLATION_INCOMPLETE not in _rules(report)


# --- 8: traceability, and 9: credentials -----------------------------------


def test_a_dangling_traceability_reference_is_blocked() -> None:
    # the page names a Registry entry that the version does not carry
    document = _document(pages=[_page(registryRefs=["ghost"])])
    report, _package = _check(document, evidence=[_evidence()])
    assert handoff.RULE_TRACEABILITY_DANGLING in _rules(report)


def test_a_credential_looking_value_in_the_package_is_blocked() -> None:
    report, package = _check(evidence=[_evidence()])
    assert report.pages[PAGE] == handoff.FRONTEND_READY
    # a hand-edited package file with a token in it must not pass the gate
    package.files["README.md"] = b"# x\nAuthorization: ApiToken abcdefghijklmnop\n"
    inputs = _inputs(_document())
    openapi = openapi_mod.compile_openapi(inputs, {"clinic": {"valueType": "TEXT"}})
    ui = uispec_mod.compile_ui_spec(
        inputs, [operation["operationKey"] for operation in openapi.operations]
    )
    again = handoff.check_handoff(inputs, package, openapi, ui, None, [_evidence()])
    assert handoff.RULE_CREDENTIAL_OR_PII in _rules(again)
    assert again.pages[PAGE] == handoff.NOT_READY


def test_documenting_the_token_scheme_is_not_mistaken_for_a_credential() -> None:
    """FR-API-005 *requires* `Authorization: ApiToken <token>`; a placeholder is not a secret."""
    report, package = _check(evidence=[_evidence()])
    blob = b"".join(package.files.values())
    assert b"ApiToken <token>" in blob
    assert handoff.RULE_CREDENTIAL_OR_PII not in _rules(report)


# --- report shape (BP-HO-003) ---------------------------------------------


def test_the_report_lists_every_check_and_the_pages_it_examined() -> None:
    report, _package = _check(evidence=[_evidence()])
    document = report.document
    assert document["kind"] == "HANDOFF_CHECK_REPORT"
    assert document["blueprintVersion"] == VERSION
    assert len(document["checks"]) == len(handoff._CHECK_DESCRIPTIONS)
    page = document["pages"][0]
    assert page["pageKey"] == PAGE and page["status"] == handoff.FRONTEND_READY
    assert "deploymentEvidence" in document and document["packageFiles"]


def test_each_check_has_a_level_and_the_report_counts_only_real_findings() -> None:
    report, _package = _check(evidence=[])
    assert all(issue.level is IssueLevel.BLOCKING for issue in report.issues)
    counts = report.document["counts"]
    assert counts["NOT_READY"] == 1 and counts["FRONTEND_READY"] == 0
