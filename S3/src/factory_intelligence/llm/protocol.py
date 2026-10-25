"""``LLMProvider`` — the only interface an agent may know (AI-LLM-001/002, AI-NFR-05).

AI-LLM-001 requires at least four capabilities; they map here as follows, and the
mapping is stated because a reviewer will check it:

===============================  ==================================================
SRS capability                    Member
===============================  ==================================================
结构化生成（按 JSON Schema 约束）  :meth:`LLMProvider.generate_structured`
视觉输入                          :attr:`factory_intelligence.llm.types.ChatMessage.images`
                                 on the request, gated by
                                 :attr:`Capabilities.vision` — one method, because a
                                 screenshot extraction is still a schema-constrained
                                 answer and splitting it in two would lose the link
                                 between the picture and the fields drawn from it
                                 (AI-INP-005 provenance)
token 计数                        :meth:`LLMProvider.count_tokens`
健康检查                          :meth:`LLMProvider.health`
===============================  ==================================================

AI-LLM-002's "适配器必须声明提供方类别与能力" is the :attr:`info` property: a provider
that cannot say which :class:`ProviderClass` it belongs to cannot be policy-evaluated
at all, so the declaration is structural, not documentary.

AI-RT-032 is enforced here rather than in a prompt: there is no method on this
protocol that can produce a side effect.  A model's only output channel is a
structured answer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from factory_intelligence.llm.types import (
    HealthStatus,
    ModelResponse,
    ProviderInfo,
    StructuredRequest,
)


@runtime_checkable
class LLMProvider(Protocol):
    """One configured model endpoint."""

    @property
    def info(self) -> ProviderInfo:
        """Provider, class, model name and capabilities (AI-LLM-002)."""
        ...

    def generate_structured(self, request: StructuredRequest) -> ModelResponse:
        """Answer constrained to ``request.json_schema``.

        Must raise :class:`~factory_intelligence.llm.errors.LlmTransportError` for a
        timeout, 429/502/503/504 or a dropped connection (AI-RT-021) and
        :class:`~factory_intelligence.llm.errors.LlmError` for anything else.  A
        response that is merely *wrong* — malformed JSON, or JSON that fails the
        schema — is returned, not raised: the Schema-failure path belongs to the
        runtime's one corrective retry (AI-RT-011), not to the transport.
        """
        ...

    def count_tokens(self, text: str) -> int:
        """Estimate tokens for ``text``.

        Used for context fitting before the call (AI-POL-007's capability check), so
        an approximation that is stable across a run is acceptable; it may not vary
        between two calls on the same text, or the byte-budget error
        :class:`~factory_intelligence.errors.ContextTooLarge` would be nondeterministic.
        """
        ...

    def health(self) -> HealthStatus:
        """Cheap liveness probe; never a billable generation."""
        ...


__all__ = ["LLMProvider"]
