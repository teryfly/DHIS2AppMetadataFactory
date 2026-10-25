"""Fencing untrusted input (AI-RT-030, AI-RT-031, AI-BR-07).

Business documents, source code, existing metadata and any text inside an image are data.
The mechanism is a pair of tags naming the source artifact and its hash, so that:

* a model can tell where project content starts and stops, which is what the notice in
  :mod:`factory_intelligence.prompts.render` refers to;
* a reviewer can prove content was fenced rather than concatenated, because the tag carries
  the artifact id and hash the run was given;
* an injection has nothing to write to — the provider protocol has no side-effecting method
  at all (AI-RT-032), so the strongest thing a fenced instruction can do is change the
  answer, which the ladder then judges.

Escaping is the part that is easy to get wrong and is the entire security property: a
business document containing ``</untrusted>`` followed by instructions must not be able to
close its own fence and become a system message.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from factory_intelligence.enums import UNTRUSTED_KINDS, ItemSourceKind
from factory_intelligence.llm.types import ChatMessage, ImageInput, Role

#: Anything that could end the current fence or start a convincing fake one.
_UNSAFE = re.compile(r"</?\s*untrusted[^>]*>", re.IGNORECASE)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def neutralise(content: str) -> str:
    """Defuse a payload that tries to close its own fence.

    The rewrite is idempotent — a second pass cannot undo the first — because the context is
    stored and re-read on resume (AI-RT-015), and a re-read that behaved differently would
    make a resumed run send content the policy engine never judged.
    """
    if not content:
        return ""
    cleaned = _CONTROL_CHARS.sub("", content)
    return _UNSAFE.sub("[fence-marker]", cleaned)


def fence(
    *,
    kind: ItemSourceKind,
    label: str,
    content: str,
    artifact_id: str | None = None,
    content_hash: str | None = None,
    images: Sequence[ImageInput] = (),
) -> ChatMessage:
    """One piece of project content, fenced, as a user message.

    ``images`` rides on the same message rather than a separate one so an image extraction
    keeps its provenance in a single unit: the picture and the fields drawn from it are one
    context item, which is what AI-INP-005's "source reference" then refers to.
    """
    attributes = [f'kind="{kind.value}"']
    if artifact_id:
        attributes.append(f'source="{artifact_id}"')
    if content_hash:
        attributes.append(f'hash="{content_hash}"')
    head = f"[{kind.value}] {label}" if label else f"[{kind.value}]"
    body = neutralise(content)
    text = f"{head}\n<untrusted {' '.join(attributes)}>\n{body}\n</untrusted>"
    return ChatMessage(role="user", content=text, images=tuple(images))


def plain(*, kind: ItemSourceKind, text: str) -> ChatMessage:
    """A message S3 authored: system rules, stage rules, thresholds, or validation feedback.

    Trusted content is *not* fenced. Marking everything untrusted is as uninformative as
    marking nothing, and the corrective ``VALIDATION_ERRORS`` block has to be distinguishable
    from project data or the model cannot tell which of its own output the errors describe.
    """
    role: Role = "system" if kind is ItemSourceKind.SYSTEM_RULES else "user"
    return ChatMessage(role=role, content=text)


def is_untrusted(kind: ItemSourceKind) -> bool:
    return kind in UNTRUSTED_KINDS


__all__ = ["fence", "plain", "neutralise", "is_untrusted"]
