"""Local expression validation for DHIS2 expressions (BP-VAL-021, D-05).

A deterministic, offline parser for the three expression languages S2 must check before
any object leaves for a server: program rule conditions, program indicator expressions
and validation rule left/right expressions.  Syntax problems are reported, never
repaired (C-07); only a grammar S2 does not ship raises.
"""

from __future__ import annotations

from factory_blueprint.validators.expressions.grammar import (
    GRAMMAR_LANGUAGES,
    LANGUAGE_PROGRAM_INDICATOR,
    LANGUAGE_PROGRAM_RULE,
    LANGUAGE_VALIDATION_RULE,
    FunctionSpec,
    Grammar,
    LanguageGrammar,
    OperatorSpec,
    PrefixOperatorSpec,
    ReferenceKindSpec,
    TernarySpec,
    available_grammars,
    grammar_versions,
)
from factory_blueprint.validators.expressions.lexer import (
    LexError,
    LexResult,
    Reference,
    SegmentSpan,
    Token,
    tokenize,
)
from factory_blueprint.validators.expressions.parser import (
    MAX_PARSE_DEPTH,
    SYNTAX_ERROR_CODES,
    ExprNode,
    ParseError,
    ParseResult,
    ast_to_json,
    collect_references,
    parse,
    parse_with_grammar,
    reference_to_json,
)
from factory_blueprint.validators.expressions.resolve import (
    RESOLVE_ERROR_CODES,
    RegistryRow,
    RegistryTarget,
    ResolvedReference,
    ResolveIssue,
    ResolveResult,
    SubstitutionResult,
    registry_index,
    resolve,
    resolve_with_grammar,
    substitute,
    substitute_with_grammar,
)

#: the name the static-validation adapter (``validators/expressions_check.py``) calls;
#: ``parse`` is the canonical entry point and this is the same function.
parse_expression = parse

__all__ = [
    "GRAMMAR_LANGUAGES",
    "LANGUAGE_PROGRAM_INDICATOR",
    "LANGUAGE_PROGRAM_RULE",
    "LANGUAGE_VALIDATION_RULE",
    "MAX_PARSE_DEPTH",
    "RESOLVE_ERROR_CODES",
    "SYNTAX_ERROR_CODES",
    "ExprNode",
    "FunctionSpec",
    "Grammar",
    "LanguageGrammar",
    "LexError",
    "LexResult",
    "OperatorSpec",
    "ParseError",
    "ParseResult",
    "PrefixOperatorSpec",
    "Reference",
    "ReferenceKindSpec",
    "RegistryRow",
    "RegistryTarget",
    "ResolvedReference",
    "ResolveIssue",
    "ResolveResult",
    "SegmentSpan",
    "SubstitutionResult",
    "TernarySpec",
    "Token",
    "ast_to_json",
    "available_grammars",
    "collect_references",
    "grammar_versions",
    "parse",
    "parse_expression",
    "parse_with_grammar",
    "reference_to_json",
    "registry_index",
    "resolve",
    "resolve_with_grammar",
    "substitute",
    "substitute_with_grammar",
    "tokenize",
]
