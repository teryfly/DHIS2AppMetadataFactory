"""Versioned expression grammars (BP-VAL-021, D-05).

The parser holds no DHIS2 vocabulary of its own: operators, precedences, reference
openers, function arities, keywords and built-in variable names all come from a grammar
data file shipped with the package and selected by Capability Profile version.  Adding a
DHIS2 version therefore means adding a JSON file, never touching the parser
(NFR-MNT-001).  Loading is read-only and cached, so a grammar is a pure value and the
same version always yields the identical object graph (BP-CMP-001).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from factory_foundation.api import FactoryError

from factory_blueprint.errors import ExpressionGrammarMissing

GRAMMAR_ROOT: Final[Path] = Path(__file__).resolve().parent / "grammars"
GRAMMAR_SUFFIX: Final[str] = ".json"

ASSOCIATION_LEFT: Final[str] = "left"
ASSOCIATION_RIGHT: Final[str] = "right"
ASSOCIATIONS: Final[frozenset[str]] = frozenset({ASSOCIATION_LEFT, ASSOCIATION_RIGHT})
RIGHT_EXPRESSION: Final[str] = "expression"
RIGHT_LIST: Final[str] = "list"
KEY_REGISTRY: Final[str] = "registry"
KEY_VARIABLE_OR_REGISTRY: Final[str] = "variableOrRegistry"
KEY_FUNCTION: Final[str] = "function"
KEY_SOURCES: Final[frozenset[str]] = frozenset(
    {KEY_REGISTRY, KEY_VARIABLE_OR_REGISTRY, KEY_FUNCTION}
)
FEATURE_TERNARY: Final[str] = "ternary"
FEATURE_LIST: Final[str] = "listLiteral"

ROLE_LPAREN: Final[str] = "lparen"
ROLE_RPAREN: Final[str] = "rparen"
ROLE_LBRACKET: Final[str] = "lbracket"
ROLE_RBRACKET: Final[str] = "rbracket"
ROLE_COMMA: Final[str] = "comma"
PUNCTUATION_ROLES: Final[tuple[str, ...]] = (
    ROLE_LPAREN,
    ROLE_RPAREN,
    ROLE_LBRACKET,
    ROLE_RBRACKET,
    ROLE_COMMA,
)

LANGUAGE_PROGRAM_RULE: Final[str] = "programRule"
LANGUAGE_PROGRAM_INDICATOR: Final[str] = "programIndicator"
LANGUAGE_VALIDATION_RULE: Final[str] = "validationRule"
GRAMMAR_LANGUAGES: Final[tuple[str, ...]] = (
    LANGUAGE_PROGRAM_INDICATOR,
    LANGUAGE_PROGRAM_RULE,
    LANGUAGE_VALIDATION_RULE,
)


@dataclass(frozen=True)
class OperatorSpec:
    """One infix operator with its binding power."""

    symbol: str
    precedence: int
    associativity: str
    word: bool
    right_operand: str


@dataclass(frozen=True)
class PrefixOperatorSpec:
    symbol: str
    precedence: int
    word: bool


@dataclass(frozen=True)
class TernarySpec:
    condition_symbol: str
    alternative_symbol: str
    precedence: int
    associativity: str


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    min_args: int
    max_args: int | None
    case_insensitive: bool
    qualified: bool

    def accepts(self, count: int) -> bool:
        return self.min_args <= count and (self.max_args is None or count <= self.max_args)

    def expectation(self) -> str:
        if self.max_args is None:
            return f"at least {self.min_args}"
        if self.min_args == self.max_args:
            return str(self.min_args)
        return f"between {self.min_args} and {self.max_args}"


@dataclass(frozen=True)
class ReferenceKindSpec:
    """A reference token: how it opens, how it closes and what it may point at."""

    name: str
    openers: tuple[str, ...]
    closer: str | None
    allows_dots: bool
    key_source: str
    allowed_types: tuple[str, ...]

    @property
    def is_function_prefix(self) -> bool:
        return self.key_source == KEY_FUNCTION


@dataclass(frozen=True)
class LiteralSyntax:
    quotes: tuple[str, ...]
    escape: str
    escapes: Mapping[str, str]


@dataclass(frozen=True)
class LanguageGrammar:
    """Everything the lexer and parser need for exactly one expression language."""

    name: str
    operators: Mapping[str, OperatorSpec]
    prefix_operators: Mapping[str, PrefixOperatorSpec]
    reference_kinds: Mapping[str, ReferenceKindSpec]
    functions: Mapping[str, FunctionSpec]
    qualified_functions: Mapping[str, FunctionSpec]
    keywords: frozenset[str]
    variables: frozenset[str]
    features: frozenset[str]
    ternary: TernarySpec

    def operator(self, symbol: str) -> OperatorSpec | None:
        return self.operators.get(symbol)

    def prefix_operator(self, symbol: str) -> PrefixOperatorSpec | None:
        return self.prefix_operators.get(symbol)

    def reference_kind(self, name: str) -> ReferenceKindSpec | None:
        return self.reference_kinds.get(name)

    def function(self, name: str) -> FunctionSpec | None:
        spec = self.functions.get(name)
        if spec is not None:
            return spec
        for candidate in self.functions.values():
            if candidate.case_insensitive and candidate.name.lower() == name.lower():
                return candidate
        return None

    def qualified_function(self, prefix_kind: str, name: str) -> FunctionSpec | None:
        spec = self.qualified_functions.get(f"{prefix_kind}/{name}")
        if spec is not None:
            return spec
        for key, candidate in self.qualified_functions.items():
            head, _, tail = key.partition("/")
            if (
                head == prefix_kind
                and candidate.case_insensitive
                and tail.lower() == name.lower()
            ):
                return candidate
        return None

    def allows(self, feature: str) -> bool:
        return feature in self.features


@dataclass(frozen=True)
class Grammar:
    """A complete, immutable grammar for one range of DHIS2 versions."""

    name: str
    aliases: tuple[str, ...]
    dhis2_versions: tuple[str, ...]
    max_depth: int
    literal: LiteralSyntax
    punctuation: Mapping[str, str]
    number_pattern: re.Pattern[str]
    identifier_pattern: re.Pattern[str]
    symbols: tuple[str, ...]
    infix_symbols: frozenset[str]
    prefix_symbols: frozenset[str]
    reference_kinds: Mapping[str, ReferenceKindSpec]
    openers: tuple[tuple[str, str], ...]
    languages: Mapping[str, LanguageGrammar]
    language_aliases: Mapping[str, str]

    @classmethod
    def load(cls, version: str) -> Grammar:
        """Load the grammar registered under ``version`` (name or alias)."""
        path = _grammar_paths().get(version)
        if path is None:
            raise ExpressionGrammarMissing(
                f"no expression grammar is registered for {version!r}; "
                f"available grammars: {', '.join(available_grammars())}"
            )
        return _load_grammar(path)

    def language(self, language: str) -> LanguageGrammar:
        """The language a caller asked for, following any alias the grammar declares."""
        spec = self.languages.get(language)
        if spec is not None:
            return spec
        alias = self.language_aliases.get(language)
        if alias is not None:
            resolved = self.languages.get(alias)
            if resolved is not None:
                return resolved
        raise ExpressionGrammarMissing(
            f"expression grammar {self.name!r} does not define the language "
            f"{language!r}; it defines: {', '.join(sorted(self.languages))}"
        )

    def defines(self, language: str) -> bool:
        return language in self.languages or language in self.language_aliases

    def reference_kind(self, name: str) -> ReferenceKindSpec | None:
        return self.reference_kinds.get(name)

    def punctuation_kind(self, character: str) -> str | None:
        for role, symbol in self.punctuation.items():
            if symbol == character:
                return role
        return None


def available_grammars() -> tuple[str, ...]:
    """Canonical names of the grammars shipped with this build, sorted."""
    return tuple(sorted(_grammar_names()))


def grammar_versions() -> tuple[str, ...]:
    """Every name and alias a shipped grammar answers to, sorted."""
    return tuple(sorted(_grammar_paths()))


@lru_cache(maxsize=8)
def _grammar_files() -> tuple[Path, ...]:
    if not GRAMMAR_ROOT.is_dir():
        return ()
    return tuple(sorted(path for path in GRAMMAR_ROOT.iterdir() if path.suffix == GRAMMAR_SUFFIX))


@lru_cache(maxsize=8)
def _grammar_names() -> Mapping[str, Path]:
    index: dict[str, Path] = {}
    for path in _grammar_files():
        index[_read_name(path)] = path
    return index


@lru_cache(maxsize=8)
def _grammar_paths() -> Mapping[str, Path]:
    """Every name and alias a shipped grammar answers to."""
    index: dict[str, Path] = {}
    for path in _grammar_files():
        raw = _read_document(path)
        name = _text(raw.get("name"), "name", path)
        identifiers = [name, *[str(alias) for alias in _list(raw.get("aliases"), path)]]
        for identifier in identifiers:
            owner = index.get(identifier)
            if owner is not None and owner != path:
                raise FactoryError(
                    f"expression grammar identifier {identifier!r} is claimed by both "
                    f"{owner.name} and {path.name}",
                    code="SCHEMA_ERROR",
                )
            index[identifier] = path
    if not index:
        raise ExpressionGrammarMissing(
            f"no expression grammar file is shipped in {GRAMMAR_ROOT}"
        )
    return index


def _read_name(path: Path) -> str:
    return _text(_read_document(path).get("name"), "name", path)


@lru_cache(maxsize=16)
def _load_grammar(path: Path) -> Grammar:
    raw = _read_document(path)
    name = _text(raw.get("name"), "name", path)
    aliases = tuple(str(alias) for alias in _list(raw.get("aliases"), path))
    dhis2_versions = tuple(str(item) for item in _list(raw.get("dhis2Versions"), path))
    max_depth = _integer(raw.get("maxDepth"), "maxDepth", path)
    literal = _literal_syntax(raw.get("stringLiteral"), path)
    punctuation = _punctuation(raw.get("punctuation"), path)
    patterns = _patterns(raw.get("tokenPatterns"), path)
    ternary = _ternary(raw.get("ternary"), path)
    operator_items = _list(raw.get("operators"), path)
    prefix_items = _list(raw.get("prefixOperators"), path)
    kind_items = _list(raw.get("referenceKinds"), path)
    function_items = _list(raw.get("functions"), path)
    qualified_items = _list(raw.get("qualifiedFunctions"), path)
    operators = {spec.symbol: spec for spec in (_operator(item, path) for item in operator_items)}
    prefix_operators = {
        spec.symbol: spec for spec in (_prefix_operator(item, path) for item in prefix_items)
    }
    reference_kinds = {
        spec.name: spec for spec in (_reference_kind(item, path) for item in kind_items)
    }
    functions = {spec.name: spec for spec in (_function(item, path) for item in function_items)}
    qualified = {
        spec.name: spec for spec in (_qualified_function(item, path) for item in qualified_items)
    }
    languages_section = _mapping(raw.get("languages"), "languages", path)
    if not languages_section:
        raise FactoryError(
            f"expression grammar {path.name} defines no language", code="SCHEMA_ERROR"
        )
    languages = {
        language: _language(
            language,
            _mapping(body, f"languages.{language}", path),
            operators,
            prefix_operators,
            reference_kinds,
            functions,
            qualified,
            ternary,
            path,
        )
        for language, body in languages_section.items()
    }
    tables = _symbol_tables(operators, prefix_operators, ternary)
    alias_section = _mapping(raw.get("languageAliases", {}), "languageAliases", path)
    aliases_by_language = {
        str(alias): _text(target, f"languageAliases.{alias}", path)
        for alias, target in alias_section.items()
    }
    for alias, target in aliases_by_language.items():
        if target not in languages:
            raise FactoryError(
                f"expression grammar {path.name}: alias {alias!r} points at the "
                f"undefined language {target!r}",
                code="SCHEMA_ERROR",
            )
    return Grammar(
        name=name,
        aliases=aliases,
        dhis2_versions=dhis2_versions,
        max_depth=max_depth,
        literal=literal,
        punctuation=punctuation,
        number_pattern=patterns[0],
        identifier_pattern=patterns[1],
        symbols=tables[0],
        infix_symbols=tables[1],
        prefix_symbols=tables[2],
        reference_kinds=reference_kinds,
        openers=_opener_table(reference_kinds),
        languages=languages,
        language_aliases=aliases_by_language,
    )


def _symbol_tables(
    operators: Mapping[str, OperatorSpec],
    prefix_operators: Mapping[str, PrefixOperatorSpec],
    ternary: TernarySpec,
) -> tuple[tuple[str, ...], frozenset[str], frozenset[str]]:
    """Grammar-wide token table: an operator any language knows still lexes here, so the
    parser can report "not part of this language" instead of the lexer claiming the text
    is not a token at all.  Word operators are never scanned as symbols, because a word
    is read by the identifier pattern."""
    symbolic: set[str] = {symbol for symbol, spec in operators.items() if not spec.word}
    symbolic |= {symbol for symbol, spec in prefix_operators.items() if not spec.word}
    symbolic |= {ternary.condition_symbol, ternary.alternative_symbol}
    ordered = tuple(sorted(symbolic, key=lambda symbol: (-len(symbol), symbol)))
    return ordered, frozenset(operators), frozenset(prefix_operators)


def _language(
    name: str,
    body: Mapping[str, Any],
    operators: Mapping[str, OperatorSpec],
    prefix_operators: Mapping[str, PrefixOperatorSpec],
    reference_kinds: Mapping[str, ReferenceKindSpec],
    functions: Mapping[str, FunctionSpec],
    qualified: Mapping[str, FunctionSpec],
    ternary: TernarySpec,
    path: Path,
) -> LanguageGrammar:
    language_operators: dict[str, OperatorSpec] = {}
    for symbol in (str(item) for item in _list(body.get("operators"), path)):
        operator_spec = operators.get(symbol)
        if operator_spec is None:
            raise FactoryError(
                f"expression grammar {path.name}: language {name} uses undeclared "
                f"operator {symbol!r}",
                code="SCHEMA_ERROR",
            )
        language_operators[symbol] = operator_spec
    language_prefix: dict[str, PrefixOperatorSpec] = {}
    for symbol in (str(item) for item in _list(body.get("prefixOperators"), path)):
        prefix_spec = prefix_operators.get(symbol)
        if prefix_spec is None:
            raise FactoryError(
                f"expression grammar {path.name}: language {name} uses undeclared prefix "
                f"operator {symbol!r}",
                code="SCHEMA_ERROR",
            )
        language_prefix[symbol] = prefix_spec
    kinds: dict[str, ReferenceKindSpec] = {}
    for kind_name in (str(item) for item in _list(body.get("referenceKinds"), path)):
        kind_spec = reference_kinds.get(kind_name)
        if kind_spec is None:
            raise FactoryError(
                f"expression grammar {path.name}: language {name} uses undeclared "
                f"reference kind {kind_name!r}",
                code="SCHEMA_ERROR",
            )
        kinds[kind_name] = kind_spec
    plain: dict[str, FunctionSpec] = {}
    for function_name in (str(item) for item in _list(body.get("functions"), path)):
        function_spec = functions.get(function_name)
        if function_spec is None:
            raise FactoryError(
                f"expression grammar {path.name}: language {name} uses undeclared "
                f"function {function_name!r}",
                code="SCHEMA_ERROR",
            )
        plain[function_spec.name] = function_spec
    prefixed_names = [str(item) for item in _list(body.get("qualifiedFunctions"), path)]
    prefix_kinds = [kind for kind in kinds.values() if kind.is_function_prefix]
    prefixed: dict[str, FunctionSpec] = {}
    if prefixed_names and not prefix_kinds:
        raise FactoryError(
            f"expression grammar {path.name}: language {name} lists prefixed functions "
            f"but no function-prefix reference kind",
            code="SCHEMA_ERROR",
        )
    for function_name in prefixed_names:
        qualified_spec = qualified.get(function_name)
        if qualified_spec is None:
            raise FactoryError(
                f"expression grammar {path.name}: language {name} uses undeclared "
                f"prefixed function {function_name!r}",
                code="SCHEMA_ERROR",
            )
        for kind in prefix_kinds:
            prefixed[f"{kind.name}/{qualified_spec.name}"] = qualified_spec
    return LanguageGrammar(
        name=name,
        operators=language_operators,
        prefix_operators=language_prefix,
        reference_kinds=kinds,
        functions=plain,
        qualified_functions=prefixed,
        keywords=frozenset(str(item) for item in _list(body.get("keywords"), path)),
        variables=frozenset(str(item) for item in _list(body.get("variables"), path)),
        features=frozenset(str(item) for item in _list(body.get("features"), path)),
        ternary=ternary,
    )


def _opener_table(
    reference_kinds: Mapping[str, ReferenceKindSpec],
) -> tuple[tuple[str, str], ...]:
    """Every opener the grammar knows, longest first: the lexer recognises a reference
    kind even in a language that does not allow it, so the parser can name it."""
    return tuple(
        sorted(
            ((opener, kind.name) for kind in reference_kinds.values() for opener in kind.openers),
            key=lambda item: (-len(item[0]), item[0], item[1]),
        )
    )


def _operator(raw: Any, path: Path) -> OperatorSpec:
    data = _mapping(raw, "operators[]", path)
    right = str(data.get("right", RIGHT_EXPRESSION))
    if right not in (RIGHT_EXPRESSION, RIGHT_LIST):
        raise FactoryError(
            f"expression grammar {path.name}: operator right operand {right!r} is unknown",
            code="SCHEMA_ERROR",
        )
    return OperatorSpec(
        symbol=_text(data.get("symbol"), "operators[].symbol", path),
        precedence=_integer(data.get("precedence"), "operators[].precedence", path),
        associativity=_association(data.get("associativity"), path),
        word=bool(data.get("word", False)),
        right_operand=right,
    )


def _prefix_operator(raw: Any, path: Path) -> PrefixOperatorSpec:
    data = _mapping(raw, "prefixOperators[]", path)
    return PrefixOperatorSpec(
        symbol=_text(data.get("symbol"), "prefixOperators[].symbol", path),
        precedence=_integer(data.get("precedence"), "prefixOperators[].precedence", path),
        word=bool(data.get("word", False)),
    )


def _ternary(raw: Any, path: Path) -> TernarySpec:
    data = _mapping(raw, "ternary", path)
    return TernarySpec(
        condition_symbol=_text(data.get("condition"), "ternary.condition", path),
        alternative_symbol=_text(data.get("alternative"), "ternary.alternative", path),
        precedence=_integer(data.get("precedence"), "ternary.precedence", path),
        associativity=_association(data.get("associativity"), path),
    )


def _association(raw: Any, path: Path) -> str:
    value = str(raw)
    if value not in ASSOCIATIONS:
        raise FactoryError(
            f"expression grammar {path.name}: associativity {value!r} is not "
            f"{ASSOCIATION_LEFT} or {ASSOCIATION_RIGHT}",
            code="SCHEMA_ERROR",
        )
    return value


def _reference_kind(raw: Any, path: Path) -> ReferenceKindSpec:
    data = _mapping(raw, "referenceKinds[]", path)
    openers = tuple(str(item) for item in _list(data.get("openers"), path))
    if not openers:
        raise FactoryError(
            f"expression grammar {path.name}: a reference kind needs at least one opener",
            code="SCHEMA_ERROR",
        )
    closer = data.get("closer")
    key_source = _text(data.get("keySource"), "referenceKinds[].keySource", path)
    if key_source not in KEY_SOURCES:
        raise FactoryError(
            f"expression grammar {path.name}: reference key source {key_source!r} is unknown",
            code="SCHEMA_ERROR",
        )
    return ReferenceKindSpec(
        name=_text(data.get("name"), "referenceKinds[].name", path),
        openers=openers,
        closer=None if closer is None else str(closer),
        allows_dots=bool(data.get("dots", False)),
        key_source=key_source,
        allowed_types=tuple(str(item) for item in _list(data.get("allowedTypes"), path)),
    )


def _function(raw: Any, path: Path) -> FunctionSpec:
    data = _mapping(raw, "functions[]", path)
    return FunctionSpec(
        name=_text(data.get("name"), "functions[].name", path),
        min_args=_integer(data.get("minArgs"), "functions[].minArgs", path),
        max_args=_optional_integer(data.get("maxArgs")),
        case_insensitive=bool(data.get("caseInsensitive", False)),
        qualified=False,
    )


def _qualified_function(raw: Any, path: Path) -> FunctionSpec:
    data = _mapping(raw, "qualifiedFunctions[]", path)
    return FunctionSpec(
        name=_text(data.get("name"), "qualifiedFunctions[].name", path),
        min_args=_integer(data.get("minArgs"), "qualifiedFunctions[].minArgs", path),
        max_args=_optional_integer(data.get("maxArgs")),
        case_insensitive=bool(data.get("caseInsensitive", True)),
        qualified=True,
    )


def _literal_syntax(raw: Any, path: Path) -> LiteralSyntax:
    data = _mapping(raw, "stringLiteral", path)
    quotes = tuple(str(item) for item in _list(data.get("quotes"), path))
    if not quotes:
        raise FactoryError(
            f"expression grammar {path.name}: stringLiteral needs at least one quote character",
            code="SCHEMA_ERROR",
        )
    escape = _text(data.get("escape"), "stringLiteral.escape", path)
    if len(escape) != 1:
        raise FactoryError(
            f"expression grammar {path.name}: stringLiteral.escape must be one character",
            code="SCHEMA_ERROR",
        )
    entries = _mapping(data.get("escapes", {}), "stringLiteral.escapes", path)
    return LiteralSyntax(
        quotes=quotes,
        escape=escape,
        escapes={str(key): str(value) for key, value in entries.items()},
    )


def _punctuation(raw: Any, path: Path) -> Mapping[str, str]:
    data = _mapping(raw, "punctuation", path)
    table: dict[str, str] = {}
    for role in PUNCTUATION_ROLES:
        symbol = _text(data.get(role), f"punctuation.{role}", path)
        if len(symbol) != 1:
            raise FactoryError(
                f"expression grammar {path.name}: punctuation.{role} must be one character",
                code="SCHEMA_ERROR",
            )
        table[role] = symbol
    return table


def _patterns(raw: Any, path: Path) -> tuple[re.Pattern[str], re.Pattern[str]]:
    data = _mapping(raw, "tokenPatterns", path)
    number = _compile(_text(data.get("number"), "tokenPatterns.number", path), "number", path)
    identifier = _compile(
        _text(data.get("identifier"), "tokenPatterns.identifier", path), "identifier", path
    )
    return number, identifier


def _compile(pattern: str, label: str, path: Path) -> re.Pattern[str]:
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise FactoryError(
            f"expression grammar {path.name}: tokenPatterns.{label} is not a valid "
            f"regular expression: {exc}",
            code="SCHEMA_ERROR",
        ) from exc
    if compiled.match("") is not None:
        raise FactoryError(
            f"expression grammar {path.name}: tokenPatterns.{label} may match the empty "
            f"string, which cannot be tokenised",
            code="SCHEMA_ERROR",
        )
    return compiled


def _read_document(path: Path) -> Mapping[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FactoryError(
            f"expression grammar {path.name} cannot be read: {exc}",
            code="SCHEMA_ERROR",
        ) from exc
    return _mapping(loaded, path.name, path)


def _mapping(raw: Any, label: str, path: Path) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise FactoryError(
            f"expression grammar {path.name}: {label} must be a JSON object",
            code="SCHEMA_ERROR",
        )
    return {str(key): value for key, value in raw.items()}


def _list(raw: Any, path: Path) -> Sequence[Any]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FactoryError(
            f"expression grammar {path.name}: expected a JSON array, found {type(raw).__name__}",
            code="SCHEMA_ERROR",
        )
    return raw


def _text(raw: Any, label: str, path: Path) -> str:
    if not isinstance(raw, str) or not raw:
        raise FactoryError(
            f"expression grammar {path.name}: {label} must be a non-empty string",
            code="SCHEMA_ERROR",
        )
    return raw


def _integer(raw: Any, label: str, path: Path) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise FactoryError(
            f"expression grammar {path.name}: {label} must be an integer",
            code="SCHEMA_ERROR",
        )
    return raw


def _optional_integer(raw: Any) -> int | None:
    return None if raw is None else int(raw)
