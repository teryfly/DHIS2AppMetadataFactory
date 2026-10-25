"""Expression tokenizer (BP-VAL-021).

A hand-written, single-pass scanner: every token carries its character offset, and all
vocabulary (reference openers, operator spellings, quote characters, punctuation) comes
from the grammar.  Lexing never repairs: an unterminated string or an unclosed reference
is reported at the offset where it started (C-07).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from factory_blueprint.validators.expressions.grammar import (
    ROLE_COMMA,
    ROLE_LBRACKET,
    ROLE_LPAREN,
    ROLE_RBRACKET,
    ROLE_RPAREN,
    Grammar,
    LanguageGrammar,
    ReferenceKindSpec,
)

TOKEN_NUMBER: Final[str] = "number"
TOKEN_STRING: Final[str] = "string"
TOKEN_WORD: Final[str] = "word"
TOKEN_OPERATOR: Final[str] = "operator"
TOKEN_REFERENCE: Final[str] = "reference"
TOKEN_REF_PREFIX: Final[str] = "ref_prefix"
TOKEN_LPAREN: Final[str] = "lparen"
TOKEN_RPAREN: Final[str] = "rparen"
TOKEN_LBRACKET: Final[str] = "lbracket"
TOKEN_RBRACKET: Final[str] = "rbracket"
TOKEN_COMMA: Final[str] = "comma"
TOKEN_EOF: Final[str] = "eof"

PUNCTUATION_KINDS: Final[dict[str, str]] = {
    ROLE_LPAREN: TOKEN_LPAREN,
    ROLE_RPAREN: TOKEN_RPAREN,
    ROLE_LBRACKET: TOKEN_LBRACKET,
    ROLE_RBRACKET: TOKEN_RBRACKET,
    ROLE_COMMA: TOKEN_COMMA,
}

CODE_UNCLOSED_REFERENCE: Final[str] = "EXPR_UNCLOSED_REFERENCE"
CODE_EMPTY_REFERENCE: Final[str] = "EXPR_EMPTY_REFERENCE"
CODE_UNTERMINATED_STRING: Final[str] = "EXPR_UNTERMINATED_STRING"
CODE_UNKNOWN_CHARACTER: Final[str] = "EXPR_UNKNOWN_CHARACTER"

_SUFFIX_SEPARATOR: Final[str] = "."


@dataclass(frozen=True)
class SegmentSpan:
    """One dot-separated piece of a reference, with the exact span it occupies."""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Reference:
    """A complete reference token such as ``#{weight}`` or ``![coc name]``."""

    kind: str
    opener: str
    closer: str | None
    text: str
    segments: tuple[SegmentSpan, ...]
    start: int
    end: int

    @property
    def name(self) -> str:
        return self.text.strip()

    def key_candidates(self) -> tuple[tuple[str, SegmentSpan], ...]:
        """Dotted prefixes of the reference, longest first, in a stable order."""
        out: list[tuple[str, SegmentSpan]] = []
        for take in range(len(self.segments), 0, -1):
            key = _SUFFIX_SEPARATOR.join(segment.text for segment in self.segments[:take])
            out.append((key, self.segments[take - 1]))
        return tuple(out)


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    offset: int
    end: int
    value: str = ""
    reference: Reference | None = None


@dataclass(frozen=True)
class LexError:
    code: str
    message: str
    offset: int


@dataclass(frozen=True)
class LexResult:
    tokens: tuple[Token, ...]
    errors: tuple[LexError, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def tokenize(source: str, grammar: Grammar, language: LanguageGrammar) -> LexResult:
    """Scan ``source`` for ``language``; the input string is never modified."""
    tokens: list[Token] = []
    errors: list[LexError] = []
    position = 0
    size = len(source)
    while position < size:
        character = source[position]
        if character.isspace():
            position += 1
            continue
        matched = _match_opener(source, position, grammar)
        if matched is not None:
            opener, spec = matched
            if spec.closer is None:
                stop = position + len(opener)
                tokens.append(Token(TOKEN_REF_PREFIX, opener, position, stop, spec.name))
                position = stop
                continue
            reference, failure = _read_reference(source, position, opener, spec, grammar)
            if failure is not None:
                errors.append(failure)
                break
            if reference is None:
                break
            tokens.append(
                Token(
                    TOKEN_REFERENCE,
                    source[reference.start : reference.end],
                    reference.start,
                    reference.end,
                    reference.name,
                    reference,
                )
            )
            position = reference.end
            continue
        if character in grammar.literal.quotes:
            token, failure, position = _read_string(source, position, grammar)
            if failure is not None:
                errors.append(failure)
                break
            tokens.append(token)
            continue
        number = grammar.number_pattern.match(source, position)
        if number is not None:
            position = _append_span(tokens, TOKEN_NUMBER, source, number.span())
            continue
        word = grammar.identifier_pattern.match(source, position)
        if word is not None:
            position = _append_span(tokens, TOKEN_WORD, source, word.span())
            continue
        role = grammar.punctuation_kind(character)
        if role is not None:
            tokens.append(Token(PUNCTUATION_KINDS[role], character, position, position + 1))
            position += 1
            continue
        symbol = _match_symbol(source, position, grammar.symbols)
        if symbol is not None:
            position = _append_span(
                tokens, TOKEN_OPERATOR, source, (position, position + len(symbol))
            )
            continue
        errors.append(
            LexError(
                CODE_UNKNOWN_CHARACTER,
                f"{character!r} is not part of the {language.name} expression vocabulary",
                position,
            )
        )
        break
    tokens.append(Token(TOKEN_EOF, "", size, size))
    return LexResult(tuple(tokens), tuple(errors))


def _append_span(tokens: list[Token], kind: str, source: str, span: tuple[int, int]) -> int:
    start, stop = span
    text = source[start:stop]
    tokens.append(Token(kind, text, start, stop, text))
    return stop


def _match_opener(
    source: str, position: int, grammar: Grammar
) -> tuple[str, ReferenceKindSpec] | None:
    for opener, kind_name in grammar.openers:
        if source.startswith(opener, position):
            spec = grammar.reference_kinds.get(kind_name)
            if spec is not None:
                return opener, spec
    return None


def _match_symbol(source: str, position: int, symbols: tuple[str, ...]) -> str | None:
    for symbol in symbols:
        if source.startswith(symbol, position):
            return symbol
    return None


def _read_reference(
    source: str, position: int, opener: str, spec: ReferenceKindSpec, grammar: Grammar
) -> tuple[Reference | None, LexError | None]:
    """Read one closed reference token, or report the opener it never reached."""
    closer = spec.closer
    if closer is None:
        return None, None
    content_start = position + len(opener)
    found = source.find(closer, content_start)
    if found < 0:
        return None, LexError(
            CODE_UNCLOSED_REFERENCE,
            f"{opener} is never closed; a {spec.name} reference needs {closer!r} before "
            f"the end of the expression",
            position,
        )
    inner = source[content_start:found]
    segments = _segments(inner, content_start)
    reference = Reference(
        kind=spec.name,
        opener=opener,
        closer=closer,
        text=inner,
        segments=segments,
        start=position,
        end=found + len(closer),
    )
    if not any(segment.text for segment in segments):
        return None, LexError(
            CODE_EMPTY_REFERENCE,
            f"{opener}{closer} names nothing in grammar {grammar.name}",
            position,
        )
    return reference, None


def _segments(inner: str, base: int) -> tuple[SegmentSpan, ...]:
    spans: list[SegmentSpan] = []
    cursor = 0
    for part in inner.split(_SUFFIX_SEPARATOR):
        start = base + cursor
        stripped = part.strip()
        text_start = start + len(part) - len(part.lstrip())
        spans.append(SegmentSpan(stripped, text_start, text_start + len(stripped)))
        cursor += len(part) + 1
    return tuple(spans)


def _read_string(
    source: str, position: int, grammar: Grammar
) -> tuple[Token, LexError | None, int]:
    quote = source[position]
    escape = grammar.literal.escape
    decoded: list[str] = []
    cursor = position + 1
    while cursor < len(source):
        character = source[cursor]
        if character == escape and cursor + 1 < len(source):
            following = source[cursor + 1]
            decoded.append(grammar.literal.escapes.get(following, following))
            cursor += 2
            continue
        if character == quote:
            token = Token(
                TOKEN_STRING,
                source[position : cursor + 1],
                position,
                cursor + 1,
                "".join(decoded),
            )
            return token, None, cursor + 1
        decoded.append(character)
        cursor += 1
    return (
        Token(TOKEN_STRING, source[position:], position, len(source)),
        LexError(CODE_UNTERMINATED_STRING, f"a {quote} string literal is never closed", position),
        len(source),
    )
