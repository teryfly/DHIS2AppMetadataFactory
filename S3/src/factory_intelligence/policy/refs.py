"""Read-only policy judgement over references (SRS 7 ``evaluate_policy``).

Named ``refs`` rather than ``evaluate`` on purpose: the package re-exports the pure
:func:`factory_intelligence.policy.engine.evaluate`, and a submodule of the same name would
shadow it for every caller that imports it from the package — two objects answering to one
name, which is exactly what ``test_policy_purity`` forbids.

The public function exists so S1 (and a future S4 pre-flight) can ask "would this be
allowed" without starting a run.  It shares its whole vocabulary with the runtime: the same
ref grammar, the same :func:`~factory_intelligence.policy.engine.evaluate`, the same
effective-policy combination.  A second implementation of that question would be a second
answer, and the interesting failure of a policy engine is the one where the pre-check says
yes and the run says no.

Classification resolution is the only I/O here, and it is *metadata* only
(``factory_foundation.api.get_artifact`` reads ``core`` columns, not object storage), which
is what lets this function be called with S1's transaction open.  A reference whose content
would have to be read to be judged is refused rather than guessed at.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from factory_foundation.api import DataClassification, ValidationError
from sqlalchemy.orm import Session

from factory_intelligence.dto.request import ContextItemRef
from factory_intelligence.dto.result import DeniedItem, PolicyOutcome
from factory_intelligence.enums import PolicyResult as _PolicyResult
from factory_intelligence.enums import ProviderClass, as_provider_class
from factory_intelligence.policy.engine import evaluate
from factory_intelligence.policy.model import ContextItem
from factory_intelligence.policy.orgpolicy import effective_policy
from factory_intelligence.policy.resolution import resolve


def judge_refs(
    project_id: str,
    item_refs: Sequence[ContextItemRef | str],
    provider_class: ProviderClass | str,
    environment_policies: Any = None,
    *,
    session: Session | None = None,
) -> PolicyOutcome:
    """Judge references against the effective policy for one provider class."""
    try:
        chosen = as_provider_class(provider_class)
    except ValueError as exc:
        raise ValidationError(
            f"unknown provider class {provider_class!r}", code="VALIDATION_ERROR"
        ) from exc
    refs = [ref if isinstance(ref, ContextItemRef) else ContextItemRef(ref=str(ref)) for ref in item_refs]
    items = _resolve_refs(refs, project_id=project_id, session=session)
    policy = effective_policy(session=session, environment_policies=environment_policies)
    verdict = evaluate(items, policy, chosen)
    return PolicyOutcome(
        result=_RESULT_ALLOWED if verdict.allowed else _RESULT_DENIED,
        provider_class=chosen,
        model=None,
        effective_policy=policy.to_payload(),
        items=[
            {"ref": item.ref, "classification": item.classification.value} for item in items
        ],
        denied_items=[
            DeniedItem(
                ref=item.ref,
                classification=_as_level(item.classification),
                reason=item.reason,
                allowed_classes=[member.value for member in item.allowed_classes],
            )
            for item in verdict.denied
        ],
        warnings=list(policy.warnings),
    )


def _resolve_refs(
    refs: Sequence[ContextItemRef], *, project_id: str, session: Session | None
) -> list[ContextItem]:
    """An explicit classification short-circuits the read (AI-NFR-01)."""
    plain = [ref for ref in refs if ref.classification]
    needs_lookup = [ref for ref in refs if not ref.classification]
    items = [
        ContextItem(ref=ref.ref, classification=DataClassification(str(ref.classification)), label=ref.label or ref.ref)
        for ref in plain
    ]
    if needs_lookup:
        items.extend(resolve(needs_lookup, project_id=project_id, session=session))
    return items


def _as_level(value: DataClassification | str) -> str:
    return str(getattr(value, "value", value))


_RESULT_ALLOWED = _PolicyResult.ALLOWED
_RESULT_DENIED = _PolicyResult.DENIED


__all__ = ["judge_refs"]
