"""Precedence-climbing expression parser (BP-VAL-021).

Recursive descent with an explicit depth bound: nesting beyond the limit aborts with a
reported error instead of exhausting the interpreter stack.  Syntax problems are
*returned* as ``ParseError`` items and never repaired (C-07); only an unknown grammar
version raises, because then S2 cannot claim anything about the expression at all.

The AST carries character offsets for every node and every reference segment, which is
what makes businessKey-to-UID substitution exact later on without any string search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from factory_blueprint.validators.expressions.grammar import (
    ASSOCIATION_RIGHT,
    FEATURE_LIST,
    FEATURE_TERNARY,
    RIGHT_EXPRESSION,
    RIGHT_LIST,
    ROLE_COMMA,
    ROLE_LBRACKET,
    ROLE_LPAREN,
    ROLE_RBRACKET,
    ROLE_RPAREN,
    FunctionSpec,
    Grammar,
    LanguageGrammar,
    OperatorSpec,
)
from factory_blueprint.validators.expressions.lexer import (
    TOKEN_COMMA,
    TOKEN_EOF,
    TOKEN_LBRACKET,
    TOKEN_LPAREN,
    TOKEN_NUMBER,
    TOKEN_OPERATOR,
    TOKEN_RBRACKET,
    TOKEN_REF_PREFIX,
    TOKEN_REFERENCE,
    TOKEN_RPAREN,
    TOKEN_STRING,
    TOKEN_WORD,
    LexError,
    Reference,
    Token,
    tokenize,
)

#: S2's own stack-safety bound; a grammar may only ask for a shallower parse.
MAX_PARSE_DEPTH: Final[int] = 128

KIND_LITERAL: Final[str] = "literal"
KIND_VARIABLE: Final[str] = "variable"
KIND_REFERENCE: Final[str] = "reference"
KIND_UNARY: Final[str] = "unary"
KIND_BINARY: Final[str] = "binary"
KIND_CALL: Final[str] = "call"
KIND_LIST: Final[str] = "list"
KIND_CONDITIONAL: Final[str] = "conditional"

CODE_EMPTY: Final[str] = "EXPR_EMPTY"
CODE_MISSING_OPERAND: Final[str] = "EXPR_MISSING_OPERAND"
CODE_UNEXPECTED_TOKEN: Final[str] = "EXPR_UNEXPECTED_TOKEN"
CODE_TRAILING_INPUT: Final[str] = "EXPR_TRAILING_INPUT"
CODE_UNBALANCED_PAREN: Final[str] = "EXPR_UNBALANCED_PAREN"
CODE_UNBALANCED_BRACKET: Final[str] = "EXPR_UNBALANCED_BRACKET"
CODE_EMPTY_LIST: Final[str] = "EXPR_EMPTY_LIST"
CODE_UNKNOWN_FUNCTION: Final[str] = "EXPR_UNKNOWN_FUNCTION"
CODE_ARITY_MISMATCH: Final[str] = "EXPR_ARITY_MISMATCH"
CODE_REFERENCE_NOT_ALLOWED: Final[str] = "EXPR_REFERENCE_NOT_ALLOWED"
CODE_CONSTRUCT_NOT_ALLOWED: Final[str] = "EXPR_CONSTRUCT_NOT_ALLOWED"
CODE_OPERATOR_NOT_ALLOWED: Final[str] = "EXPR_OPERATOR_NOT_ALLOWED"
CODE_SUFFIX_NOT_ALLOWED: Final[str] = "EXPR_SUFFIX_NOT_ALLOWED"
CODE_DEPTH_EXCEEDED: Final[str] = "EXPR_DEPTH_EXCEEDED"
CODE_MALFORMED_TERNARY: Final[str] = "EXPR_MALFORMED_TERNARY"

SYNTAX_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        CODE_EMPTY,
        CODE_MISSING_OPERAND,
        CODE_UNEXPECTED_TOKEN,
        CODE_TRAILING_INPUT,
        CODE_UNBALANCED_PAREN,
        CODE_UNBALANCED_BRACKET,
        CODE_EMPTY_LIST,
        CODE_UNKNOWN_FUNCTION,
        CODE_ARITY_MISMATCH,
        CODE_REFERENCE_NOT_ALLOWED,
        CODE_CONSTRUCT_NOT_ALLOWED,
        CODE_OPERATOR_NOT_ALLOWED,
        CODE_SUFFIX_NOT_ALLOWED,
        CODE_DEPTH_EXCEEDED,
        CODE_MALFORMED_TERNARY,
        "EXPR_UNCLOSED_REFERENCE",
        "EXPR_EMPTY_REFERENCE",
        "EXPR_UNTERMINATED_STRING",
        "EXPR_UNKNOWN_CHARACTER",
    }
)


@dataclass(frozen=True)
class ParseError:
    """One syntax finding: code, message and the character offset it starts at."""

    code: str
    message: str
    offset: int


@dataclass(frozen=True)
class ExprNode:
    """An immutable AST node; ``refs`` are the references held by this node itself."""

    kind: str
    op: str | None = None
    name: str | None = None
    args: tuple[ExprNode, ...] = ()
    refs: tuple[Reference, ...] = ()
    offset: int = 0
    length: int = 0
    text: str | None = None

    @property
    def end(self) -> int:
        return self.offset + self.length


@dataclass(frozen=True)
class ParseResult:
    ok: bool
    ast: ExprNode | None
    errors: list[ParseError]

    @property
    def codes(self) -> list[str]:
        return [error.code for error in self.errors]

    @property
    def messages(self) -> list[str]:
        return [error.message for error in self.errors]


class _Abort(Exception):
    """Internal control flow only: it never escapes :func:`parse`."""

    def __init__(self, code: str, message: str, offset: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.offset = offset


def parse(expression: str, *, language: str, grammar_version: str) -> ParseResult:
    """Parse one expression of one language under one grammar version."""
    grammar = Grammar.load(grammar_version)
    return parse_with_grammar(expression, grammar=grammar, language=language)


def parse_with_grammar(
    expression: str, *, grammar: Grammar, language: str | LanguageGrammar
) -> ParseResult:
    """Same as :func:`parse` for a caller that already holds the grammar."""
    spec = grammar.language(language) if isinstance(language, str) else language
    lexed = tokenize(expression, grammar, spec)
    if not lexed.ok:
        return ParseResult(False, None, [_to_parse_error(error) for error in lexed.errors])
    try:
        node = _Reader(grammar, spec, lexed.tokens).read()
    except _Abort as abort:
        return ParseResult(False, None, [ParseError(abort.code, abort.message, abort.offset)])
    return ParseResult(True, node, [])


def _to_parse_error(error: LexError) -> ParseError:
    return ParseError(error.code, error.message, error.offset)


def ast_to_json(node: ExprNode) -> dict[str, Any]:
    """Deterministic JSON form of an AST: fixed key order, children in source order."""
    data: dict[str, Any] = {"kind": node.kind, "offset": node.offset, "length": node.length}
    if node.op is not None:
        data["op"] = node.op
    if node.name is not None:
        data["name"] = node.name
    if node.text is not None:
        data["text"] = node.text
    if node.args:
        data["args"] = [ast_to_json(child) for child in node.args]
    if node.refs:
        data["refs"] = [reference_to_json(reference) for reference in node.refs]
    return data


def reference_to_json(reference: Reference) -> dict[str, Any]:
    return {
        "kind": reference.kind,
        "opener": reference.opener,
        "closer": reference.closer,
        "text": reference.text,
        "start": reference.start,
        "end": reference.end,
        "segments": [
            {"text": segment.text, "start": segment.start, "end": segment.end}
            for segment in reference.segments
        ],
    }


def collect_references(node: ExprNode) -> tuple[Reference, ...]:
    """Every reference in the tree, in source order."""
    found: list[Reference] = []
    _gather(node, found)
    found.sort(key=_reference_order)
    return tuple(found)


def _gather(node: ExprNode, out: list[Reference]) -> None:
    out.extend(node.refs)
    for child in node.args:
        _gather(child, out)


def _reference_order(reference: Reference) -> tuple[int, int]:
    return (reference.start, reference.end)


class _Reader:
    def __init__(
        self, grammar: Grammar, language: LanguageGrammar, tokens: tuple[Token, ...]
    ) -> None:
        self._grammar = grammar
        self._language = language
        self._tokens = tokens
        self._index = 0
        self._depth = 0
        self._limit = min(grammar.max_depth, MAX_PARSE_DEPTH)

    @property
    def _peek(self) -> Token:
        return self._tokens[self._index]

    def _advance(self) -> Token:
        token = self._tokens[self._index]
        if token.kind != TOKEN_EOF:
            self._index += 1
        return token

    def read(self) -> ExprNode:
        if self._peek.kind == TOKEN_EOF:
            raise _Abort(
                CODE_EMPTY,
                f"a {self._language.name} expression must not be empty",
                self._peek.offset,
            )
        node = self._expression(0)
        trailing = self._peek
        if trailing.kind != TOKEN_EOF:
            raise _Abort(
                CODE_TRAILING_INPUT,
                f"{self._describe(trailing)} follows a complete expression",
                trailing.offset,
            )
        return node

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > self._limit:
            raise _Abort(
                CODE_DEPTH_EXCEEDED,
                f"the expression nests deeper than the grammar limit of "
                f"{self._limit} levels",
                self._peek.offset,
            )

    def _leave(self) -> None:
        self._depth -= 1

    def _expression(self, minimum: int) -> ExprNode:
        self._enter()
        try:
            left = self._operand()
            while True:
                token = self._peek
                if self._is_ternary(token):
                    ternary = self._ternary_spec(token)
                    if ternary.precedence < minimum:
                        return left
                    left = self._conditional(left, ternary)
                    continue
                spec = self._infix(token)
                if spec is None:
                    self._reject_construct(token)
                    return left
                if spec.precedence < minimum:
                    return left
                self._advance()
                right = (
                    self._list_operand()
                    if spec.right_operand == RIGHT_LIST
                    else self._expression(spec.precedence + 1)
                )
                left = ExprNode(
                    kind=KIND_BINARY,
                    op=spec.symbol,
                    args=(left, right),
                    offset=left.offset,
                    length=right.end - left.offset,
                )
        finally:
            self._leave()

    def _infix(self, token: Token) -> OperatorSpec | None:
        if token.kind in (TOKEN_OPERATOR, TOKEN_WORD):
            return self._language.operator(token.text)
        return None

    def _is_ternary(self, token: Token) -> bool:
        if token.kind != TOKEN_OPERATOR:
            return False
        return token.text == self._language.ternary.condition_symbol

    def _ternary_spec(self, token: Token) -> OperatorSpec:
        if not self._language.allows(FEATURE_TERNARY):
            raise _Abort(
                CODE_CONSTRUCT_NOT_ALLOWED,
                f"the ternary {token.text!r} operator is not part of the "
                f"{self._language.name} language in grammar {self._grammar.name}",
                token.offset,
            )
        ternary = self._language.ternary
        return OperatorSpec(
            symbol=ternary.condition_symbol,
            precedence=ternary.precedence,
            associativity=ternary.associativity,
            word=False,
            right_operand=RIGHT_EXPRESSION,
        )

    def _conditional(self, condition: ExprNode, spec: OperatorSpec) -> ExprNode:
        ternary = self._language.ternary
        self._advance()
        consequence = self._expression(0)
        marker = self._peek
        if marker.kind != TOKEN_OPERATOR or marker.text != ternary.alternative_symbol:
            raise _Abort(
                CODE_MALFORMED_TERNARY,
                f"the {ternary.condition_symbol!r} branch has no "
                f"{ternary.alternative_symbol!r} alternative, found "
                f"{self._describe(marker)}",
                marker.offset,
            )
        self._advance()
        if spec.associativity == ASSOCIATION_RIGHT:
            minimum = spec.precedence
        else:
            minimum = spec.precedence + 1
        alternative = self._expression(minimum)
        return ExprNode(
            kind=KIND_CONDITIONAL,
            op=spec.symbol,
            name="conditional",
            args=(condition, consequence, alternative),
            offset=condition.offset,
            length=alternative.end - condition.offset,
        )

    def _reject_construct(self, token: Token) -> None:
        """A construct the grammar knows but this language does not is reported, not skipped."""
        if token.kind not in (TOKEN_OPERATOR, TOKEN_WORD):
            return
        if self._foreign_operator(token.text):
            raise _Abort(
                CODE_OPERATOR_NOT_ALLOWED,
                f"operator {token.text!r} is not part of the {self._language.name} language "
                f"in grammar {self._grammar.name}",
                token.offset,
            )

    def _foreign_operator(self, symbol: str) -> bool:
        """Known to the grammar, unusable in this language in any position."""
        if symbol in self._language.operators or symbol in self._language.prefix_operators:
            return False
        return symbol in self._grammar.infix_symbols or symbol in self._grammar.prefix_symbols

    def _operand(self) -> ExprNode:
        self._enter()
        try:
            token = self._peek
            if token.kind == TOKEN_EOF:
                raise _Abort(
                    CODE_MISSING_OPERAND,
                    "the expression ends where an operand was expected",
                    token.offset,
                )
            if token.kind == TOKEN_OPERATOR:
                return self._prefixed(token)
            if token.kind in (TOKEN_NUMBER, TOKEN_STRING):
                self._advance()
                return ExprNode(
                    kind=KIND_LITERAL,
                    name=token.kind,
                    text=token.value,
                    offset=token.offset,
                    length=token.end - token.offset,
                )
            if token.kind == TOKEN_WORD:
                prefix = self._language.prefix_operator(token.text)
                if prefix is not None and prefix.word:
                    return self._prefixed(token)
                return self._word()
            if token.kind == TOKEN_REFERENCE:
                return self._reference(self._advance())
            if token.kind == TOKEN_REF_PREFIX:
                return self._qualified_call(self._advance())
            if token.kind == TOKEN_LPAREN:
                return self._grouped()
            if token.kind == TOKEN_LBRACKET:
                return self._list_operand()
            raise _Abort(
                CODE_UNEXPECTED_TOKEN,
                f"{self._describe(token)} cannot start an expression",
                token.offset,
            )
        finally:
            self._leave()

    def _prefixed(self, token: Token) -> ExprNode:
        spec = self._language.prefix_operator(token.text)
        if spec is None:
            if self._foreign_operator(token.text):
                raise _Abort(
                    CODE_OPERATOR_NOT_ALLOWED,
                    f"operator {token.text!r} is not a prefix operator of the "
                    f"{self._language.name} language in grammar {self._grammar.name}",
                    token.offset,
                )
            raise _Abort(
                CODE_UNEXPECTED_TOKEN,
                f"{self._describe(token)} cannot start an expression",
                token.offset,
            )
        self._advance()
        operand = self._expression(spec.precedence)
        return ExprNode(
            kind=KIND_UNARY,
            op=spec.symbol,
            args=(operand,),
            offset=token.offset,
            length=operand.end - token.offset,
        )

    def _word(self) -> ExprNode:
        token = self._advance()
        if self._peek.kind == TOKEN_LPAREN:
            spec = self._language.function(token.text)
            if spec is None:
                raise _Abort(
                    CODE_UNKNOWN_FUNCTION,
                    f"{token.text!r} is not a function of the "
                    f"{self._language.name} language in grammar {self._grammar.name}",
                    token.offset,
                )
            self._advance()
            return self._call(spec, token.text, token.offset, token.offset)
        if token.text in self._language.keywords:
            return ExprNode(
                kind=KIND_LITERAL,
                name="keyword",
                text=token.text,
                offset=token.offset,
                length=token.end - token.offset,
            )
        return ExprNode(
            kind=KIND_VARIABLE,
            name=token.text,
            text=token.text,
            offset=token.offset,
            length=token.end - token.offset,
        )

    def _qualified_call(self, prefix: Token) -> ExprNode:
        kind = self._language.reference_kind(prefix.value)
        if kind is None:
            raise _Abort(
                CODE_REFERENCE_NOT_ALLOWED,
                f"the reference prefix {prefix.text!r} is not part of the "
                f"{self._language.name} language in grammar {self._grammar.name}",
                prefix.offset,
            )
        name = self._peek
        if name.kind != TOKEN_WORD:
            raise _Abort(
                CODE_UNEXPECTED_TOKEN,
                f"a function name must follow {prefix.text!r}, found {self._describe(name)}",
                name.offset,
            )
        self._advance()
        spec = self._language.qualified_function(kind.name, name.text)
        if spec is None:
            raise _Abort(
                CODE_UNKNOWN_FUNCTION,
                f"{prefix.text}{name.text} is not a function of the "
                f"{self._language.name} language in grammar {self._grammar.name}",
                prefix.offset,
            )
        opening = self._peek
        if opening.kind != TOKEN_LPAREN:
            raise _Abort(
                CODE_UNEXPECTED_TOKEN,
                f"{prefix.text}{name.text} must be called, found {self._describe(opening)}",
                opening.offset,
            )
        self._advance()
        written = f"{prefix.text}{name.text}"
        return self._call(spec, written, prefix.offset, name.offset)

    def _call(
        self,
        spec: FunctionSpec,
        written: str,
        offset: int,
        name_offset: int,
    ) -> ExprNode:
        arguments: list[ExprNode] = []
        if self._peek.kind == TOKEN_RPAREN:
            closing = self._advance()
        else:
            while True:
                arguments.append(self._expression(0))
                separator = self._peek
                if separator.kind == TOKEN_COMMA:
                    self._advance()
                    if self._peek.kind == TOKEN_RPAREN:
                        raise _Abort(
                            CODE_MISSING_OPERAND,
                            f"a value is missing after {self._grammar.punctuation[ROLE_COMMA]!r} "
                            f"in the call to {written}",
                            separator.offset,
                        )
                    continue
                if separator.kind == TOKEN_RPAREN:
                    break
                raise _Abort(
                    CODE_UNEXPECTED_TOKEN,
                    f"{self._describe(separator)} inside the call to {written}, expected "
                    f"{self._grammar.punctuation[ROLE_COMMA]!r} or "
                    f"{self._grammar.punctuation[ROLE_RPAREN]!r}",
                    separator.offset,
                )
            closing = self._advance()
        if not spec.accepts(len(arguments)):
            raise _Abort(
                CODE_ARITY_MISMATCH,
                f"{written} expects {spec.expectation()} argument(s) but got "
                f"{len(arguments)}",
                name_offset,
            )
        return ExprNode(
            kind=KIND_CALL,
            name=spec.name,
            args=tuple(arguments),
            offset=offset,
            length=closing.end - offset,
            text=written,
        )

    def _reference(self, token: Token) -> ExprNode:
        reference = token.reference
        if reference is None:
            raise _Abort(CODE_UNEXPECTED_TOKEN, self._describe(token), token.offset)
        self._check_reference(reference, token.offset)
        spec = self._language.reference_kind(reference.kind)
        if spec is not None and len(reference.segments) > 1 and not spec.allows_dots:
            raise _Abort(
                CODE_SUFFIX_NOT_ALLOWED,
                f"a {reference.kind} reference in the {self._language.name} language takes "
                f"no dotted suffix",
                reference.start,
            )
        return ExprNode(
            kind=KIND_REFERENCE,
            name=reference.kind,
            text=reference.name,
            refs=(reference,),
            offset=reference.start,
            length=reference.end - reference.start,
        )

    def _check_reference(self, reference: Reference, offset: int) -> None:
        if self._language.reference_kind(reference.kind) is not None:
            return
        opener = reference.opener
        closer = reference.closer or ""
        raise _Abort(
            CODE_REFERENCE_NOT_ALLOWED,
            f"the {reference.kind} reference ({opener}…{closer}) is not part of the "
            f"{self._language.name} language in grammar {self._grammar.name}",
            offset,
        )

    def _grouped(self) -> ExprNode:
        opening = self._advance()
        inner = self._expression(0)
        closing = self._peek
        if closing.kind != TOKEN_RPAREN:
            raise _Abort(
                CODE_UNBALANCED_PAREN,
                f"the {self._grammar.punctuation[ROLE_LPAREN]!r} opened here is never "
                f"closed, found {self._describe(closing)}",
                opening.offset,
            )
        self._advance()
        return ExprNode(
            kind=inner.kind,
            op=inner.op,
            name=inner.name,
            args=inner.args,
            refs=inner.refs,
            text=inner.text,
            offset=inner.offset,
            length=closing.end - inner.offset,
        )

    def _list_operand(self) -> ExprNode:
        opening = self._peek
        if not self._language.allows(FEATURE_LIST):
            raise _Abort(
                CODE_CONSTRUCT_NOT_ALLOWED,
                f"a list literal is not part of the {self._language.name} language in "
                f"grammar {self._grammar.name}",
                opening.offset,
            )
        if opening.kind != TOKEN_LBRACKET:
            left = self._grammar.punctuation[ROLE_LBRACKET]
            right = self._grammar.punctuation[ROLE_RBRACKET]
            raise _Abort(
                CODE_UNEXPECTED_TOKEN,
                f"{self._describe(opening)} where a list literal ({left}...{right}) of the "
                f"{self._language.name} language was expected",
                opening.offset,
            )
        self._advance()
        if self._peek.kind == TOKEN_RBRACKET:
            raise _Abort(CODE_EMPTY_LIST, "the list literal holds no value", opening.offset)
        items: list[ExprNode] = []
        while True:
            items.append(self._expression(0))
            separator = self._peek
            if separator.kind == TOKEN_COMMA:
                self._advance()
                if self._peek.kind == TOKEN_RBRACKET:
                    raise _Abort(
                        CODE_MISSING_OPERAND,
                        f"a value is missing after {self._grammar.punctuation[ROLE_COMMA]!r} "
                        f"in the list",
                        separator.offset,
                    )
                continue
            if separator.kind == TOKEN_RBRACKET:
                break
            raise _Abort(
                CODE_UNBALANCED_BRACKET,
                f"the {self._grammar.punctuation[ROLE_LBRACKET]!r} opened here is never "
                f"closed, found {self._describe(separator)}",
                opening.offset,
            )
        closing = self._advance()
        return ExprNode(
            kind=KIND_LIST,
            name="list",
            args=tuple(items),
            offset=opening.offset,
            length=closing.end - opening.offset,
        )

    def _describe(self, token: Token) -> str:
        if token.kind == TOKEN_EOF:
            return "the end of the expression"
        return f"{token.kind} {token.text!r}"
