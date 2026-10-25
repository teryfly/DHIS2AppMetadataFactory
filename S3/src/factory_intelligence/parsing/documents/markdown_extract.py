"""Markdown text extraction — the deterministic half of AI-INP-002's document row.

Markdown states its own structure, so reading it needs no model: a heading is a heading
because a line begins with ``#``, and a paragraph is the lines between two blank ones.  That
mechanical reading is what this module does, and it does two things the LLM half cannot do
for itself:

* **it keeps the line numbers**, so every fact a model later draws from a paragraph carries
  ``Location.line(n)`` — the coordinate AI-INP-005 requires and AI-INP-009 lists in a
  conflict;
* **it drops nothing silently**.  A file that yields no readable text is an error
  (``AI_PARSER_INPUT_INVALID``), not an empty document, because AI-INP-003a's reasoning —
  a silently empty NRR is worse than a loud failure — applies to every format.

Emphasis markers, list bullets and table pipes are stripped from the *text* the model reads
but never from the line numbering, because a model asked to extract requirements from
``**Patient age** (月龄)`` should not have to parse markup, while a reviewer following the
citation must still land on the right line.  Code fences are kept as text with their own
coordinate: a fenced example inside a specification often *is* the requirement (a payload
shape, an enumeration), and dropping it would be a silent truncation of the input.
"""

from __future__ import annotations

import re
from typing import Any

from factory_intelligence.parsing.base import Location, ParseError
from factory_intelligence.parsing.documents import DocumentText, TextSegment

#: Registered in ``factory_intelligence.registries.AI_ERROR_CODES`` (00 doc 6.5).
INPUT_UNREADABLE = "AI_PARSER_INPUT_INVALID"

_HEADING = re.compile(r"^(?P<level>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_RULE = re.compile(r"^ {0,3}(?:(?:[-*_])\s*){3,}$")
_LIST_ITEM = re.compile(r"^(?P<marker>\s*(?:[-*+]|\d+[.)]))\s+(?P<text>.+)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_DIVIDER = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")
_FENCE = re.compile(r"^\s*(?P<fence>```+|~~~+)")

#: Markup that is stripped, each pattern paired so it cannot eat the text it wraps.  A lone
#: ``_`` is deliberately *not* among them: ``patient_age`` and ``weight_kg`` are field names,
#: and a reader that stripped intra-word underscores would turn a data requirement into a
#: word nobody recognises — which is exactly the kind of silent corruption a deterministic
#: reader may not commit.
_LINK = re.compile(r"!?\[(?P<alt>[^\]]*)\]\((?:[^)]*)\)")
_STRONG = re.compile(r"(?:\*\*|__)(?P<body>.+?)(?:\*\*|__)")
_EMPHASISED = re.compile(r"\*(?P<body>[^*\n]+)\*")
_STRUCK = re.compile(r"~~(?P<body>.+?)~~")
_CODE = re.compile(r"`+(?P<body>[^`]*)`+")

#: A setext heading (``Title`` + ``===``) is a heading, not a paragraph and not a rule.
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(?:=+|-+)\s*$")


def extract_markdown(content: bytes) -> DocumentText:
    """Read Markdown bytes into ordered segments, each located at its real 1-based line."""
    text = _decode(content)
    segments = _blocks(text)
    if not segments:
        raise ParseError(
            INPUT_UNREADABLE,
            "the Markdown input contains no readable text, so no requirement could be "
            "extracted from it (AI-INP-003a: an empty NRR is reported, not written)",
        )
    notes: dict[str, Any] = {
        "lines": len(text.splitlines()),
        "segments": len(segments),
        "characters": sum(len(item.text) for item in segments),
        "outline": _outline(text),
    }
    return DocumentText(segments=tuple(segments), notes=notes)


def _outline(text: str) -> list[dict[str, Any]]:
    """The document's headings with their line numbers — the structure a reviewer scans.

    Recorded in the parse notes because it is the one thing a Markdown reader can state with
    certainty, and it lets a reader check that a fact was taken from the section it names.
    """
    out: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        heading = _HEADING.match(line)
        if heading is not None:
            out.append(
                {
                    "level": len(heading.group("level")),
                    "text": _clean(heading.group("text")),
                    "line": number,
                }
            )
    return out


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(
            INPUT_UNREADABLE, f"the Markdown input is not UTF-8 text: {exc}"
        ) from exc


def _blocks(text: str) -> list[TextSegment]:
    """Group lines into the blocks a reader would call one statement.

    A heading is its own block: it is what a section of the document is *about*, so a fact
    taken from under it must be able to cite both the heading's line and the paragraph's.
    """
    lines = text.splitlines()
    out: list[TextSegment] = []
    index = 0
    while index < len(lines):
        number = index + 1
        line = lines[index]
        stripped = line.strip()
        if not stripped or _RULE.match(stripped):
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence is not None:
            block, index = _fenced_block(lines, index)
            if block:
                out.append(TextSegment(text=block, location=Location.line(number)))
            continue
        heading = _HEADING.match(line)
        if heading is not None and not _is_setext_underline(lines, index):
            out.append(
                TextSegment(
                    text=_clean(heading.group("text")),
                    location=Location.line(number),
                )
            )
            index += 1
            continue
        if _is_setext_underline(lines, index + 1):
            #: ``Title`` over ``=====`` is one heading, and the coordinate is the title line
            out.append(TextSegment(text=_clean(stripped), location=Location.line(number)))
            index += 2
            continue
        if _TABLE_ROW.match(line):
            block, index = _table_block(lines, index)
            out.append(TextSegment(text=block, location=Location.line(number)))
            continue
        if _LIST_ITEM.match(line):
            #: one list item is one statement, so each keeps its own line: a bullet list of
            #: fields is a field list, and merging the bullets into one paragraph would give
            #: the extraction one fact where the document wrote several
            items, index = _list_items(lines, index)
            out.extend(
                TextSegment(text=text, location=Location.line(number))
                for number, text in items
            )
            continue
        block, index = _paragraph(lines, index)
        if block:
            out.append(TextSegment(text=block, location=Location.line(number)))
    return out


def _list_items(lines: list[str], start: int) -> tuple[list[tuple[int, str]], int]:
    """The consecutive list items from ``start``, each with its own 1-based line number."""
    out: list[tuple[int, str]] = []
    index = start
    while index < len(lines):
        item = _LIST_ITEM.match(lines[index])
        if item is None:
            break
        out.append((index + 1, _clean(item.group("text"))))
        index += 1
    return out, index


def _paragraph(lines: list[str], start: int) -> tuple[str, int]:
    """One paragraph: consecutive non-blank lines that are not a heading, list, table or fence."""
    collected: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if (
            not stripped
            or _FENCE.match(line)
            or _HEADING.match(line)
            or _TABLE_ROW.match(line)
            or _LIST_ITEM.match(line)
        ):
            break
        if _is_setext_underline(lines, index) and collected:
            index += 1
            break
        if _RULE.match(stripped):
            break
        collected.append(_clean(stripped))
        index += 1
    return " ".join(collected), index


def _table_block(lines: list[str], start: int) -> tuple[str, int]:
    """A whole Markdown table as one segment, delimiter row discarded.

    A table of fields is the clearest thing in a requirements document, and splitting it
    row-by-row would throw away the header that says what each column means.
    """
    rows: list[str] = []
    index = start
    while index < len(lines) and _TABLE_ROW.match(lines[index]):
        cells = [
            _clean(cell.strip())
            for cell in lines[index].strip().strip("|").split("|")
            if cell.strip()
        ]
        if not _TABLE_DIVIDER.match(lines[index]) and cells:
            rows.append(" | ".join(cells))
        index += 1
    return " /// ".join(rows), index


def _fenced_block(lines: list[str], start: int) -> tuple[str, int]:
    marker = _FENCE.match(lines[start])
    fence = marker.group("fence") if marker else "```"
    body: list[str] = []
    index = start + 1
    while index < len(lines):
        if lines[index].strip().startswith(fence[0] * 3):
            index += 1
            break
        body.append(lines[index].strip())
        index += 1
    return " ".join(part for part in body if part), index


def _is_setext_underline(lines: list[str], index: int) -> bool:
    return 0 <= index < len(lines) and bool(_SETEXT_UNDERLINE.match(lines[index]))


def _clean(value: str) -> str:
    """Strip paired markup and link targets, keep the words.

    ``[DHIS2](https://dhis2.org)`` is written as a link but *reads* as ``DHIS2``; the model is
    asked about requirements, not about Markdown, and the coordinate is unaffected.  Paired
    patterns only — see :data:`_STRONG` for why a stray underscore survives.
    """
    text = _LINK.sub(r"\g<alt>", value)
    text = _CODE.sub(r"\g<body>", text)
    text = _STRONG.sub(r"\g<body>", text)
    text = _STRUCK.sub(r"\g<body>", text)
    text = _EMPHASISED.sub(r"\g<body>", text)
    return " ".join(text.split()).strip()


__all__ = ["extract_markdown", "INPUT_UNREADABLE"]
