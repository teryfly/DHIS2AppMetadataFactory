"""Routing: the one place AI-INP-003 is either enforced or broken (SRS 3.1, AC-REQ-02).

:func:`factory_intelligence.parsing.service.route_parser` is the boundary between
AI-INP-002's two halves, so it is tested as a pure function over a name and its bytes — no
database, no provider, no artifact.  What is being pinned is not "does a CSV parse" (the
parser tests do that) but *who is allowed to read it*: a format a deterministic parser covers
must never be handed to a model, and a caller's preference may not overturn that in either
direction.

Three groups, each a reading of a different clause:

* **the deterministic rows** — every fixture the parser tests already use, routed from its own
  file name or, when the name says nothing, from the content's first line;
* **``options.parser``** — allowed to *narrow* toward a deterministic parser for an
  oddly-named file, refused when it would widen a covered format toward a model
  (AI-INP-003 / AI-BR-02), and refused outright when it contradicts the input's own content;
* **the refusal texts** — AI-INP-003a exists so an unsupported input comes back with a next
  step rather than an empty NRR, so the message is asserted as behaviour, not left to luck.

The last group is a consistency check on the tables themselves: ``dispatch`` publishes the
model-assisted suffixes it deliberately does *not* claim, and this module is the half that
claims them.  If the two ever disagree about who owns ``.svg``, the routing decision stops
being one lookup.
"""

from __future__ import annotations

import json

import pytest

from factory_intelligence.dto.request import ParseOptions
from factory_intelligence.enums import DETERMINISTIC_PARSERS, Milestone, ParserType
from factory_intelligence.errors import FeatureNotEnabled
from factory_intelligence.parsing import dispatch
from factory_intelligence.parsing.base import ParseError
from factory_intelligence.parsing.service import (
    EXPORTED_AS_PARSERS,
    LATE_MILESTONE_PARSERS_BY_SUFFIX,
    MODEL_PARSERS,
    MODEL_PARSERS_BY_MEDIA_TYPE,
    MODEL_PARSERS_BY_SUFFIX,
    PARSEABLE_INPUT_TYPES,
    route_parser,
)
from factory_intelligence.registries import AI_AUDIT_EVENT_TYPES, AI_ERROR_CODES
from tests.unit.test_parsers_dhis2 import DHIS2_EXPORT
from tests.unit.test_parsers_diagrams import MERMAID_ER, PLANTUML_USECASE
from tests.unit.test_parsers_tabular import CSV_FIELDS, CSV_OPTIONS, XLSX_FIXTURE

CSV_BYTES = CSV_FIELDS.encode("utf-8")
MERMAID_BYTES = MERMAID_ER.encode("utf-8")
PLANTUML_BYTES = PLANTUML_USECASE.encode("utf-8")
DHIS2_BYTES = json.dumps(DHIS2_EXPORT, ensure_ascii=False).encode("utf-8")

#: A PNG header with no name to go on: the magic bytes are the only evidence there is.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
#: A one-page PDF with no text at all, as an image-only export writes it.
PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\nnot really a pdf body\n%%EOF\n"
#: Something no row of AI-INP-002 names.
MYSTERY_BYTES = b"\x00\x01\x02binary blob that declares nothing\x03"

DETERMINISTIC_CASES: tuple[tuple[str, bytes, ParserType], ...] = (
    ("er-diagram.mmd", MERMAID_BYTES, ParserType.MERMAID),
    ("flow.mermaid", MERMAID_BYTES, ParserType.MERMAID),
    ("use-case.puml", PLANTUML_BYTES, ParserType.PLANTUML),
    ("fields.csv", CSV_BYTES, ParserType.CSV),
    ("选项.tsv", CSV_OPTIONS.replace(",", "\t").encode("utf-8"), ParserType.CSV),
    ("fields.xlsx", XLSX_FIXTURE, ParserType.XLSX),
    ("meta.json", DHIS2_BYTES, ParserType.DHIS2_METADATA),
    #: a name that says nothing at all, settled by the content's own first line
    ("requirements", MERMAID_BYTES, ParserType.MERMAID),
    ("paste-from-chat", PLANTUML_BYTES, ParserType.PLANTUML),
)


# --- AI-INP-003: the deterministic rows are owned, in both directions ------------


@pytest.mark.parametrize(("name", "content", "expected"), DETERMINISTIC_CASES)
def test_a_deterministic_format_is_routed_to_its_parser(
    name: str, content: bytes, expected: ParserType
) -> None:
    assert route_parser(name, content=content) is expected
    assert expected in DETERMINISTIC_PARSERS


@pytest.mark.parametrize(("name", "content", "expected"), DETERMINISTIC_CASES)
def test_a_deterministic_format_never_routes_to_a_model(
    name: str, content: bytes, expected: ParserType
) -> None:
    """AI-INP-003 / AI-BR-02, as a property of the routing rather than of a caller's care.

    ``options.parser`` naming a model-assisted type is the way a caller could still get a CSV
    into a prompt, and it is refused with the registered parser code rather than obeyed.
    """
    for model_type in sorted(MODEL_PARSERS, key=lambda member: member.value):
        with pytest.raises(ParseError) as caught:
            route_parser(name, content=content, options=ParseOptions(parser=model_type.value))
        assert caught.value.code == dispatch.UNMATCHED
        assert "AI-INP-003" in caught.value.message


@pytest.mark.parametrize(("name", "content", "expected"), DETERMINISTIC_CASES)
def test_routing_the_same_input_twice_gives_the_same_parser(
    name: str, content: bytes, expected: ParserType
) -> None:
    """AI-INP-010's reproducibility starts here: the decision is a function of the bytes."""
    assert route_parser(name, content=content) is route_parser(name, content=content) is expected


def test_forcing_a_deterministic_parser_on_an_unnamed_input_is_allowed() -> None:
    """A Mermaid file called ``requirements`` has no extension; the caller may say what it is.

    The direction matters: forcing *toward* a deterministic parser can never send content to a
    model, so it is the safe half of ``options.parser`` (AI-INP-006).
    """
    assert route_parser("requirements", content=MERMAID_BYTES, options=ParseOptions(parser="MERMAID")) is (
        ParserType.MERMAID
    )
    assert route_parser("data", content=CSV_BYTES, options=ParseOptions(parser="CSV")) is ParserType.CSV


def test_forcing_a_deterministic_parser_contradicting_the_content_is_refused() -> None:
    """Two deterministic claims about one input cannot both be filed (AI-INP-002)."""
    with pytest.raises(ParseError) as caught:
        route_parser("diagram.puml", content=PLANTUML_BYTES, options=ParseOptions(parser="MERMAID"))
    assert "contradicts" in caught.value.message
    assert caught.value.code == dispatch.UNMATCHED


# --- the model-assisted rows ------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("notes.md", b"# Requirements\n- The midwife registers a child.\n", ParserType.MARKDOWN),
        ("spec.txt", b"The midwife registers a child.\n", ParserType.MARKDOWN),
        ("flow.svg", b"<svg><text>Register</text></svg>", ParserType.MARKDOWN),
        ("export.xml", b"<mxGraphModel/>", ParserType.MARKDOWN),
        ("memo.docx", b"PK\x03\x04fake word bytes", ParserType.DOCX),
        ("spec.pdf", b"%PDF-1.7 something", ParserType.PDF),
        ("screenshot.png", PNG_BYTES, ParserType.IMAGE),
        ("photo.jpg", b"\xff\xd8\xff\xe0jpeg bytes", ParserType.IMAGE),
    ],
)
def test_a_model_assisted_row_routes_to_its_extractor(
    name: str, content: bytes, expected: ParserType
) -> None:
    assert route_parser(name, content=content) is expected


def test_media_type_routes_when_the_artifact_has_no_filename() -> None:
    """An upload registered without a name still says ``text/csv`` (SRS 6.1)."""
    assert route_parser("", content=CSV_BYTES, media_type="text/csv") is ParserType.CSV
    assert route_parser("", content=b"# heading\n", media_type="text/markdown") is (
        ParserType.MARKDOWN
    )


def test_magic_bytes_route_an_unnamed_picture() -> None:
    assert route_parser("clipboard", content=PNG_BYTES) is ParserType.IMAGE
    assert route_parser("paste", content=PDF_BYTES) is ParserType.PDF


@pytest.mark.parametrize(
    ("exported_as", "expected"),
    [
        ("png", ParserType.IMAGE),
        ("SVG", ParserType.MARKDOWN),
        ("xml", ParserType.MARKDOWN),
        (".jpg", ParserType.IMAGE),
    ],
)
def test_a_declared_export_format_decides_the_handling(
    exported_as: str, expected: ParserType
) -> None:
    """AI-INP-002's drawing-tool row: the user exports first and the export picks the parser."""
    assert (
        route_parser("flow.drawio", content=b"<xml/>", options=ParseOptions(exported_as=exported_as))
        is expected
    )


def test_a_native_drawing_file_is_refused_with_the_export_it_asks_for() -> None:
    """The row says "先导出为 XML、SVG 或 PNG", so the refusal says exactly that (AI-INP-003a)."""
    for name in ("flow.drawio", "plan.vsdx"):
        with pytest.raises(ParseError) as caught:
            route_parser(name, content=b"<xml/>")
        assert caught.value.code == dispatch.UNMATCHED
        assert "SVG" in caught.value.message and "PNG" in caught.value.message


@pytest.mark.parametrize("name", ["service.py", "Patient.java", "openapi.yaml", "api.json"])
def test_the_m3_row_is_reported_as_a_later_milestone(name: str) -> None:
    """AI-INP-002's 源代码 / OpenAPI row is M3; SRS 12 gates it rather than faking it."""
    with pytest.raises(FeatureNotEnabled) as caught:
        route_parser(name, content=b"whatever the file holds")
    assert caught.value.details["milestone"] == Milestone.M3.value


def test_a_dhis2_export_and_an_openapi_document_share_an_extension_not_a_row() -> None:
    """``.json`` is both of AI-INP-002's rows, so the content decides (dispatch's own note)."""
    assert route_parser("meta.json", content=DHIS2_BYTES) is ParserType.DHIS2_METADATA
    openapi = json.dumps({"openapi": "3.0.0", "paths": {}}).encode("utf-8")
    with pytest.raises(FeatureNotEnabled):
        route_parser("openapi.json", content=openapi)


def test_an_input_no_row_of_ai_inp_002_names_is_refused_with_the_formats_that_work() -> None:
    with pytest.raises(ParseError) as caught:
        route_parser("mystery.bin", content=MYSTERY_BYTES)
    message = caught.value.message
    assert caught.value.code == dispatch.UNMATCHED
    for named in ("CSV", "Mermaid", "DHIS2", "options.parser"):
        assert named in message


def test_an_unknown_options_parser_name_is_reported_not_ignored() -> None:
    """Silently ignoring a typo would run the wrong parser on purpose (AI-INP-006)."""
    with pytest.raises(ParseError) as caught:
        route_parser("fields.csv", content=CSV_BYTES, options=ParseOptions(parser="SPREADSHEET"))
    assert "not a parser S3 knows" in caught.value.message


def test_an_unknown_options_parser_never_falls_through_to_a_model() -> None:
    """The refusal is checked before the deterministic table is consulted at all."""
    with pytest.raises(ParseError):
        route_parser("fields.csv", content=CSV_BYTES, options=ParseOptions(parser="NOPE"))


# --- the tables agree with dispatch ----------------------------------------------


def test_this_module_claims_exactly_what_dispatch_declines() -> None:
    """``dispatch`` lists the model-assisted suffixes it will not own; this is that half.

    ``.json`` is the one suffix on both sides, because two rows of AI-INP-002 share it and the
    content — a DHIS2 marker or its absence — is what decides which one it is.
    """
    claimed = set(MODEL_PARSERS_BY_SUFFIX) | set(LATE_MILESTONE_PARSERS_BY_SUFFIX)
    assert claimed - {".json"} <= set(dispatch.NON_DETERMINISTIC_SUFFIXES)
    for suffix in MODEL_PARSERS_BY_SUFFIX:
        assert suffix not in dispatch.PARSERS_BY_SUFFIX, suffix
    for suffix in dispatch.PARSERS_BY_SUFFIX:
        assert suffix not in MODEL_PARSERS_BY_SUFFIX, suffix


def test_the_model_media_types_are_the_ones_dispatch_declines_to_route() -> None:
    """A media type is claimed by exactly one half, and by this one only when dispatch says so."""
    for media_type in MODEL_PARSERS_BY_MEDIA_TYPE:
        assert media_type not in dispatch.PARSERS_BY_MEDIA_TYPE, media_type
        assert media_type in dispatch.NON_DETERMINISTIC_MEDIA_TYPES, media_type


def test_every_exported_as_value_is_a_row_of_ai_inp_002() -> None:
    #: the keys are the bare export names, so a caller may write "png" or ".png"
    assert set(EXPORTED_AS_PARSERS) <= {"png", "jpg", "jpeg", "gif", "svg", "xml", "html"}
    for export, parser_type in EXPORTED_AS_PARSERS.items():
        assert route_parser("flow.drawio", content=b"<xml/>", options=ParseOptions(exported_as=f".{export}")) is (
            parser_type
        )


def test_model_parsers_are_exactly_the_complement_of_the_deterministic_set() -> None:
    """One definition of "may reach a model", so a new type cannot slip between two lists."""
    assert set(ParserType) - set(DETERMINISTIC_PARSERS) == MODEL_PARSERS
    assert not MODEL_PARSERS & DETERMINISTIC_PARSERS


# --- the vocabulary the service writes with ---------------------------------------


def test_the_service_only_uses_registered_error_codes_and_events() -> None:
    """S0 refuses an unregistered code or event *inside the caller's transaction* (00 doc 6.5)."""
    assert dispatch.UNMATCHED in AI_ERROR_CODES
    for event in ("ai.input.parsed", "ai.input.parse_failed", "ai.policy.denied"):
        assert event in AI_AUDIT_EVENT_TYPES


def test_the_parseable_input_types_are_the_registered_input_rows() -> None:
    """BUSINESS_INPUT is S1's upload type and DISCOVERY_RESULT is AI-INP-002's 发现结果 row."""
    assert frozenset({"BUSINESS_INPUT", "DISCOVERY_RESULT"}) == PARSEABLE_INPUT_TYPES
