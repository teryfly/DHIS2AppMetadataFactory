"""PDF text-layer extraction (AI-INP-002 "文档 … 仅含文本层", AI-INP-003a).

A PDF page is either a picture of text or text with a picture around it, and only the second
kind can be read without a vision model.  :mod:`pypdf`'s ``extract_text()`` returns the text
layer of one page — and an empty string for a page that is a scan — which is exactly the
distinction AI-INP-003a asks the parser to make: MVP does not support scanned input, and the
parse *fails*, with a message that tells the user what to upload instead.

A refusal rather than an empty result is the requirement.  A 300-page scan that "parsed"
into zero entries would pass every later stage of the factory — a Requirements Agent given an
empty NRR would happily invent one — and nothing downstream could tell an empty document from
an unreadable one.

The coordinate is ``page:<n>``, the one a reader of a PDF can actually use; a page is then
split at its blank lines so a fact cites the block it was written in, and the block order is
the reading order the file states.
"""

from __future__ import annotations

import io
from typing import Any

from factory_intelligence.errors import ParserDependencyMissing, ScannedDocumentUnsupported
from factory_intelligence.parsing.base import Location, ParseError
from factory_intelligence.parsing.documents import DocumentText, TextSegment

#: Registered in ``factory_intelligence.registries.AI_ERROR_CODES`` (00 doc 6.5).
INPUT_UNREADABLE = "AI_PARSER_INPUT_INVALID"

#: An empty page is normal in a report (a figure, a separator); a document that is *mostly*
#: empty pages is a scan, and the message a user gets must not accuse them of that on the
#: strength of one blank page.  Below this share of text-bearing pages, the input is a scan.
MIN_TEXT_PAGE_SHARE = 0.05

#: A page number is only a coordinate if it is the one the reader counts; PDFs that carry
#: page labels (``i``, ``ii``, ``1`` after a roman preface) are read by physical position.
PAGE_LABEL_NOTE = "physical page order, not PDF page labels"

SCAN_MESSAGE = (
    "the PDF has no text layer: it is a scan or an image-only export, which MVP does not "
    "support. Upload a text document, or the key pages as images (PNG/JPG) so they can go "
    "through the visual extractor instead (AI-INP-003a)"
)


def extract_pdf(content: bytes, *, filename: str | None = None) -> DocumentText:
    """Read the text layer of every page into ordered, page-located segments."""
    reader = _open(content, filename=filename)
    pages = list(reader.pages)
    if not pages:
        raise ParseError(INPUT_UNREADABLE, "the PDF input has no pages at all")

    segments: list[TextSegment] = []
    text_pages = 0
    unreadable: list[dict[str, Any]] = []
    for number, page in enumerate(pages, start=1):
        try:
            raw = str(page.extract_text() or "")
        except Exception as exc:
            #: one broken page must not lose the rest of the document, and must not be
            #: silent: the page is recorded as unreadable in the parse notes instead of
            #: being paraphrased into the text a model would then read as a fact
            unreadable.append({"page": number, "error": str(exc)})
            raw = ""
        if not raw.strip():
            continue
        text_pages += 1
        segments.extend(_blocks(raw, number))

    if not segments or (text_pages / len(pages)) < MIN_TEXT_PAGE_SHARE:
        raise ScannedDocumentUnsupported(SCAN_MESSAGE)

    notes: dict[str, Any] = {
        "pages": len(pages),
        "textPages": text_pages,
        "segments": len(segments),
        "characters": sum(len(item.text) for item in segments),
        "pageCoordinates": PAGE_LABEL_NOTE,
        "metadata": _metadata(reader, filename=filename),
    }
    if unreadable:
        notes["unreadablePages"] = unreadable
    return DocumentText(segments=tuple(segments), notes=notes)


def _open(content: bytes, *, filename: str | None) -> Any:
    """One PDF reader, refusing an encrypted file rather than reporting an empty document."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - pypdf is a declared dependency
        raise ParserDependencyMissing(
            "pypdf is not installed, so the mandatory PDF input format cannot be parsed; "
            "the file was not skipped and no empty NRR was produced"
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise ParseError(
            INPUT_UNREADABLE, f"the PDF input could not be opened ({filename or 'no name'}): {exc}"
        ) from exc
    #: a password-only PDF has no text S3 may read, and "decrypt with an empty password"
    #: is the one attempt that is always safe to make
    if getattr(reader, "is_encrypted", False) and not reader.decrypt(""):
        raise ParseError(
            INPUT_UNREADABLE,
            f"the PDF input ({filename or 'no name'}) is encrypted; supply an "
            "unencrypted file or the pages as images",
        )
    return reader


def _blocks(raw: str, number: int) -> list[TextSegment]:
    """Split a page's text at blank lines, keeping non-empty blocks in reading order."""
    out: list[TextSegment] = []
    for block in io.StringIO(raw).read().split("\n\n"):
        text = " ".join(block.split()).strip()
        if text:
            out.append(TextSegment(text=text, location=Location.page(number)))
    return out


def _metadata(reader: Any, *, filename: str | None) -> dict[str, Any]:
    info: dict[str, Any] = {"filename": filename} if filename else {}
    document = getattr(reader, "metadata", None)
    getter = getattr(document, "get", None)
    if not callable(getter):
        return info
    for key in ("/Title", "/Author", "/CreationDate"):
        value = getter(key)
        if isinstance(value, str) and value.strip():
            info[key.lstrip("/")] = value.strip()
    return info


__all__ = ["extract_pdf", "INPUT_UNREADABLE", "SCAN_MESSAGE", "MIN_TEXT_PAGE_SHARE"]
