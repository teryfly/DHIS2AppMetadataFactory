"""Word (.docx) text extraction — no dependency, no model (AI-INP-002, AI-INP-003a).

A ``.docx`` is a zip of XML parts, and the body of the document is one of them:
``word/document.xml``.  Reading it with ``zipfile`` and :mod:`xml.etree.ElementTree` is
deliberate — S3's dependency list stays at the two libraries AI-INP-002's mandatory formats
need (``openpyxl`` for workbooks, ``pypdf`` for PDFs) and a Word reader is a paragraph walk
over one part, not a format to install for.

The coordinate vocabulary is ``section:<heading>``.  A Word file has no line numbers a
reviewer can use — pagination is computed by the application at render time, not stored —
so the position that means something to a human is the heading the paragraph sits under.
That is also why the walk is ordered: the current heading is whatever the last heading-styled
paragraph said, and content before the first one is reported as
:data:`~factory_intelligence.parsing.documents.NO_SECTION` rather than being attributed to a
heading the document does not have.

Two refusals, both required by AI-INP-003a's reasoning:

* a file that is not a zip at all is the legacy binary ``.doc``.  S3 does not read it, says
  so, and tells the user to save it as ``.docx`` or supply the pages as images;
* a document whose paragraphs carry no text (an image pasted into a one-page Word file is a
  scan, whatever the extension says) raises
  :class:`~factory_intelligence.errors.ScannedDocumentUnsupported`, because the alternative
  is the silently empty NRR the SRS forbids.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree

from factory_intelligence.errors import ScannedDocumentUnsupported
from factory_intelligence.parsing.base import Location, ParseError
from factory_intelligence.parsing.documents import (
    NO_SECTION,
    SECTION_LABEL_LIMIT,
    DocumentText,
    TextSegment,
)

#: Registered in ``factory_intelligence.registries.AI_ERROR_CODES`` (00 doc 6.5).
INPUT_UNREADABLE = "AI_PARSER_INPUT_INVALID"

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCUMENT_PART = "word/document.xml"

#: ``Heading1``/``heading 2`` are the built-in names; a template may use its own style id,
#: in which case an explicit ``w:outlineLvl`` on the paragraph is what makes it a heading.
_HEADING_STYLE = re.compile(r"^(?:heading|标题)\s*([1-9])$", re.IGNORECASE)
_OUTLINE_LEVEL = re.compile(r"^[1-9]$")

_SKIP_STYLE_PREFIXES = ("TOC", "Hyperlink", "Footer", "Header", "AnnotationReference")

#: An OOXML part is a plain document: it declares no DTD and needs no entity definitions.
#: Rejecting a part that does is the guard against an entity-expansion or external-entity
#: payload (the input is an untrusted upload), since :mod:`xml.etree.ElementTree` is
#: documented as *not* hardened against hostile XML and adding a dependency is not on the
#: table for a format this parser only reads one part of.
_FORBIDDEN_XML = re.compile(rb"<\s*(?:!DOCTYPE|!ENTITY)", re.IGNORECASE)

#: Bounds on what one Word input may cost, so a compressed or nested payload cannot exhaust
#: the worker that was asked to read it.  AI-NFR-06's 100 MB is a limit on the *file*; a
#: document that decompresses past these is reported, never half-read.
MAX_PART_BYTES = 64 * 1024 * 1024
MAX_PARAGRAPHS = 200_000


def extract_docx(content: bytes, *, filename: str | None = None) -> DocumentText:
    """Read ``word/document.xml`` into ordered paragraph segments, located by section."""
    document = _read_part(content, filename=filename)
    root = _parse_xml(document, filename=filename)
    segments: list[TextSegment] = []
    heading = NO_SECTION
    paragraph_index = 0
    for paragraph in _paragraphs(root):
        paragraph_index += 1
        if paragraph_index > MAX_PARAGRAPHS:
            raise ParseError(
                INPUT_UNREADABLE,
                f"the Word input has more than {MAX_PARAGRAPHS} paragraphs, which no "
                "requirements document has; it is reported rather than read partially, "
                "because a truncated NRR would be the silent truncation AI-POL-003 forbids",
            )
        text = _paragraph_text(paragraph)
        if not text:
            continue
        level = _heading_level(paragraph)
        if level is not None:
            #: a heading is a section of its own and the label every later paragraph cites
            heading = text[:SECTION_LABEL_LIMIT]
            segments.append(TextSegment(text=text, location=Location.section(heading)))
            continue
        segments.append(TextSegment(text=text, location=Location.section(heading)))
    if not segments:
        raise ScannedDocumentUnsupported(
            "the Word document has no text layer: it appears to be a scan or an image-only "
            "file. Upload a text document, or the key pages as images so they can go through "
            "the visual extractor instead (AI-INP-003a)"
        )
    notes: dict[str, Any] = {
        "paragraphs": paragraph_index,
        "segments": len(segments),
        "characters": sum(len(item.text) for item in segments),
        "sections": _unique_headings(segments),
    }
    return DocumentText(segments=tuple(segments), notes=notes)


def _read_part(content: bytes, *, filename: str | None) -> bytes:
    """The document body part, or a refusal that names what the user should do instead."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo(DOCUMENT_PART) if DOCUMENT_PART in archive.namelist() else None
            if info is None:
                raise ParseError(
                    INPUT_UNREADABLE,
                    f"the Word input has no {DOCUMENT_PART} part, so it is not a .docx "
                    f"document ({filename or 'no filename given'})",
                )
            if info.file_size > MAX_PART_BYTES:
                raise ParseError(
                    INPUT_UNREADABLE,
                    f"the {DOCUMENT_PART} part of the Word input claims {info.file_size} "
                    f"bytes, over the {MAX_PART_BYTES} byte limit; the document is reported, "
                    "not read partially",
                )
            with archive.open(info) as handle:
                payload = handle.read(MAX_PART_BYTES + 1)
            if len(payload) > MAX_PART_BYTES:
                raise ParseError(
                    INPUT_UNREADABLE,
                    f"the {DOCUMENT_PART} part of the Word input decompresses past the "
                    f"{MAX_PART_BYTES} byte limit; it is reported rather than truncated",
                )
            return payload
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParseError(
            INPUT_UNREADABLE,
            "the Word input is not a .docx package: a legacy binary .doc file must be saved "
            "as .docx first, or the pages supplied as images for the visual extractor "
            f"(AI-INP-002); could not read it ({exc})",
        ) from exc


def _parse_xml(document: bytes, *, filename: str | None) -> ElementTree.Element:
    if _FORBIDDEN_XML.search(document[:65_536]):
        raise ParseError(
            INPUT_UNREADABLE,
            f"the {DOCUMENT_PART} part of the Word input ({filename or 'no filename given'}) "
            "declares a DTD or an entity; an OOXML document part never does, so the file is "
            "refused instead of parsed (AI-RT-030: input is data, not instructions)",
        )
    try:
        return ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise ParseError(
            INPUT_UNREADABLE,
            f"the {DOCUMENT_PART} part of the Word input ({filename or 'no filename given'}) "
            f"is not well-formed XML: {exc}",
        ) from exc


def _paragraphs(root: ElementTree.Element) -> list[ElementTree.Element]:
    """Every paragraph in document order, table cells included.

    ``iter()`` is the ordered walk; a table's cells are paragraphs in the file, and a field
    list written as a Word table is still a field list — skipping it would silently drop the
    input's content.
    """
    body = root.find(_tag("body"))
    scope = body if body is not None else root
    return list(scope.iter(_tag("p")))


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        tag = node.tag
        if tag == _tag("t"):
            parts.append(node.text or "")
        elif tag in (_tag("tab"), _tag("br"), _tag("cr")):
            parts.append(" ")
        elif tag == _tag("instrText"):
            #: a field instruction (``PAGEREF``, ``TOC``) is Word's own machinery, not a
            #: business fact, and quoting it into a requirement would be noise a human reads
            continue
    return " ".join(" ".join(parts).split()).strip()


def _heading_level(paragraph: ElementTree.Element) -> int | None:
    """The heading level a paragraph declares, or None when it is ordinary content."""
    properties = paragraph.find(_tag("pPr"))
    if properties is None:
        return None
    style = properties.find(_tag("pStyle"))
    value = (style.get(_attr("val")) or "").strip() if style is not None else ""
    if value:
        match = _HEADING_STYLE.match(value.replace("_", " "))
        if match is not None:
            return int(match.group(1))
        if value.startswith(_SKIP_STYLE_PREFIXES):
            return None
    outline = properties.find(_tag("outlineLvl"))
    if outline is not None:
        level = (outline.get(_attr("val")) or "").strip()
        if _OUTLINE_LEVEL.match(level):
            return int(level)
    return None


def _tag(name: str) -> str:
    return f"{{{WORD_NS}}}{name}"


def _attr(name: str) -> str:
    return f"{{{WORD_NS}}}{name}"


def _unique_headings(segments: list[TextSegment]) -> list[str]:
    out: list[str] = []
    for segment in segments:
        label = segment.location.value
        if label not in out:
            out.append(label)
    return out


__all__ = ["extract_docx", "DOCUMENT_PART", "WORD_NS", "INPUT_UNREADABLE"]
