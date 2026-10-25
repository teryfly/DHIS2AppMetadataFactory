"""Adapter in front of the local expression parser.

Static validation needs one narrow question — "is this expression well formed and
are its references resolvable?" — and the parser answers it.  Keeping the call in
one file means the parser's own API can settle without touching the validators.
"""

from __future__ import annotations

LANGUAGE_PROGRAM_RULE_CONDITION = "programRuleCondition"
LANGUAGE_PROGRAM_INDICATOR = "programIndicator"
LANGUAGE_VALIDATION_RULE = "validationRule"
#: a rule's ``expression`` field does not say which language it is written in, so
# both grammars are tried and an error is reported only when neither accepts it
LANGUAGE_PROGRAM_INDICATOR_OR_RULE = "programRuleConditionOrIndicator"

_LANGUAGES = (
    LANGUAGE_PROGRAM_RULE_CONDITION,
    LANGUAGE_PROGRAM_INDICATOR,
    LANGUAGE_VALIDATION_RULE,
)


def available_grammars() -> list[str]:
    try:
        from factory_blueprint.validators.expressions import available_grammars as inner

        return list(inner())
    except Exception:  # pragma: no cover - reported as a blocking problem instead
        return []


def check(text: str, *, language: str, grammar_version: str | None) -> list[str]:
    """Syntax and reference errors of one compiled expression."""
    if not text.strip():
        return ["the expression is empty"]
    try:
        from factory_blueprint.validators.expressions import parse_expression
    except Exception as error:  # the parser is part of S2, so its absence is a defect
        return [f"the local expression parser is unavailable: {error}"]
    if grammar_version is None:
        return ["the Capability Profile names no expression grammar version (P-10)"]
    candidates = _languages_for(language)
    errors: list[str] = []
    for candidate in candidates:
        result = parse_expression(text, language=candidate, grammar_version=grammar_version)
        if result.ok:
            return []
        errors.extend(f"{candidate}: {error}" for error in result.messages)
    return errors


def _languages_for(language: str) -> list[str]:
    if language == LANGUAGE_PROGRAM_INDICATOR_OR_RULE:
        return [LANGUAGE_PROGRAM_RULE_CONDITION, LANGUAGE_PROGRAM_INDICATOR]
    if language in _LANGUAGES:
        return [language]
    return list(_LANGUAGES)


def references(text: str) -> list[str]:
    from factory_blueprint.compilers import refs

    return refs.reference_keys(text)
