"""The four-rung output ladder — AI-RT-010, AI-RT-011, AI-RT-012, AI-ASM-03, AI-RT-031.

Rungs are ``envelope`` → ``jsonSchema`` → ``constraint`` (rung 3, S2's ``submit_patch``, is
the runtime's and is deliberately not reachable from here).  What this file pins:

* every failure the SRS names has one finding with a stable id, and a failing rung stops
  the pass, so a model gets the *cheapest* explanation first (AI-RT-011);
* an entry is validated against **S2's own** section schema, read at test time, so S3's
  verdict and S2's cannot drift apart (AI-ASM-03) — the assertion quotes a pattern string
  taken out of ``get_section_schema`` rather than a copy kept here;
* ``additionalProperties: false`` on the envelope is what a prompt-injection payload
  hits first (AI-RT-031), and the corrective block is data about the answer, never a new
  command;
* a path outside the agent's declared sections is refused *before* its value is examined
  (AI-RT-012), which is asserted by planting an invalid value at such a path and showing
  that only the scope finding appears.

The fixtures are built by minimal variation of a known-valid base, and one test below
re-validates every base entry against S2 so a fixture that quietly stopped being legal
fails loudly instead of testing the wrong thing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import jsonschema
import pytest
from factory_blueprint.api import Section, get_section_schema
from factory_foundation.api import new_id

# Imported before ``factory_intelligence.agents`` on purpose: the two packages import each
# other and ``agents`` cannot be the first one loaded (see
# ``test_the_agents_package_is_importable_on_its_own``, which is red until that is fixed).
import factory_intelligence.output  # noqa: F401
from factory_intelligence.agents.checks.base import CheckContext
from factory_intelligence.agents.spec import AGENT_SPECS, AgentSpec, spec_for
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.dto.common import Issue
from factory_intelligence.enums import AgentType
from factory_intelligence.output.errors import (
    LADDER_BLUEPRINT,
    LADDER_CONSTRAINT,
    LADDER_ENVELOPE,
    LADDER_SCHEMA,
    Finding,
    ValidationReport,
)
from factory_intelligence.output.schema import (
    Container,
    classify_path,
    envelope_schema,
    is_within,
    section_containers,
)
from factory_intelligence.output.validate import is_within_section, validate

#: The version the run was built against; ``patch.baseVersion`` must equal it (AI-RT-010).
BASE_VERSION = "7"
#: A UID in DHIS2's own shape — 11 characters, letter-initial, mixed case (AI-AC-02).
UID_SHAPE = "p7dLt2q9xKz"


def _source() -> dict[str, Any]:
    """Provenance in the only form S2 accepts: a reference to a real artifact id."""
    return {"artifactId": new_id("art")}


def _decision(**changes: Any) -> dict[str, Any]:
    """A decision that satisfies AI-AG-003 (one alternative) and AI-AG-012 (certain)."""
    decision: dict[str, Any] = {
        "id": "MAP-001",
        "subject": "demographic_code",
        "selected": {"option": "dataElement"},
        "alternatives": [{"option": "trackedEntityAttribute", "reasonRejected": "not reportable"}],
        "rationale": "the code must be reportable per org unit",
        "confidence": 1.0,
        "impactLevel": "MEDIUM",
    }
    decision.update(changes)
    return decision


def _requirement(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "businessKey": "req.demo.001",
        "statement": "a midwife must record each birth",
        "priority": "MUST",
        "status": "CANDIDATE",
        "confidence": 0.9,
        "source": _source(),
    }
    entry.update(changes)
    return entry


def _mapping_entry(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "businessKey": "map.demo.001",
        "dhis2Type": "dataElement",
        "decisionId": "MAP-001",
        "confidence": 0.95,
        "source": _source(),
    }
    entry.update(changes)
    return entry


def _registry_entry(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "businessKey": "reg.demo.001",
        "dhis2Type": "dataElement",
        "disposition": "CREATE",
        "status": "PROPOSED",
        "decisionId": "MAP-001",
        "source": _source(),
    }
    entry.update(changes)
    return entry


def _domain_entity(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "businessKey": "person.demo",
        "displayName": {"default": "Person"},
        "attributes": [
            {
                "name": "medicalNumber",
                "valueType": "TEXT",
                "required": True,
                "sensitivity": "PHI_PII",
            }
        ],
        "source": _source(),
    }
    entry.update(changes)
    return entry


def _architecture_decision(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "decisionId": "ADR-001",
        "title": "one code list per organisation level",
        "context": "the design reference environment already publishes one",
        "decision": "reuse the existing code list",
        "status": "PROPOSED",
    }
    entry.update(changes)
    return entry


def _page(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "pageKey": "antenatal-list",
        "name": "Antenatal visits",
        "purpose": "list the visits of one pregnancy",
        "routes": ["/antenatal"],
        "useCases": ["uc.demo"],
        "actors": ["actor.midwife"],
        "registryRefs": ["reg.demo.001"],
        "source": _source(),
    }
    entry.update(changes)
    return entry


def _api_intent(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "intentKey": "api-antenatal-list",
        "pageKey": "antenatal-list",
        "operationKey": "antenatalList",
        "purpose": "read the visits of one pregnancy",
        "resource": "dataelement.demographicCode",
        "method": "GET",
        "source": _source(),
    }
    entry.update(changes)
    return entry


def _ui_intent(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "intentKey": "ui-antenatal-list",
        "pageKey": "antenatal-list",
        "route": "/antenatal",
        "purpose": "show the visits",
        "components": [{"name": "Table", "use": "a table of visits"}],
        "states": ["READY"],
        "fieldMappings": [{"field": "code", "businessKey": "map.demo.001", "valueType": "TEXT"}],
        "source": _source(),
    }
    entry.update(changes)
    return entry


def _fixture_spec(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "businessKey": "fx.person",
        "targetKey": "person.demo",
        "count": 50,
        "distribution": {"strategy": "uniform"},
        "source": _source(),
    }
    entry.update(changes)
    return entry


def _add(path: str, value: Any, *, op: str = "add") -> dict[str, Any]:
    """One RFC 6902 operation, in the shape S2 parses."""
    operation: dict[str, Any] = {"op": op, "path": path}
    if value is not None:
        operation["value"] = value
    return operation


def _answer(operations: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    """AI-RT-010's envelope, valid apart from whatever a test overrides.

    A test passes ``_ABSENT`` to remove a top-level key rather than hand-assembling a
    whole envelope per case.
    """
    answer: dict[str, Any] = {
        "summary": "consolidated one requirement from the input artifact",
        "confidence": 0.9,
        "decisions": [_decision()],
        "questions": [],
        "patch": {"baseVersion": BASE_VERSION, "operations": list(operations or [])},
    }
    answer.update(overrides)
    return {key: value for key, value in answer.items() if value is not _ABSENT}


class _Absent:
    """Sentinel: "this key must not be in the answer at all"."""


_ABSENT = _Absent()


def _context(spec: AgentSpec, **changes: Any) -> CheckContext:
    """A context carrying the containers of exactly the sections this agent may write."""
    containers = {section.value: section_containers(section) for section in spec.sections}
    arguments: dict[str, Any] = {
        "agent_type": spec.agent_type.value,
        "stage": spec.stage.value,
        "containers": containers,
        "base_version": BASE_VERSION,
    }
    arguments.update(changes)
    return CheckContext(**arguments)


def _run(
    answer: Any, agent: str = "REQUIREMENTS", **context_changes: Any
) -> tuple[AgentOutput | None, ValidationReport]:
    spec = spec_for(agent)
    return validate(answer, spec=spec, context=_context(spec, **context_changes))


def _rules(report: ValidationReport) -> list[str]:
    return [finding.rule_id for finding in report.sorted_findings()]


def _one(answer: Any, agent: str = "REQUIREMENTS") -> ValidationReport:
    """Validate and return the report, asserting the answer was refused."""
    output, report = _run(answer, agent)
    assert output is None, "a refused answer must never be handed to the runtime"
    assert not report.ok
    return report


#: Every base entry this file builds, with the container it belongs to.
_BASE_ENTRIES: tuple[tuple[Section, str, dict[str, Any]], ...] = (
    (Section.REQUIREMENTS, "items", _requirement()),
    (Section.MAPPING, "entries", _mapping_entry()),
    (Section.REGISTRY, "entries", _registry_entry()),
    (Section.DOMAIN, "entities", _domain_entity()),
    (Section.ARCHITECTURE, "decisions", _architecture_decision()),
    (Section.PAGES, "items", _page()),
    (Section.API_INTENTS, "items", _api_intent()),
    (Section.UI_INTENTS, "items", _ui_intent()),
    (Section.FIXTURES, "specifications", _fixture_spec()),
)


# --- fixtures of my own must be right, or the rungs prove nothing -----------------


BASE_ENTRY_IDS = [f"{section.value}.{container}" for section, container, _ in _BASE_ENTRIES]


@pytest.mark.parametrize("section,container,entry", _BASE_ENTRIES, ids=BASE_ENTRY_IDS)
def test_the_base_entry_of_every_section_is_valid_against_s2(
    section: Section, container: str, entry: dict[str, Any]
) -> None:
    """AI-ASM-03: if a fixture here stopped matching S2's schema, every rung test below
    would be measuring the wrong thing."""
    found = next(
        (item for item in section_containers(section) if item.property_name == container), None
    )
    assert found is not None, f"{section.value} exposes no container {container}"
    errors = list(jsonschema.Draft202012Validator(found.standalone_schema()).iter_errors(entry))
    assert not errors, [error.message for error in errors]


# --- rung 0: the envelope ----------------------------------------------------------


def test_a_valid_requirements_answer_passes_and_returns_a_typed_output() -> None:
    """AI-RT-010: the ladder hands back a bound ``AgentOutput``, not a dict."""
    output, report = _run(_answer([_add("/requirements/items/-", _requirement())]))
    assert report.ok, report.messages()
    assert report.stopped_at is None
    assert isinstance(output, AgentOutput)
    assert output.summary
    assert output.has_patch
    assert output.decision_ids == ["MAP-001"]


def test_a_valid_mapping_answer_passes_every_rung() -> None:
    """AI-RT-012: an in-scope Mapping answer with its decision recorded is accepted.

    "recorded" is the document's word, not the answer's: FR-MAP-001 and BP-VAL-010 make
    ``/architecture/decisions`` the only place a cited decisionId resolves, so the decision
    travels there with the entries that cite it.
    """
    operations = [
        _add("/architecture/decisions/-", _architecture_decision(decisionId="MAP-001")),
        _add("/registry/entries/-", _registry_entry()),
        _add("/mapping/entries/-", _mapping_entry()),
    ]
    output, report = _run(_answer(operations), "MAPPING")
    assert report.ok, report.messages()
    assert output is not None and output.has_patch


def test_an_answer_may_carry_no_patch_at_all() -> None:
    """AI-AG-008: the Review Assistant answers without touching the draft."""
    answer = _answer([], patch=_ABSENT)
    output, report = _run(answer, "REVIEW")
    assert report.ok, report.messages()
    assert output is not None and not output.has_patch


@pytest.mark.parametrize(
    "raw",
    [[], "text", None, 7, [{"summary": "x"}]],
    ids=["list", "string", "null", "number", "list-of-object"],
)
def test_a_non_object_answer_is_one_envelope_finding(raw: object) -> None:
    """AI-RT-010: the shape is checked before the content, so the model gets one clear
    sentence rather than a schema dump."""
    report = _one(raw)
    assert report.stopped_at == LADDER_ENVELOPE
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "AI-ENV-001"
    assert finding.ladder == LADDER_ENVELOPE
    assert "the answer must be one JSON object" in finding.message


@pytest.mark.parametrize("raw", [[], "text", None], ids=["list", "string", "null"])
def test_no_later_rung_runs_after_the_envelope_fails(raw: object) -> None:
    """AI-RT-011: a rung that stops takes the whole pass with it — no schema or
    constraint finding may appear."""
    report = _one(raw)
    assert {finding.ladder for finding in report.findings} == {LADDER_ENVELOPE}
    assert report.stopped_at == LADDER_ENVELOPE


@pytest.mark.parametrize(
    "key",
    ["summary", "decisions", "questions", "confidence"],
    ids=["summary", "decisions", "questions", "confidence"],
)
def test_a_missing_top_level_key_is_named_by_name(key: str) -> None:
    """AI-RT-010: the four keys the SRS names at the top level are mandatory."""
    report = _one(_answer([], **{key: _ABSENT}))
    assert report.stopped_at == LADDER_ENVELOPE
    assert _rules(report) == ["AI-ENV-002"]
    assert report.findings[0].location == key
    assert repr(key) in report.findings[0].message


def test_patch_is_the_optional_part_of_the_envelope() -> None:
    """AI-RT-010: ``patch`` is listed but an answer with no patch is well formed."""
    output, report = _run(_answer([], patch=_ABSENT))
    assert report.ok, report.messages()
    assert output is not None and output.patch is None


def test_several_missing_keys_are_each_reported() -> None:
    """AI-RT-011: the corrective retry sees the complete list in one round trip."""
    report = _one(_answer([], decisions=_ABSENT, questions=_ABSENT))
    assert _rules(report) == ["AI-ENV-002", "AI-ENV-002"]
    assert sorted(finding.location or "" for finding in report.findings) == [
        "decisions",
        "questions",
    ]


@pytest.mark.parametrize("summary", ["", "   ", None], ids=["empty", "whitespace", "null"])
def test_an_empty_summary_is_refused(summary: str | None) -> None:
    """AI-RT-014: the summary is the only auditable decision record a run leaves."""
    report = _one(_answer([], summary=summary))
    assert report.stopped_at == LADDER_ENVELOPE
    assert _rules(report) == ["AI-ENV-003"]
    assert report.findings[0].location == "summary"


def test_null_confidence_is_valid_but_an_absent_one_is_not() -> None:
    """AI-RT-010: ``null`` is an answer, an absent key is a shape error."""
    output, report = _run(_answer([], confidence=None))
    assert report.ok, report.messages()
    assert output is not None and output.confidence is None


# --- rung 1: the envelope's JSON Schema -------------------------------------------


def test_an_unknown_top_level_key_is_refused() -> None:
    """AI-RT-031: ``additionalProperties: false`` is the cheapest defence there is — an
    injection that adds a side-effect key is refused by the shape, not by prose."""
    report = _one(_answer([], sideEffects={"execute": True}))
    assert report.stopped_at == LADDER_SCHEMA
    assert _rules(report) == ["AI-SCO-001"]
    assert "properties the schema forbids" in report.findings[0].message


def test_a_question_severity_outside_the_three_is_refused() -> None:
    """AI-RT-010 / AI-RT-015: HIGH, MEDIUM, LOW are the whole vocabulary S1 can parse."""
    report = _one(_answer([], questions=[{"id": "Q1", "text": "which code?", "severity": "BLOCKER"}]))
    assert _rules(report) == ["AI-SCO-001"]
    assert report.findings[0].location == "/questions/0/severity"
    assert "must be one of" in report.findings[0].message


def test_a_suggestion_without_an_id_is_refused() -> None:
    """AI-RT-010: an answer selects a suggestion *by id*, so an anonymous option could
    never be resolved on resume (AI-RT-016)."""
    questions = [{"id": "Q1", "text": "which code?", "suggestions": [{"text": "reuse it"}]}]
    report = _one(_answer([], questions=questions))
    assert _rules(report) == ["AI-SCO-001"]
    assert report.findings[0].location == "/questions/0/suggestions/0"
    assert "'id' is a required property" in report.findings[0].message


def test_more_operations_than_the_limit_are_refused() -> None:
    """AI-RT-010: the bound is a parameter of the ladder, so a runaway answer cannot fill
    a draft.  Three operations pass at the default of 200 and are refused at two."""
    spec = spec_for("REQUIREMENTS")
    operations = [
        _add("/requirements/items/-", _requirement(businessKey=f"req.demo.{index:03d}"))
        for index in range(3)
    ]
    answer = _answer(operations)
    output, report = validate(answer, spec=spec, context=_context(spec))
    assert report.ok, report.messages()
    assert output is not None
    output, tight = validate(
        answer, spec=spec, context=_context(spec), max_operations=2
    )
    assert output is None
    assert tight.stopped_at == LADDER_SCHEMA
    assert _rules(tight) == ["AI-SCO-001"]
    assert tight.findings[0].location == "/patch/operations"


def test_a_patch_without_a_base_version_is_refused() -> None:
    """AI-RT-010: ``baseVersion`` is what makes a patch reviewable against a draft."""
    report = _one(_answer([], patch={"operations": []}))
    assert _rules(report) == ["AI-SCO-001"]
    assert report.findings[0].location == "/patch"
    assert "'baseVersion' is a required property" in report.findings[0].message


def test_an_operation_without_op_is_refused() -> None:
    """AI-RT-010: an operation is ``{op, path, value}`` — RFC 6902, the form S2 parses."""
    report = _one(_answer([{"path": "/requirements/items/-", "value": _requirement()}]))
    assert _rules(report) == ["AI-SCO-001"]
    assert "'op' is a required property" in report.findings[0].message


def test_an_operation_with_an_unknown_op_is_refused() -> None:
    """AI-RT-012: only add/replace/remove reach S2; a ``move`` is refused here."""
    report = _one(_answer([{"op": "move", "path": "/requirements/items/-", "from": "/x"}]))
    assert _rules(report) == ["AI-SCO-001"]


def test_an_operation_may_not_carry_extra_keys() -> None:
    """AI-RT-031: the operation shape is closed too, so no ``comment``/``execute`` field
    rides into the patch."""
    operation = {"op": "add", "path": "/requirements/items/-", "value": _requirement(), "force": True}
    report = _one(_answer([operation]))
    assert _rules(report) == ["AI-SCO-001"]
    assert "Additional properties are not allowed" in report.findings[0].message


def test_a_decision_may_carry_fields_the_envelope_does_not_name() -> None:
    """AI-RT-010: decisions are the agent's own reasoning; only the top level is closed."""
    answer = _answer(
        [], decisions=[_decision(extraEvidence="the environment already has one")], patch=_ABSENT
    )
    output, report = _run(answer)
    assert report.ok, report.messages()
    assert output is not None


def test_envelope_schema_carries_the_operation_limit() -> None:
    """AI-RT-010: the same function is what the prompt shows the model, so the limit has
    to be visible in it."""
    assert envelope_schema()["properties"]["patch"]["properties"]["operations"]["maxItems"] == 200
    assert envelope_schema(max_operations=4)["properties"]["patch"]["properties"][
        "operations"
    ]["maxItems"] == 4
    assert envelope_schema()["required"] == ["summary", "questions", "decisions", "confidence"]


# --- rung 1: scope and S2's own item schema ---------------------------------------


def test_a_patch_authored_against_the_wrong_base_version_is_refused() -> None:
    """AI-RT-010 / AI-SCO-002: the patch must name the version the context was built
    against, or the runtime would apply it to a draft the model never saw."""
    operations = [_add("/requirements/items/-", _requirement())]
    report = _one(_answer(operations, patch={"baseVersion": "8", "operations": operations}))
    assert report.stopped_at == LADDER_SCHEMA
    assert _rules(report) == ["AI-SCO-002"]
    assert report.findings[0].location == "patch.baseVersion"
    assert "'8'" in report.findings[0].message and "'7'" in report.findings[0].message


def test_no_base_version_in_context_means_no_comparison() -> None:
    """AI-ASM-02: a run with no draft has no base version, so nothing may be refused for
    disagreeing with it."""
    spec = spec_for("REQUIREMENTS")
    answer = _answer([_add("/requirements/items/-", _requirement())])
    output, report = validate(answer, spec=spec, context=_context(spec, base_version=None))
    assert report.ok, report.messages()
    assert output is not None


def test_a_path_outside_the_agents_sections_is_refused_without_reading_its_value() -> None:
    """AI-RT-012 / AI-SCO-003: scope is decided before content.  The value planted here
    is invalid on purpose: if it were examined, a second finding would appear."""
    report = _one(_answer([_add("/pages/items/-", {"businessKey": "NOT A KEY"})]), "MAPPING")
    assert report.stopped_at == LADDER_SCHEMA
    assert _rules(report) == ["AI-SCO-003"]
    assert "outside this agent's scope" in report.findings[0].message
    assert "architecture, mapping, registry" in report.findings[0].message


def test_a_write_to_the_whole_of_a_section_with_containers_is_refused() -> None:
    """AI-RT-012 / AI-SCO-004: a section made of containers is addressed entry by entry,
    never replaced wholesale."""
    report = _one(_answer([_add("/mapping", None, op="replace")]), "MAPPING")
    assert _rules(report) == ["AI-SCO-004"]
    assert "address an entry inside one of entries" in report.findings[0].message


def test_a_property_that_is_not_a_container_is_named_as_one() -> None:
    """AI-RT-012 / AI-SCO-005: the pointer's second segment must be a real container."""
    report = _one(_answer([_add("/mapping/notEntries/x", {"businessKey": "map.demo.001"})]), "MAPPING")
    assert _rules(report) == ["AI-SCO-005"]
    assert "not a container" in report.findings[0].message


def test_an_add_with_no_value_is_refused() -> None:
    """AI-RT-012 / AI-SCO-006: there is nothing to insert, which S2 would only discover
    after a database round trip."""
    report = _one(_answer([{"op": "add", "path": "/mapping/entries/map.demo.001"}]), "MAPPING")
    assert _rules(report) == ["AI-SCO-006"]
    assert report.findings[0].location == "patch.operations[0].value"


def test_a_remove_needs_no_value() -> None:
    """AI-RT-012: ``remove`` is the one operation whose value is the path."""
    output, report = _run(
        _answer([_add("/mapping/entries/map.demo.001", None, op="remove")], decisions=[]),
        "MAPPING",
    )
    assert report.ok, report.messages()
    assert output is not None


def test_a_whole_object_section_may_be_replaced_as_a_whole() -> None:
    """AI-SCO-004: replacing a section as one object is refused wherever the section has
    entries to address, and allowed where it genuinely is one object.

    ``rules`` used to fall in the second group only because container discovery missed
    arrays declared behind a property-level ``$ref``; now that its five rule arrays are
    found, a whole-``/rules`` replace is refused.  ``generationPolicy`` remains the real
    whole-object case, so both halves of the rule are asserted here.
    """
    spec = spec_for("METADATA_DESIGN")
    answer = _answer(
        [_add("/rules", {"validationRules": []}, op="replace")],
        decisions=[],
    )
    _output, report = validate(answer, spec=spec, context=_context(spec))
    assert report.stopped_at == LADDER_SCHEMA
    assert _rules(report) == ["AI-SCO-004"]

    whole = spec_for("MAPPING")
    policy_answer = _answer(
        [_add("/generationPolicy", {"codePrefix": "x_"}, op="replace")],
        decisions=[],
    )
    policy_context = _context(whole)
    _output, allowed = validate(
        policy_answer,
        spec=AgentSpec(
            agent_type=AgentType.MAPPING,
            stage=whole.stage,
            milestone=whole.milestone,
            prompt_name=whole.prompt_name,
            sections=whole.sections + (Section.GENERATION_POLICY,),
            checks=(),
        ),
        context=policy_context,
    )
    assert allowed.ok, allowed.messages()


def test_an_entry_that_breaks_s2s_schema_is_refused_with_s2s_own_words() -> None:
    """AI-ASM-03: one schema, both subsystems — the message quotes the pattern S2
    publishes, read here at test time rather than copied into S3."""
    pattern = get_section_schema(Section.MAPPING)["definitions"]["businessKey"]["pattern"]
    report = _one(_answer([_add("/mapping/entries/-", _mapping_entry(businessKey="BAD KEY"))]), "MAPPING")
    assert report.stopped_at == LADDER_SCHEMA
    assert _rules(report) == ["AI-SCO-007"]
    finding = report.findings[0]
    assert "mapping.entries" in finding.message and "S2's own section schema" in finding.message
    assert pattern in finding.message
    assert finding.location == "patch.operations[0].value/businessKey"


def test_an_entry_missing_a_required_field_reports_s2s_required_message() -> None:
    """AI-ASM-03: the requirement text that comes back is S2's, not a paraphrase — and
    every missing field is named in the one pass."""
    report = _one(
        _answer([_add("/registry/entries/-", {"businessKey": "reg.demo.001"})]), "MAPPING"
    )
    assert _rules(report) == ["AI-SCO-007"] * 4
    assert "is a required property" in report.findings[0].message
    assert {finding.location for finding in report.findings} == {
        "patch.operations[0].value" for _ in report.findings
    }


def test_several_bad_entries_are_all_reported_in_one_pass() -> None:
    """AI-RT-011: one corrective retry, so every violation has to arrive together."""
    operations = [
        _add("/mapping/entries/-", _mapping_entry(businessKey="BAD ONE")),
        _add("/registry/entries/-", _registry_entry(status="PENDING")),
    ]
    report = _one(_answer(operations, decisions=[]), "MAPPING")
    assert _rules(report) == ["AI-SCO-007", "AI-SCO-007"]
    assert {finding.location for finding in report.findings} == {
        "patch.operations[0].value/businessKey",
        "patch.operations[1].value/status",
    }


def test_the_entry_schema_is_read_from_the_container_of_the_right_section() -> None:
    """AI-ASM-03: the domain section has two containers, which is what breaks any
    hand-written section→single-container map."""
    containers = {item.property_name: item for item in section_containers(Section.DOMAIN)}
    assert set(containers) == {"entities", "relationships"}
    bad_attribute = {"name": "x", "valueType": "TEXT", "required": True}
    report = _one(
        _answer([_add("/domain/entities/-", _domain_entity(attributes=[bad_attribute]))]),
        "DOMAIN",
    )
    assert "domain.entities" in report.findings[0].message


# --- the order of the rungs -------------------------------------------------------


def test_the_envelope_rung_stops_before_the_schema_rung() -> None:
    """AI-RT-011: the cheapest rung first — a non-object with nothing else wrong gets one
    finding, and the schema is never built."""
    report = _one(_answer([], summary=""))
    assert report.stopped_at == LADDER_ENVELOPE
    assert _rules(report) == ["AI-ENV-003"]


def test_the_schema_rung_stops_before_the_constraint_rung() -> None:
    """AI-RT-012: constraints are the expensive rung and only see answers that are
    already well formed — here a UID (AIC-003) goes unreported because the shape broke."""
    answer = _answer(
        [_add("/registry/entries/-", _registry_entry(uid=UID_SHAPE))],
        questions=[{"id": "Q1", "text": "which?", "severity": "BLOCKER"}],
    )
    report = _one(answer, "MAPPING")
    assert report.stopped_at == LADDER_SCHEMA
    assert _rules(report) == ["AI-SCO-001"]
    assert not any(rule.startswith("AIC") for rule in _rules(report))


def test_an_entry_failure_stops_before_the_constraints_run() -> None:
    """AI-RT-012 / AI-ASM-03: S2's schema first, then S3's own rules — a bad entry with a
    UID in it is one schema finding, not two ladders."""
    report = _one(
        _answer([_add("/registry/entries/-", _registry_entry(uid=UID_SHAPE, status="PENDING"))],
                decisions=[]),
        "MAPPING",
    )
    assert report.stopped_at == LADDER_SCHEMA
    assert all(finding.ladder == LADDER_SCHEMA for finding in report.findings)


def test_a_constraint_violation_is_reported_as_the_constraint_rung() -> None:
    """AI-RT-012: a constraint failure is not a schema failure — the two consume
    different retry budgets."""
    report = _one(
        _answer(
            [
                _add("/architecture/decisions/-", _architecture_decision(decisionId="MAP-001")),
                _add("/registry/entries/-", _registry_entry(uid=UID_SHAPE)),
            ]
        ),
        "MAPPING",
    )
    assert report.stopped_at == LADDER_CONSTRAINT
    assert _rules(report) == ["AIC-003"]
    assert report.findings[0].ladder == LADDER_CONSTRAINT


def test_the_ladder_never_reaches_s2_from_here() -> None:
    """AI-RT-013: rung 3 is the runtime's ``submit_patch``, so a report produced here can
    never name the blueprint rung."""
    _, report = _run(_answer([_add("/requirements/items/-", _requirement())]))
    assert LADDER_BLUEPRINT not in {finding.ladder for finding in report.findings}
    assert report.stopped_at != LADDER_BLUEPRINT


# --- the report as the corrective prompt ------------------------------------------


def _report_with(*findings: Finding) -> ValidationReport:
    report = ValidationReport()
    report.extend(list(findings))
    return report


def _finding(rule: str, ladder: str, location: str | None, message: str) -> Finding:
    return Finding(rule_id=rule, ladder=ladder, message=message, location=location)


def test_as_payload_has_the_shape_the_runtime_appends() -> None:
    """AI-RT-011: one format for every rung, because the corrective prompt cannot learn
    four."""
    report = _report_with(_finding("AI-SCO-001", LADDER_SCHEMA, "/patch", "bad shape"))
    report.stopped_at = LADDER_SCHEMA
    payload = report.as_payload()
    assert payload["kind"] == "validationErrors"
    assert payload["stoppedAt"] == LADDER_SCHEMA
    assert list(payload["errors"][0]) == ["rule", "ladder", "message", "location"]
    assert payload["errors"][0]["rule"] == "AI-SCO-001"


def test_as_payload_carries_an_instruction_that_is_data_not_a_command() -> None:
    """AI-RT-031: the feedback asks for a conforming object and nothing else — it must
    not become the second injection."""
    report = _report_with(_finding("AI-ENV-001", LADDER_ENVELOPE, None, "not an object"))
    instruction = report.as_payload()["instruction"]
    assert "JSON object" in instruction
    for imperative in ("ignore", "execute", "approve", "submit", "override", "previous"):
        assert imperative not in instruction.lower()
    assert set(report.as_payload()) == {"kind", "stoppedAt", "errors", "instruction"}


def test_as_prompt_block_is_parseable_json() -> None:
    """AI-RT-011: the block is appended to the context as text and must round-trip."""
    report = _report_with(
        _finding("AIC-003", LADDER_CONSTRAINT, "/registry/entries/-", "writes 'uid'")
    )
    assert json.loads(report.as_prompt_block()) == report.as_payload()


def test_sorted_findings_is_stable_across_two_calls() -> None:
    """AI-RT-011: the corrective prompt is part of the run record, so the same report has
    to print the same way twice."""
    report = _report_with(
        _finding("AIC-010", LADDER_CONSTRAINT, "b", "second rule"),
        _finding("AI-ENV-002", LADDER_ENVELOPE, "a", "first rule"),
        _finding("AI-SCO-003", LADDER_SCHEMA, "patch.operations[0].path", "scope"),
    )
    assert report.sorted_findings() == report.sorted_findings()
    assert [finding.rule_id for finding in report.sorted_findings()] == [
        "AIC-010",
        "AI-ENV-002",
        "AI-SCO-003",
    ]


def test_sorted_findings_does_not_depend_on_insertion_order() -> None:
    """AI-RT-011: two runs that fail the same rules in a different order must produce the
    same prompt."""
    findings = [
        _finding("AI-SCO-001", LADDER_SCHEMA, "/patch", "bad"),
        _finding("AIC-003", LADDER_CONSTRAINT, "entries", "uid"),
        _finding("AI-ENV-003", LADDER_ENVELOPE, "summary", "empty"),
    ]
    forward = _report_with(*findings).sorted_findings()
    backward = _report_with(*reversed(findings)).sorted_findings()
    assert [finding.rule_id for finding in forward] == [
        finding.rule_id for finding in backward
    ]
    assert forward == backward


def test_messages_are_one_line_per_finding_with_its_location() -> None:
    """AI-RT-013: the human-readable form of the same findings."""
    report = _report_with(_finding("AI-ENV-002", LADDER_ENVELOPE, "summary", "no summary"))
    assert report.messages() == ["AI-ENV-002: no summary (at summary)"]
    assert _finding("AI-ENV-001", LADDER_ENVELOPE, None, "x").as_line() == "AI-ENV-001: x"


def test_to_issues_are_blocking_and_uniquely_identified() -> None:
    """AI-RT-013: a failed run has to be displayable, and S1 keys issues by id."""
    report = _report_with(
        _finding("AIC-003", LADDER_CONSTRAINT, "entries", "uid"),
        _finding("AIC-003", LADDER_CONSTRAINT, "entries", "uid"),
        _finding("AI-ENV-001", LADDER_ENVELOPE, None, "not an object"),
    )
    issues = report.to_issues(stage="MAPPING")
    assert len(issues) == 3
    assert {issue.level for issue in issues} == {"BLOCKING"}
    assert len({issue.issue_id for issue in issues}) == 3
    assert all(isinstance(issue, Issue) for issue in issues)


def test_to_issues_carries_the_rule_the_finding_blames() -> None:
    """AI-RT-013: a user reads the issue, so the rule id and the stage survive."""
    report = _report_with(_finding("AI-SCO-007", LADDER_SCHEMA, "patch.operations[0].value", "bad entry"))
    issue = report.to_issues(stage="DOMAIN")[0]
    assert issue.rule_id == "AI-SCO-007"
    assert issue.source_stage == "DOMAIN"
    # the finding's coordinate is its *location*; S1's mirror declares no location field yet
    # (it does declare object_key, which is a business key), so this is the canonical spelling
    # and the S1 gap is recorded in the README rather than papered over here
    assert issue.location == "patch.operations[0].value"
    assert issue.description == "bad entry"


def test_an_empty_report_is_ok_and_carries_no_errors() -> None:
    """AI-RT-011: the passing case is still a report, so the retry path has one shape."""
    report = ValidationReport()
    assert report.ok and report.count == 0 and report.stopped_at is None
    assert report.as_payload()["errors"] == []
    assert report.as_payload()["stoppedAt"] is None
    assert report.to_issues() == []


# --- container discovery (AI-ASM-03) ----------------------------------------------

#: Sections whose identity field is *not* ``businessKey`` — the list S3 cannot import
#: from S2 and would otherwise silently fall behind (see ``KEY_FIELDS``).
NON_BUSINESS_KEY_CONTAINERS: tuple[tuple[Section, str, str], ...] = (
    (Section.ARCHITECTURE, "decisions", "decisionId"),
    (Section.PAGES, "items", "pageKey"),
    (Section.API_INTENTS, "items", "intentKey"),
    (Section.UI_INTENTS, "items", "intentKey"),
)


@pytest.mark.parametrize("section", list(Section), ids=lambda value: value.value)
def test_every_discovered_container_key_is_required_by_s2(section: Section) -> None:
    """AI-ASM-03: a container is only a container if S2 itself requires the key field on
    its items — the regression net for ``KEY_FIELDS`` drifting behind S2."""
    document = get_section_schema(section)
    for container in section_containers(section):
        assert container.section == section.value
        assert container.key_field in (container.item_schema.get("required") or []), container
        assert container.property_name in document.get("properties", {})


@pytest.mark.parametrize(
    "section,property_name,key_field",
    NON_BUSINESS_KEY_CONTAINERS,
    ids=[f"{section.value}" for section, _, _ in NON_BUSINESS_KEY_CONTAINERS],
)
def test_the_sections_keyed_away_from_business_key_are_pinned(
    section: Section, property_name: str, key_field: str
) -> None:
    """AI-ASM-03: S2 spells the identity field per section, and a new spelling must be
    added to ``KEY_FIELDS`` in the same change that adds it here."""
    containers = {item.property_name: item for item in section_containers(section)}
    assert property_name in containers
    assert containers[property_name].key_field == key_field


@pytest.mark.parametrize("section", [Section.PROJECT, Section.GENERATION_POLICY], ids=str)
def test_whole_object_sections_expose_no_container(section: Section) -> None:
    """AI-RT-012: a section without a keyed array is replaced whole, so scope checks must
    not invent a container for it."""
    assert section_containers(section) == ()


def test_an_array_of_plain_strings_is_not_a_container() -> None:
    """AI-ASM-03: ``project.translationLanguages`` is an array of locale codes; requiring
    nothing, it is not something an agent may address entry by entry."""
    containers = section_containers(Section.PROJECT)
    assert "translationLanguages" not in {item.property_name for item in containers}


def test_section_containers_accepts_the_section_value_as_a_string() -> None:
    """AI-ASM-03: the ladder is handed S2's section *values* from the draft, not enum
    members."""
    assert [item.property_name for item in section_containers("mapping")] == [
        item.property_name for item in section_containers(Section.MAPPING)
    ]


def test_the_domain_section_exposes_both_of_its_containers() -> None:
    """AI-ASM-03: the discovery walks the schema, which is the only way a section with
    two containers survives."""
    assert [item.property_name for item in section_containers(Section.DOMAIN)] == [
        "entities",
        "relationships",
    ]


def test_the_rules_section_exposes_its_rule_arrays_as_containers() -> None:
    """AI-ASM-03: production defect — see the report.  S2's ``rules`` section declares five
    arrays whose items require ``businessKey``, so a Metadata Design patch may address
    ``/rules/validationRules/<businessKey>``.  Discovery resolves a ``$ref`` only on an
    ``items`` node, and these properties are ``{"$ref": "#/definitions/ruleArray"}``, so
    the section reports no containers and the ladder refuses its own agents with
    AI-SCO-005."""
    containers = {item.property_name: item for item in section_containers(Section.RULES)}
    assert {"validationRules", "programRules", "programRuleVariables", "programIndicators"} <= set(
        containers
    ), f"rules exposes {sorted(containers)}; S2 declares keyed arrays behind a property $ref"
    assert containers["validationRules"].key_field == "businessKey"
    report = _one(
        _answer(
            [_add("/rules/validationRules/-", {"businessKey": "rule.demo.001"})], decisions=[]
        ),
        "METADATA_DESIGN",
    )
    assert _rules(report) != ["AI-SCO-005"]


def test_standalone_schema_resolves_a_ref_the_bare_item_schema_cannot() -> None:
    """AI-ASM-03: this is the defect ``standalone_schema()`` exists for.  An extracted
    item still points at ``#/definitions/businessKey``; a validator built from the
    fragment alone raises instead of reporting, and dropping the definition block looks
    exactly like a passing check."""
    container = section_containers(Section.REQUIREMENTS)[0]
    entry = _requirement()
    with pytest.raises(jsonschema.exceptions._RefResolutionError) as caught:
        jsonschema.Draft202012Validator(container.item_schema).validate(entry)
    assert "PointerToNowhere" in repr(caught.value)
    assert "#/definitions/businessKey" in str(caught.value)
    assert jsonschema.Draft202012Validator(container.standalone_schema()).is_valid(entry)


def test_standalone_schema_keeps_the_items_own_definitions() -> None:
    """AI-ASM-03: an item that carries its own definition block wins over the section's
    copy of the same name."""
    container = Container(
        section="mapping",
        property_name="entries",
        item_schema={
            "type": "object",
            "required": ["businessKey"],
            "properties": {"businessKey": {"$ref": "#/definitions/businessKey"}},
            "definitions": {"businessKey": {"type": "integer"}},
        },
        definitions={"businessKey": {"type": "string"}},
    )
    schema = container.standalone_schema()
    assert schema["definitions"]["businessKey"] == {"type": "integer"}
    assert schema["$schema"].endswith("2020-12/schema")
    assert jsonschema.Draft202012Validator(schema).is_valid({"businessKey": 1})
    assert not jsonschema.Draft202012Validator(schema).is_valid({"businessKey": "1"})


def test_a_container_names_the_pointer_its_add_operation_must_use() -> None:
    """AI-RT-012: the append marker is part of the contract with S2's pointer grammar."""
    container = section_containers(Section.MAPPING)[0]
    assert container.pointer_prefix == "/mapping/entries"
    assert is_within(container.pointer_prefix, f"{container.pointer_prefix}/-")


# --- the path grammar --------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/mapping/entries/bk", ("mapping", "entries", "bk")),
        ("/requirements/items/-", ("requirements", "items", "-")),
        ("/generationPolicy", ("generationPolicy", None, None)),
        ("/", (None, None, None)),
        ("/x", ("x", None, None)),
        ("", (None, None, None)),
        ("mapping/entries/bk", (None, None, None)),
        ("/apiIntents/items/key/operationKey", ("apiIntents", "items", "key/operationKey")),
    ],
    ids=[
        "entry",
        "append",
        "whole-object",
        "root-slash",
        "section-only",
        "empty",
        "unrooted",
        "nested",
    ],
)
def test_classify_path_splits_section_container_and_remainder(
    path: str, expected: tuple[str | None, str | None, str | None]
) -> None:
    """AI-RT-012: S3 and S2 must read the same three parts out of one pointer."""
    assert classify_path(path) == expected


@pytest.mark.parametrize(
    "prefix,path,expected",
    [
        ("/requirements/items", "/requirements/items", True),
        ("/requirements/items", "/requirements/items/-", True),
        ("/requirements/items", "/requirements/items/req.demo.001", True),
        ("/requirements/items", "/requirements/items/req.demo.001/statement", True),
        ("/requirements/items", "/requirements/items-0", True),
        ("/requirements/items", "/requirements/itemsX", False),
        ("/requirements/items", "/requirements", False),
        ("/pages/items", "/apiIntents/items/-", False),
    ],
    ids=[
        "equal",
        "append-marker",
        "entry",
        "field",
        "hyphen-suffix",
        "similar-prefix",
        "parent",
        "other-section",
    ],
)
def test_is_within_decides_whether_a_path_addresses_the_prefix(
    prefix: str, path: str, expected: bool
) -> None:
    """AI-RT-012: the same test the runtime uses to keep a patch inside a page."""
    assert is_within(prefix, path) is expected


def test_is_within_the_append_marker_is_a_sibling_not_a_child() -> None:
    """AI-RT-012: ``/requirements/items/-`` addresses a new entry, so a prefix naming the
    marker matches only itself."""
    assert is_within("/requirements/items/-", "/requirements/items/-")
    assert not is_within("/requirements/items/-", "/requirements/items/req.demo.001")


def test_is_within_section_accepts_the_agents_declared_sections() -> None:
    """AI-RT-012: the runtime's own path test, exposed for the patch builder."""
    prefixes = [container.pointer_prefix for container in section_containers(Section.MAPPING)]
    assert is_within_section("/mapping/entries/-", prefixes)
    assert not is_within_section("/registry/entries/-", prefixes)


# --- the import cycle --------------------------------------------------------------


def test_the_agents_package_is_importable_on_its_own() -> None:
    """AI-RT-010: ``factory_intelligence.agents`` and ``factory_intelligence.output``
    import each other, and ``agents`` cannot be the first of the two loaded — a consumer
    that writes ``from factory_intelligence.agents.spec import spec_for`` gets an
    ImportError.  Red until ``output/__init__`` stops importing ``validate`` eagerly."""
    probe = subprocess.run(
        [sys.executable, "-c", "import factory_intelligence.agents.spec"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr[-400:]


def test_every_declared_section_of_every_spec_is_a_real_section() -> None:
    """AI-AG-001 … AI-AG-008: a spec naming a section S2 does not have would make the
    ladder refuse the agent's only legal patch."""
    values = {section.value for section in Section}
    for spec in AGENT_SPECS.values():
        assert set(spec.writable_section_values) <= values, spec.key
        assert set(section.value for section in spec.read_only_sections) <= values, spec.key
