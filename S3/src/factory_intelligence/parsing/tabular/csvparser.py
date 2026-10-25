"""CSV / TSV input parser — field lists, option lists, organisation lists (AI-INP-002, AI-INP-006).

Excel/CSV is the "结构化清单" row of AI-INP-002: a deterministic parser plus a column
mapping, with the *mapping* reviewed by a human.  Two consequences shape this module:

* **AI-INP-003 / AI-BR-02.**  Nothing here imports the LLM layer; a CSV is always parsed by
  rules, so a model can never be chosen for it.
* **AI-INP-006.**  When no ``column_mapping`` was supplied the parser still parses — it
  proposes a mapping by deterministic header-name matching, marks the fragment
  ``needs_review`` with a reason, returns **one** open question describing the proposal,
  and every entry of that fragment is ``CANDIDATE``: nothing may be confirmed on an
  unconfirmed mapping.  When a mapping *is* supplied it is used as-is and no question is
  asked.

The header→category rules, the proposal, the collector that turns a row into an entry and
the review gate all live in :mod:`factory_intelligence.parsing.columnmapping`, shared with
the ``.xlsx`` parser so one header cannot mean two things in two formats.  What is CSV-only
is in this file: reading the text, sniffing the delimiter, and numbering rows the way the
file does.

Locations are ``Location.row(n, sheet=<file basename or "csv">)`` with the real 1-based row
of the file, because an AI-INP-009 conflict has to name the row that disagrees.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Mapping
from typing import Any

from factory_foundation.api import DataClassification

from factory_intelligence.enums import NrrStatus, ParserType
from factory_intelligence.parsing.base import Fragment, Location, ParseError
from factory_intelligence.parsing.columnmapping import (
    ACTION_HEADER_PATTERN,
    ACTION_VERB_PATTERN,
    CATEGORY_PHRASES,
    HEADER_CATEGORY_RULES,
    OPTION_HEADER_RULES,
    SET_HEADER_PATTERN,
    TablePlan,
    TabularCollector,
    analyse_table,
    apply_mapping_gate,
    category_for_header,
    effective_headers,
    mapping_of,
    mapping_question,
    normalise_header,
    option_columns,
    sheet_name,
)

#: Registered in ``factory_intelligence.registries.AI_ERROR_CODES`` (00 doc 6.5): the bytes
#: are not readable text, so no row — and therefore no location — exists.
INPUT_UNREADABLE = "AI_PARSER_INPUT_INVALID"


class CsvParser:
    """CSV / TSV → NRR fragment, with the AI-INP-006 column-mapping gate."""

    parser_type: ParserType = ParserType.CSV
    parser_version: str = "1.0.0"

    def parse(
        self,
        content: bytes,
        *,
        source_artifact_id: str,
        classification: DataClassification,
        options: Mapping[str, Any],
    ) -> Fragment:
        text = _decode(content, "CSV/TSV")
        delimiter = _delimiter_for(text, options)
        rows = _read_rows(text, delimiter)
        if not any(any(_present(value) for value in row) for row in rows):
            raise ParseError(INPUT_UNREADABLE, "the CSV/TSV input has no rows at all")

        fragment = Fragment(
            parser_type=self.parser_type,
            parser_version=self.parser_version,
            source_artifact_id=source_artifact_id,
            classification=classification,
        )
        sheet = sheet_name(options, default="csv")
        has_header = bool(options.get("has_header", True))
        mapping = mapping_of(options, sheet)
        fragment.notes.update(
            _record_notes(
                content, delimiter=delimiter, has_header=has_header, mapping_given=bool(mapping)
            )
        )

        header_row = rows[0] if has_header else []
        if has_header:
            headers = effective_headers(header_row, mapping)
            numbered = list(enumerate(rows[1:], start=2))
        else:
            width = max(len(row) for row in rows)
            headers = effective_headers([str(index) for index in range(1, width + 1)], mapping)
            numbered = list(enumerate(rows, start=1))
        fragment.notes["headers"] = list(headers)

        body = [(number, row) for number, row in numbered if any(_present(v) for v in row)]
        plan = analyse_table(headers, [row for _number, row in body])
        if plan is None:
            apply_mapping_gate(
                fragment,
                sheet=sheet,
                headers=headers,
                plan=None,
                unmatched=True,
                affected_keys=(),
                per_sheet=False,
            )
            return fragment

        collector = TabularCollector(
            fragment=fragment,
            sheet=sheet,
            plan=plan,
            #: AI-INP-006: an unconfirmed mapping confirms nothing downstream either
            status=NrrStatus.CANDIDATE if not mapping else NrrStatus.CONFIRMED,
            source_artifact_id=source_artifact_id,
            location_of=lambda row_number, _column: Location.row(row_number, sheet=sheet),
        )
        collector.collect(body)

        if not mapping:
            apply_mapping_gate(
                fragment,
                sheet=sheet,
                headers=headers,
                plan=plan,
                unmatched=False,
                affected_keys=[entry.business_key for entry in fragment.entries],
                per_sheet=False,
            )
        return fragment


def _present(value: object) -> bool:
    """A cell counts when it says something, not merely when it exists."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _decode(content: bytes, label: str) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(INPUT_UNREADABLE, f"the {label} input is not UTF-8 text: {exc}") from exc


def _delimiter_for(text: str, options: Mapping[str, Any]) -> str:
    supplied = options.get("delimiter")
    if isinstance(supplied, str) and supplied:
        return supplied
    filename = options.get("filename")
    if isinstance(filename, str) and filename.lower().endswith(".tsv"):
        return "\t"
    sample = text[:4096]
    if not sample.strip():
        return ","
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        #: a single-column sheet has nothing to sniff between its fields; the commonest
        #: delimiter is used and recorded in notes rather than guessed from a model
        return ","


def _read_rows(text: str, delimiter: str) -> list[list[str]]:
    return [list(row) for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]


def _record_notes(
    content: bytes, *, delimiter: str, has_header: bool, mapping_given: bool
) -> dict[str, Any]:
    """The AI-INP-004 fields a parse record carries besides parserType/parserVersion."""
    return {
        "inputHash": "sha256:" + hashlib.sha256(content).hexdigest(),
        "inputBytes": len(content),
        "usedModel": False,
        "delimiter": delimiter,
        "hasHeader": has_header,
        "mappingSupplied": mapping_given,
    }


__all__ = [
    "CsvParser",
    "INPUT_UNREADABLE",
    # the AI-INP-006 rule lives in ``columnmapping``; these names are re-exported because
    # both tabular formats and their regression tests address them through this module
    "TabularCollector",
    "TablePlan",
    "HEADER_CATEGORY_RULES",
    "OPTION_HEADER_RULES",
    "ACTION_HEADER_PATTERN",
    "ACTION_VERB_PATTERN",
    "SET_HEADER_PATTERN",
    "CATEGORY_PHRASES",
    "normalise_header",
    "category_for_header",
    "analyse_table",
    "option_columns",
    "effective_headers",
    "mapping_question",
    "sheet_name",
]
