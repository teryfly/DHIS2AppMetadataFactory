"""The deterministic text-diagram parsers: goldens, purity and the AI-INP-005 categories.

Two things are asserted for every parser here, and both are the SRS's requirements rather
than a preference:

* **AI-INP-010 / AC-REQ-01** — a fixed input gives a fixed NRR fragment.  Each golden
  compares the whole ``(category, businessKey, location, statement)`` table of one input
  against the expected structure, so a changed wording, a changed line number or a silently
  new entry fails a test instead of shipping.  The fixtures are source strings written in
  this file: no binary input is added to the repository.
* **AI-INP-005** — every record carries its source location, and the fact is placed in one
  of the thirteen categories with confidence 1.0 (a deterministic parse measures, it does
  not estimate).

The line numbers are checked because AI-INP-009 can only list "各来源的位置" if the parser
reported the real one, and the last test pins AI-INP-003 by reading the parsers' own source:
a diagram parser that imported the LLM layer would be a routing violation this test sees.
"""

from __future__ import annotations

import ast
import inspect
import json
from types import ModuleType
from typing import Any

import pytest
from factory_foundation.api import DataClassification

from factory_intelligence import registries
from factory_intelligence.enums import ParserType
from factory_intelligence.parsing.base import (
    Fragment,
    NrrCategory,
    ParseError,
    Parser,
)
from factory_intelligence.parsing.tabular import csvparser, xlsxparser
from factory_intelligence.parsing.textdiagrams import mermaid, plantuml
from factory_intelligence.parsing.textdiagrams.mermaid import MermaidParser
from factory_intelligence.parsing.textdiagrams.plantuml import PlantUMLParser

ARTIFACT = "art-0000000000000001"

MERMAID_ER = """erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ LINE-ITEM : contains
    CUSTOMER }o--|| BILLING : ?
    CUSTOMER {
        string name
        string custNumber PK "customer number"
        string sector FK
    }
    ORDER {
        int orderNumber PK
    }
"""

MERMAID_FLOW = """flowchart TD
    subgraph intake[Intake desk]
        A[Register patient] --> B{Eligible?}
    end
    B -->|yes| C[Collect weight]
    B -->|no| D[Reject and refer]
    C -- records --> E[(Facility registry)]
    B --> G[Follow up next visit]
    F[Send SMS reminder]
"""

MERMAID_DUPLICATE_EDGES = """flowchart LR
    A[Submit] --> B[Review]
    A[Submit] --> B[Review]
"""

MERMAID_SEQUENCE = """sequenceDiagram
    doctor->>system: record weight
"""

PLANTUML_USECASE = """@startuml
' a comment the parser must ignore
!theme plain
skinparam backgroundColor white
actor "Midwife" as MW
usecase "Register child" as UC1
usecase "Verify eligibility" as UC2
MW --> UC1
UC1 ..> UC2 : <<include>>
entity Child {
  -id : Long {PK}
  name : varchar(40)
  Date birthDate
}
Child "1" -- "0..*" Visit : has
Child --> Payment : ?
@enduml
"""

MERMAID_ER_GOLDEN: list[tuple[str, str, str, str]] = [
    (
        "businessEntity",
        "businessentity_customer",
        "line:2",
        'Business entity \'CUSTOMER\' has attributes: name (string), custNumber (string, PK, '
        '"customer number"), sector (string, FK).',
    ),
    (
        "businessEntity",
        "businessentity_order",
        "line:2",
        "Business entity 'ORDER' has attributes: orderNumber (int, PK).",
    ),
    (
        "businessEntity",
        "businessentity_line_item",
        "line:3",
        "Business entity 'LINE-ITEM' appears in the data model.",
    ),
    (
        "businessEntity",
        "businessentity_billing",
        "line:4",
        "Business entity 'BILLING' appears in the data model.",
    ),
    (
        "businessRule",
        "businessrule_customer_places_order",
        "line:2",
        "'CUSTOMER' relates to 'ORDER': exactly one 'CUSTOMER' to zero or more 'ORDER', "
        "labelled 'places'.",
    ),
    (
        "businessRule",
        "businessrule_order_contains_line_item",
        "line:3",
        "'ORDER' relates to 'LINE-ITEM': exactly one 'ORDER' to one or more 'LINE-ITEM', "
        "labelled 'contains'.",
    ),
    (
        "openQuestion",
        "openquestion_customer_to_billing",
        "line:4",
        "The relationship between 'CUSTOMER' and 'BILLING' (line 4) is labelled '?', which "
        "does not state what connects them. What is the meaning of this link — ownership, "
        "reference or workflow?",
    ),
]

MERMAID_FLOW_GOLDEN: list[tuple[str, str, str, str]] = [
    (
        "participant",
        "participant_intake_desk",
        "line:2",
        "Participant or role grouping 'Intake desk' owns the steps drawn inside it.",
    ),
    (
        "businessProcess",
        "businessprocess_send_sms_reminder",
        "line:9",
        "Process step 'Send SMS reminder'.",
    ),
    (
        "businessProcess",
        "businessprocess_register_patient_to_eligible",
        "line:3",
        "Process step 'Register patient' leads to 'Eligible?'.",
    ),
    (
        "businessRule",
        "businessrule_eligible_to_collect_weight",
        "line:5",
        "Decision 'Eligible?' takes the branch to 'Collect weight' when 'yes'.",
    ),
    (
        "businessRule",
        "businessrule_eligible_to_reject_and_refer",
        "line:6",
        "Decision 'Eligible?' takes the branch to 'Reject and refer' when 'no'.",
    ),
    (
        "businessProcess",
        "businessprocess_collect_weight_to_facility_registry",
        "line:7",
        "Process step 'Collect weight' leads to 'Facility registry' via 'records'.",
    ),
    (
        "openQuestion",
        "openquestion_eligible_to_follow_up_next_visit",
        "line:8",
        "Decision 'Eligible?' takes the branch to 'Follow up next visit' (condition not stated).",
    ),
]

PLANTUML_GOLDEN: list[tuple[str, str, str, str]] = [
    (
        "participant",
        "participant_midwife",
        "line:5",
        "Actor 'Midwife' takes part in the process.",
    ),
    (
        "useCase",
        "usecase_register_child",
        "line:6",
        "Use case 'Register child' is performed by the actors linked to it.",
    ),
    (
        "useCase",
        "usecase_verify_eligibility",
        "line:7",
        "Use case 'Verify eligibility' is performed by the actors linked to it.",
    ),
    (
        "businessEntity",
        "businessentity_child",
        "line:10",
        "Business entity 'Child' has attributes: id (Long, PK), name (varchar(40)), "
        "birthDate (Date).",
    ),
    (
        "businessProcess",
        "businessprocess_midwife_to_register_child",
        "line:8",
        "Actor 'Midwife' interacts with 'Register child'.",
    ),
    (
        "businessRule",
        "businessrule_register_child_to_verify_eligibility",
        "line:9",
        "Composition rule: 'Register child' include 'Verify eligibility' (declared as "
        "'<<include>>').",
    ),
    (
        "businessRule",
        "businessrule_child_to_visit",
        "line:15",
        'Structural link: \'Child\' ("1") relates to \'Visit\' ("0..*"), labelled \'has\'.',
    ),
    (
        "openQuestion",
        "openquestion_child_to_payment",
        "line:16",
        "The '-->' link between 'Child' and 'Payment' (line 16) carries no label that says "
        "what connects them. What is the meaning of this link, and does it constrain the "
        "data or describe a step of the process?",
    ),
]

DIAGRAM_CASES: tuple[tuple[str, Parser, bytes, dict[str, Any]], ...] = (
    ("mermaid erDiagram", MermaidParser(), MERMAID_ER.encode(), {}),
    ("mermaid flowchart", MermaidParser(), MERMAID_FLOW.encode(), {}),
    ("mermaid repeated edge", MermaidParser(), MERMAID_DUPLICATE_EDGES.encode(), {}),
    ("mermaid unsupported type", MermaidParser(), MERMAID_SEQUENCE.encode(), {}),
    ("plantuml use cases", PlantUMLParser(), PLANTUML_USECASE.encode(), {}),
)


def parse(parser: Parser, source: str, **options: Any) -> Fragment:
    return parser.parse(
        source.encode("utf-8"),
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )


def table(fragment: Fragment) -> list[tuple[str, str, str, str]]:
    """The golden shape: category, key, location and statement, in production order."""
    return [
        (entry.category.value, entry.business_key, str(entry.location), entry.statement)
        for entry in fragment.entries
    ]


def serialise(fragment: Fragment) -> str:
    return json.dumps([entry.to_nrr_record() for entry in fragment.entries], sort_keys=True)


def test_mermaid_er_diagram_golden_fragment() -> None:
    """AI-INP-010: one fixed ER diagram, one fixed NRR fragment."""
    fragment = parse(MermaidParser(), MERMAID_ER)

    assert table(fragment) == MERMAID_ER_GOLDEN
    assert fragment.parser_type is ParserType.MERMAID
    assert fragment.parser_version == "1.0.0"
    assert fragment.source_artifact_id == ARTIFACT
    assert fragment.classification is DataClassification.INTERNAL
    assert fragment.used_model is False
    assert fragment.needs_review is False


def test_mermaid_er_attributes_and_cardinality_are_kept_in_payload() -> None:
    """AI-INP-005: the attribute list is the entity's own material, not statement prose."""
    fragment = parse(MermaidParser(), MERMAID_ER)
    customer = fragment.entries[0]
    relationship = fragment.entries[4]

    assert [item["name"] for item in customer.payload["attributes"]] == [
        "name",
        "custNumber",
        "sector",
    ]
    assert customer.payload["attributes"][1]["keys"] == ["PK"]
    assert customer.payload["attributes"][1]["comment"] == "customer number"
    assert relationship.payload == {
        "from": "CUSTOMER",
        "to": "ORDER",
        "leftCardinality": "||",
        "rightCardinality": "o{",
        "label": "places",
    }


def test_mermaid_flowchart_golden_fragment() -> None:
    fragment = parse(MermaidParser(), MERMAID_FLOW)

    assert table(fragment) == MERMAID_FLOW_GOLDEN


def test_mermaid_flowchart_records_the_subgraph_as_a_participant() -> None:
    """A ``subgraph`` names who owns the steps, so the steps cite it as an actor."""
    fragment = parse(MermaidParser(), MERMAID_FLOW)
    participant, _lone, step, branch = fragment.entries[:4]

    assert participant.category is NrrCategory.PARTICIPANT
    assert step.actors == ("participant_intake_desk",)
    assert branch.actors == ("participant_intake_desk",)
    assert branch.category is NrrCategory.BUSINESS_RULE
    assert branch.payload["branch"] == "yes"


def test_mermaid_ambiguous_relationship_label_becomes_an_open_question() -> None:
    """AI-INP-008 read backwards: what the diagram does not say is never confirmed."""
    fragment = parse(MermaidParser(), MERMAID_ER)
    question_entry = fragment.entries[-1]

    assert question_entry.category is NrrCategory.OPEN_QUESTION
    assert str(question_entry.location) == "line:4"
    assert question_entry.open_questions[0]["severity"] == "MEDIUM"
    assert fragment.open_questions == [question_entry.open_questions[0]]


def test_mermaid_unsupported_diagram_type_asks_instead_of_guessing() -> None:
    """A sequenceDiagram is outside this parser's documented grammar: it asks, and says so.

    Returning no entries at all would be the silently empty NRR AI-INP-003a forbids.
    """
    fragment = parse(MermaidParser(), MERMAID_SEQUENCE)

    assert fragment.needs_review is True
    assert fragment.review_reason is not None
    assert "sequenceDiagram" in fragment.review_reason
    assert len(fragment.open_questions) == 1
    assert [entry.category for entry in fragment.entries] == [NrrCategory.OPEN_QUESTION]
    assert fragment.notes["diagramType"] == "sequenceDiagram"


def test_plantuml_golden_fragment() -> None:
    fragment = parse(PlantUMLParser(), PLANTUML_USECASE)

    assert table(fragment) == PLANTUML_GOLDEN
    assert fragment.parser_type is ParserType.PLANTUML
    assert fragment.parser_version == "1.0.0"
    assert fragment.used_model is False


def test_plantuml_ignores_presentation_directives() -> None:
    """``'`` comments, ``!theme`` and ``skinparam`` state no business fact (AI-INP-002)."""
    fragment = parse(PlantUMLParser(), PLANTUML_USECASE)
    statements = " ".join(entry.statement for entry in fragment.entries)

    assert "skinparam" not in statements
    assert "theme" not in statements
    assert fragment.notes["diagrams"] == 1
    assert fragment.notes["declarations"] == 4
    assert fragment.notes["relationships"] == 4


def test_plantuml_class_fields_become_attributes_with_key_markers() -> None:
    fragment = parse(PlantUMLParser(), PLANTUML_USECASE)
    child = next(entry for entry in fragment.entries if entry.category is NrrCategory.BUSINESS_ENTITY)

    assert child.payload["kind"] == "entity"
    assert [item["keys"] for item in child.payload["attributes"]] == [["PK"], [], []]
    assert [item["type"] for item in child.payload["attributes"]] == ["Long", "varchar(40)", "Date"]


def test_plantuml_include_dependency_is_a_rule_and_an_actor_link_is_a_process() -> None:
    fragment = parse(PlantUMLParser(), PLANTUML_USECASE)
    interaction = fragment.entries[4]
    dependency = fragment.entries[5]

    assert interaction.category is NrrCategory.BUSINESS_PROCESS
    assert interaction.actors == ("participant_midwife",)
    assert dependency.category is NrrCategory.BUSINESS_RULE
    assert dependency.payload["stereotype"] == "include"


@pytest.mark.parametrize("label,parser,content,options", DIAGRAM_CASES)
def test_parsing_the_same_input_twice_gives_the_same_fragment(
    label: str, parser: Parser, content: bytes, options: dict[str, Any]
) -> None:
    """AI-INP-010 / AC-REQ-01: byte-identical output, twice, with no provider installed."""
    first = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )
    second = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )

    assert serialise(first) == serialise(second), label
    assert first.notes == second.notes, label


@pytest.mark.parametrize("label,parser,content,options", DIAGRAM_CASES)
def test_business_keys_are_unique_within_one_fragment(
    label: str, parser: Parser, content: bytes, options: dict[str, Any]
) -> None:
    """S2's ``requirements.items`` requires a businessKey unique per container."""
    fragment = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )
    keys = [entry.business_key for entry in fragment.entries]

    assert len(keys) == len(set(keys)), label


def test_repeated_labels_are_disambiguated_by_location() -> None:
    """Two identical edges are two facts at two lines, so they get two keys (AI-INP-009)."""
    fragment = parse(MermaidParser(), MERMAID_DUPLICATE_EDGES)
    first, second = fragment.entries

    assert first.statement == second.statement
    assert str(first.location) == "line:2"
    assert str(second.location) == "line:3"
    assert first.business_key == "businessprocess_submit_to_review"
    assert second.business_key != first.business_key
    assert second.business_key.startswith(first.business_key)


def test_a_diagram_that_is_not_text_raises_rather_than_returning_nothing() -> None:
    """AI-INP-003a's reasoning: an input nothing can be read from is a failure, not an
    empty NRR — and the code it names must be one S0 will accept (00 doc 6.5)."""
    for module, parser in ((mermaid, MermaidParser()), (plantuml, PlantUMLParser())):
        with pytest.raises(ParseError) as raised:
            parse(parser, "")
        assert raised.value.code == module.INPUT_UNREADABLE
        assert raised.value.code in registries.AI_ERROR_CODES


def test_the_diagram_parsers_never_reach_for_a_model() -> None:
    """AI-INP-003 / AI-BR-02 by construction: the parsers import no LLM layer at all."""
    for module in (mermaid, plantuml):
        imported = _imported_modules(module)
        assert not [name for name in imported if name.startswith("factory_intelligence.llm")], (
            module.__name__,
            sorted(imported),
        )
        assert "factory_intelligence.deps" not in imported


def test_the_deterministic_parsers_read_no_clock_or_database() -> None:
    """The purity rule of the ``Parser`` protocol: same bytes plus same options, same NRR."""
    forbidden_imports = ("random", "httpx", "socket", "urllib", "sqlalchemy", "requests")
    forbidden_calls = {"now", "today", "utcnow", "time", "random", "randint", "uuid4", "sleep"}
    for module in (mermaid, plantuml, csvparser, xlsxparser):
        imported = _imported_modules(module)
        for name in forbidden_imports:
            assert name not in imported, f"{module.__name__} imports {name}"
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, (
                    f"{module.__name__} calls {node.func.attr}()"
                )


def _imported_modules(module: ModuleType) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out
