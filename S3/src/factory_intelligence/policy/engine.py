"""The policy decision itself (AI-POL-001, AI-POL-003, AI-POL-005).

``evaluate`` is the whole verdict: one pass over the items, one set membership per item,
and a refusal that names every offender.  It reads neither S0's artifacts nor S2's API —
that restriction is asserted by ``tests/unit/test_architecture.py`` and is the reason
AI-NFR-01's latency budget is met by structure rather than by tuning.

**No partial send.**  A denied item is never dropped so the rest may proceed: the SRS
calls that "静默截断" and forbids it (AI-POL-003, AI-BR-03), and the alternative would let
a document quietly lose the section that made it sensitive.
"""

from __future__ import annotations

from collections.abc import Sequence

from factory_foundation.api import DataClassification

from factory_intelligence.enums import ProviderClass
from factory_intelligence.policy.model import ContextItem, DeniedItem, EffectivePolicy, Verdict


def evaluate(
    items: Sequence[ContextItem],
    policy: EffectivePolicy,
    provider_class: ProviderClass | str,
) -> Verdict:
    """Judge a whole context against one provider class (AI-RT-006, AI-POL-003).

    A denial lists **every** offending item, not the first: a caller that fixed one
    artifact and re-submitted would otherwise meet a second refusal it had no way to
    anticipate, and AI-RT-017 asks the result to carry the rejected set in one round trip.
    """
    chosen = provider_class if isinstance(provider_class, ProviderClass) else ProviderClass(provider_class)
    denied: list[DeniedItem] = []
    for item in items:
        reason = policy.deny_reason(item.classification, chosen)
        if reason is None:
            continue
        denied.append(
            DeniedItem(
                ref=item.ref,
                classification=item.classification,
                reason=reason,
                allowed_classes=tuple(
                    sorted(policy.classes_for(item.classification), key=lambda member: member.value)
                ),
                label=item.label,
            )
        )
    return Verdict(
        allowed=not denied,
        denied=tuple(denied),
        provider_class=chosen,
        evaluated_items=len(items),
    )


def highest_classification(items: Sequence[ContextItem]) -> DataClassification:
    """The level a derived artifact must carry (AI-POL-006, S0's FD-ART-008 floor)."""
    if not items:
        return DataClassification.PUBLIC
    return DataClassification.max_of([item.classification for item in items])


__all__ = ["evaluate", "highest_classification"]
