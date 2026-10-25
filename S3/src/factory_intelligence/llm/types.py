"""Transport-neutral types for model calls (AI-LLM-001 … 004).

No vendor SDK type crosses this boundary — that is the whole point of AI-LLM-002:
an agent depends on these shapes and on the :class:`~factory_intelligence.llm.protocol.LLMProvider`
protocol, so adding a provider means adding one adapter file and nothing else
(AI-NFR-05).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from factory_intelligence.enums import ProviderClass

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ImageInput:
    """One image handed to a vision-capable model (AI-INP-002, image rows)."""

    media_type: str
    data_base64: str
    label: str = "image"


@dataclass(frozen=True)
class ChatMessage:
    """One turn of the constructed context.

    ``images`` is what makes this double as the vision input: a message may carry
    both the fenced text and the picture it describes, which is how a screenshot
    extraction keeps its provenance in one place instead of two calls.
    """

    role: Role
    content: str
    images: tuple[ImageInput, ...] = ()

    @property
    def has_images(self) -> bool:
        return bool(self.images)


@dataclass(frozen=True)
class Capabilities:
    """What a registered model can do (AI-LLM-002, AI-REG-004, AI-POL-007)."""

    structured_output: bool = False
    vision: bool = False
    max_context_tokens: int = 8000


@dataclass(frozen=True)
class ProviderInfo:
    """Identity of the endpoint behind a provider.

    ``api_key_secret_ref`` is a *reference* such as ``env://MODEL_API_KEY``, never the
    key: the adapter resolves it at request time and the value never reaches a DTO, a
    log line, an audit payload or an exception message (AI-LLM-005, 00 doc 6.9).  It is
    also what makes ``repr`` of this record safe to put in a log, which the ``__repr__``
    below exists to keep true.
    """

    provider: str
    provider_class: ProviderClass
    model: str
    capabilities: Capabilities
    source: str = "LIVE"
    endpoint: str | None = None
    api_key_secret_ref: str | None = None

    def __post_init__(self) -> None:
        # A credential that arrived where a reference was expected is a bug upstream, and
        # saying so here is cheaper than leaking it three calls later.
        value = self.api_key_secret_ref
        if value and "://" not in value:
            raise ValueError(
                "api_key_secret_ref must be a Secret reference like 'env://NAME', not a literal value"
            )


@dataclass(frozen=True)
class StructuredRequest:
    """A request whose answer must validate against ``json_schema``.

    A provider that cannot constrain decoding may still satisfy this by injecting the
    schema into the prompt and parsing the reply; the contract is that
    :meth:`LLMProvider.generate_structured` either returns parsed JSON or raises.

    ``agent_hint`` names which agent and stage issued the call.  A live provider ignores
    it — the answer must not depend on it — but the mock and record-replay providers key
    their scripts on it, which is how AI-MOCK-001 asks for "by agent type and stage"
    without those two tools reaching around the request for run context.
    """

    messages: tuple[ChatMessage, ...]
    json_schema: dict[str, Any]
    schema_name: str = "response"
    temperature: float = 0.0
    max_output_tokens: int | None = None
    agent_hint: AgentHint | None = None


@dataclass(frozen=True)
class AgentHint:
    """Which agent, at which stage, made this call (mock and replay keying only)."""

    agent_type: str
    stage: str
    run_id: str | None = None
    attempt: int = 1

    @property
    def key(self) -> str:
        return f"{self.agent_type}/{self.stage}"


@dataclass(frozen=True)
class Usage:
    """Token accounting for one call (AI-LLM-004, AI-COST-001)."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ModelResponse:
    """A decoded answer plus its accounting.

    ``raw_text`` exists for accounting and for the evaluation diff.  It is *never*
    persisted as a product: AI-RT-014 and AI-BR-08 forbid keeping the model's own
    working, and the only thing the runtime writes is the validated structured
    envelope plus a decision summary.
    """

    payload: dict[str, Any]
    raw_text: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    finish_reason: str | None = None
    request_id: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    detail: str | None = None


__all__ = [
    "Role",
    "AgentHint",
    "ImageInput",
    "ChatMessage",
    "Capabilities",
    "ProviderInfo",
    "StructuredRequest",
    "Usage",
    "ModelResponse",
    "HealthStatus",
]
