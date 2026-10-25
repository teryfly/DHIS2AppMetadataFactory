"""The AI-RT-012 constraint rules — every one of them, with a case that passes and the
case the SRS names that must fail (AI-NFR-04, ≥90 % on ``agents/checks``).

``run_checks`` is called with **one rule name at a time**, so a failure names the rule
rather than leaving a reader to work out which of sixteen produced the finding.  What the
file is really guarding is the *narrowness* of three rules, because a checker that is
wider than the requirement it implements teaches whoever fixes the prompt to route around
it:

``uid_ban`` refuses ``uid`` and nothing UID-shaped — ``observedUid`` is what S2 accepts for
a brownfield REUSE entry (``factory_blueprint/draft/service.py:546``), and refusing it
would make S3 the obstacle AI-AG-011 was written to remove.
``no_dhis2_reference`` reads structure, never prose: a statement about "a data element"
must stay acceptable, or AI-AG-001 becomes a text filter.
``no_final_products`` needs **two** markers in one document, so a field a human happened to
name ``metadata`` is not a smuggled payload.

Each of those three is pinned in both directions on purpose.
"""

from __future__ import annotations

from typing import Any

import pytest

# Imported before ``factory_intelligence.agents`` on purpose: the two packages import each
# other and ``agents`` cannot be the first one loaded (pinned red in
# ``test_output_ladder.test_the_agents_package_is_importable_on_its_own``).
import factory_intelligence.output  # noqa: F401
from factory_intelligence.agents.checks.base import (
    CheckContext,
    affects_keys,
    entry_values,
    finding,
    removes,
    walk_documents,
    writes,
)
from factory_intelligence.agents.checks.rules import (
    DHIS2_TYPE_NAMES,
    FINAL_PRODUCT_MARKERS,
    RULE_PREFIX,
    RULES,
    run_checks,
)
from factory_intelligence.agents.spec import AGENT_SPECS, PatchMode, spec_for
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.enums import AgentType
from factory_intelligence.output.errors import LADDER_CONSTRAINT, Finding

#: A UID in DHIS2's own shape: 11 characters, letter-initial, mixed case.
UID = "p7dLt2q9xKz"
#: A lowercase eleven-letter word, which is a word and not a UID.
WORD = "demographic"
#: The SRS 3.3 stage of each agent, as the table's "所属阶段" column.
SRS_STAGES: dict[str, str] = {
    "REQUIREMENTS": "REQUIREMENTS",
    "DOMAIN": "DOMAIN",
    "MAPPING": "MAPPING",
    "METADATA_DESIGN": "MAPPING",
    "API": "PAGE_DESIGN",
    "UI": "PAGE_DESIGN",
    "FIXTURE": "PAGE_DESIGN",
    "REVIEW": "REQUIREMENTS",
}
#: AI-AG-007 ships Fixture in MAPPING (mode A) and PAGE_DESIGN (mode B); the spec runs the
#: page stage, which is where mode B's UI work lands.
SRS_STAGES["FIXTURE"] = "PAGE_DESIGN"


def _context(agent: str = "MAPPING", **changes: Any) -> CheckContext:
    arguments: dict[str, Any] = {"agent_type": agent, "stage": SRS_STAGES[agent]}
    arguments.update(changes)
    return CheckContext(**arguments)


def _output(
    operations: list[dict[str, Any]] | None = None,
    *,
    decisions: list[dict[str, Any]] | None = None,
    questions: list[dict[str, Any]] | None = None,
    summary: str = "checked answer",
    confidence: float | None = 0.9,
) -> AgentOutput:
    """A schema-shaped answer carrying exactly the operations a rule is being shown."""
    answer: dict[str, Any] = {
        "summary": summary,
        "confidence": confidence,
        "decisions": decisions if decisions is not None else [_decision()],
        "questions": questions or [],
    }
    if operations:
        answer["patch"] = {"baseVersion": "7", "operations": operations}
    return AgentOutput.model_validate(answer)


def _decision(**changes: Any) -> dict[str, Any]:
    decision: dict[str, Any] = {
        "id": "MAP-001",
        "subject": "demographic code",
        "alternatives": [{"option": "attribute", "reasonRejected": "not reportable"}],
        "confidence": 1.0,
        "impactLevel": "MEDIUM",
    }
    decision.update(changes)
    return decision


def _entry(path: str, value: Any, *, op: str = "add") -> dict[str, Any]:
    """One write operation; the path is what the rules read as an address."""
    return {"op": op, "path": path, "value": value}


#: The container each section is addressed through.  ``test_output_ladder.py`` discovers
#: these from S2's schema; they are spelled here only to keep each fixture one line.
SECTION_CONTAINERS: dict[str, str] = {
    "registry": "entries",
    "mapping": "entries",
    "requirements": "items",
    "domain": "entities",
    "fixtures": "specifications",
    "rules": "validationRules",
    "apiIntents": "items",
    "uiIntents": "items",
    "architecture": "decisions",
}


def _writes(
    *documents: dict[str, Any],
    section: str = "registry",
    container: str | None = None,
) -> list[dict[str, Any]]:
    """Each document as an ``add`` of one entry, at a pointer S2 would accept."""
    name = container if container is not None else SECTION_CONTAINERS[section]
    return [_entry(f"/{section}/{name}/-", document) for document in documents]


def _ask_about(*targets: str) -> list[dict[str, Any]]:
    return [{"id": "Q1", "text": "which of these is the real source?", "affects": list(targets)}]


def _check(
    rule: str,
    operations: list[dict[str, Any]] | None = None,
    *,
    agent: str = "MAPPING",
    decisions: list[dict[str, Any]] | None = None,
    questions: list[dict[str, Any]] | None = None,
    **context_changes: Any,
) -> list[Finding]:
    """Run exactly one rule, so a failure names it."""
    output = _output(operations, decisions=decisions, questions=questions)
    return run_checks(output, _context(agent, **context_changes), [rule])


def _ids(findings: list[Finding]) -> list[str]:
    return [item.rule_id for item in findings]


def _location(item: Finding) -> str:
    """A rule that blames something always says where; ``None`` is itself a failure."""
    assert item.location is not None, item
    return item.location


# --- the scaffolding the rules are built on ---------------------------------------


def test_writes_sees_add_and_replace_and_nothing_else() -> None:
    """AI-RT-012: rules read the answer's writes, and a ``remove`` is not a write."""
    output = _output(
        [
            _entry("/mapping/entries/-", {"businessKey": "map.a"}),
            _entry("/mapping/entries/map.a", {"businessKey": "map.a"}, op="replace"),
            _entry("/mapping/entries/map.b", None, op="remove"),
        ]
    )
    seen = writes(output)
    assert [item.op for item in seen] == ["add", "replace"]
    assert [item.section for item in seen] == ["mapping", "mapping"]
    assert [item.container for item in seen] == ["entries", "entries"]


def test_writes_of_an_answer_without_a_patch_is_empty() -> None:
    """AI-AG-008: an explain-only answer has nothing for a rule to read."""
    assert writes(_output(None)) == []
    assert removes(_output(None)) == []


def test_write_entry_key_is_none_for_the_append_marker() -> None:
    """AI-RT-012: ``-`` addresses a new entry, whose key does not exist yet, so a rule
    must not treat the marker as an identity."""
    output = _output([_entry("/mapping/entries/-", {"businessKey": "map.a"})])
    assert output.patch is not None
    assert writes(output)[0].entry_key is None
    addressed = _output([_entry("/mapping/entries/map.a", {"businessKey": "map.a"})])
    assert writes(addressed)[0].entry_key == "map.a"
    # a whole-object section such as generationPolicy is addressed as one object, so there
    # is no entry key to read and no rule may invent one
    whole = _output([_entry("/generationPolicy", {"codePrefix": "d2"}, op="replace")])
    assert [(item.section, item.container, item.entry_key) for item in writes(whole)] == [
        ("generationPolicy", None, None)
    ]


def test_removes_returns_index_and_path() -> None:
    """AI-RT-012: the rules that forbid a deletion still have to see it."""
    output = _output(
        [
            _entry("/registry/entries/-", {"businessKey": "reg.a"}),
            _entry("/mapping/entries/map.a", None, op="remove"),
        ]
    )
    assert removes(output) == [(1, "/mapping/entries/map.a")]


def test_walk_documents_reaches_every_nested_mapping() -> None:
    """AI-BR-01: a payload hidden inside ``structure.parts[2]`` is as final a product as
    one at the top level, so the walk is the rules' only defence."""
    value = {"a": {"b": [{"c": 1}]}, "list": ["scalar", {"d": 2}], "plain": 3}
    walked = list(walk_documents(value, path="$"))
    assert [path for path, _document in walked] == [
        "$",
        "$/a",
        "$/a/b/0",
        "$/list/1",
    ]
    assert walked[0][1]["plain"] == 3
    # an operation value may be a bare scalar, and walking it must not raise
    assert list(walk_documents("text")) == []
    assert list(walk_documents(None)) == []




def test_entry_values_selects_one_container_of_one_section() -> None:
    """AI-RT-012: a Mapping rule reads mapping entries, not the registry entries in the
    same answer."""
    output = _output(
        [
            _entry("/mapping/entries/-", {"businessKey": "map.a"}),
            _entry("/registry/entries/-", {"businessKey": "reg.a"}),
            _entry("/mapping/notEntries/-", {"businessKey": "nope"}),
        ]
    )
    assert [item["businessKey"] for item in entry_values(output, "mapping", "entries")] == ["map.a"]
    assert entry_values(output, "registry", "notAContainer") == []


def test_entry_values_ignores_an_operation_whose_value_is_not_an_object() -> None:
    """AI-RT-012: a ``replace`` of a scalar has no entry to read."""
    output = _output([_entry("/mapping/entries/map.a", "just a string")])
    assert entry_values(output, "mapping", "entries") == []


def test_affects_keys_collects_the_identity_fields_of_an_entry() -> None:
    """AI-AG-012: a question is tied to the thing it gates by one of these four fields."""
    entry = {"businessKey": "map.a", "decisionId": "MAP-001", "intentKey": "", "pageKey": "page-a"}
    assert affects_keys(entry) == {"map.a", "MAP-001", "page-a"}


def test_finding_reports_the_constraint_rung() -> None:
    """AI-RT-012: a constraint failure is never filed as a schema failure."""
    item = finding("AIC-001", "message", location="here")
    assert item.ladder == LADDER_CONSTRAINT
    assert item.rule_id == f"{RULE_PREFIX}-001"


# --- uid_ban ----------------------------------------------------------------------


def test_uid_ban_refuses_a_uid_on_a_registry_create() -> None:
    """AI-AC-02: a CREATE entry carrying ``uid`` is refused before S2 ever sees it."""
    findings = _check(
        "uid_ban",
        _writes({"businessKey": "reg.a", "disposition": "CREATE", "uid": UID}),
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-003"]
    assert "allocated by S2" in findings[0].message


def test_uid_ban_refuses_a_plural_uids_field() -> None:
    """AI-AC-02: the same ban, whichever spelling the model reaches for."""
    findings = _check("uid_ban", _writes({"businessKey": "reg.a", "uids": [UID]}))
    assert _ids(findings) == [f"{RULE_PREFIX}-003"]
    assert "businessKeys instead" in findings[0].message


def test_uid_ban_allows_an_observed_uid() -> None:
    """AI-AG-011: ``observedUid`` records what discovery saw and is what S2 accepts for a
    brownfield entry — refusing it would refuse every REUSE."""
    findings = _check("uid_ban", _writes({"businessKey": "reg.a", "disposition": "REUSE", "observedUid": UID}))
    assert findings == []


def test_uid_ban_is_only_about_the_uid_field() -> None:
    """AI-AC-02: ``decisionId`` and ``code`` are identities too, and neither is a UID."""
    findings = _check("uid_ban", _writes({"businessKey": "reg.a", "decisionId": "MAP-001", "code": WORD}))
    assert findings == []


def test_uid_ban_finds_a_uid_nested_inside_a_structure() -> None:
    """AI-AC-02: the ban is on writing a UID anywhere, not on the top level of an entry."""
    findings = _check(
        "uid_ban",
        _writes({"businessKey": "reg.a", "structure": {"parts": [{"name": "stage", "uid": UID}]}}),
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-003"]
    assert _location(findings[0]).endswith("/structure/parts/0")


def test_uid_ban_ignores_a_remove() -> None:
    """AI-RT-012: deleting an entry is not writing a UID into one."""
    findings = _check("uid_ban", [_entry("/registry/entries/reg.a", None, op="remove")])
    assert findings == []


# --- no_final_products / no_dhis2_payload -----------------------------------------


@pytest.mark.parametrize(
    "document,markers",
    [
        ({"@class": "DataElement", "href": "/api/dataElements/p7d"}, "@class/href"),
        ({"openapi": "3.0.1", "paths": {"/x": {}}}, "openapi/paths"),
        ({"uiSpecVersion": "1.0", "components": []}, "uiSpecVersion/components"),
        ({"metadata": [], "importStrategy": "CREATE"}, "metadata/importStrategy"),
    ],
    ids=["dhis2-metadata", "openapi", "ui-spec", "import-envelope"],
)
def test_a_final_product_in_a_patch_value_is_refused(document: dict[str, Any], markers: str) -> None:
    """AI-BR-01 / SRS 1.3: one of these four documents is a deliverable, and it arrived
    through the wrong door."""
    _ = markers
    findings = _check("no_final_products", _writes(document))
    assert set(_ids(findings)) == {f"{RULE_PREFIX}-001"}
    assert len(findings) == 2  # the banner plus the offending document


def test_a_final_product_nested_in_a_value_is_refused() -> None:
    """AI-BR-01: the walk covers nesting, so a payload cannot hide one level down."""
    findings = _check(
        "no_final_products",
        _writes({"businessKey": "reg.a", "structure": {"parts": [{"@class": "DataElement", "href": "/api/x"}]}}),
    )
    assert findings and _location(findings[-1]).endswith("/structure/parts/0")


def test_a_final_product_inside_a_decision_is_refused() -> None:
    """AI-BR-01: the ban covers the whole answer, not only its patch."""
    decision = _decision(selected={"@class": "DataElement", "href": "/api/dataElements/p7d"})
    findings = _check("no_final_products", None, decisions=[decision])
    assert _ids(findings) == [f"{RULE_PREFIX}-001", f"{RULE_PREFIX}-001"]
    assert findings[-1].location == "decision/selected"


@pytest.mark.parametrize(
    "document",
    [{"metadata": "the design metadata"}, {"paths": ["/a"]}, {"components": ["Table"]}, {"href": "/api/x"}],
    ids=["metadata", "paths", "components", "href"],
)
def test_one_marker_alone_is_not_a_final_product(document: dict[str, Any]) -> None:
    """AI-BR-01: the two-marker rule is deliberate — a field a human named ``metadata`` is
    far more likely than a smuggled payload, and this pins it so nobody widens the rule."""
    assert _check("no_final_products", _writes(document)) == []


def test_the_marker_list_is_the_one_the_docstring_claims() -> None:
    """AI-BR-01: the vocabulary is public; a marker removed from it silently stops being
    banned."""
    assert {"@class", "href", "openapi", "paths", "uiSpecVersion", "components"} <= set(
        FINAL_PRODUCT_MARKERS
    )


def test_no_dhis2_payload_refuses_a_metadata_object_by_class() -> None:
    """AI-AG-003 / AI-AG-006: the agents whose output *is* about DHIS2 get the same ban,
    worded for them."""
    findings = _check("no_dhis2_payload", _writes({"@class": "dataElement", "shortName": "n"}))
    assert _ids(findings) == [f"{RULE_PREFIX}-001"]
    assert "S2's compiler emits the metadata" in findings[0].message


def test_no_dhis2_payload_needs_href_and_id_together() -> None:
    """AI-AG-003: ``href`` with an ``id`` is a REST object; either alone is prose."""
    assert _check("no_dhis2_payload", _writes({"href": "/api/x"})) == []
    assert _check("no_dhis2_payload", _writes({"id": UID})) == []
    findings = _check("no_dhis2_payload", _writes({"href": "/api/x", "id": UID}))
    assert _ids(findings) == [f"{RULE_PREFIX}-001"]


def test_no_dhis2_payload_does_not_look_at_decisions() -> None:
    """AI-AG-003: this rule is about the patch; ``no_final_products`` owns the answer."""
    decision = _decision(selected={"@class": "DataElement", "href": "/api/x"})
    assert _check("no_dhis2_payload", None, decisions=[decision]) == []


# --- no_dhis2_reference -----------------------------------------------------------


def test_a_key_that_is_a_dhis2_type_name_is_refused() -> None:
    """AI-AG-001: a requirements answer may not introduce a DHIS2 object *type*."""
    findings = _check(
        "no_dhis2_reference",
        _writes({"dataElement": {"name": "x"}}, section="requirements"),
        agent="REQUIREMENTS",
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-002"]
    assert "the stage that owns DHIS2 shapes is MAPPING" in findings[0].message


def test_a_business_key_that_is_a_dhis2_type_name_is_refused() -> None:
    """AI-AG-001: the same ban on the entry's own identity."""
    findings = _check(
        "no_dhis2_reference",
        [_entry("/requirements/items/dataElement", {"statement": "x", "source": {}})],
        agent="REQUIREMENTS",
    )
    assert f"{RULE_PREFIX}-002" in _ids(findings)
    assert "DHIS2 object type name" in findings[-1].message


@pytest.mark.parametrize(
    "value",
    [
        {"businessKey": "req.a", "statement": "each pregnancy must be recorded as a data element"},
        {"businessKey": "req.a", "statement": "reuse the dataElement the environment publishes"},
    ],
    ids=["prose-spaces", "prose-camel"],
)
def test_prose_naming_a_dhis2_object_is_not_a_reference(value: dict[str, Any]) -> None:
    """AI-AG-001: this narrow reading is the whole point.  Widening the rule to text would
    reject a legitimate statement and teach whoever fixes the prompt to route around the
    checker — pinned so nobody does it later."""
    assert _check(
        "no_dhis2_reference", _writes(value, section="requirements"), agent="REQUIREMENTS"
    ) == []


@pytest.mark.parametrize(
    "field",
    ["dhis2Type", "disposition", "uid", "controlledFields", "optionSet"],
    ids=["dhis2Type", "disposition", "uid", "controlledFields", "optionSet"],
)
def test_mapping_only_fields_are_refused_under_a_requirements_path(field: str) -> None:
    """AI-AG-001 / AI-AG-002: those five fields belong to a later stage; a requirements or
    domain entry may not carry them."""
    value = {"businessKey": "req.a", "statement": "x", "source": {}}
    value[field] = {"nested": 1} if field == "optionSet" else ["a"] if field == "controlledFields" else "v"
    findings = _check("no_dhis2_reference", _writes(value, section="requirements"), agent="REQUIREMENTS")
    assert _ids(findings) != []
    assert all(item.rule_id == f"{RULE_PREFIX}-002" for item in findings)
    assert any(field in item.message for item in findings)


def test_the_domain_agent_is_bound_by_the_same_rule() -> None:
    """AI-AG-002: the Domain Agent's prohibition is the same sentence as the Requirements
    Agent's."""
    findings = _check(
        "no_dhis2_reference",
        _writes({"businessKey": "person.a", "categoryCombo": {"default": True}}, section="domain"),
        agent="DOMAIN",
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-002"]


def test_the_type_vocabulary_is_lowercase_normalised() -> None:
    """AI-AG-001: ``data_element``, ``dataElement`` and ``DataElement`` are one name."""
    for key in ("dataElement", "data_element", "DataElement"):
        findings = _check(
            "no_dhis2_reference", _writes({key: 1}, section="requirements"), agent="REQUIREMENTS"
        )
        assert findings, key
    assert "dataelement" in DHIS2_TYPE_NAMES


def test_only_the_write_that_carries_the_field_is_blamed() -> None:
    """AI-AG-001 / AI-RT-011: with two requirements entries in one answer, the finding
    names the one that carries the mapping-only field."""
    operations = [
        _entry("/requirements/items/req.a", {"businessKey": "req.a", "dhis2Type": "dataElement"}),
        _entry("/requirements/items/req.b", {"businessKey": "req.b"}),
    ]
    findings = _check("no_dhis2_reference", operations, agent="REQUIREMENTS")
    assert _ids(findings) == [f"{RULE_PREFIX}-002"]
    assert findings[0].location == "/requirements/items/req.a"


def test_a_clean_requirements_entry_is_accepted() -> None:
    """AI-AG-001: the pass case — an entry that only says what the business needs."""
    value = {"businessKey": "req.a", "statement": "record each birth", "source": {"note": "interview"}}
    assert _check(
        "no_dhis2_reference", _writes(value, section="requirements"), agent="REQUIREMENTS"
    ) == []


# --- decision_coverage ------------------------------------------------------------


def test_a_mapping_entry_without_a_decision_is_refused() -> None:
    """AI-AG-003: every mapped element carries the decision that chose its shape."""
    findings = _check("decision_coverage", _writes({"businessKey": "map.a"}, section="mapping", container="entries"))
    assert _ids(findings) == [f"{RULE_PREFIX}-004"]
    assert "no decisionId" in findings[0].message


def test_a_registry_entry_is_read_by_the_same_rule() -> None:
    """AI-AG-003: the Registry entries an agent proposes are decisions too."""
    findings = _check("decision_coverage", _writes({"businessKey": "reg.a"}, section="registry", container="notIt"))
    assert findings == []
    findings = _check("decision_coverage", _writes({"businessKey": "reg.a"}, section="registry"))
    assert _ids(findings) == [f"{RULE_PREFIX}-004"]


def test_a_decision_the_answer_never_makes_is_refused_by_name() -> None:
    """AI-AG-003: citing an undefined decision is worse than omitting one, because S2
    would resolve nothing."""
    entry = {"businessKey": "map.a", "decisionId": "MAP-999"}
    findings = _check(
        "decision_coverage",
        _writes(entry, section="mapping"),
        decisions=[_decision(id="MAP-001")],
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-004"]
    assert "MAP-999" in findings[0].message


def test_an_unmapped_element_is_exempt_from_the_rule() -> None:
    """AI-AG-003: requiring a decision for ``notMapped: true`` would push the model to
    invent a fake option to satisfy the checker."""
    entry = {"businessKey": "map.a", "notMapped": True, "notMappedReason": "purely a workflow step"}
    assert (
        _check(
            "decision_coverage", _writes(entry, section="mapping"), decisions=[_decision()]
        )
        == []
    )


def test_a_decision_without_an_alternative_is_refused() -> None:
    """AI-AG-003: a multi-solution element needs at least one rejected alternative."""
    decision = _decision(alternatives=[], confidence=0.8)
    entry = {"businessKey": "map.a", "decisionId": "MAP-001"}
    findings = _check(
        "decision_coverage", _writes(entry, section="mapping"), decisions=[decision]
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-004"]
    assert "no alternative" in findings[0].message


def test_a_certain_forced_choice_needs_no_alternative() -> None:
    """AI-AG-003: confidence 1.0 says the choice was forced; fabricating a rejected option
    to satisfy a checker is worse than no option."""
    decision = _decision(alternatives=[], confidence=1.0)
    entry = {"businessKey": "map.a", "decisionId": "MAP-001"}
    assert (
        _check(
            "decision_coverage", _writes(entry, section="mapping"), decisions=[decision]
        )
        == []
    )


def test_a_decision_may_be_keyed_by_either_spelling() -> None:
    """AI-AG-003: the entry cites an id; the answer may name it ``id`` or ``decisionId``."""
    entry = {"businessKey": "map.a", "decisionId": "MAP-001"}
    decision = _decision(alternatives=[], confidence=1.0)
    decision["decisionId"] = decision.pop("id")
    assert (
        _check(
            "decision_coverage", _writes(entry, section="mapping"), decisions=[decision]
        )
        == []
    )


def test_a_decision_without_an_identifier_is_named_as_unknown() -> None:
    """AI-AG-003: an unidentifiable decision is still reported, never skipped."""
    findings = _check("decision_coverage", None, decisions=[{"confidence": 0.2, "alternatives": []}])
    assert _ids(findings) == [f"{RULE_PREFIX}-004"]
    assert "'?'" in findings[0].message


# --- low_confidence_question (AI-AG-012) ------------------------------------------


def test_a_weak_decision_without_a_question_is_refused() -> None:
    """AI-AG-012 / AI-BR-05: below the threshold an agent may not adopt its own decision."""
    decision = _decision(confidence=0.4, alternatives=[{"option": "x"}])
    findings = _check("low_confidence_question", None, decisions=[decision])
    assert _ids(findings) == [f"{RULE_PREFIX}-005"]
    assert "0.4 < 0.7" in findings[0].message


def test_the_same_decision_asked_of_a_human_is_accepted() -> None:
    """AI-AG-012: the question must name the decision it gates."""
    decision = _decision(confidence=0.4, alternatives=[{"option": "x"}])
    findings = _check(
        "low_confidence_question",
        None,
        decisions=[decision],
        questions=_ask_about("MAP-001"),
    )
    assert findings == []


def test_a_high_impact_decision_is_asked_even_at_full_confidence() -> None:
    """AI-AG-012: impact HIGH is the second half of the rule; certainty is not consent."""
    decision = _decision(confidence=1.0, impactLevel="high")
    findings = _check("low_confidence_question", None, decisions=[decision])
    assert _ids(findings) == [f"{RULE_PREFIX}-005"]
    assert "impact level HIGH" in findings[0].message
    assert _check("low_confidence_question", None, decisions=[decision], questions=_ask_about("MAP-001")) == []


def test_a_question_may_name_the_decisions_subject_instead_of_its_id() -> None:
    """AI-AG-012: a model describes what it is asking about in business words; matching on
    the subject is what keeps this rule usable."""
    decision = _decision(confidence=0.3, subject="demographic code")
    assert _check(
        "low_confidence_question", None, decisions=[decision], questions=_ask_about("demographic code")
    ) == []


def test_the_threshold_comes_from_the_blueprint_not_the_answer() -> None:
    """AI-AG-012 / AI-INP-008: the comparison is against ``decisionConfidenceThreshold``,
    which S2 owns."""
    decision = _decision(confidence=0.6, alternatives=[{"option": "x"}])
    assert _check("low_confidence_question", None, decisions=[decision], questions=_ask_about("MAP-001")) == []
    findings = _check("low_confidence_question", None, decisions=[decision], decision_confidence_threshold=0.5)
    assert findings == []


def test_a_decision_without_an_identifier_is_left_alone() -> None:
    """AI-AG-012: nothing can be asked about an unnamed decision, and inventing an id for
    the question would be S3 deciding."""
    assert _check("low_confidence_question", None, decisions=[{"confidence": 0.1}]) == []


def test_a_subject_list_counts_as_the_decision_surface() -> None:
    """AI-AG-012: ``affectedArtifacts`` is the other spelling a real model uses."""
    decision = _decision(confidence=0.3, affectedArtifacts=["map.demo.001"])
    findings = _check("low_confidence_question", None, decisions=[decision])
    assert _ids(findings) == [f"{RULE_PREFIX}-005"]
    assert _check(
        "low_confidence_question", None, decisions=[decision], questions=_ask_about("map.demo.001")
    ) == []


# --- reuse_decision_per_similar_object (AI-AG-011) -------------------------------


def test_an_unaddressed_similar_object_is_refused() -> None:
    """AI-AG-011: every similar object found in the reference environment needs a REUSE or
    CREATE decision with a reason."""
    findings = _check(
        "reuse_decision_per_similar_object",
        _writes({"businessKey": "map.a"}),
        similar_object_keys=["demographic_code"],
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-006"]
    assert "demographic_code" in findings[0].message


def test_a_decision_naming_the_similar_object_is_accepted() -> None:
    """AI-AG-011: the decision may be recorded anywhere in the answer's decisions."""
    decision = _decision(subject="reuse demographic_code from the reference environment")
    findings = _check(
        "reuse_decision_per_similar_object",
        None,
        decisions=[decision],
        similar_object_keys=["demographic_code"],
    )
    assert findings == []


def test_a_patch_entry_naming_the_similar_object_counts_too() -> None:
    """AI-AG-011: the surface is decisions *and* patch content; an entry keyed by the
    similar object's businessKey is a reuse decision."""
    findings = _check(
        "reuse_decision_per_similar_object",
        _writes({"businessKey": "demographic_code"}),
        similar_object_keys=["demographic_code"],
    )
    assert findings == []


def test_no_similar_objects_means_no_findings() -> None:
    """AI-AG-011: with nothing found by the similarity search the rule is silent — an
    empty context must not become an empty answer's failure."""
    assert _check("reuse_decision_per_similar_object", _writes({"businessKey": "map.a"})) == []


def test_every_unaddressed_similar_object_is_named() -> None:
    """AI-AG-011: one finding per object, so the retry can fix all of them."""
    findings = _check(
        "reuse_decision_per_similar_object",
        None,
        decisions=[],
        similar_object_keys=["demographic_code", "phone_number"],
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-006", f"{RULE_PREFIX}-006"]
    assert {item.location for item in findings} == {"demographic_code", "phone_number"}


# --- business_key_references_only (AI-AG-006) ------------------------------------


@pytest.mark.parametrize("token", [UID, "ZebraCodes1", "p7DLt2q9xKz"], ids=["mixed", "digits", "both"])
def test_a_uid_inside_an_expression_is_refused(token: str) -> None:
    """AI-AG-006 / FR-CMP-010: an expression frozen to one environment's UID is not
    portable design."""
    findings = _check(
        "business_key_references_only", _entry_expression(token), agent="METADATA_DESIGN"
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-007"]
    assert "reference objects by businessKey" in findings[0].message


def _entry_expression(token: str) -> list[dict[str, Any]]:
    return [_entry("/rules/validationRules/-", {"expression": f"D2:VALUE({token}) > 5"})]


@pytest.mark.parametrize(
    "value",
    [f"D2:VALUE({WORD}) > 5", f"D2:VALUE({WORD}Value) > 5", "D2:VALUE(demographic) + D2:VALUE(birthweight)"],
    ids=["eleven-lowercase", "eleven-then-more", "two-eleven-words"],
)
def test_an_eleven_letter_lowercase_word_is_not_a_uid(value: str) -> None:
    """AI-AG-006: length alone is not the test — an all-lowercase run of eleven letters is
    a word, and refusing it would refuse every expression written by businessKey."""
    assert _check("business_key_references_only", _expression(value), agent="METADATA_DESIGN") == []


def _expression(value: str) -> list[dict[str, Any]]:
    return [_entry("/rules/validationRules/-", {"expression": value})]


def test_the_rule_reads_only_the_expression_fields() -> None:
    """AI-AG-006: a name or a note quoting an observed UID is not an expression; only the
    four fields the compiler substitutes into are scanned."""
    document = {"name": UID, "rationale": UID, "kind": UID}
    assert _check("business_key_references_only", _writes(document), agent="METADATA_DESIGN") == []


@pytest.mark.parametrize(
    "field", ["expression", "candidateExpressions", "programExpression", "indicatorExpression"],
    ids=["expression", "candidateExpressions", "programExpression", "indicatorExpression"],
)
def test_every_expression_spelling_is_scanned(field: str) -> None:
    """AI-AG-006: the candidate set is a list and the indicator forms are its own fields;
    none may slip through."""
    document: dict[str, Any] = {"businessKey": "rule.a"}
    document[field] = [f"D2:VALUE({UID})"] if field == "candidateExpressions" else UID
    findings = _check(
        "business_key_references_only", _writes(document, section="rules"), agent="METADATA_DESIGN"
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-007"]


def test_a_non_text_expression_is_not_tokenised() -> None:
    """AI-AG-006: an expression is text; a number or a mapping in that field is rung 1's
    schema problem, not a UID for this rule."""
    document = {"businessKey": "rule.a", "expression": 42, "candidateExpressions": {"a": 1}}
    findings = _check(
        "business_key_references_only", _writes(document, section="rules"), agent="METADATA_DESIGN"
    )
    assert findings == []


# --- attribute_sensitivity (FR-DOM-001) ------------------------------------------


def test_an_attribute_without_a_sensitivity_is_refused() -> None:
    """FR-DOM-001 / AI-AG-002: an unlabelled attribute cannot be classified by AI-POL-001
    and cannot be built safely."""
    entity = {
        "businessKey": "person.a",
        "attributes": [{"name": "medicalNumber", "valueType": "TEXT", "required": True}],
    }
    findings = _check(
        "attribute_sensitivity", _writes(entity, section="domain"), agent="DOMAIN"
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-008"]
    assert "medicalNumber" in findings[0].message


def test_a_labelled_attribute_is_accepted() -> None:
    """FR-DOM-001 / AI-AG-002: the pass case."""
    entity = {
        "businessKey": "person.a",
        "attributes": [
            {"name": "medicalNumber", "valueType": "TEXT", "required": True, "sensitivity": "PHI_PII"}
        ],
    }
    assert _check("attribute_sensitivity", _writes(entity, section="domain"), agent="DOMAIN") == []


def test_an_entity_without_attributes_reports_nothing() -> None:
    """FR-DOM-001 / AI-AG-002: an entity may legitimately have no attributes; the rule
    must not invent one."""
    assert _check(
        "attribute_sensitivity",
        _writes({"businessKey": "person.a"}, section="domain"),
        agent="DOMAIN",
    ) == []


def test_a_non_object_attribute_is_skipped_not_crashed_on() -> None:
    """AI-RT-012: a malformed entry is rung 1's business; a constraint rule may not raise."""
    entity = {"businessKey": "person.a", "attributes": ["just a string", {"name": "code"}]}
    findings = _check(
        "attribute_sensitivity", _writes(entity, section="domain"), agent="DOMAIN"
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-008"]
    assert _location(findings[0]).endswith("attributes.1")


# --- nrr_confidence_parity (AI-INP-008) ------------------------------------------


def test_a_confirmed_item_below_the_threshold_is_refused() -> None:
    """AI-INP-008: a low-confidence extraction may not enter the confirmed part of the NRR."""
    item = {"businessKey": "req.a", "status": "CONFIRMED", "confidence": 0.4}
    findings = _check(
        "nrr_confidence_parity",
        _writes(item, section="requirements"),
        agent="REQUIREMENTS",
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-009"]
    assert "stay CANDIDATE" in findings[0].message


def test_a_candidate_below_the_threshold_is_accepted() -> None:
    """AI-INP-008: CANDIDATE is exactly where a weak item belongs."""
    item = {"businessKey": "req.a", "status": "CANDIDATE", "confidence": 0.2}
    assert _check("nrr_confidence_parity", _writes(item, section="requirements"), agent="REQUIREMENTS") == []


def test_a_confirmed_item_at_the_threshold_is_accepted() -> None:
    """AI-INP-008: the comparison is strictly below."""
    item = {"businessKey": "req.a", "status": "CONFIRMED", "confidence": 0.7}
    assert _check("nrr_confidence_parity", _writes(item, section="requirements"), agent="REQUIREMENTS") == []


def test_an_agent_may_not_reject_a_requirement() -> None:
    """AI-INP-008: REJECTED is a human verdict recorded at the Gate, whatever its
    confidence says."""
    item = {"businessKey": "req.a", "status": "REJECTED", "confidence": 0.99}
    findings = _check("nrr_confidence_parity", _writes(item, section="requirements"), agent="REQUIREMENTS")
    assert _ids(findings) == [f"{RULE_PREFIX}-009"]
    assert "that verdict is a human's" in findings[0].message


def test_an_item_without_a_status_is_left_to_rung_one() -> None:
    """AI-INP-008: S2's schema requires the status; this rule only polices the parity."""
    assert _check(
        "nrr_confidence_parity",
        _writes({"businessKey": "req.a"}, section="requirements"),
        agent="REQUIREMENTS",
    ) == []


# --- single_page_scope (AI-AG-004, AI-AG-013) ------------------------------------


def test_an_answer_spanning_two_pages_is_refused() -> None:
    """AI-AG-013 / FR-API-004: one run designs one page, because the stage runs per page."""
    operations = [
        _entry("/apiIntents/items/-", {"intentKey": "api.a", "pageKey": "antenatal-list"}),
        _entry("/apiIntents/items/-", {"intentKey": "api.b", "pageKey": "child-list"}),
    ]
    findings = _check("single_page_scope", operations, agent="API")
    assert _ids(findings) == [f"{RULE_PREFIX}-012"]
    assert "2 pages" in findings[0].message


def test_an_answer_about_one_page_is_accepted() -> None:
    """AI-AG-013: two intents on the same page is one page's design."""
    operations = [
        _entry("/apiIntents/items/-", {"intentKey": "api.a", "pageKey": "antenatal-list"}),
        _entry("/apiIntents/items/-", {"intentKey": "api.b", "pageKey": "antenatal-list"}),
    ]
    assert _check("single_page_scope", operations, agent="API") == []


def test_an_answer_naming_no_page_is_accepted() -> None:
    """AI-AG-013: the rule counts pages it can find; it does not require one."""
    assert _check("single_page_scope", _writes({"businessKey": "reg.a"}), agent="API") == []


def test_a_page_key_inside_the_path_is_counted() -> None:
    """AI-AG-013: an answer with no ``pageKey`` field but two page-scoped paths still spans
    two pages."""
    operations = [
        _entry("/pages/items/page:antenatal-list", {"purpose": "a"}),
        _entry("/pages/items/page:child-list", {"purpose": "b"}),
    ]
    assert _ids(_check("single_page_scope", operations, agent="API")) == [f"{RULE_PREFIX}-012"]


def test_a_remove_naming_a_second_page_is_counted() -> None:
    """AI-AG-013: deleting another page's intent is out of scope too."""
    operations = [
        _entry("/apiIntents/items/-", {"intentKey": "api.a", "pageKey": "antenatal-list"}),
        _entry("/apiIntents/items/page:child-list", None, op="remove"),
    ]
    assert _ids(_check("single_page_scope", operations, agent="API")) == [f"{RULE_PREFIX}-012"]


# --- no_registry_write (AI-AG-004) -----------------------------------------------


@pytest.mark.parametrize(
    "path", ["/registry/entries/reg.a", "/mapping/entries/map.a", "/architecture/decisions/ADR-001"],
    ids=["registry", "mapping", "architecture"],
)
def test_a_page_agent_may_not_edit_a_frozen_design_fact(path: str) -> None:
    """AI-AG-004: the API agent's job is the page, not the mapping or the Registry."""
    findings = _check("no_registry_write", [_entry(path, {"businessKey": "x"})], agent="API")
    assert _ids(findings) == [f"{RULE_PREFIX}-013"]
    assert findings[0].location == path


def test_a_page_agents_own_intent_is_not_a_frozen_fact() -> None:
    """AI-AG-004: the pass case — writing the apiIntents section is the whole job."""
    operations = [_entry("/apiIntents/items/-", {"intentKey": "api.a"})]
    assert _check("no_registry_write", operations, agent="API") == []


def test_a_removal_from_the_registry_is_also_refused() -> None:
    """AI-AG-004: deleting a Registry entry edits it."""
    findings = _check("no_registry_write", [_entry("/registry/entries/reg.a", None, op="remove")], agent="API")
    assert _ids(findings) == [f"{RULE_PREFIX}-013"]


# --- logical_resource_names (AI-AG-004) -----------------------------------------


def test_a_resource_named_with_a_uid_is_refused() -> None:
    """AI-AG-004: a UID in a resource name freezes one environment's identity into the
    design; the resolver binds per environment instead."""
    findings = _check(
        "logical_resource_names",
        [_entry("/apiIntents/items/-", {"intentKey": "api.a", "resource": f"dataelement.{UID}"})],
        agent="API",
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-014"]
    assert "name the resource logically" in findings[0].message


def test_a_logically_named_resource_is_accepted() -> None:
    """AI-AG-004: an eleven-letter lowercase segment is a word, so the same length test
    that protects expressions protects names."""
    operations = [_entry("/apiIntents/items/-", {"intentKey": "api.a", "resource": f"{WORD}.record"})]
    assert _check("logical_resource_names", operations, agent="API") == []


def test_the_resource_name_spelling_is_covered() -> None:
    """AI-AG-004: a model that writes ``resourceName`` is describing the same thing."""
    operations = [_entry("/apiIntents/items/-", {"resourceName": f"dataelement.{UID}"})]
    assert _ids(_check("logical_resource_names", operations, agent="API")) == [f"{RULE_PREFIX}-014"]


# --- ui_data_source_resolves (AI-AG-005) ----------------------------------------


def test_a_data_source_that_is_nothing_offered_is_refused() -> None:
    """AI-AG-005 / FR-UII-002: a UI component may not invent a source its page's API
    intent never offered — that is the drift AI-AG-013 exists to prevent."""
    operations = [_entry("/uiIntents/items/-", {"components": [{"name": "Table", "dataSource": "mystery"}]})]
    findings = _check(
        "ui_data_source_resolves", operations, agent="UI", api_operation_keys=["antenatalList"]
    )
    assert _ids(findings) == [f"{RULE_PREFIX}-015"]
    assert "operationKey" in findings[0].message


def test_a_known_operation_key_is_accepted() -> None:
    """AI-AG-005: the pass case."""
    operations = [_entry("/uiIntents/items/-", {"components": [{"name": "Table", "dataSource": "antenatalList"}]})]
    assert _check("ui_data_source_resolves", operations, agent="UI", api_operation_keys=["antenatalList"]) == []


def test_a_registry_reference_is_accepted() -> None:
    """AI-AG-005: a component may bind straight to a Registry entry, which is the other
    half of the requirement."""
    operations = [_entry("/uiIntents/items/-", {"components": [{"name": "Select", "dataSource": "registry:demographic.code"}]})]
    assert _check("ui_data_source_resolves", operations, agent="UI", api_operation_keys=[]) == []


def test_an_operation_prefixed_source_must_still_resolve() -> None:
    """AI-AG-005: ``operation:`` says which kind of reference it is, not that it exists."""
    operations = [_entry("/uiIntents/items/-", {"source": "operation:mystery"})]
    assert _ids(_check("ui_data_source_resolves", operations, agent="UI", api_operation_keys=["known"])) == [
        f"{RULE_PREFIX}-015"
    ]


def test_a_bare_source_string_is_not_read_as_a_data_source() -> None:
    """AI-AG-005: ``source`` is provenance unless it is prefixed with a kind; a rule that
    read every ``source`` would refuse every entry."""
    operations = [_entry("/uiIntents/items/-", {"source": "the antenatal interview"})]
    assert _check("ui_data_source_resolves", operations, agent="UI", api_operation_keys=[]) == []


def test_an_empty_data_source_is_ignored() -> None:
    """AI-AG-005: nothing to resolve, nothing to refuse."""
    operations = [_entry("/uiIntents/items/-", {"components": [{"name": "Heading", "dataSource": ""}]})]
    assert _check("ui_data_source_resolves", operations, agent="UI", api_operation_keys=[]) == []


def test_the_operation_key_field_is_checked_as_a_source() -> None:
    """AI-AG-005: an intent that names its operation directly is bound the same way."""
    operations = [_entry("/uiIntents/items/-", {"operationKey": "mystery"})]
    assert _ids(_check("ui_data_source_resolves", operations, agent="UI", api_operation_keys=["known"])) == [
        f"{RULE_PREFIX}-015"
    ]


# --- no_phi_fixture / synthetic_only (AI-AG-007) --------------------------------


def test_a_fixture_based_on_a_phi_artifact_is_refused() -> None:
    """AI-AG-007 / AI-POL-010: real patient data may not be a fixture basis under any
    policy, including a relaxed one."""
    operations = [_entry("/fixtures/specifications/-", {"businessKey": "fx.a", "sourceArtifactId": "art-aaaaaaa11111"})]
    findings = _check("no_phi_fixture", operations, agent="FIXTURE", phi_refs=["art-aaaaaaa11111"])
    assert _ids(findings) == [f"{RULE_PREFIX}-010"]
    assert "PHI_PII" in findings[0].message


@pytest.mark.parametrize("field", ["sourceArtifactId", "artifactId", "source", "basis"], ids=str)
def test_every_provenance_spelling_is_checked(field: str) -> None:
    """AI-AG-007: the citation may arrive under any of these names."""
    operations = [_entry("/fixtures/specifications/-", {field: "art-aaaaaaa11111"})]
    findings = _check("no_phi_fixture", operations, agent="FIXTURE", phi_refs=["art-aaaaaaa11111"])
    assert _ids(findings) == [f"{RULE_PREFIX}-010"]


def test_a_clean_fixture_basis_is_accepted() -> None:
    """AI-AG-007: a spec built on a non-PHI artifact passes."""
    operations = [_entry("/fixtures/specifications/-", {"sourceArtifactId": "art-bbbbbbb22222"})]
    assert _check("no_phi_fixture", operations, agent="FIXTURE", phi_refs=["art-aaaaaaa11111"]) == []


def test_a_fixture_answer_without_phi_in_the_run_is_accepted() -> None:
    """AI-AG-007: with nothing excluded from the context there is nothing to cite."""
    operations = [_entry("/fixtures/specifications/-", {"sourceArtifactId": "art-aaaaaaa11111"})]
    assert _check("no_phi_fixture", operations, agent="FIXTURE") == []


def test_a_literal_value_list_is_refused() -> None:
    """AI-AG-007: a fixture spec states a distribution and a count; eleven literal values
    is captured data."""
    operations = [_entry("/fixtures/specifications/-", {"values": [f"value-{index}" for index in range(11)]})]
    findings = _check("synthetic_only", operations, agent="FIXTURE")
    assert _ids(findings) == [f"{RULE_PREFIX}-011"]
    assert "11 literal values" in findings[0].message


def test_a_short_example_list_is_accepted() -> None:
    """AI-AG-007: three examples illustrate a distribution, which is what the SRS asks
    for."""
    operations = [_entry("/fixtures/specifications/-", {"values": ["a", "b", "c"]})]
    assert _check("synthetic_only", operations, agent="FIXTURE") == []


@pytest.mark.parametrize("field", ["values", "records", "rows"], ids=str)
def test_every_data_row_spelling_is_refused(field: str) -> None:
    """AI-AG-007: the ban is on captured people, however the model names the list."""
    operations = [_entry("/fixtures/specifications/-", {field: [index for index in range(11)]})]
    assert _ids(_check("synthetic_only", operations, agent="FIXTURE")) == [f"{RULE_PREFIX}-011"]


def test_a_distribution_is_not_data() -> None:
    """AI-AG-007: the pass case — a spec, not a dataset."""
    operations = [_entry("/fixtures/specifications/-", {"distribution": {"strategy": "uniform"}, "count": 500})]
    assert _check("synthetic_only", operations, agent="FIXTURE") == []


# --- run_checks and the table ----------------------------------------------------


def test_run_checks_reports_every_named_rule_not_just_the_first() -> None:
    """AI-RT-011: one corrective retry, so the model must see the complete list; the same
    reason S2 reports every failed rule in one round trip."""
    output = _output(
        [
            _entry("/registry/entries/-", {"businessKey": "reg.a", "uid": UID}),
            _entry("/mapping/entries/-", {"businessKey": "map.a"}),
        ]
    )
    findings = run_checks(output, _context(), ["uid_ban", "decision_coverage", "no_registry_write"])
    assert _ids(findings) == [
        f"{RULE_PREFIX}-003",
        f"{RULE_PREFIX}-004",
        f"{RULE_PREFIX}-004",
        f"{RULE_PREFIX}-013",
        f"{RULE_PREFIX}-013",
    ]


def test_an_unknown_rule_name_is_a_finding_rather_than_silence() -> None:
    """AI-RT-012: a typo in a spec's ``checks`` tuple must not run that agent with no
    constraint at all."""
    findings = run_checks(_output(None), _context(), ["no_such_rule_anywhere"])
    assert _ids(findings) == [f"{RULE_PREFIX}-999"]
    assert "no_such_rule_anywhere" in findings[0].message
    assert findings[0].ladder == LADDER_CONSTRAINT


def test_the_rules_table_is_the_sixteen_the_srs_implies() -> None:
    """AI-RT-012: ``RULES`` is the only name list a spec may reference, so its keys are the
    contract between the table and the runtime."""
    assert len(RULES) == 16
    assert set(RULES) == {
        "no_final_products",
        "no_dhis2_payload",
        "no_dhis2_reference",
        "uid_ban",
        "decision_coverage",
        "low_confidence_question",
        "reuse_decision_per_similar_object",
        "business_key_references_only",
        "attribute_sensitivity",
        "nrr_confidence_parity",
        "no_phi_fixture",
        "synthetic_only",
        "single_page_scope",
        "no_registry_write",
        "logical_resource_names",
        "ui_data_source_resolves",
    }


@pytest.mark.parametrize("name", sorted(RULES))
def test_every_rule_takes_an_output_and_a_context(name: str) -> None:
    """AI-RT-012: every entry is callable with the two arguments ``run_checks`` gives it."""
    rule = RULES[name]
    assert callable(rule)
    assert rule(_output(None), _context()) == []


def test_every_check_an_spec_names_exists() -> None:
    """AI-AG-001 … AI-AG-008: a typo in a spec's ``checks`` tuple would run that agent with
    one fewer constraint than the SRS requires."""
    for spec in AGENT_SPECS.values():
        missing = [name for name in spec.checks if name not in RULES]
        assert not missing, f"{spec.key} names {missing}"


@pytest.mark.parametrize(
    "agent,stage,milestone",
    [
        ("REQUIREMENTS", "REQUIREMENTS", "M2"),
        ("DOMAIN", "DOMAIN", "M2"),
        ("MAPPING", "MAPPING", "M2"),
        ("METADATA_DESIGN", "MAPPING", "M2"),
        ("API", "PAGE_DESIGN", "M3"),
        ("UI", "PAGE_DESIGN", "M3"),
        ("FIXTURE", "PAGE_DESIGN", "M3"),
        ("REVIEW", "REQUIREMENTS", "M3"),
    ],
)
def test_the_agent_table_matches_srs_33(agent: str, stage: str, milestone: str) -> None:
    """AI-AG-001 … AI-AG-008: the SRS's table is data here, and the M2/M3 split is what
    ``feature`` gates on — four M2 agents and three M3 agents plus the reviewer."""
    spec = spec_for(agent)
    assert spec.stage.value == stage, spec.key
    assert spec.milestone.value == milestone, spec.key


def test_the_table_has_exactly_the_eight_agents_of_the_srs_table() -> None:
    """AI-AG-001 … AI-AG-008: one row each, no extras."""
    assert {spec.agent_type for spec in AGENT_SPECS.values()} == set(AgentType)
    assert len(AGENT_SPECS) == 8


def test_the_m2_milestone_split_is_the_one_the_srs_publishes() -> None:
    """AI-AG-001 … AI-AG-008, SRS 12: Requirements, Domain, Mapping and Metadata Design
    are MVP; API, UI, Fixture and Review are M3."""
    by_milestone: dict[str, set[str]] = {"M2": set(), "M3": set()}
    for spec in AGENT_SPECS.values():
        by_milestone[spec.milestone.value].add(spec.key)
    assert by_milestone["M2"] == {"REQUIREMENTS", "DOMAIN", "MAPPING", "METADATA_DESIGN"}
    assert by_milestone["M3"] == {"API", "UI", "FIXTURE", "REVIEW"}


def test_each_agent_may_write_only_its_own_sections() -> None:
    """AI-RT-012: the sections are the structural half of the prohibition — a Requirements
    Agent cannot write a mapping and a Mapping Agent cannot write a page, with no prompt
    involved."""
    expected: dict[str, tuple[str, ...]] = {
        "REQUIREMENTS": ("requirements",),
        "DOMAIN": ("domain", "useCases", "actors"),
        "MAPPING": ("mapping", "registry", "architecture"),
        "METADATA_DESIGN": ("rules",),
        "API": ("apiIntents",),
        "UI": ("uiIntents",),
        "FIXTURE": ("fixtures",),
        "REVIEW": (),
    }
    for key, sections in expected.items():
        assert spec_for(key).writable_section_values == sections, key


def test_the_agents_the_srs_runs_per_page_declare_it() -> None:
    """AI-AG-013: API and UI run once per page; nothing else may claim a page scope."""
    assert {spec.key for spec in AGENT_SPECS.values() if spec.per_page} == {"API", "UI"}


def test_only_the_mapping_agent_may_carry_a_user_confirmation() -> None:
    """AI-RT-016 / BP-DEC-004: a confirmation record belongs to the decisions a human
    answered, which are the Mapping Agent's."""
    assert {spec.key for spec in AGENT_SPECS.values() if spec.confirmable} == {"MAPPING"}


def test_the_review_assistant_needs_no_open_draft() -> None:
    """AI-AG-008 / AI-ASM-02: an explain-only assistant has nowhere to put a patch, so a
    missing draft is not its failure."""
    assert not spec_for("REVIEW").requires_draft
    assert all(spec.requires_draft for spec in AGENT_SPECS.values() if spec.key != "REVIEW")


def test_spec_for_accepts_the_enum_and_the_string() -> None:
    """AI-AG-001: the runtime reads the agent type off the request, which is a string."""
    assert spec_for(AgentType.MAPPING) is spec_for("MAPPING")
    with pytest.raises(ValueError, match="MAPPING2"):
        spec_for("MAPPING2")


# --- accepts_patch (AI-AG-006) ---------------------------------------------------


def test_an_explain_only_agent_never_patches() -> None:
    """AI-AG-008: the Review Assistant records its output and changes nothing, frozen or
    not."""
    spec = spec_for("REVIEW")
    assert spec.patch_mode is PatchMode.EXPLAIN_ONLY
    assert not spec.accepts_patch(mapping_frozen=False)
    assert not spec.accepts_patch(mapping_frozen=True)


def test_a_frozen_section_agent_stops_patching_once_the_mapping_is_frozen() -> None:
    """AI-AG-006 / AI-BR-01: after ARCHITECTURE_GATE the Metadata Design Agent explains
    and proposes, and produces no Patch — the runtime must know that before S2 refuses it."""
    spec = spec_for("METADATA_DESIGN")
    assert spec.frozen_sections
    assert spec.accepts_patch(mapping_frozen=False)
    assert not spec.accepts_patch(mapping_frozen=True)


def test_the_mapping_agent_is_also_frozen_by_the_gate() -> None:
    """AI-AG-006: its own sections are the ones the gate freezes."""
    spec = spec_for("MAPPING")
    assert spec.accepts_patch(mapping_frozen=False)
    assert not spec.accepts_patch(mapping_frozen=True)
    assert {section.value for section in spec.frozen_sections} == {"mapping", "registry", "architecture"}


def test_an_agent_without_frozen_sections_keeps_patching_after_the_gate() -> None:
    """AI-AG-006: the requirement and domain stages are not frozen by ARCHITECTURE_GATE, so
    they keep producing patches."""
    for key in ("REQUIREMENTS", "DOMAIN", "FIXTURE"):
        spec = spec_for(key)
        assert not spec.frozen_sections, key
        assert spec.accepts_patch(mapping_frozen=True), key


def test_a_frozen_agent_that_is_not_frozen_this_run_still_patches() -> None:
    """AI-AG-006: the flag is per run, taken from the draft's state."""
    spec = spec_for("API")
    assert spec.frozen_sections and spec.accepts_patch(mapping_frozen=False)


def test_the_read_only_sections_of_a_spec_are_never_writable_ones() -> None:
    """AI-RT-012: the context is read into the prompt and the same section must not be
    writable by the same agent under a different name."""
    for spec in AGENT_SPECS.values():
        overlap = set(spec.writable_section_values) & {
            section.value for section in spec.read_only_sections
        }
        assert not overlap, f"{spec.key} both reads and writes {sorted(overlap)}"
