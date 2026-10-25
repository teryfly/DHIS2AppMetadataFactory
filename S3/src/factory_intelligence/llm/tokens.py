"""Deterministic token estimation (AI-LLM-001's ``count_tokens`` half).

Used *before* a call, to decide which model's context window the constructed context
fits (AI-POL-007).  A real count requires the provider's tokenizer, which is a network
call per model and a different library per vendor — so this is a stable upper-bound-ish
estimate instead, and that is the right trade: the decision it feeds is "is this context
three times too big", where precision buys nothing, while *stability* buys everything
because the same context must produce the same decision twice (AI-INP-010, AC-REQ-01).
"""

from __future__ import annotations

from factory_intelligence.llm.types import ChatMessage, StructuredRequest

#: Rough characters per token for the Latin-script prose these prompts are written in.
_CHARS_PER_TOKEN = 4
#: A single token floor per message, for the role delimiters a chat template adds.
_MESSAGE_OVERHEAD_TOKENS = 4
#: Assumed cost of one image part; vendors quote anywhere from 250 to 1100 tokens for a
#: mid-resolution picture, and over-estimating is the safe direction for a fit test.
_IMAGE_TOKENS = 850


def estimate_text(text: str) -> int:
    """Tokens for one string: characters over four, rounded up, never negative.

    Whitespace is not stripped: a context's formatting is part of what the model pays
    for, and collapsing it would under-count the fenced blocks that make up most of a
    document extraction.
    """
    if not text:
        return 0
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def estimate_messages(messages: list[ChatMessage] | tuple[ChatMessage, ...]) -> int:
    total = 0
    for message in messages:
        total += estimate_text(message.content) + _MESSAGE_OVERHEAD_TOKENS
        total += _IMAGE_TOKENS * len(message.images)
    return total


def estimate_request(request: StructuredRequest) -> int:
    """The whole billable prompt, including the JSON schema a structured call carries.

    A schema is sent to the model on every corrective retry too, so omitting it would
    under-state the largest single non-document component of a mapping run.
    """
    from factory_foundation.api import canonical_json

    total = estimate_messages(request.messages)
    total += estimate_text(canonical_json(request.json_schema).decode("utf-8")) // 2
    return total


__all__ = ["estimate_text", "estimate_messages", "estimate_request"]
