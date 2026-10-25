"""Document text extraction: the deterministic half of AI-INP-002's "文档" row.

A Markdown, Word or PDF input is read in two steps and this package holds the seam between
them.  Step one — :mod:`.markdown_extract`, :mod:`.docx_extract`, :mod:`.pdf_extract` — is
purely mechanical: it turns bytes into the document's text **with the coordinate each piece
was read from**.  Step two — :mod:`.llm_extract` — asks a model which of AI-INP-005's
thirteen categories each piece of text states.  Splitting them is what makes AI-INP-003
auditable for documents: the reader that could be replaced by a rule never calls a model,
and the reader that cannot be replaced by a rule receives text whose position it cannot
invent.

The two types here are the whole contract between the steps, and they live in the package
initialiser rather than in one extractor because all three extractors produce them and
neither may import from the LLM half: a module that reads a file has no business depending
on a model call, and the import guard on the deterministic parsers would then have an
exception it could not explain.

``str(segment.location)`` is the coordinate vocabulary: ``line:12`` for Markdown,
``page:3`` for a PDF, ``section:<heading>`` for Word.  It is the same string that lands in
the NRR's ``source.location`` and in an AI-INP-009 conflict, so a reviewer opens the file at
the place the extractor stood when it read the fact.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from factory_intelligence.parsing.base import Location

#: Word content before the first heading.  A name has to be stated for the coordinate to be
#: useful, and inventing a heading the document does not have would be worse than saying
#: that there was none: this is what ``source.location`` reads for a preamble paragraph.
NO_SECTION = "(no heading)"

#: A heading is a coordinate a human navigates by, so it is kept short enough to read in one
#: line while staying the heading itself; S2 caps ``source.location`` at 512 characters.
SECTION_LABEL_LIMIT = 120


@dataclass(frozen=True)
class TextSegment:
    """One readable piece of a document, with the position it was read from."""

    text: str
    location: Location

    @property
    def marker(self) -> str:
        """The coordinate as the extraction prompt shows it, and as the NRR stores it."""
        return str(self.location)


@dataclass(frozen=True)
class DocumentText:
    """What a deterministic document reader produces: ordered text and its coordinates."""

    segments: tuple[TextSegment, ...]
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return sum(len(segment.text) for segment in self.segments)

    def markers(self) -> dict[str, Location]:
        """``coordinate -> Location``, the table the model's answer is checked against."""
        out: dict[str, Location] = {}
        for segment in self.segments:
            out.setdefault(segment.marker, segment.location)
        return out

    def rendered(self, segments: Iterable[TextSegment]) -> str:
        """Chunk body with each piece labelled by its own coordinate.

        The label is what lets a model *cite* where a fact was written rather than guess,
        and it is why a chunk is assembled from segments and not from a character slice: a
        slice could cut a sentence and leave the reader with a fact and no source line.
        """
        return "\n".join(f"[{segment.marker}] {segment.text}" for segment in segments)


__all__ = [
    "DocumentText",
    "NO_SECTION",
    "SECTION_LABEL_LIMIT",
    "TextSegment",
]
