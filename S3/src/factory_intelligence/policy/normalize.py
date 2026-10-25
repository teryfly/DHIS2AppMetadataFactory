"""Turning a caller's environment policy document into an :class:`EffectivePolicy`.

**This is the highest-leverage defensive code in S3.**  S1 forwards S4's
``data_export_policy`` verbatim as an untyped dict (``factory_control/services/steps.py``,
``factory_control/ports/dto.py``), and whatever S4 spells it as tomorrow is not something this
parser can be told about in advance.  Two rules make that safe in both directions:

1. A document is honoured only if it states which provider classes are allowed, either
   per classification level or as one set for all levels. A **ceiling** — ``llmDataPolicy``
   (S4's environment protection, DX-ENV-001) or ``maxProviderClass`` — is read as that class
   **plus every stricter one**, because 总体 SRS 20.2 defines the class by how far data may go:
   a wide ceiling must not deny a local model, and PHI_PII stays denied whatever the ceiling says.
2. Anything unrecognised, ambiguous or empty becomes the **strictest** non-degenerate
   policy, with a recorded warning.  Never the widest reading, and never a silent pass.

Rule 2 means a future S4 that ships a different spelling can only ever *narrow* what S3
sends until this parser learns it — a failure an operator sees as a denied run with a
warning, rather than as data that left the building.  Assuming the opposite would make a
schema change in another subsystem a silent privacy incident (AI-BR-03: the default
answer is no).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from factory_foundation.api import DataClassification

from factory_intelligence.enums import PROVIDER_CLASS_RANK, ProviderClass, as_provider_class
from factory_intelligence.policy.model import _LEVELS, EffectivePolicy

#: Keys that wrap a policy document one level down, as several systems spell it.
_WRAPPER_KEYS = ("dataExportPolicy", "data_export_policy", "policy", "exportPolicy")
#: Keys whose value is the per-level mapping.
_LEVEL_MAP_KEYS = (
    "allowedClassesByLevel",
    "allowed_classes_by_level",
    "byLevel",
    "by_level",
    "levels",
)
#: Keys whose value is one set of classes for every level.
_FLAT_KEYS = ("allowedProviderClasses", "allowed_provider_classes", "allowedClasses", "classes")
#: A single class name, the shape a UI is most likely to post.
_SINGLE_KEYS = ("providerClass", "class")
#: Keys that name a **ceiling**: the widest provider class this environment may send to, with every
#: stricter class allowed alongside it.  S4's environment protection answers ``llmDataPolicy``
#: (DX-ENV-001, 总体 SRS 20.2) and S1 forwards that document verbatim
#: (``factory_control/services/steps.py``), so without this branch a deliberate
#: ``APPROVED_EXTERNAL`` arrives here as unrecognised keys and collapses to the fail-safe — the
#: operator's setting doing nothing, and every stricter provider being denied with it.
_CEILING_KEYS = ("llmDataPolicy", "llm_data_policy", "maxProviderClass", "max_provider_class")


def _at_or_stricter_than(ceiling: ProviderClass) -> frozenset[ProviderClass]:
    """Every class that carries data no further out than ``ceiling`` (rank grows as it gets stricter)."""
    limit = PROVIDER_CLASS_RANK[ceiling]
    return frozenset(
        member for member in ProviderClass if PROVIDER_CLASS_RANK[member] >= limit
    )

_LEVEL_NAME_BY_VALUE = {level.value: level for level in _LEVELS}


def normalize_environment_policies(
    documents: Mapping[str, Any] | Iterable[tuple[str, Any]] | None,
) -> list[EffectivePolicy]:
    """One :class:`EffectivePolicy` per target environment (AI-POL-004).

    ``documents`` is S1's ``{environmentKey: policy}`` mapping; a sequence of pairs is
    accepted too because that is what a caller holding an ordered list has.
    """
    if documents is None:
        return []
    pairs: list[tuple[str, Any]]
    if isinstance(documents, Mapping):
        pairs = [(str(key), value) for key, value in documents.items()]
    else:
        pairs = [(str(key), value) for key, value in documents]
    return [normalize_one(value, scope=f"env:{key}") for key, value in pairs]


def normalize_one(document: Any, *, scope: str = "env") -> EffectivePolicy:
    """Parse one policy document, falling back to the fail-safe with a warning."""
    if document is None:
        return _fail_safe(scope, [f"{scope}: no data-export policy supplied"])
    unwrapped, note = _unwrap(document)
    if isinstance(unwrapped, str):
        unwrapped = {key: unwrapped for key in _SINGLE_KEYS}
    if not isinstance(unwrapped, Mapping):
        return _fail_safe(scope, [f"{scope}: policy is a {type(unwrapped).__name__}, not an object"])

    warnings: list[str] = [note] if note else []

    per_level = _extract_level_map(unwrapped)
    if per_level is not None:
        return _from_levels(per_level, scope=scope, warnings=warnings)

    for key in _FLAT_KEYS:
        value = unwrapped.get(key)
        if isinstance(value, (list, tuple, set)):
            classes = _classes(value)
            if not classes:
                return _fail_safe(scope, [*warnings, f"{scope}.{key}: no recognised class"])
            # One set for every level, with PHI_PII still excluded by construction: a
            # flat allow-list must not become a way to send patient data out (AI-POL-005).
            widened = {
                level: (frozenset() if level is DataClassification.PHI_PII else classes)
                for level in _LEVELS
            }
            warnings.append(f"{scope}.{key}: applied to every classification level")
            return EffectivePolicy(allowed=widened, sources=(scope,), warnings=tuple(warnings))

    for key in _CEILING_KEYS:
        value = unwrapped.get(key)
        if isinstance(value, str):
            try:
                ceiling = ProviderClass(value)
            except ValueError:
                return _fail_safe(
                    scope, [*warnings, f"{scope}.{key}: unknown provider class {value!r}"]
                )
            widest = _at_or_stricter_than(ceiling)
            widened = {
                level: (frozenset() if level is DataClassification.PHI_PII else widest)
                for level in _LEVELS
            }
            warnings.append(
                f"{scope}.{key}: {ceiling.value} read as a ceiling — stricter classes allowed, "
                "PHI_PII still denied"
            )
            return EffectivePolicy(allowed=widened, sources=(scope,), warnings=tuple(warnings))

    for key in _SINGLE_KEYS:
        value = unwrapped.get(key)
        if isinstance(value, str):
            try:
                chosen = ProviderClass(value)
            except ValueError:
                return _fail_safe(
                    scope, [*warnings, f"{scope}.{key}: unknown provider class {value!r}"]
                )
            single = frozenset({chosen})
            narrowed = {
                level: (frozenset() if level is DataClassification.PHI_PII else single)
                for level in _LEVELS
            }
            warnings.append(f"{scope}.{key}: one class applied to every classification level")
            return EffectivePolicy(allowed=narrowed, sources=(scope,), warnings=tuple(warnings))

    recognised = set(_LEVEL_NAME_BY_VALUE) | set(_WRAPPER_KEYS) | set(_LEVEL_MAP_KEYS)
    recognised |= set(_FLAT_KEYS) | set(_SINGLE_KEYS) | set(_CEILING_KEYS)
    unknown = sorted(str(key) for key in unwrapped if key not in recognised)
    detail = f"{scope}: unrecognised keys {unknown}" if unknown else f"{scope}: empty policy"
    return _fail_safe(scope, [*warnings, detail])


def _extract_level_map(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in _LEVEL_MAP_KEYS:
        value = document.get(key)
        if isinstance(value, Mapping):
            return value
    # the bare level-keyed form: {"INTERNAL": [...], "CONFIDENTIAL": [...]}
    level_keys = [key for key in document if str(key).upper() in _LEVEL_NAME_BY_VALUE]
    if level_keys:
        return {str(key).upper(): document[key] for key in level_keys}
    return None


def _from_levels(
    per_level: Mapping[str, Any], *, scope: str, warnings: list[str]
) -> EffectivePolicy:
    """A per-level document that omits a level denies it: partial means unsure, not open."""
    allowed: dict[DataClassification, frozenset[ProviderClass]] = {}
    for level in _LEVELS:
        value = per_level.get(level.value)
        if value is None:
            value = per_level.get(level.value.lower())
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            allowed[level] = frozenset()
            if level is not DataClassification.PHI_PII:
                warnings.append(f"{scope}.{level.value}: not stated, treated as no provider")
            continue
        allowed[level] = frozenset() if level is DataClassification.PHI_PII else _classes(value)
    return EffectivePolicy(allowed=allowed, sources=(scope,), warnings=tuple(warnings))


def _unwrap(document: Any) -> tuple[Any, str | None]:
    """Descend through a wrapper object, noting that we did."""
    current = document
    note: str | None = None
    for _ in range(3):
        if not isinstance(current, Mapping):
            break
        found = None
        for key in _WRAPPER_KEYS:
            if key in current:
                found = current[key]
                break
        if found is None:
            break
        note = f"{key}: descended one level"
        current = found
    return current, note


def _classes(values: Iterable[Any]) -> frozenset[ProviderClass]:
    out: list[ProviderClass] = []
    for value in values:
        try:
            out.append(as_provider_class(value))
        except ValueError:
            continue
    return frozenset(out)


def _fail_safe(scope: str, warnings: list[str]) -> EffectivePolicy:
    strictest = EffectivePolicy.strictest()
    return EffectivePolicy(
        allowed=strictest.allowed,
        sources=(scope,),
        warnings=tuple([*warnings, f"{scope}: falling back to LOCAL_ONLY (fail-safe)"]),
    )


def org_policy_from_payload(payload: Mapping[str, Any] | None) -> EffectivePolicy:
    """The stored organisation policy, or the SRS baseline when none is set (AI-POL-002)."""
    if not payload:
        return EffectivePolicy.organisation_default()
    parsed = normalize_one(payload, scope="org")
    if parsed.warnings:
        # an organisation document nobody understood must not quietly become open either
        return EffectivePolicy(
            allowed=parsed.allowed, sources=("org",), warnings=parsed.warnings
        )
    return EffectivePolicy(allowed=parsed.allowed, sources=("org",), warnings=())


__all__ = ["normalize_environment_policies", "normalize_one", "org_policy_from_payload"]
