"""Reference resolution: businessKey to UID (BP-CMP-028, FR-CMP-010).

Blueprint content always speaks businessKey; the compiled payload speaks UID.
Substitution walks the reference syntax instead of doing a substring replace, so a
short key can never corrupt a longer one that contains it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: DHIS2 expression reference forms S2 has to honour.  The opener of a ``#{}``
#: reference is optional: a rule that starts with one, or one that follows a space or
#: a comma, is the common case and must not be skipped.
REFERENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?P<prefix>A#\{|V#\{|C#\{|DV#\{|RV#\{|iR#\{|D2:|d2:)?#\{(?P<key>[^{}]+)\}"),
    re.compile(r"!\{(?P<curly>[^{}]+)\}"),
    re.compile(r"!\[(?P<key2>[^\]]+)\]"),
    re.compile(r"\[(?P<key3>[^\]\s]+)\]"),
)

UID_PLACEHOLDER = re.compile(r"\{\{ref:(?P<key>[^{}]+)\}\}")


@dataclass
class Substitution:
    """The rewritten text plus every reference it contained."""

    text: str
    references: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def placeholder(business_key: str) -> str:
    """The baseline-compile stand-in for an environment-dependent reference."""
    return f"{{{{ref:{business_key}}}}}"


def is_placeholder(value: str) -> bool:
    return bool(UID_PLACEHOLDER.fullmatch(value))


def key_of_placeholder(value: str) -> str | None:
    match = UID_PLACEHOLDER.fullmatch(value)
    return match.group("key") if match else None


def reference_keys(text: str) -> list[str]:
    """Every businessKey referenced by an expression, in order of appearance."""
    keys: list[str] = []
    for pattern in REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            for raw in _matched_keys(match):
                key = _base_key(raw)
                if key and key not in keys:
                    keys.append(key)
    for match in UID_PLACEHOLDER.finditer(text):
        key = _base_key(match.group("key"))
        if key not in keys:
            keys.append(key)
    return keys


def _matched_keys(match: re.Match[str]) -> list[str]:
    return [value for value in match.groupdict().values() if isinstance(value, str)]


def _base_key(raw: str) -> str:
    """``weight.coc`` addresses one object; the qualifier after the dot stays."""
    return raw.split(".", 1)[0].strip()


def substitute_references(text: str, bindings: Mapping[str, str]) -> Substitution:
    """Replace each known reference with its UID, keeping the surrounding syntax.

    An unknown key is left exactly as it was and reported, so a reference S2 cannot
    resolve is a problem a caller sees rather than a string that ships (BP-CMP-028).
    """
    references: list[str] = []
    unresolved: list[str] = []

    def replace(match: re.Match[str]) -> str:
        found: str | None = None
        for raw in _matched_keys(match):
            base = _base_key(raw)
            if base in bindings:
                found = raw
                break
        if found is None:
            for raw in _matched_keys(match):
                base = _base_key(raw)
                if base and base not in unresolved:
                    unresolved.append(base)
            return match.group(0)
        base = _base_key(found)
        qualifier = found[len(base) :]
        uid = bindings[base]
        if base not in references:
            references.append(base)
        return match.group(0).replace(found, f"{uid}{qualifier}", 1)

    result = text
    for pattern in REFERENCE_PATTERNS:
        result = pattern.sub(replace, result)
    return Substitution(text=result, references=sorted(references), unresolved=sorted(unresolved))


def substitute_value(
    value: Any, bindings: Mapping[str, str], *, environment_bound: bool
) -> tuple[Any, list[str]]:
    """Recursively resolve references inside a payload value.

    Reference objects ``{"id": "<businessKey>"}`` become ``{"id": "<uid>"}``.
    """
    touched: list[str] = []
    if isinstance(value, dict):
        if set(value) == {"id"} and isinstance(value["id"], str):
            key = value["id"]
            uid = bindings.get(key) or (placeholder(key) if not environment_bound else key)
            if key in bindings or not environment_bound:
                touched.append(key)
            return {"id": uid}, touched
        out: dict[str, Any] = {}
        for key, item in value.items():
            resolved, nested = substitute_value(item, bindings, environment_bound=environment_bound)
            out[key] = resolved
            touched.extend(nested)
        return out, touched
    if isinstance(value, list):
        collected: list[Any] = []
        for item in value:
            resolved, nested = substitute_value(item, bindings, environment_bound=environment_bound)
            collected.append(resolved)
            touched.extend(nested)
        return collected, touched
    if isinstance(value, str) and REFERENCE_PATTERNS[0].search(value):
        substitution = substitute_references(value, bindings)
        touched.extend(substitution.references)
        return substitution.text, touched
    return value, touched


def classify_expression(
    references: Iterable[str], existing_in_environment: Iterable[str]
) -> str:
    """DHIS2 can only check an expression whose objects are all already there."""
    present = set(existing_in_environment)
    missing = [key for key in references if key not in present]
    return "DHIS2_VALIDATE" if not missing else "POST_VERIFY"
