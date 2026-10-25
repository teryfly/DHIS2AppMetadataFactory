"""Provider and model selection (AI-POL-007, AC-AG-01).

The agent's ordered model list is walked from the front, and the winner is the first
model that satisfies *both* halves of AI-POL-007: the effective policy permits the whole
context to go to that model's provider class, and its declared capabilities cover the
request (structured output, vision when an image is present, a context window that fits).

Every rejection is kept, with its reason, because AC-AG-01 asks for "每个模型的拒绝原因"
to be visible: "no model available" alone cannot be acted on, and the operator's next
move is either to relax a policy or to register a bigger-context model.

Pure by construction — :class:`CandidateModel` is a plain record the registry layer
builds — so the whole selection matrix is testable without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from factory_intelligence.enums import ProviderClass
from factory_intelligence.policy.engine import evaluate
from factory_intelligence.policy.model import (
    ContextItem,
    DeniedItem,
    EffectivePolicy,
    Verdict,
)


@dataclass(frozen=True)
class CandidateModel:
    """One entry of an agent's preference list, as the Model Registry describes it."""

    model: str
    provider: str
    provider_class: ProviderClass
    structured_output: bool = True
    vision: bool = False
    max_context_tokens: int = 8000
    enabled: bool = True
    #: position in the agent's preference list; lower is tried first (AI-POL-007)
    priority: int = 100
    #: estimated tokens of the constructed context, supplied by the caller
    context_tokens: int = 0

    def fits(self, needed_tokens: int, *, needs_vision: bool, needs_structured: bool) -> str | None:
        """Why this model cannot serve the request, or ``None`` when it can."""
        if not self.enabled:
            return "provider or model is disabled"
        if needs_structured and not self.structured_output:
            return "no structured-output capability"
        if needs_vision and not self.vision:
            return "no vision capability for an image input"
        if needed_tokens > self.max_context_tokens:
            return f"context of {needed_tokens} tokens exceeds its {self.max_context_tokens} window"
        return None


@dataclass(frozen=True)
class ModelDenial:
    model: str
    provider: str
    provider_class: str
    reason: str

    def as_payload(self) -> dict[str, str]:
        return {
            "model": self.model,
            "provider": self.provider,
            "providerClass": self.provider_class,
            "reason": self.reason,
        }

    def as_line(self) -> str:
        """The form :attr:`AgentRunResult.denial_reasons` carries."""
        return f"{self.provider}/{self.model}: {self.reason}"


@dataclass(frozen=True)
class Selection:
    chosen: CandidateModel | None
    denials: tuple[ModelDenial, ...] = ()
    #: the verdict for the chosen model, kept so the caller can persist it verbatim
    verdict: Verdict | None = None
    #: items no candidate could take.  On a total refusal there is no chosen model and so no
    #: verdict, and AI-POL-003 still requires each refused item to be *named* rather than
    #: folded into a sentence — the reason a caller may not send its data is a reportable fact.
    refused: tuple[DeniedItem, ...] = ()

    @property
    def available(self) -> bool:
        return self.chosen is not None

    @property
    def denied_items(self) -> tuple[DeniedItem, ...]:
        """What was refused, whichever way the selection ended."""
        if self.verdict is not None:
            return self.verdict.denied
        return self.refused

    @property
    def reasons(self) -> list[str]:
        return [denial.as_line() for denial in self.denials]

    @property
    def payloads(self) -> list[dict[str, str]]:
        return [denial.as_payload() for denial in self.denials]


def select(
    items: Sequence[ContextItem],
    policy: EffectivePolicy,
    candidates: Sequence[CandidateModel],
    *,
    needed_tokens: int = 0,
    needs_vision: bool | None = None,
    needs_structured_output: bool = True,
) -> Selection:
    """Pick the first model that policy and capability both allow (AI-POL-007)."""
    ordered = sorted(candidates, key=lambda candidate: (candidate.priority, candidate.model))
    wants_vision = (
        any(item.is_image for item in items) if needs_vision is None else needs_vision
    )
    denials: list[ModelDenial] = []
    refused: list[DeniedItem] = []
    for candidate in ordered:
        capability_problem = candidate.fits(
            needed_tokens,
            needs_vision=wants_vision,
            needs_structured=needs_structured_output,
        )
        if capability_problem:
            denials.append(
                ModelDenial(
                    model=candidate.model,
                    provider=candidate.provider,
                    provider_class=candidate.provider_class.value,
                    reason=capability_problem,
                )
            )
            continue
        verdict = evaluate(items, policy, candidate.provider_class)
        if not verdict.allowed:
            # AI-POL-003: the whole context is refused for this class, and the reason
            # names the levels and classes involved rather than the first artifact only.
            first = verdict.denied[0] if verdict.denied else None
            reason = first.reason if first else "policy denied"
            if len(verdict.denied) > 1:
                reason = f"{reason}; {len(verdict.denied) - 1} more item(s)"
            denials.append(
                ModelDenial(
                    model=candidate.model,
                    provider=candidate.provider,
                    provider_class=candidate.provider_class.value,
                    reason=reason,
                )
            )
            for item in verdict.denied:
                if not any(seen.ref == item.ref for seen in refused):
                    refused.append(item)
            continue
        return Selection(chosen=candidate, denials=tuple(denials), verdict=verdict)
    return Selection(chosen=None, denials=tuple(denials), refused=tuple(refused))


__all__ = ["CandidateModel", "ModelDenial", "Selection", "select"]
