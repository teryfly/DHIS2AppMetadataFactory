"""Parser dispatch: extension → parser, and the AI-INP-003 refusal by construction.

AI-INP-003 / AI-BR-02 say a format a deterministic parser can handle must not be handed to
an LLM.  That cannot be a rule a caller remembers, so it is a table:
:data:`PARSERS_BY_SUFFIX` maps every suffix the four deterministic families of AI-INP-002
cover onto a :class:`~factory_intelligence.enums.ParserType` member that
:data:`~factory_intelligence.enums.DETERMINISTIC_PARSERS` already declares model-free, and
:func:`deterministic_for` answers the routing question from that table alone.  A caller that
asked a model to parse a ``.csv`` would have to contradict a returned ``True``, not a
paragraph of prose.

Three decisions a reviewer should see:

* **The model-assisted suffixes are named here and map to nothing.**  PNG, JPG, Markdown,
  Word, PDF, draw.io exports, source code and OpenAPI belong to AI-INP-002's other rows and
  to whoever implements them; listing them keeps "no deterministic parser covers this" a
  recorded fact instead of a missing key that silently falls through.
* **``.json`` is checked against its content.**  DHIS2 metadata and an OpenAPI document share
  one extension and sit on opposite rows of AI-INP-002, so a JSON body is claimed only when
  it carries a DHIS2 marker; otherwise dispatch declines and the caller decides.
* **The refusal is an error, not an empty fragment.**  :func:`require_deterministic_parser`
  raises :class:`~factory_intelligence.parsing.base.ParseError` with the registered code
  ``AI_PARSER_UNMATCHED`` when a caller forbade the model path and nothing matched — the
  same reasoning as AI-INP-003a: no silently empty NRR.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from factory_foundation.api import DataClassification

from factory_intelligence.enums import DETERMINISTIC_PARSERS, ParserType
from factory_intelligence.parsing.base import Fragment, ParseError, Parser
from factory_intelligence.parsing.dhis2.metajson import Dhis2MetadataParser
from factory_intelligence.parsing.tabular.csvparser import CsvParser
from factory_intelligence.parsing.tabular.xlsxparser import XlsxParser
from factory_intelligence.parsing.textdiagrams.mermaid import MermaidParser
from factory_intelligence.parsing.textdiagrams.plantuml import PlantUMLParser

#: Registered in ``factory_intelligence.registries.AI_ERROR_CODES`` (00 doc 6.5).  Named for
#: the one thing dispatch can fail at: no deterministic parser covers the input *and* the
#: caller refused the model-assisted path.
UNMATCHED = "AI_PARSER_UNMATCHED"

#: The parser instances dispatch hands out.  Every one of them is pure (the ``Parser``
#: protocol's rule), so one instance per process is enough, and two parses of one file are
#: guaranteed to run the same code.
INSTANCES: Final[Mapping[ParserType, Parser]] = {
    ParserType.MERMAID: MermaidParser(),
    ParserType.PLANTUML: PlantUMLParser(),
    ParserType.CSV: CsvParser(),
    ParserType.XLSX: XlsxParser(),
    ParserType.DHIS2_METADATA: Dhis2MetadataParser(),
}

#: The routing table: a suffix, the parser type it belongs to, and the AI-INP-002 family that
#: says why a deterministic parser is enough for it.
#:
#: 文本图 (Mermaid, PlantUML) · 结构化清单 (Excel, CSV) · 既有 DHIS2 元数据 (导出 JSON).
PARSERS_BY_SUFFIX: Final[Mapping[str, ParserType]] = {
    ".mmd": ParserType.MERMAID,
    ".mermaid": ParserType.MERMAID,
    ".mmdc": ParserType.MERMAID,
    ".pu": ParserType.PLANTUML,
    ".puml": ParserType.PLANTUML,
    ".plantuml": ParserType.PLANTUML,
    ".iuml": ParserType.PLANTUML,
    ".csv": ParserType.CSV,
    ".tsv": ParserType.CSV,
    ".xlsx": ParserType.XLSX,
    ".json": ParserType.DHIS2_METADATA,
}

#: Suffixes of AI-INP-002's model-assisted rows, and the draw.io exports the SRS hands to
#: "按导出格式".  Nothing here may gain a deterministic entry without amending the SRS.
NON_DETERMINISTIC_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        # 图片 — 视觉模型抽取
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        # 文档 — 确定性文本提取 + LLM 抽取
        ".md",
        ".markdown",
        ".txt",
        ".doc",
        ".docx",
        ".pdf",
        # 绘图工具文件 — 用户先导出为 XML / SVG / PNG
        ".drawio",
        ".vsdx",
        ".xml",
        ".svg",
        ".html",
        ".htm",
        # 既有前端代码 / API 文档 — LLM 辅助抽取 (M3)
        ".openapi.json",
        ".yaml",
        ".yml",
        ".py",
        ".java",
        ".kt",
        ".ts",
        ".tsx",
        ".js",
        ".vue",
        ".go",
        ".rb",
        ".cs",
        ".c",
        ".h",
    }
)

#: Media types a caller may pass where S0 would otherwise pass only a filename.
PARSERS_BY_MEDIA_TYPE: Final[Mapping[str, ParserType]] = {
    "text/csv": ParserType.CSV,
    "application/csv": ParserType.CSV,
    "text/tab-separated-values": ParserType.CSV,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ParserType.XLSX,
    "application/json": ParserType.DHIS2_METADATA,
    "text/vnd.plantuml": ParserType.PLANTUML,
    "text/x-mermaid": ParserType.MERMAID,
}

#: Media types of the model-assisted rows (listed for the same reason as the suffixes).
NON_DETERMINISTIC_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/plain",
        "text/markdown",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/xml",
        "image/svg+xml",
        "text/html",
        "application/yaml",
        "application/x-yaml",
        "text/yaml",
    }
)

#: Strings that only a DHIS2 metadata export or an S4 discovery result writes.  Used solely
#: to tell a DHIS2 JSON from an OpenAPI JSON, which share an extension and differ in row.
DHIS2_MARKERS: Final[tuple[str, ...]] = (
    "dataElements",
    "optionSets",
    "optionGroupSet",
    "programIndicators",
    "dataElementOperands",
    "userGroups",
    "organisationUnits",
    "categoryOptionCombos",
    "dhis2Type",
    "objectType",
    '"klass"',
    "shortName",
)

_MERMAID_HEADS: Final[tuple[str, ...]] = (
    "erDiagram",
    "flowchart",
    "graph",
    "sequenceDiagram",
    "stateDiagram",
    "classDiagram",
    "journey",
    "gantt",
)


def resolve_parser_type(name: str) -> ParserType | None:
    """The parser type AI-INP-002 assigns to this filename or media type, or None.

    Pure over the string: no file access, no clock, no guessing from the bytes.
    """
    candidate = (name or "").strip().lower()
    if not candidate:
        return None
    if candidate in PARSERS_BY_MEDIA_TYPE:
        return PARSERS_BY_MEDIA_TYPE[candidate]
    if "/" in candidate and "." not in candidate.rsplit("/", 1)[-1]:
        #: shaped like a media type ("text/csv"), not like a path with a suffix
        return None
    suffix = _suffix(candidate)
    if suffix is None:
        return None
    return PARSERS_BY_SUFFIX.get(suffix)


def deterministic_for(name: str, content: bytes | None = None) -> bool:
    """True when a deterministic parser owns this input, so a model may not (AI-INP-003).

    Answered from the routing table, and from the content only where the table cannot
    distinguish: ``.json`` names both DHIS2 metadata (a deterministic row) and an OpenAPI
    document (a model-assisted one), so with ``content`` given the marker check decides and
    without it the answer is the conservative name-only one.
    """
    parser_type = resolve_parser_type(name)
    if (
        parser_type is ParserType.DHIS2_METADATA
        and content is not None
        and not _carries_dhis2_markers(content)
    ):
        return False
    return parser_type is not None and parser_type in DETERMINISTIC_PARSERS


def select_parser(filename: str, content: bytes) -> Parser | None:
    """The deterministic parser for this input, or None when only a model could try.

    ``content`` is used for two narrow purposes and never overrides the name: a file whose
    name says nothing (``requirements``, ``paste-from-chat``) is recognised from the text it
    starts with, because Mermaid and PlantUML write their own first line; and a ``.json``
    body is claimed only if it carries a DHIS2 marker, since an OpenAPI document shares the
    extension but belongs to another row of AI-INP-002.
    """
    parser_type = resolve_parser_type(filename)
    if parser_type is ParserType.DHIS2_METADATA and not _carries_dhis2_markers(content):
        parser_type = None
    if parser_type is None:
        parser_type = _sniff(content)
    if parser_type is None:
        return None
    return INSTANCES[parser_type]


def require_deterministic_parser(filename: str, content: bytes) -> Parser:
    """The ``route_to_model=False`` path: no match is an error, never a silent empty NRR."""
    parser = select_parser(filename, content)
    if parser is None:
        raise ParseError(
            UNMATCHED,
            f"no deterministic parser covers '{filename}' and the caller refused the "
            "model-assisted path (AI-INP-003); the input is reported, not guessed",
        )
    return parser


def parse_deterministically(
    filename: str,
    content: bytes,
    *,
    source_artifact_id: str,
    classification: DataClassification,
    options: Mapping[str, object] | None = None,
    route_to_model: bool = True,
) -> Fragment | None:
    """Run the parser the table chose, so AI-INP-003 is a routing decision, not a promise.

    With ``route_to_model=True`` an input no deterministic parser covers returns None and the
    caller takes the model-assisted branch (another increment's work).  With
    ``route_to_model=False`` the same case raises, because a caller that forbade the model
    needs to be told rather than handed an empty fragment.
    """
    parser = (
        select_parser(filename, content)
        if route_to_model
        else require_deterministic_parser(filename, content)
    )
    if parser is None:
        return None
    return parser.parse(
        content,
        source_artifact_id=source_artifact_id,
        classification=classification,
        options=dict(options or {}),
    )


def _suffix(candidate: str) -> str | None:
    name = candidate.replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in sorted(NON_DETERMINISTIC_SUFFIXES, key=len, reverse=True):
        if name.endswith(suffix):
            return suffix
    if "." not in name:
        return None
    return "." + name.rsplit(".", 1)[-1]


def _decoded_head(content: bytes) -> str | None:
    try:
        return content[:8192].decode("utf-8-sig")
    except UnicodeDecodeError:
        #: a binary payload is not one of the four deterministic families; PNG and JPG are
        #: AI-INP-002's model-assisted rows, so nothing here may claim them
        return None


def _carries_dhis2_markers(content: bytes) -> bool:
    head = _decoded_head(content)
    if head is None:
        return False
    return any(marker in head for marker in DHIS2_MARKERS)


def _sniff(content: bytes) -> ParserType | None:
    """Recognise a self-declaring deterministic input when the name said nothing."""
    head = _decoded_head(content)
    if head is None or not head.strip():
        return None
    stripped = head.lstrip()
    if stripped.startswith("@start"):
        return ParserType.PLANTUML
    if stripped.startswith(_MERMAID_HEADS):
        return ParserType.MERMAID
    if stripped.startswith(("{", "[")) and _carries_dhis2_markers(content):
        return ParserType.DHIS2_METADATA
    return None


__all__ = [
    "select_parser",
    "require_deterministic_parser",
    "deterministic_for",
    "resolve_parser_type",
    "parse_deterministically",
    "PARSERS_BY_SUFFIX",
    "PARSERS_BY_MEDIA_TYPE",
    "NON_DETERMINISTIC_SUFFIXES",
    "NON_DETERMINISTIC_MEDIA_TYPES",
    "DHIS2_MARKERS",
    "INSTANCES",
    "UNMATCHED",
]
