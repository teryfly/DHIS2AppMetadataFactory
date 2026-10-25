"""M3 compiler rules that need no database (BP-CMP-041…044, BP-HO).

The four new compilers are pure functions of a Blueprint document, the Capability
Profile and the bindings, so every decision they make — which endpoint an intent
becomes, what value a fixture row carries, whether a page may be handed off — is
testable here rather than only through an integration build.

The cases are chosen by what could go silently wrong in a delivered package: a shared
shape duplicated, a parameter named for the wrong version, a sensitive value that looks
real, a rule ID nobody emits.
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
from factory_blueprint.dto.issue import Issue
from factory_blueprint.enums import IssueLevel
from factory_blueprint.s4 import contract
from factory_blueprint.s4.stub import profile_document
from tests.unit import helpers

PROJECT = "p1"
VERSION = "1.0.0"
ENVIRONMENT = "TEST"


def _inputs(
    document: dict[str, Any],
    *,
    bindings: Mapping[str, str] | None = None,
    environment_key: str | None = None,
    profile: contract.CapabilityProfile | None = None,
) -> CompileInputs:
    return CompileInputs(
        project_id=PROJECT,
        blueprint_version=VERSION,
        blueprint_hash=helpers.BASE_HASH,
        document=document,
        build_context=BuildContext(
            built_at=datetime(2026, 5, 5, tzinfo=UTC), seed=7, requester="tester"
        ),
        blueprint_artifact_id="art-01BLUEPRINT00000000000000",
        profile=profile if profile is not None else contract.profile_from_json(profile_document()),
        environment_key=environment_key,
        bindings=dict(bindings or {}),
        design_environment="DEV",
    )


def _document(
    *,
    registry: Sequence[dict[str, Any]] = (),
    intents: Sequence[dict[str, Any]] = (),
    pages: Sequence[dict[str, Any]] = (),
    ui_intents: Sequence[dict[str, Any]] = (),
    fixtures: Sequence[dict[str, Any]] = (),
    demo_users: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    document = helpers.empty_document()
    document["registry"] = {"entries": list(registry)}
    document["apiIntents"] = {"items": list(intents)}
    document["pages"] = {"items": list(pages)}
    document["uiIntents"] = {"items": list(ui_intents)}
    document["fixtures"] = {"specifications": list(fixtures), "demoUsers": list(demo_users)}
    return document


def _blocking(issues: list[Issue], rule: str) -> list[Issue]:
    return [
        issue
        for issue in issues
        if issue.rule_id == rule and issue.level is IssueLevel.BLOCKING
    ]


# --- resource resolution (FR-API-001, FR-API-002) -------------------------


def _intent(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "intentKey": "read-clinic",
        "pageKey": "clinic-list",
        "operationKey": "readClinics",
        "purpose": "read the clinics",
        "resource": "metadata.dataElement",
        "method": "GET",
        "source": {"decisionId": "MAP-001"},
    }
    entry.update(overrides)
    return entry


def test_a_resource_the_profile_lacks_is_blocking_and_yields_no_operation() -> None:
    product = openapi_mod.compile_openapi(
        _inputs(_document(intents=[_intent(resource="metadata.nope")])), {}
    )
    assert [issue.rule_id for issue in product.issues] == [openapi_mod.RULE_RESOURCE_UNKNOWN]
    assert product.operations == []


def test_a_legacy_tracker_path_is_blocked_even_though_it_resolves() -> None:
    profile = contract.profile_from_json(
        profile_document(
            resources=[
                {"logicalName": "metadata.dataElement", "path": "dataElements", "method": "GET"},
                {
                    "logicalName": "metadata.trackedEntityInstances",
                    "path": "trackedEntityInstances",
                    "method": "GET",
                },
            ]
        )
    )
    product = openapi_mod.compile_openapi(
        _inputs(
            _document(intents=[_intent(resource="metadata.trackedEntityInstances")]),
            profile=profile,
        ),
        {},
    )
    assert _blocking(product.issues, openapi_mod.RULE_LEGACY_TRACKER)


def test_an_endpoint_the_profile_marks_removed_is_blocked() -> None:
    profile = contract.profile_from_json(
        profile_document(
            resources=[{"logicalName": "metadata.dataElement", "path": "dataElements", "method": "GET"}],
            removed=[{"kind": "endpoint", "name": "/dataElements"}],
        )
    )
    product = openapi_mod.compile_openapi(_inputs(_document(intents=[_intent()]), profile=profile), {})
    assert _blocking(product.issues, openapi_mod.RULE_REMOVED_ENDPOINT)


def test_the_tracker_route_a_profile_flags_is_accepted() -> None:
    profile = contract.profile_from_json(
        profile_document(
            resources=[
                {
                    "logicalName": "tracker.trackedEntities",
                    "path": "tracker/trackedEntities",
                    "method": "GET",
                    "trackerApi": True,
                }
            ]
        )
    )
    product = openapi_mod.compile_openapi(
        _inputs(_document(intents=[_intent(resource="tracker.trackedEntities")]), profile=profile),
        {},
    )
    assert product.issues == [] or not _blocking(product.issues, openapi_mod.RULE_LEGACY_TRACKER)
    assert product.operations[0]["path"] == "/tracker/trackedEntities"


# --- parameters (FR-API-003) ----------------------------------------------


def test_a_parameter_this_version_lacks_is_blocked_with_what_it_offers() -> None:
    profile = contract.profile_from_json(
        profile_document(
            resources=[
                {
                    "logicalName": "metadata.dataElement",
                    "path": "dataElements",
                    "method": "GET",
                    "parameters": {"id": "id", "orgUnit": "ou"},
                }
            ]
        )
    )
    product = openapi_mod.compile_openapi(
        _inputs(
            _document(intents=[_intent(queryParams={"nope": {"from": "user"}})]), profile=profile
        ),
        {},
    )
    finding = _blocking(product.issues, openapi_mod.RULE_PARAM_UNKNOWN)
    assert finding, [i.message for i in product.issues]
    assert "offered" in finding[0].details


def test_a_multi_uid_parameter_is_one_comma_separated_value() -> None:
    product = openapi_mod.compile_openapi(
        _inputs(
            _document(intents=[_intent(queryParams={"id": {"from": "route", "multiple": True}})])
        ),
        {},
    )
    parameter = next(
        item for item in product.operations[0]["definition"]["parameters"] if item["name"] == "id"
    )
    assert parameter["schema"]["type"] == "array"
    assert parameter["style"] == "form" and parameter["explode"] is False


def test_a_paging_intent_adds_the_paging_query_parameters() -> None:
    product = openapi_mod.compile_openapi(
        _inputs(_document(intents=[_intent(paging={"strategy": "page"})])), {}
    )
    names = {item["name"] for item in product.operations[0]["definition"]["parameters"]}
    assert {"page", "pageSize"} <= names


def test_a_profile_that_documents_no_parameters_leaves_the_name_alone() -> None:
    profile = contract.profile_from_json(
        profile_document(
            resources=[
                {"logicalName": "metadata.dataElement", "path": "dataElements", "method": "GET"}
            ]
        )
    )
    product = openapi_mod.compile_openapi(
        _inputs(
            _document(intents=[_intent(queryParams={"ou": {"from": "route"}})]), profile=profile
        ),
        {},
    )
    assert not _blocking(product.issues, openapi_mod.RULE_PARAM_UNKNOWN)


# --- shared shapes (FR-API-004) and examples ------------------------------


def test_two_identical_shapes_become_one_component() -> None:
    intents = [
        _intent(),
        _intent(intentKey="other", operationKey="otherClinics"),
    ]
    product = openapi_mod.compile_openapi(
        _inputs(_document(intents=intents)),
        {"clinic": {"valueType": "TEXT"}},
    )
    schemas = product.document["components"]["schemas"]
    assert len(schemas) == 1, sorted(schemas)
    for operation in product.operations:
        schema = operation["definition"]["responses"]["200"]["schema"]
        assert schema["$ref"].startswith("#/components/schemas/")


def test_a_single_use_shape_stays_inline() -> None:
    product = openapi_mod.compile_openapi(_inputs(_document(intents=[_intent()])), {})
    assert "schemas" not in product.document["components"]
    assert product.operations[0]["definition"]["responses"]["200"]["schema"]["type"] == "object"


def test_a_shared_shape_is_named_from_camel_case_words() -> None:
    name = openapi_mod._component_name("response:readPatientData", ["a", "b"])
    assert name == "ReadPatientDataResult"


def test_an_option_set_becomes_an_enum_and_the_example_picks_its_first_code() -> None:
    intent = _intent(fields=["bloodType"], registryRefs=["blood"])
    objects = {
        "blood": {
            "valueType": "TEXT",
            "optionSet": {"id": "bloodOptions"},
        },
        "bloodOptions": {"options": [{"code": "AB-"}, {"code": "O+"}]},
    }
    product = openapi_mod.compile_openapi(_inputs(_document(intents=[intent])), objects)
    operation = product.operations[0]
    assert operation["responseExample"] == {"bloodType": "AB-"}


def test_examples_are_generated_for_every_declared_value_type() -> None:
    for value_type, schema in openapi_mod.VALUE_TYPE_SCHEMAS.items():
        example = openapi_mod._example_of(dict(schema))
        assert example is not None, value_type
    assert openapi_mod._example_of({"type": "string", "format": "date"}) == "2026-01-01"
    assert openapi_mod._example_of({"enum": ["a", "b"]}) == "a"


# --- registry references (FR-API-005, BP-CMP-028) -------------------------


def test_an_unregistered_reference_is_blocked() -> None:
    product = openapi_mod.compile_openapi(
        _inputs(_document(intents=[_intent(registryRefs=["ghost"])])), {}
    )
    assert _blocking(product.issues, openapi_mod.RULE_REGISTRY_REF_UNKNOWN)


def test_an_unbound_reference_keeps_the_baseline_placeholder() -> None:
    """A baseline product must not carry the design environment's number (D-02)."""
    registry = [helpers.registry_entry("clinic", "dataElement", "CREATE", uid="deClinic001")]
    product = openapi_mod.compile_openapi(
        _inputs(_document(registry=registry, intents=[_intent(registryRefs=["clinic"])])), {}
    )
    ref = product.operations[0]["registryRefs"][0]
    assert ref["businessKey"] == "clinic"
    assert ref["environmentDependent"] is True
    assert ref["uid"] == openapi_mod.placeholder("clinic")


def test_a_page_with_no_resolved_operation_is_blocked() -> None:
    page = {
        "pageKey": "lonely",
        "name": "Lonely",
        "purpose": "p",
        "routes": ["/lonely"],
        "useCases": ["uc"],
        "actors": ["a"],
        "registryRefs": [],
        "source": {"decisionId": "MAP-001"},
    }
    product = openapi_mod.compile_openapi(_inputs(_document(pages=[page])), {})
    assert _blocking(product.issues, openapi_mod.RULE_PAGE_WITHOUT_INTENT)


def test_no_intents_at_all_produces_an_empty_document_not_an_error() -> None:
    product = openapi_mod.compile_openapi(_inputs(_document()), {})
    assert product.issues == []
    assert product.document["paths"] == {}, "3.1 requires `paths` to be present"
    assert "schemas" not in product.document["components"]


# --- fixtures (FR-POL-006/008, FR-DOM-001) --------------------------------


def _spec(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "businessKey": "people",
        "targetKey": "person",
        "count": 2,
        "source": {"decisionId": "MAP-001"},
    }
    entry.update(overrides)
    return entry


def _fixture_inputs(fixtures: Sequence[dict[str, Any]], **kwargs: Any) -> CompileInputs:
    registry = kwargs.pop("registry", None) or [
        helpers.registry_entry("person", "trackedEntityType", "CREATE", uid="tePerson001"),
        helpers.registry_entry("phone", "attribute", "CREATE", uid="atPhone00001"),
        helpers.registry_entry("parity", "attribute", "CREATE", uid="atParity0001"),
        helpers.registry_entry("blood", "attribute", "CREATE", uid="atBlood0001"),
        helpers.registry_entry("clinic", "organisationUnit", "CREATE", uid="ouClinic0001"),
    ]
    document = _document(registry=registry, fixtures=fixtures)
    bindings = {str(entry["businessKey"]): str(entry.get("uid") or "") for entry in registry}
    return _inputs(
        document, bindings=bindings, environment_key=ENVIRONMENT, **kwargs
    )


def _entities(product: fixture_mod.FixtureProduct) -> list[dict[str, Any]]:
    return list(product.payload["data"]["trackedEntities"])


def test_a_target_outside_the_registry_is_blocked_rather_than_guessed() -> None:
    product = fixture_mod.compile_fixtures(_fixture_inputs([_spec(targetKey="ghost")]), {})
    assert _blocking(product.issues, fixture_mod.RULE_TARGET_UNKNOWN)
    assert _entities(product) == []


def test_an_unknown_value_strategy_is_blocked_not_defaulted() -> None:
    spec = _spec(attributeValues=[{"attributeKey": "phone", "strategy": "telepathy"}])
    product = fixture_mod.compile_fixtures(_fixture_inputs([spec]), {})
    assert _blocking(product.issues, fixture_mod.RULE_STRATEGY_UNKNOWN)


def test_an_attribute_outside_the_registry_is_blocked() -> None:
    spec = _spec(attributeValues=[{"attributeKey": "ghost", "strategy": "prefix"}])
    product = fixture_mod.compile_fixtures(_fixture_inputs([spec]), {})
    assert _blocking(product.issues, fixture_mod.RULE_ATTRIBUTE_UNKNOWN)


def test_the_prefix_strategy_numbers_within_its_own_spec() -> None:
    spec = _spec(attributeValues=[{"attributeKey": "phone", "strategy": "prefix", "prefix": "p-"}])
    product = fixture_mod.compile_fixtures(_fixture_inputs([spec]), {})
    values = [entity["attributes"][0]["value"] for entity in _entities(product)]
    assert values == ["p-1", "p-2"]


def test_a_sensitive_attribute_is_marked_in_the_value_itself() -> None:
    """FR-DOM-001: a demo record must not be mistakable for a real person's data."""
    spec = _spec(attributeValues=[{"attributeKey": "phone", "strategy": "prefix", "prefix": "+47"}])
    objects = {"phone": {"valueType": "TEXT", "sensitivity": "PHI_PII"}}
    product = fixture_mod.compile_fixtures(_fixture_inputs([spec]), objects)
    values = [entity["attributes"][0]["value"] for entity in _entities(product)]
    assert all(value.startswith(fixture_mod.SENSITIVE_PREFIX) for value in values), values


def test_a_numeric_range_and_weighted_options_stay_within_the_spec() -> None:
    spec = _spec(
        attributeValues=[
            {"attributeKey": "parity", "strategy": "range", "range": [1, 4]},
            {"attributeKey": "blood", "strategy": "optionWeights", "optionWeights": {"O+": 2}},
        ]
    )
    objects = {
        "parity": {"valueType": "INTEGER_POSITIVE"},
        "blood": {"optionSet": {"id": "bloodOptions"}},
        "bloodOptions": {"options": [{"code": "O+"}, {"code": "AB-"}]},
    }
    product = fixture_mod.compile_fixtures(_fixture_inputs([spec]), objects)
    for entity in _entities(product):
        by_key = {item["x-attribute-key"]: item["value"] for item in entity["attributes"]}
        assert 1 <= by_key["parity"] <= 4, by_key
        assert by_key["blood"] in {"O+", "AB-"}, by_key


def test_an_unbound_organisation_unit_is_blocked() -> None:
    spec = _spec(orgUnitKeys=["clinic"])
    profile = contract.profile_from_json(
        profile_document(
            resources=[
                {
                    "logicalName": "tracker.trackedEntities",
                    "path": "tracker/trackedEntities",
                    "method": "POST",
                    "trackerApi": True,
                }
            ]
        )
    )
    inputs = _inputs(
        _document(
            registry=[
                helpers.registry_entry("person", "trackedEntityType", "CREATE", uid="tePerson001"),
                helpers.registry_entry("clinic", "organisationUnit", "REFERENCE", observed_uid="ouX000000001"),
            ],
            fixtures=[spec],
        ),
        # bound in the design environment only: its observed UID is not this environment's
        bindings={"person": "tePerson001"},
        environment_key=ENVIRONMENT,
        profile=profile,
    )
    product = fixture_mod.compile_fixtures(inputs, {})
    assert _blocking(product.issues, fixture_mod.RULE_ORG_UNIT_UNBOUND)


def test_a_program_target_needs_its_entity_type_declared() -> None:
    registry = [
        helpers.registry_entry("anc", "program", "CREATE", uid="pgAnc00001"),
    ]
    tracker = contract.profile_from_json(
        profile_document(
            resources=[
                {
                    "logicalName": "tracker.trackedEntities",
                    "path": "tracker/trackedEntities",
                    "method": "POST",
                    "trackerApi": True,
                }
            ]
        )
    )
    inputs = _inputs(
        _document(registry=registry, fixtures=[_spec(targetKey="anc")]),
        bindings={"anc": "pgAnc00001"},
        environment_key=ENVIRONMENT,
        profile=tracker,
    )
    product = fixture_mod.compile_fixtures(inputs, {})
    assert _blocking(product.issues, fixture_mod.RULE_TARGET_UNKNOWN)


def test_a_program_that_declares_its_entity_type_enrolls_under_it() -> None:
    registry = [
        helpers.registry_entry("anc", "program", "CREATE", uid="pgAnc00001"),
        helpers.registry_entry("person", "trackedEntityType", "CREATE", uid="tePerson001"),
    ]
    inputs = _inputs(
        _document(registry=registry, fixtures=[_spec(targetKey="anc")]),
        bindings={entry["businessKey"]: entry["uid"] for entry in registry},
        environment_key=ENVIRONMENT,
    )
    objects = {"anc": {"trackedEntityType": {"id": "tePerson001"}}}
    product = fixture_mod.compile_fixtures(inputs, objects)
    entity = _entities(product)[0]
    assert entity["trackedEntityType"] == {"id": "tePerson001"}
    enrollment = product.payload["data"]["enrollments"][0]
    assert enrollment["program"] == {"id": "pgAnc00001"}
    assert enrollment["trackedEntity"] == entity["trackedEntityInstance"]


def test_the_temporary_instance_id_is_synthetically_marked() -> None:
    product = fixture_mod.compile_fixtures(_fixture_inputs([_spec(count=1)]), {})
    instance = _entities(product)[0]["trackedEntityInstance"]
    assert instance.startswith(fixture_mod.TEMP_ID_MARK)
    assert len(instance) <= 11


def test_a_boundary_case_is_recorded_as_info_not_silently_generated() -> None:
    spec = _spec(boundaryCases=["a value at the range maximum"])
    product = fixture_mod.compile_fixtures(_fixture_inputs([spec]), {})
    info = [issue for issue in product.issues if issue.level is IssueLevel.INFO]
    assert info and info[0].rule_id == fixture_mod.RULE_SENSITIVE_LITERAL


def test_no_fixture_specification_produces_no_product() -> None:
    product = fixture_mod.compile_fixtures(_fixture_inputs([]), {})
    assert product.requested is False
    assert product.files == {}


def test_the_seed_decides_a_weighted_pick_and_a_different_seed_does_not() -> None:
    spec = _spec(
        count=6,
        attributeValues=[
            {"attributeKey": "blood", "strategy": "optionWeights", "optionWeights": {"O+": 1, "AB-": 1}}
        ],
    )
    objects = {"blood": {"valueType": "TEXT"}}

    def drawn(seed: int) -> list[Any]:
        inputs = _fixture_inputs([spec])
        product = fixture_mod.compile_fixtures(
            CompileInputs(
                **{
                    **inputs.__dict__,
                    "build_context": BuildContext(
                        built_at=datetime(2026, 5, 5, tzinfo=UTC), seed=seed, requester="tester"
                    ),
                }
            ),
            objects,
        )
        return [entity["attributes"][0]["value"] for entity in _entities(product)]

    same_run = drawn(7)
    assert same_run == drawn(7), "identical inputs, identical values"
    assert len(set(same_run)) > 1, "the weighted pool is actually consulted"
    assert drawn(99) != same_run, "a different seed draws a different dataset"


def test_a_profile_without_a_tracker_route_blocks_a_fixture_project() -> None:
    profile = contract.profile_from_json(
        profile_document(resources=[{"logicalName": "metadata.dataElement", "path": "dataElements"}])
    )
    product = fixture_mod.compile_fixtures(_fixture_inputs([_spec()], profile=profile), {})
    assert _blocking(product.issues, fixture_mod.RULE_NO_TRACKER_IMPORT)


def test_a_zero_count_makes_no_records() -> None:
    product = fixture_mod.compile_fixtures(_fixture_inputs([_spec(count=0)]), {})
    assert _entities(product) == []
    assert product.requested is True


# --- UI specification -----------------------------------------------------


def _page(page_key: str = "clinic-list", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "pageKey": page_key,
        "name": "Clinics",
        "purpose": "show clinics",
        "routes": ["/clinics"],
        "useCases": ["uc-clinic"],
        "actors": ["midwife"],
        "registryRefs": [],
        "source": {"decisionId": "MAP-001"},
    }
    entry.update(overrides)
    return entry


def _ui_intent(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "intentKey": "ui-1",
        "pageKey": "clinic-list",
        "route": "/clinics",
        "purpose": "show clinics",
        "components": [{"name": "DataTable", "use": "rows"}],
        "states": ["READY"],
        "fieldMappings": [],
        "source": {"decisionId": "MAP-001"},
    }
    entry.update(overrides)
    return entry


def test_a_component_without_a_data_source_is_still_described() -> None:
    product = uispec_mod.compile_ui_spec(
        _inputs(_document(pages=[_page()], ui_intents=[_ui_intent(components=[{"name": "Divider", "use": "spacing"}])]))
    )
    component = product.pages["clinic-list"]["components"][0]
    assert component["library"] == "@dhis2/ui"
    assert "dataSource" not in component


def test_a_data_source_is_named_with_its_page_own_operation_or_object() -> None:
    intents = [_intent()]
    ui = _ui_intent(
        components=[
            {"name": "DataTable", "use": "rows", "dataSource": {"operationKey": "readClinics"}},
            {"name": "Input", "use": "x", "dataSource": {"businessKey": "ghost"}},
        ]
    )  # the page entry below is what makes pages["clinic-list"] exist
    product = uispec_mod.compile_ui_spec(
        _inputs(_document(pages=[_page()], intents=intents, ui_intents=[ui]))
    )
    first, second = product.pages["clinic-list"]["components"]
    assert first["dataSource"]["declaredOnThisPage"] is True
    assert second["dataSource"]["registered"] is False
    assert second["dataSource"]["type"] == "registry"


def test_an_unregistered_field_mapping_is_flagged_without_inventing_a_uid() -> None:
    ui = _ui_intent(
        fieldMappings=[{"field": "name", "businessKey": "ghost", "valueType": "TEXT"}]
    )
    product = uispec_mod.compile_ui_spec(
        _inputs(_document(pages=[_page()], ui_intents=[ui]))
    )
    mapping = product.pages["clinic-list"]["fieldMappings"][0]
    assert mapping["registered"] is False
    assert mapping["uid"] == uispec_mod.placeholder("ghost")


def test_a_registered_field_mapping_carries_the_type_and_number() -> None:
    registry = [helpers.registry_entry("clinic", "dataElement", "CREATE", uid="deClinic001")]
    ui = _ui_intent(
        fieldMappings=[{"field": "name", "businessKey": "clinic", "valueType": "TEXT"}]
    )
    product = uispec_mod.compile_ui_spec(
        _inputs(
            _document(pages=[_page()], registry=registry, ui_intents=[ui]),
            bindings={"clinic": "deClinic001"},
        )
    )
    mapping = product.pages["clinic-list"]["fieldMappings"][0]
    assert mapping["dhis2Type"] == "dataElement"
    assert mapping["uid"] == "deClinic001"
    assert mapping["registered"] is True


def test_a_visible_when_clause_may_be_one_string_or_several() -> None:
    ui = _ui_intent(components=[{"name": "Input", "use": "u", "visibleWhen": "READY"}])
    product = uispec_mod.compile_ui_spec(
        _inputs(_document(pages=[_page()], ui_intents=[ui]))
    )
    assert product.pages["clinic-list"]["components"][0]["visibleWhen"] == ["READY"]


def test_a_page_without_a_ui_intent_says_so() -> None:
    page = {
        "pageKey": "naked",
        "name": "Naked",
        "purpose": "p",
        "routes": ["/naked"],
        "useCases": ["uc"],
        "actors": ["a"],
        "registryRefs": [],
        "source": {"decisionId": "MAP-001"},
    }
    product = uispec_mod.compile_ui_spec(_inputs(_document(pages=[page])))
    assert product.pages["naked"]["hasUiIntent"] is False
    assert set(product.pages["naked"]["missingStates"]) == set(uispec_mod.PAGE_STATES)


# --- the development package (FR-FE-001…006) ------------------------------


def _package(
    document: dict[str, Any],
    *,
    evidence: Sequence[dict[str, Any]] = (),
    bindings: Mapping[str, str] | None = None,
) -> package_mod.PackageProduct:
    inputs = _inputs(document, bindings=bindings, environment_key=ENVIRONMENT)
    openapi = openapi_mod.compile_openapi(inputs, {})
    ui = uispec_mod.compile_ui_spec(inputs)
    return package_mod.compile_package(
        inputs,
        openapi,
        ui,
        None,
        [package_mod.TargetEnvironment(environment_key=ENVIRONMENT, bindings=dict(bindings or {}))],
        list(evidence),
    )


def test_a_package_generated_without_evidence_says_nothing_is_deployed() -> None:
    product = _package(_document(intents=[_intent()], ui_intents=[_ui_intent()]))
    manifest = package_mod._deployment_manifest(
        _inputs(_document()),
        [package_mod.TargetEnvironment(environment_key=ENVIRONMENT)],
        [],
    )
    status = manifest["environments"][0]["deployment"]
    assert status["deployed"] is False
    assert "no deployment evidence" in status["note"]
    assert product.files  # the package itself is still complete


def test_evidence_for_another_version_is_reported_not_trusted() -> None:
    product = _package(
        _document(intents=[_intent()], ui_intents=[_ui_intent()]),
        evidence=[{"environmentKey": ENVIRONMENT, "blueprintVersion": "9.9.9", "artifactId": "art-x"}],
    )
    assert _blocking(product.issues, package_mod.RULE_EVIDENCE_VERSION_MISMATCH)


def test_an_environment_the_evidence_does_not_mention_is_warned_about() -> None:
    product = _package(
        _document(intents=[_intent()], ui_intents=[_ui_intent()]),
        evidence=[{"environmentKey": "OTHER", "blueprintVersion": VERSION}],
    )
    assert [issue.level for issue in product.issues] and any(
        issue.rule_id == package_mod.RULE_MISSING_EVIDENCE for issue in product.issues
    )


def test_registry_json_marks_an_entry_that_differs_between_environments() -> None:
    registry = [helpers.registry_entry("clinic", "dataElement", "CREATE", uid="deClinic001")]
    document = _document(registry=registry, intents=[_intent()], ui_intents=[_ui_intent()])
    inputs = _inputs(document, bindings={"clinic": "deClinic001"}, environment_key=ENVIRONMENT)
    openapi = openapi_mod.compile_openapi(inputs, {})
    ui = uispec_mod.compile_ui_spec(inputs)
    product = package_mod.compile_package(
        inputs,
        openapi,
        ui,
        None,
        [
            package_mod.TargetEnvironment(environment_key="TEST", bindings={"clinic": "deClinic001"}),
            package_mod.TargetEnvironment(environment_key="UAT", bindings={"clinic": "deOther00001"}),
        ],
        [],
    )
    assert product.registry_by_environment["TEST"]["clinic"]["uid"] == "deClinic001"
    assert product.registry_by_environment["UAT"]["clinic"]["uid"] == "deOther00001"
    # FR-FE-002 marks the entry itself, in the document a developer reads
    import json

    registry_json = json.loads(product.files[package_mod.REGISTRY_FILE])
    entry = next(item for item in registry_json["entries"] if item["businessKey"] == "clinic")
    assert entry["environmentDependent"] is True, registry_json
    assert entry["uid"] == {"TEST": "deClinic001", "UAT": "deOther00001"}


def test_the_environment_example_carries_no_credential() -> None:
    document = _document(intents=[_intent()], ui_intents=[_ui_intent()])
    example = package_mod._environment_example(
        _inputs(document), [package_mod.TargetEnvironment(environment_key=ENVIRONMENT)]
    )
    assert example["instance"]["baseUrl"].startswith("https://<")
    assert "token" not in str(example.get("authentication", {}).get("mode"))
    assert "note" in example


def test_demo_user_reference_tables_render_as_business_keys() -> None:
    assert fixture_mod._join_refs([{"businessKey": "a"}, {"businessKey": "b"}]) == "`a`, `b`"
    assert fixture_mod._join_refs(None) == ""


def test_a_data_source_label_shows_when_the_target_is_missing() -> None:
    missing_op = uispec_mod._data_source_label(
        {"type": "operation", "operationKey": "gone", "declaredOnThisPage": False}
    )
    assert "no such operationKey" in missing_op
    missing_registry = uispec_mod._data_source_label(
        {"type": "registry", "businessKey": "ghost", "registered": False}
    )
    assert "not in the Registry" in missing_registry
