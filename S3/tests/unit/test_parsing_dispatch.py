"""Dispatch, and the two claims every deterministic parser has to be able to back up.

The dispatch tests are about AI-INP-003 / AI-BR-02 as a *routing* property: the extension
table, not a caller's memory, decides that a Mermaid file, a CSV or a DHIS2 export never
reaches a model.  AC-REQ-01 is then run end to end with no provider installed at all — the
Mermaid ER diagram is parsed because the table said so, and a photo is refused because the
table says a deterministic parser cannot read it.

The two shared parametrised cases at the bottom are deliberately over **all five** parsers,
imported from the fixture files rather than restated here:

* **AI-INP-005 / AI-INP-010** — a deterministic entry is CONFIRMED at confidence 1.0 and has
  a location, always, in every format.
* **the ``parsing/base.py`` claim** that an NRR entry is a *superset* of S2's
  ``requirements.items`` — proven by projecting every entry and validating it against S2's
  real section schema.  If a parser puts something into a field the Blueprint forbids, this
  is the test that says so before S2 does, at ``submit_patch`` time.
"""

from __future__ import annotations

import hashlib
import json

import jsonschema
import pytest
from factory_blueprint.api import get_section_schema
from factory_foundation.api import DataClassification

from factory_intelligence import registries
from factory_intelligence.enums import DETERMINISTIC_PARSERS, NrrStatus, ParserType
from factory_intelligence.parsing import dispatch
from factory_intelligence.parsing.base import NrrCategory, ParseError, Parser
from factory_intelligence.parsing.dhis2.metajson import Dhis2MetadataParser
from factory_intelligence.parsing.tabular.csvparser import CsvParser
from factory_intelligence.parsing.tabular.xlsxparser import XlsxParser
from factory_intelligence.parsing.textdiagrams.mermaid import MermaidParser
from factory_intelligence.parsing.textdiagrams.plantuml import PlantUMLParser
from tests.unit.test_parsers_dhis2 import DHIS2_CASES, DHIS2_EXPORT
from tests.unit.test_parsers_diagrams import (
    DIAGRAM_CASES,
    MERMAID_ER,
    MERMAID_FLOW,
    PLANTUML_USECASE,
)
from tests.unit.test_parsers_tabular import (
    CSV_FIELDS,
    FIELD_MAPPING,
    TABULAR_CASES,
    TABULAR_CONFIRMED_CASES,
    XLSX_FIXTURE,
)

ARTIFACT = "art-0000000000000001"

ALL_CASES: tuple[tuple[str, Parser, bytes, dict[str, object]], ...] = (
    DIAGRAM_CASES + TABULAR_CASES + DHIS2_CASES
)

#: the same inputs where a mapping *was* supplied, so the review gate of AI-INP-006 does not
#: apply and every entry may be CONFIRMED
CONFIRMED_CASES: tuple[tuple[str, Parser, bytes, dict[str, object]], ...] = (
    DIAGRAM_CASES + TABULAR_CONFIRMED_CASES + DHIS2_CASES
)

#: ``get_section_schema`` returns a self-contained document whose item sub-schema resolves
#: ``#/definitions/…`` against that document, so the definitions travel with the sub-schema
#: handed to ``jsonschema.validate`` — otherwise the refs point nowhere and the test would
#: prove nothing.
_REQUIREMENTS = get_section_schema("requirements")
REQUIREMENTS_ITEM_SCHEMA: dict[str, object] = {
    **_REQUIREMENTS["properties"]["items"]["items"],
    "definitions": _REQUIREMENTS["definitions"],
}


def test_the_routing_table_covers_every_deterministic_parser() -> None:
    """AI-INP-002's four deterministic families, each reachable by its own suffix."""
    assert set(dispatch.PARSERS_BY_SUFFIX.values()) == set(DETERMINISTIC_PARSERS)
    assert dispatch.deterministic_for("er.mmd") is True
    assert dispatch.deterministic_for("use-case.puml") is True
    assert dispatch.deterministic_for("fields.csv") is True
    assert dispatch.deterministic_for("options.tsv") is True
    assert dispatch.deterministic_for("fields.xlsx") is True
    assert dispatch.deterministic_for("meta.json") is True


def test_a_model_assisted_format_is_never_routed_to_a_deterministic_parser() -> None:
    """The other half of AI-INP-003: the refusal is a recorded fact, not a missing key."""
    for name, suffix in (
        ("photo.png", ".png"),
        ("sketch.jpg", ".jpg"),
        ("notes.md", ".md"),
        ("spec.docx", ".docx"),
        ("flow.pdf", ".pdf"),
        ("service.py", ".py"),
    ):
        assert dispatch.deterministic_for(name) is False, name
        assert suffix in dispatch.NON_DETERMINISTIC_SUFFIXES, name
        assert dispatch.select_parser(name, b"whatever the bytes are") is None, name


def test_the_same_format_is_in_both_tables_at_most_once() -> None:
    """A suffix may not be claimed by both rows of AI-INP-002 at once."""
    assert not set(dispatch.PARSERS_BY_SUFFIX) & dispatch.NON_DETERMINISTIC_SUFFIXES
    assert not set(dispatch.PARSERS_BY_MEDIA_TYPE) & dispatch.NON_DETERMINISTIC_MEDIA_TYPES


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("er.mmd", MERMAID_ER.encode(), MermaidParser),
        ("model.plantuml", PLANTUML_USECASE.encode(), PlantUMLParser),
        ("fields.csv", CSV_FIELDS.encode(), CsvParser),
        ("fields.xlsx", XLSX_FIXTURE, XlsxParser),
        ("meta.json", json.dumps(DHIS2_EXPORT).encode(), Dhis2MetadataParser),
    ],
)
def test_select_parser_hands_back_the_parser_the_table_names(
    name: str, content: bytes, expected: type[Parser]
) -> None:
    chosen = dispatch.select_parser(name, content)

    assert isinstance(chosen, expected)
    assert chosen is dispatch.INSTANCES[expected.parser_type]


def test_ac_req_01_parses_a_mermaid_er_diagram_with_no_provider_installed() -> None:
    """AC-REQ-01: deterministic route, no model, and the same result twice."""
    assert dispatch.deterministic_for("er.mmd") is True
    parser = dispatch.select_parser("er.mmd", MERMAID_ER.encode())
    assert isinstance(parser, MermaidParser)

    first = parser.parse(
        MERMAID_ER.encode(),
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options={},
    )
    second = parser.parse(
        MERMAID_ER.encode(),
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options={},
    )

    assert first.used_model is False
    assert first.parser_type is ParserType.MERMAID
    assert first.entries
    assert json.dumps([e.to_nrr_record() for e in first.entries], sort_keys=True) == json.dumps(
        [e.to_nrr_record() for e in second.entries], sort_keys=True
    )

    flow = dispatch.select_parser("flow.mmd", MERMAID_FLOW.encode())
    assert isinstance(flow, MermaidParser)
    assert any(entry.category is NrrCategory.BUSINESS_PROCESS for entry in flow.parse(
        MERMAID_FLOW.encode(),
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options={},
    ).entries)


def test_a_photo_is_not_deterministic_and_the_refusal_is_loud() -> None:
    """``route_to_model=False`` must not become an empty NRR (AI-INP-003a's reasoning)."""
    assert dispatch.deterministic_for("photo.png") is False
    assert dispatch.select_parser("photo.png", b"\x89PNG\r\n\x1a\n") is None

    with pytest.raises(ParseError) as raised:
        dispatch.require_deterministic_parser("photo.png", b"\x89PNG\r\n\x1a\n")

    assert raised.value.code == dispatch.UNMATCHED
    assert raised.value.code in registries.AI_ERROR_CODES
    assert registries.AI_ERROR_CODES[dispatch.UNMATCHED] == ("S3", False)


def test_the_parser_input_error_codes_are_registered() -> None:
    """00 doc 6.5: an unregistered code reports as not-retryable and loses its meaning."""
    for code in (
        dispatch.UNMATCHED,
        "AI_PARSER_INPUT_INVALID",
        "AI_PARSER_DEPENDENCY_MISSING",
        "SCANNED_DOCUMENT_UNSUPPORTED",
    ):
        assert code in registries.AI_ERROR_CODES, code
        assert registries.AI_ERROR_CODES[code] == ("S3", False), code


def test_a_name_with_no_suffix_is_read_from_the_bytes_it_starts_with() -> None:
    """Mermaid and PlantUML declare themselves on their first line; the name may stay mute."""
    assert isinstance(dispatch.select_parser("paste", b"erDiagram\n  A ||--o{ B : has\n"), MermaidParser)
    assert isinstance(dispatch.select_parser("requirements", b"@startuml\nactor Doctor\n@enduml"), PlantUMLParser)
    assert isinstance(
        dispatch.select_parser("export", b'{"dataElements": [{"code": "X", "name": "Y"}]}'),
        Dhis2MetadataParser,
    )
    assert dispatch.select_parser("notes", b"just prose about what the clinic needs") is None


def test_a_json_is_claimed_only_when_it_is_dhis2_metadata() -> None:
    """DHIS2 export and OpenAPI share ``.json`` but sit on opposite rows of AI-INP-002."""
    openapi = b'{"openapi": "3.0.0", "info": {"title": "App"}, "paths": {}}'

    assert dispatch.deterministic_for("openapi.json", openapi) is False
    assert dispatch.select_parser("openapi.json", openapi) is None
    assert dispatch.deterministic_for("meta.json", b'{"dataElements": []}') is True


def test_media_types_route_like_filenames() -> None:
    assert dispatch.deterministic_for("text/csv") is True
    assert dispatch.resolve_parser_type("application/json") is ParserType.DHIS2_METADATA
    assert dispatch.resolve_parser_type("image/png") is None
    assert dispatch.deterministic_for("uploads/2024/fields.CSV") is True


def test_parse_deterministically_returns_none_or_raises_by_route() -> None:
    content = CSV_FIELDS.encode()

    parsed = dispatch.parse_deterministically(
        "fields.csv",
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options={"column_mapping": FIELD_MAPPING},
    )
    deferred = dispatch.parse_deterministically(
        "photo.png",
        b"\x89PNG\r\n\x1a\n",
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
    )
    with pytest.raises(ParseError):
        dispatch.parse_deterministically(
            "photo.png",
            b"\x89PNG\r\n\x1a\n",
            source_artifact_id=ARTIFACT,
            classification=DataClassification.INTERNAL,
            route_to_model=False,
        )

    assert parsed is not None and parsed.parser_type is ParserType.CSV
    assert deferred is None


def test_dispatch_is_a_table_lookup_and_nothing_else() -> None:
    """Same name, same bytes, same parser instance — the purity rule at the routing step."""
    content = MERMAID_ER.encode()

    assert dispatch.select_parser("er.mmd", content) is dispatch.select_parser("er.mmd", content)
    assert dispatch.deterministic_for("er.mmd") is dispatch.deterministic_for("er.mmd")


@pytest.mark.parametrize("label,parser,content,options", CONFIRMED_CASES)
def test_every_confirmed_entry_is_certain_and_located(
    label: str, parser: Parser, content: bytes, options: dict[str, object]
) -> None:
    """AI-INP-005 / AI-INP-010 over all five parsers: 1.0, CONFIRMED, and a real location.

    A deterministic parse measures the input, so confidence is a fact rather than an
    estimate, and the location is what AI-INP-009 lists when two inputs disagree.
    """
    fragment = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )

    assert fragment.entries, label
    assert fragment.used_model is False, label
    for entry in fragment.entries:
        assert entry.confidence == 1.0, (label, entry.business_key)
        assert entry.status is NrrStatus.CONFIRMED, (label, entry.business_key)
        assert entry.location is not None, (label, entry.business_key)
        assert entry.location.value, (label, entry.business_key)
        assert entry.source_payload()["artifactId"] == ARTIFACT, label
        assert str(entry.location) in entry.source_payload()["location"], label


@pytest.mark.parametrize("label,parser,content,options", ALL_CASES)
def test_the_input_classification_and_parser_identity_travel_onto_the_fragment(
    label: str, parser: Parser, content: bytes, options: dict[str, object]
) -> None:
    """AI-INP-004: parserType, parserVersion and the artifact's own classification.

    The NRR may not be classified below the input it came from (FD-ART-008), so the
    classification is passed through unchanged rather than chosen by the parser.
    """
    fragment = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.CONFIDENTIAL,
        options=options,
    )

    assert fragment.classification is DataClassification.CONFIDENTIAL, label
    assert fragment.parser_type is parser.parser_type, label
    assert fragment.parser_type in DETERMINISTIC_PARSERS, label
    assert fragment.parser_version == "1.0.0", label
    assert fragment.source_artifact_id == ARTIFACT, label
    assert fragment.used_model is False, label
    assert fragment.notes["inputHash"] == "sha256:" + hashlib.sha256(content).hexdigest(), label
    assert fragment.notes["inputBytes"] == len(content), label


@pytest.mark.parametrize("label,parser,content,options", ALL_CASES)
def test_every_entry_projects_onto_s2s_requirements_item_schema(
    label: str, parser: Parser, content: bytes, options: dict[str, object]
) -> None:
    """The ``parsing/base.py`` claim, tested: an NRR entry is a superset of a section item.

    ``to_requirements_item()`` must validate against S2's *real* schema — the one a patch is
    checked with — while ``to_nrr_record()`` adds the category and payload the section's
    ``additionalProperties: false`` cannot hold.
    """
    fragment = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )

    assert fragment.entries, label
    for entry in fragment.entries:
        item = entry.to_requirements_item()
        jsonschema.validate(item, REQUIREMENTS_ITEM_SCHEMA)
        record = entry.to_nrr_record()
        assert record["category"] == entry.category.value
        assert set(record) - set(item) <= {"category", "payload"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, REQUIREMENTS_ITEM_SCHEMA)


def test_the_requirements_item_schema_actually_rejects_an_extra_key() -> None:
    """A guard for the guard: the schema above is S2's, with ``additionalProperties`` false."""
    assert REQUIREMENTS_ITEM_SCHEMA["additionalProperties"] is False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"businessKey": "datarequirement_x", "statement": "s", "source": {"note": "n"},
             "category": "dataRequirement"},
            REQUIREMENTS_ITEM_SCHEMA,
        )
