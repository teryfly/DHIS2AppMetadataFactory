"""The three validation layers and the ``VALIDATION_REPORT`` (S2 SRS 7.4, BP-VAL-001 … 031).

AC-VAL-01 (a missing reference, a wrong type or an unregistered object BLOCKS), BP-AC-09 (a
ProgramRule condition that does not parse, or that names an unknown businessKey, BLOCKS from
the local grammar — never from a server), AC-REG-02 (a malformed Registry UID BLOCKS),
AC-COV-02 (a missing translation warns) and BP-VAL-001 … 004 (the report document, its sorted
issues with stable IDs, their source stages and the counts on ``bp.validation_run``).
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from factory_foundation.api import unit_of_work
from sqlalchemy import select

from factory_blueprint import artifacts, deps
from factory_blueprint.build import service as builds
from factory_blueprint.compilers import metadata as metadata_compiler
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue
from factory_blueprint.enums import BuildStatus, IssueLevel
from factory_blueprint.errors import ExpressionGrammarMissing
from factory_blueprint.models.build import ValidationRunRow
from factory_blueprint.s4.contract import CapabilityProfile
from factory_blueprint.s4.stub import StubDhis2, profile_document
from factory_blueprint.testing.harness import BlueprintHarness
from factory_blueprint.validators import cross_artifact, static
from factory_blueprint.validators import report as report_module
from factory_blueprint.validators.blueprint import validate_blueprint
from factory_blueprint.version import service as version_service
from tests.integration.test_compile import (
    DESIGN_ENV,
    PROJECT,
    _discovery_artifact,
    _profile_artifact,
    approved_version,
    context,
)
from tests.unit import helpers

SOURCE = {"artifactId": "art-01INPUT0001"}
NO_SERVER = "static validation reached a DHIS2 instance instead of the local grammar"
PAGE = {"name": "page", "purpose": "record", "routes": ["/x"], "useCases": [], "actors": [],
        "registryRefs": [], "source": dict(SOURCE)}
INTENT = {"purpose": "read", "resource": "programs", "method": "GET", "registryRefs": [],
          "source": dict(SOURCE)}
REGISTRY = {"dhis2Type": "program", "disposition": "CREATE", "status": "APPROVED", "managed": True,
            "decisionId": "MAP-001", "controlledFields": [], "uid": "Fixed0000001",
            "source": dict(SOURCE)}
RULE = {"name": {"default": "rule"}, "programKey": "pregnancy", "source": dict(SOURCE)}
INCOMPLETE_PAGE = {"pageKey": "half-a-page", "name": "incomplete"}
REPORT_FIELDS = {"schemaVersion", "kind", "projectId", "blueprintVersion", "blueprintHash",
                 "environmentKey", "compiler", "buildContext", "counts", "issues", "note"}
ISSUE_FIELDS = {"issueId", "ruleId", "level", "message", "businessKey", "uid", "dhis2Type",
                "artifactId", "location", "section", "sourceStage", "remediation", "details"}


@pytest.fixture(autouse=True)
def _prepared(harness: BlueprintHarness) -> None:
    """Every test starts from a freshly migrated core + bp schema."""


@pytest.fixture(autouse=True)
def stub() -> Iterator[StubDhis2]:
    instance = StubDhis2()
    deps.set_dhis2(instance)
    yield instance
    deps.reset_providers()


@pytest.fixture
def version() -> str:
    return approved_version()


@pytest.fixture
def good_document(version: str) -> dict[str, Any]:
    """The approved scenario Blueprint, exactly as its validators see it."""
    document, _classification = artifacts.read_json(_row(version).artifact_id)
    return document


def _row(version: str) -> Any:
    with unit_of_work() as session:
        return version_service.get_version(session, PROJECT, version)


def _profile(grammar_version: str | None = "dhis2-2.41") -> CapabilityProfile:
    document = profile_document(version="2.41", expression_grammar_version=grammar_version)
    return CapabilityProfile.model_validate(document)


def _with(document: dict[str, Any], path: str, *items: dict[str, Any]) -> dict[str, Any]:
    return helpers.with_entries(document, path, *items)


def _rule(business_key: str, expression: str, **overrides: Any) -> dict[str, Any]:
    return {**RULE, "businessKey": business_key, "name": {"default": business_key},
            "expression": expression, **overrides}


def _inputs(document: dict[str, Any], version: str, profile: CapabilityProfile | None = None) -> CompileInputs:
    row = _row(version)
    entries = BlueprintDocument(document).container_entries(CONTAINER_BY_PATH["/registry/entries"])
    return CompileInputs(
        project_id=PROJECT, blueprint_version=version, blueprint_hash=row.content_hash,
        document=document, build_context=context(), blueprint_artifact_id=row.artifact_id,
        profile=profile if profile is not None else _profile(), environment_key=DESIGN_ENV,
        bindings={str(entry["businessKey"]): str(entry["uid"]) for entry in entries
                  if isinstance(entry.get("uid"), str)},
    )


def _static_issues(document: dict[str, Any], version: str, profile: CapabilityProfile | None = None) -> list[Issue]:
    inputs = _inputs(document, version, profile)
    return static.validate_build(inputs, metadata_compiler.compile_metadata(inputs))


def _by_rule(issues: list[Issue], rule_id: str) -> list[Issue]:
    return [issue for issue in issues if issue.rule_id == rule_id]


def _french_translations(document: dict[str, Any]) -> dict[str, Any]:
    """The same Blueprint with every mapped display field carried into French."""
    result = copy.deepcopy(document)
    for entry in result["mapping"]["entries"]:
        for value in (entry.get("fields") or {}).values():
            if isinstance(value, dict) and "default" in value:
                value.setdefault("translations", {})["fr"] = f"FR {value['default']}"
    return result


def _document_key(issue: dict[str, Any]) -> tuple[Any, ...]:
    return (issue["level"], issue["ruleId"], issue["businessKey"] or "",
            issue["location"] or "", issue["issueId"])


class _RefusesEveryRead:
    """BP-VAL-021 / D-05: the expression verdict comes from the grammar data alone."""

    def generate_uids(self, environment_key: str, count: int) -> list[str]:
        raise AssertionError(NO_SERVER)

    def uid_exists(self, environment_key: str, uids: Sequence[str]) -> dict[str, bool]:
        raise AssertionError(NO_SERVER)

    def code_exists(self, key: str, dhis2_type: str, codes: Sequence[str]) -> dict[str, bool]:
        raise AssertionError(NO_SERVER)

    def has_associated_data(self, key: str, dhis2_type: str, uid: str) -> bool:
        raise AssertionError(NO_SERVER)


def test_blueprint_layer_accepts_the_approved_version(good_document: dict[str, Any]) -> None:
    """AC-VAL-01 / BP-VAL-010, BP-VAL-011: a clean Blueprint reports nothing."""
    assert validate_blueprint(good_document) == []


def test_blueprint_layer_blocks_schema_duplicates_and_dangling_references(
    good_document: dict[str, Any],
) -> None:
    """BP-VAL-010: section schema, duplicate businessKey and a dangling reference."""
    document = _with(good_document, "/registry/entries", good_document["registry"]["entries"][0])
    document = _with(document, "/rules/programRules",
                     _rule("rule.orphan", "#{weight} > 1", programKey="nosuchprogram"))
    document = _with(document, "/pages/items", dict(INCOMPLETE_PAGE))

    issues = validate_blueprint(document)
    assert all(issue.level is IssueLevel.BLOCKING for issue in issues)
    schema = _by_rule(issues, "BPV-BP-001")
    assert schema and {issue.location for issue in schema} == {"/pages/items/0"}
    assert {issue.source_stage.value for issue in schema} == {"PAGE_DESIGN"}
    keys = _by_rule(issues, "BPV-BP-002")
    assert [(issue.business_key, issue.location) for issue in keys if "holds 2" in issue.message] == [
        ("person", "/registry/entries")]
    dangling = [issue for issue in keys if "unknown" in issue.message]
    assert [(issue.business_key, issue.source_stage.value) for issue in dangling] == [
        ("rule.orphan", "MAPPING")]
    assert "nosuchprogram" in dangling[0].message
    assert dangling[0].remediation == "register the referenced object first"
    assert issues == sorted(issues, key=Issue.sort_key)


def test_blueprint_layer_blocks_a_duplicate_secondary_key(good_document: dict[str, Any]) -> None:
    """BP-VAL-010: every field the promoted index keys on has to be unique.

    Two apiIntents may share an ``operationKey`` and still be distinct intents under their
    ``intentKey`` — but ``bp.business_key_index`` carries a row per key kind, so the second one
    collides on its primary key.  Validating only the container's primary key let the clash
    through, and it surfaced as a 500 while the approved version was being written.
    """
    document = _with(
        good_document, "/apiIntents/items",
        {**INTENT, "intentKey": "intent.a", "operationKey": "readPregnancy"},
        {**INTENT, "intentKey": "intent.b", "operationKey": "readPregnancy"},
    )

    duplicates = [
        issue
        for issue in _by_rule(validate_blueprint(document), "BPV-BP-002")
        if "operationKey" in issue.message
    ]
    assert [(issue.business_key, issue.location) for issue in duplicates] == [
        ("readPregnancy", "/apiIntents/items")], [issue.message for issue in duplicates]
    assert "holds 2 entries whose operationKey is 'readPregnancy'" in duplicates[0].message


def test_blueprint_layer_blocks_a_malformed_or_unregistered_uid(good_document: dict[str, Any]) -> None:
    """AC-REG-02 / BP-VAL-011: a too-long or digit-leading UID, and a UID literal inline."""
    document = _with(good_document, "/registry/entries",
                     {**REGISTRY, "businessKey": "too.long.uid", "uid": "Reg1stryUid0001"},
                     {**REGISTRY, "businessKey": "digit.uid", "uid": "1Reg1stryUid0"},
                     {**REGISTRY, "businessKey": "well.formed", "uid": "WellForm001"})
    document = _with(document, "/pages/items",
                     {**PAGE, "pageKey": "pregnancy-page", "registryRefs": ["Facility001"]})

    issues = validate_blueprint(document)
    malformed = _by_rule(issues, "BPV-BP-004")
    assert {issue.business_key for issue in malformed} == {"too.long.uid", "digit.uid"}
    assert all(issue.level is IssueLevel.BLOCKING for issue in malformed)
    unregistered = _by_rule(issues, "BPV-BP-008")
    assert [(issue.business_key, issue.uid) for issue in unregistered] == [("pregnancy-page", None)]
    assert "Facility001" in unregistered[0].message


def test_static_layer_blocks_a_program_rule_condition_that_does_not_parse(
    good_document: dict[str, Any], version: str, stub: StubDhis2
) -> None:
    """BP-AC-09 / BP-VAL-021: an unparsable condition BLOCKS, decided locally (D-05)."""
    document = _with(good_document, "/rules/programRules", _rule("rule.overdue", "#{dueDate} == "))
    calls_before = len(stub.calls)
    expression = _by_rule(_static_issues(document, version), "BPV-ST-007")

    assert expression and {issue.business_key for issue in expression} == {"rule.overdue"}
    assert all(issue.level is IssueLevel.BLOCKING for issue in expression)
    assert all(issue.location == "expression" for issue in expression)
    assert "operand was expected" in expression[0].message
    assert len(stub.calls) == calls_before, NO_SERVER


def test_the_expression_verdict_needs_no_server(good_document: dict[str, Any], version: str) -> None:
    """BP-VAL-021 / D-05: the same BLOCKING syntax verdict with a DHIS2 that answers nothing."""
    document = _with(good_document, "/rules/programRules", _rule("rule.overdue", "#{dueDate} == "))
    deps.set_dhis2(_RefusesEveryRead())
    issues = _static_issues(document, version)
    assert {issue.business_key for issue in _by_rule(issues, "BPV-ST-007")} == {"rule.overdue"}


def test_static_layer_blocks_a_rule_reference_that_resolves_to_nothing(
    good_document: dict[str, Any], version: str, stub: StubDhis2
) -> None:
    """BP-AC-09 / BP-VAL-021: a condition naming an unknown businessKey BLOCKS."""
    document = _with(good_document, "/rules/programRules", _rule("rule.week", "V#{ghost} > 12"))
    calls_before = len(stub.calls)
    issues = _static_issues(document, version)

    reference = _by_rule(issues, "BPV-ST-002")
    assert [(issue.business_key, issue.level) for issue in reference] == [
        ("rule.week", IssueLevel.BLOCKING)]
    assert "ghost" in reference[0].message
    assert _by_rule(issues, "BPV-ST-007") == [], "a well-formed condition is no syntax error"
    assert len(stub.calls) == calls_before, NO_SERVER


def test_static_layer_blocks_a_plain_data_element_reference_to_an_unknown_object(
    good_document: dict[str, Any], version: str
) -> None:
    """BP-AC-09: the commonest reference form is checked like any other."""
    document = _with(good_document, "/rules/programRules", _rule("rule.sugar", "#{bloodSugar} > 14"))
    issues = _static_issues(document, version)
    assert [issue.rule_id for issue in issues if issue.business_key == "rule.sugar"] == ["BPV-ST-002"]


def test_a_foreign_expression_grammar_is_reported_instead_of_guessed(
    good_document: dict[str, Any], version: str
) -> None:
    """BP-VAL-021 / P-10: no grammar for the Profile's version means no verdict at all."""
    document = _with(good_document, "/rules/programRules", _rule("rule.overdue", "#{dueDate} == "))
    with pytest.raises(ExpressionGrammarMissing) as refused:
        _static_issues(document, version, _profile(grammar_version="dhis2-9.99"))
    assert refused.value.error_code == "BP_EXPRESSION_GRAMMAR_MISSING"
    assert "dhis2-9.99" in str(refused.value) and "dhis2-2.41" in str(refused.value)

    unnamed = _static_issues(document, version, _profile(grammar_version=None))
    assert [issue.message for issue in _by_rule(unnamed, "BPV-ST-007")] == [
        "rule.overdue.expression: the Capability Profile names no expression grammar "
        "version (P-10)"]


def test_cross_artifact_layer_catches_the_inconsistency_dimensions(
    good_document: dict[str, Any], version: str
) -> None:
    """BP-VAL-030/031: mapping, pages, intents, permissions and the managed scope."""
    document = _with(good_document, "/registry/entries",
                     {**REGISTRY, "businessKey": "lab.sample", "dhis2Type": "programStage",
                      "status": "PROPOSED", "uid": "LabSample0001"})
    document = _with(document, "/mapping/entries",
                     helpers.mapping_entry("lab.orph", "programStage", decision_id="MAP-001"))
    document = _with(document, "/pages/items",
                     {**PAGE, "pageKey": "lab-page", "actors": ["midwife"],
                      "apiIntentRefs": ["intent.absent"], "registryRefs": ["no.such.entry"]})
    document = _with(document, "/apiIntents/items",
                     {**INTENT, "intentKey": "intent.lab", "pageKey": "lab-page",
                      "operationKey": "readLab",
                      "registryRefs": ["lab.sample", "no.such.entry"]})
    document = _with(document, "/fixtures/specifications",
                     {"businessKey": "fixture.lab", "targetKey": "no.such.entry",
                      "source": dict(SOURCE)})
    inputs = _inputs(document, version)
    output = metadata_compiler.compile_metadata(inputs)
    output.objects["facility"] = {"id": "OUfacility1", "name": "Facility"}

    issues = cross_artifact.validate(document, output, inputs)
    assert all(issue.level is IssueLevel.BLOCKING for issue in issues)
    assert {issue.rule_id for issue in issues} >= {
        "BPV-XA-001", "BPV-XA-002", "BPV-XA-004", "BPV-XA-005", "BPV-XA-006", "BPV-XA-007"}
    assert [(issue.rule_id, issue.business_key) for issue in issues if "PROPOSED" in issue.message] == [
        ("BPV-XA-004", "intent.lab")]
    assert {
        (issue.rule_id, issue.business_key) for issue in issues if "no.such.entry" in issue.message
    } == {("BPV-XA-004", "lab-page"), ("BPV-XA-004", "intent.lab"), ("BPV-XA-006", "fixture.lab")}
    scope = _by_rule(issues, "BPV-XA-007")
    assert [issue.business_key for issue in scope] == ["facility"]
    assert "must never be imported" in scope[0].message
    assert issues == sorted(issues, key=Issue.sort_key)
    assert [issue.business_key for issue in _by_rule(issues, "BPV-XA-001")] == ["lab.orph"], (
        "13.4 asks only whether each mapping lands on a Registry entry"
    )
    assert "lab.sample" not in {issue.business_key for issue in _by_rule(issues, "BPV-XA-001")}


def _without_mapping(document: dict[str, Any], business_key: str) -> dict[str, Any]:
    result = copy.deepcopy(document)
    result["mapping"]["entries"] = [
        entry
        for entry in result["mapping"]["entries"]
        if entry.get("businessKey") != business_key
    ]
    return result


def _without_field(document: dict[str, Any], business_key: str, field_name: str) -> dict[str, Any]:
    result = copy.deepcopy(document)
    for entry in result["mapping"]["entries"]:
        if entry.get("businessKey") == business_key:
            entry["fields"].pop(field_name, None)
    return result


def test_the_architecture_dimension_finds_the_approved_package_consistent(
    good_document: dict[str, Any], version: str
) -> None:
    """BP-VAL-020 架构: a package compiled from the approved version has nothing to report."""
    assert _by_rule(_static_issues(good_document, version), "BPV-ST-004") == []


def test_an_object_no_mapping_decision_produced_blocks_the_package(
    good_document: dict[str, Any], version: str
) -> None:
    document = _without_mapping(good_document, "person")
    findings = _by_rule(_static_issues(document, version), "BPV-ST-004")
    assert "person" in {issue.business_key for issue in findings}
    drift = [issue for issue in findings if issue.business_key is None]
    assert drift and "not the approved version" in drift[0].message, (
        "the document no longer matches the hash the build names"
    )


def test_a_tracker_program_without_a_tracked_entity_type_blocks(
    good_document: dict[str, Any], version: str
) -> None:
    """13.3: Tracker 与 Event 结构与映射一致."""
    document = _without_field(good_document, "pregnancy", "trackedEntity")
    findings = [
        issue
        for issue in _by_rule(_static_issues(document, version), "BPV-ST-004")
        if issue.business_key == "pregnancy"
    ]
    assert findings and "Tracker program" in findings[0].message


def test_static_layer_grades_a_missing_translation_as_a_warning(
    good_document: dict[str, Any], version: str
) -> None:
    """AC-COV-02 / FR-COV-010: an absent translation warns, and filling it in silences it."""
    issues = _by_rule(_static_issues(good_document, version), "BPV-ST-006")
    assert "person" in {issue.business_key for issue in issues}
    assert issues and all(issue.level is IssueLevel.WARNING for issue in issues)
    assert all(issue.remediation and "fr" in issue.message for issue in issues)
    assert _by_rule(_static_issues(_french_translations(good_document), version), "BPV-ST-006") == []


def _environment_build(idempotency_key: str, version: str) -> Any:
    return builds.compile_for_environment(
        PROJECT, version, DESIGN_ENV, _discovery_artifact(DESIGN_ENV), _profile_artifact(), None,
        context(), idempotency_key=idempotency_key, design_reference_environment=DESIGN_ENV)


def _report(result: Any) -> dict[str, Any]:
    document, _classification = artifacts.read_json(result.artifact_ids["VALIDATION_REPORT"])
    return document


def test_validate_build_writes_the_report_artifact_and_its_counts(version: str) -> None:
    """BP-VAL-001 … 004: the report document, its sorted issues and bp.validation_run."""
    built = _environment_build("k-report", version)
    validated = builds.validate_build(built.build_id, idempotency_key="k-report-run")
    assert validated.status is BuildStatus.COMPLETED
    assert validated.validation.blocking_count == 0, [
        issue.message for issue in validated.validation.warnings
    ]

    document = _report(validated)
    assert set(document) == REPORT_FIELDS
    assert (document["kind"], document["projectId"], document["blueprintVersion"]) == (
        "validation", PROJECT, version)
    assert document["environmentKey"] == DESIGN_ENV
    assert document["blueprintHash"] == _row(version).content_hash
    assert document["compiler"]["dhis2Version"] == "2.41"
    assert set(document["buildContext"]) == {"builtAt", "requester", "seed"}
    assert document["issues"], "the scenario is expected to carry findings"
    assert all(set(issue) == ISSUE_FIELDS and issue["issueId"].startswith("bpis-")
               for issue in document["issues"])
    order = [_document_key(issue) for issue in document["issues"]]
    assert order == sorted(order), "issues are sorted, not emitted in validator order"
    assert len({issue["issueId"] for issue in document["issues"]}) == len(document["issues"])

    with unit_of_work() as session:
        run = session.scalars(select(ValidationRunRow).where(
            ValidationRunRow.report_artifact_id == validated.artifact_ids["VALIDATION_REPORT"]
        )).one()
    assert document["counts"] == {"BLOCKING": run.blocking_count, "WARNING": run.warning_count,
                                  "INFO": run.info_count}
    assert run.issues_digest == validated.validation.issues_digest


def test_two_validation_runs_of_one_build_agree(version: str) -> None:
    """BP-CMP-001 / BP-VAL-001: issue IDs and the run digest follow from the input only."""
    build_id = _environment_build("k-determinism", version).build_id
    first = builds.validate_build(build_id, idempotency_key="k-determinism-a")
    second = builds.validate_build(build_id, idempotency_key="k-determinism-b")
    assert first.validation.issues_digest == second.validation.issues_digest
    assert first.output_hashes["VALIDATION_REPORT"] == second.output_hashes["VALIDATION_REPORT"]
    assert [issue["issueId"] for issue in _report(first)["issues"]] == [
        issue["issueId"] for issue in _report(second)["issues"]]


def test_the_report_records_the_source_stage_a_rule_provides(
    good_document: dict[str, Any], version: str
) -> None:
    """BP-VAL-004: the stage comes from the section the finding is in (appendix A)."""
    document = _with(good_document, "/pages/items", dict(INCOMPLETE_PAGE))
    document = _with(document, "/rules/programRules",
                     _rule("rule.orphan", "#{weight} > 1", programKey="nosuchprogram"))
    issues = [*validate_blueprint(document), *_static_issues(document, version)]
    rendered = report_module.document(_inputs(document, version), issues, kind="validation")

    assert {issue["sourceStage"] for issue in rendered["issues"]
            if issue["ruleId"].startswith("BPV-BP")} == {"MAPPING", "PAGE_DESIGN"}
    assert {issue["sourceStage"] for issue in rendered["issues"]
            if issue["ruleId"].startswith(("BPV-ST", "BPV-XA"))} == {None}
