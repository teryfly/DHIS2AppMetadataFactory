"""Pure policy data structures (AI-POL-001, AI-POL-004).

Nothing in this module touches a database, an artifact or S2: :class:`EffectivePolicy`
is a mapping of five classification levels to sets of provider classes, and combining
policies is set intersection.  That is what makes AI-NFR-01 (P95 < 50 ms for a hundred
context items) a property of the data structure rather than of a tuned loop, and what
lets ``tests/perf/test_policy_latency.py`` and the import guard in
``tests/unit/test_architecture.py`` hold it there.

**Strictest-of is intersection, not a ranking.**  D-07 and AI-POL-004 ask for the most
restrictive of the organisation policy and every target environment's policy.  Computed
as ``org ∩ env₁ ∩ … ∩ envₙ`` per level that is commutative, idempotent and associative,
so "most restrictive" cannot drift from a hand-written ordering as classes are added;
``PROVIDER_CLASS_RANK`` survives only as a way to *describe* a difference.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import DataClassification

from factory_intelligence.enums import (
    POLICY_MATRIX,
    PROVIDER_CLASS_RANK,
    SYSTEM_DEFAULT_MATRIX,
    ItemSourceKind,
    ProviderClass,
    as_provider_class,
)

_LEVELS: tuple[DataClassification, ...] = (
    DataClassification.PUBLIC,
    DataClassification.INTERNAL,
    DataClassification.CONFIDENTIAL,
    DataClassification.RESTRICTED,
    DataClassification.PHI_PII,
)


@dataclass(frozen=True)
class ContextItem:
    """One piece of a prospective model context (AI-RT-006, AI-POL-006)."""

    ref: str
    classification: DataClassification
    kind: ItemSourceKind = ItemSourceKind.INPUT_ARTIFACT
    label: str = ""
    #: bytes this item contributes, for the budget check that must refuse rather than
    #: truncate (AI-POL-003)
    size_bytes: int = 0
    is_image: bool = False
    #: blueprint entries are attributed to their entry key, artifacts to their id
    entry_key: str | None = None


@dataclass(frozen=True)
class DeniedItem:
    """A context item that blocked this call, with the reason (AI-POL-003)."""

    ref: str
    classification: DataClassification
    reason: str
    allowed_classes: tuple[ProviderClass, ...] = ()
    label: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "classification": self.classification.value,
            "reason": self.reason,
            "allowedClasses": [member.value for member in self.allowed_classes],
            "label": self.label,
        }


@dataclass(frozen=True)
class EffectivePolicy:
    """What may be sent where, for this project, right now.

    ``allowed`` always carries all five levels; a level missing from a source policy is
    treated as sendable nowhere, never as unrestricted — the default has to be deny for
    AI-BR-03 to hold when a caller passes a partial document.
    """

    allowed: Mapping[DataClassification, frozenset[ProviderClass]] = field(
        default_factory=lambda: dict(SYSTEM_DEFAULT_MATRIX)
    )
    #: which policies were combined, for the audit record ("org", "env:prod-1", …)
    sources: tuple[str, ...] = ()
    #: normalisation notes, reported rather than silently applied (AI-POL-004)
    warnings: tuple[str, ...] = ()

    @classmethod
    def organisation_default(cls) -> EffectivePolicy:
        """The starting policy: AI-POL-002's default, which denies both external classes.

        Not :data:`POLICY_MATRIX`.  The matrix is the ceiling an organisation may widen
        *to*; using it as the default would leave nothing to relax (AI-POL-002 requires the
        default to deny external providers), and a relaxation that cannot change behaviour
        is an audit trail for a decision nobody made.
        """
        return cls(allowed=dict(SYSTEM_DEFAULT_MATRIX), sources=("org",))

    @classmethod
    def ceiling(cls) -> EffectivePolicy:
        """The outermost policy an organisation may widen *to* (:data:`POLICY_MATRIX`).

        Read alongside :meth:`organisation_default`, never instead of it: the two are the two
        halves AI-POL-002 keeps apart, and a caller that can see only the first cannot tell a
        legal relaxation from one that will be refused.  Returning it as a policy object is what
        lets S1 hand the ceiling to a form in the same shape as the effective matrix, so the two
        can be subtracted without either side inventing a wire spelling.
        """
        return cls(allowed=dict(POLICY_MATRIX), sources=("ceiling",))

    @classmethod
    def strictest(cls) -> EffectivePolicy:
        """LOCAL_ONLY for everything, PHI_PII nowhere.  The fail-safe for an
        unrecognised environment policy — see :func:`normalize`."""
        narrow = {
            level: (
                frozenset({ProviderClass.LOCAL_ONLY})
                if level is not DataClassification.PHI_PII
                else frozenset()
            )
            for level in _LEVELS
        }
        return cls(allowed=narrow, sources=("fail-safe",))

    @classmethod
    def combine(cls, policies: Iterable[EffectivePolicy]) -> EffectivePolicy:
        """Pointwise intersection of any number of policies (AI-BR-04)."""
        items = list(policies)
        if not items:
            return cls.organisation_default()
        merged: dict[DataClassification, frozenset[ProviderClass]] = {
            level: frozenset(ProviderClass) for level in _LEVELS
        }
        for level in _LEVELS:
            acc: frozenset[ProviderClass] | None = None
            for policy in items:
                current = policy.classes_for(level)
                acc = current if acc is None else (acc & current)
            merged[level] = acc or frozenset()
        sources: list[str] = []
        warnings: list[str] = []
        for policy in items:
            sources.extend(source for source in policy.sources if source not in sources)
            warnings.extend(policy.warnings)
        return cls(allowed=merged, sources=tuple(sources), warnings=tuple(warnings))

    def classes_for(self, level: DataClassification) -> frozenset[ProviderClass]:
        return self.allowed.get(level, frozenset())

    def allows(self, level: DataClassification, provider_class: ProviderClass) -> bool:
        return provider_class in self.classes_for(level)

    def deny_reason(
        self, level: DataClassification, provider_class: ProviderClass
    ) -> str | None:
        """Why this pairing is refused, naming what *is* allowed (AI-RT-017's denial list)."""
        if self.allows(level, provider_class):
            return None
        if level is DataClassification.PHI_PII:
            return "PHI_PII may never be sent to any model, under any policy (AC-POL-02)"
        allowed = self.classes_for(level)
        if not allowed:
            return f"{level.value} may not be sent to any provider under the effective policy"
        names = ", ".join(sorted(member.value for member in allowed))
        return f"{level.value} may not go to {provider_class.value}; allowed: {names}"

    def narrower_than(self, other: EffectivePolicy) -> bool:
        """Reporting helper: this policy permits a strict subset of ``other``."""
        return all(
            self.classes_for(level) <= other.classes_for(level) for level in _LEVELS
        ) and any(self.classes_for(level) < other.classes_for(level) for level in _LEVELS)

    def rank_of(self, provider_class: ProviderClass) -> int:
        return PROVIDER_CLASS_RANK[provider_class]

    def to_payload(self) -> dict[str, Any]:
        """The form stored in ``ai.policy_decision.effective_policy`` (AI-POL-008)."""
        return {
            "allowed": {
                level.value: sorted(member.value for member in self.classes_for(level))
                for level in _LEVELS
            },
            "sources": list(self.sources),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> EffectivePolicy:
        """Read back :meth:`to_payload`.  Unknown class names are dropped, not trusted.

        ``PHI_PII`` is forced empty on the way in rather than read like the other four
        levels.  A stored row is data that a migration, a bug or a hand-edited database
        could have set to anything, and the one rule AC-POL-02 states without exception is
        that PHI_PII never reaches a model — so a policy reconstructed from a row must be
        incapable of permitting it, even if the row says otherwise.
        """
        raw = payload.get("allowed") or {}
        allowed: dict[DataClassification, frozenset[ProviderClass]] = {}
        for level in _LEVELS:
            names = raw.get(level.value) or []
            allowed[level] = (
                frozenset()
                if level is DataClassification.PHI_PII
                else _known_classes(names)
            )
        return cls(
            allowed=allowed,
            sources=tuple(str(item) for item in (payload.get("sources") or ())),
            warnings=tuple(str(item) for item in (payload.get("warnings") or ())),
        )


def _known_classes(names: Iterable[object]) -> frozenset[ProviderClass]:
    out: list[ProviderClass] = []
    for name in names:
        try:
            out.append(as_provider_class(name))
        except ValueError:
            continue
    return frozenset(out)


@dataclass(frozen=True)
class Verdict:
    """The engine's answer: send, or refuse and say what and why."""

    allowed: bool
    denied: tuple[DeniedItem, ...] = ()
    provider_class: ProviderClass | None = None
    evaluated_items: int = 0

    @property
    def denied_payload(self) -> list[dict[str, Any]]:
        return [item.as_payload() for item in self.denied]


__all__ = ["ContextItem", "DeniedItem", "EffectivePolicy", "Verdict", "_LEVELS"]
