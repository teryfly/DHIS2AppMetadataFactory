"""Reference resolution and businessKey-to-UID substitution (BP-VAL-021, BP-CMP-028).

A parsed expression references Registry objects by businessKey inside the reference
tokens its grammar defines.  Resolution checks every one of them against the Registry
index of the Blueprint version: is the key known, does it point at a type this
reference kind allows, is the entry approved, was a UID assigned — and is every bare
variable the expression uses declared by the grammar.

Substitution splices UIDs into the source at the spans the tokens carry, so two
occurrences of one businessKey, and a businessKey that also appears inside another name,
are each handled by position rather than by string search.  The input string is read
only; the result is a new string built from slices.  Nothing here repairs an expression
(C-07): a single unresolved reference means no substituted text is produced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from factory_blueprint.enums import RegistryEntryStatus
from factory_blueprint.validators.expressions.grammar import (
    KEY_VARIABLE_OR_REGISTRY,
    Grammar,
    LanguageGrammar,
)
from factory_blueprint.validators.expressions.lexer import Reference, SegmentSpan
from factory_blueprint.validators.expressions.parser import KIND_REFERENCE, KIND_VARIABLE, ExprNode

CODE_UNRESOLVED_REFERENCE: Final[str] = "EXPR_UNRESOLVED_REFERENCE"
CODE_REFERENCE_TYPE_MISMATCH: Final[str] = "EXPR_REFERENCE_TYPE_MISMATCH"
CODE_REFERENCE_NOT_APPROVED: Final[str] = "EXPR_REFERENCE_NOT_APPROVED"
CODE_REFERENCE_STATUS_UNKNOWN: Final[str] = "EXPR_REFERENCE_STATUS_UNKNOWN"
CODE_UID_NOT_ASSIGNED: Final[str] = "EXPR_UID_NOT_ASSIGNED"
CODE_UNDECLARED_VARIABLE: Final[str] = "EXPR_UNDECLARED_VARIABLE"

RESOLVE_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        CODE_UNRESOLVED_REFERENCE,
        CODE_REFERENCE_TYPE_MISMATCH,
        CODE_REFERENCE_NOT_APPROVED,
        CODE_REFERENCE_STATUS_UNKNOWN,
        CODE_UID_NOT_ASSIGNED,
        CODE_UNDECLARED_VARIABLE,
    }
)

#: ``businessKey -> (dhis2Type, uid, status)``, the shape the Registry index is given in.
RegistryRow = tuple[str, str | None, str]


@dataclass(frozen=True)
class RegistryTarget:
    """The Registry facts one expression reference is checked against."""

    business_key: str
    dhis2_type: str
    uid: str | None
    status: str


@dataclass(frozen=True)
class ResolveIssue:
    code: str
    message: str
    offset: int
    key: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class ResolvedReference:
    """A reference, the key it resolved to and the exact span that key occupies."""

    reference: Reference
    key: str
    kind: str
    target: RegistryTarget | None
    span: tuple[int, int]
    replacement: str | None


@dataclass(frozen=True)
class ResolveResult:
    ok: bool
    references: tuple[ResolvedReference, ...]
    issues: list[ResolveIssue]
    #: BP-CMP-028: every referenced object exists in the target environment, so the
    #: expression can be checked by DHIS2_VALIDATE; otherwise it waits for POST_VERIFY.
    server_verifiable: bool
    deferred_to_post_verify: bool


@dataclass(frozen=True)
class SubstitutionResult:
    ok: bool
    text: str
    references: tuple[ResolvedReference, ...]
    issues: list[ResolveIssue]
    server_verifiable: bool
    deferred_to_post_verify: bool


def registry_index(
    rows: Mapping[str, RegistryRow | RegistryTarget],
) -> Mapping[str, RegistryTarget]:
    """Normalise the caller's index; the input mapping is never modified."""
    index: dict[str, RegistryTarget] = {}
    for business_key in sorted(rows):
        row = rows[business_key]
        if isinstance(row, RegistryTarget):
            index[business_key] = row
        else:
            dhis2_type, uid, status = row
            index[business_key] = RegistryTarget(
                business_key=business_key, dhis2_type=dhis2_type, uid=uid, status=status
            )
    return index


def resolve(
    ast: ExprNode,
    registry: Mapping[str, RegistryRow | RegistryTarget],
    *,
    language: str,
    grammar_version: str,
    field: str | None = None,
    expected_types: Mapping[str, Sequence[str]] | None = None,
) -> ResolveResult:
    """Check every reference and variable of a parsed expression against the Registry."""
    return resolve_with_grammar(
        ast,
        registry,
        grammar=Grammar.load(grammar_version),
        language=language,
        field=field,
        expected_types=expected_types,
    )


def resolve_with_grammar(
    ast: ExprNode,
    registry: Mapping[str, RegistryRow | RegistryTarget],
    *,
    grammar: Grammar,
    language: str | LanguageGrammar,
    field: str | None = None,
    expected_types: Mapping[str, Sequence[str]] | None = None,
) -> ResolveResult:
    """Same as :func:`resolve` for a caller that already holds the grammar."""
    spec = grammar.language(language) if isinstance(language, str) else language
    index = registry_index(registry)
    found, issues = _resolve_tree(ast, spec, index, grammar, field, expected_types)
    verifiable = not issues
    return ResolveResult(
        ok=verifiable,
        references=found,
        issues=issues,
        server_verifiable=verifiable,
        deferred_to_post_verify=not verifiable,
    )


def substitute(
    source: str,
    ast: ExprNode,
    registry: Mapping[str, RegistryRow | RegistryTarget],
    *,
    language: str,
    grammar_version: str,
    field: str | None = None,
    expected_types: Mapping[str, Sequence[str]] | None = None,
) -> SubstitutionResult:
    """Replace every businessKey reference with the UID the Registry assigns."""
    return substitute_with_grammar(
        source,
        ast,
        registry,
        grammar=Grammar.load(grammar_version),
        language=language,
        field=field,
        expected_types=expected_types,
    )


def substitute_with_grammar(
    source: str,
    ast: ExprNode,
    registry: Mapping[str, RegistryRow | RegistryTarget],
    *,
    grammar: Grammar,
    language: str | LanguageGrammar,
    field: str | None = None,
    expected_types: Mapping[str, Sequence[str]] | None = None,
) -> SubstitutionResult:
    """Same as :func:`substitute` for a caller that already holds the grammar."""
    result = resolve_with_grammar(
        ast,
        registry,
        grammar=grammar,
        language=language,
        field=field,
        expected_types=expected_types,
    )
    if not result.ok:
        return SubstitutionResult(
            ok=False,
            text=source,
            references=result.references,
            issues=result.issues,
            server_verifiable=False,
            deferred_to_post_verify=True,
        )
    splices = tuple(
        (item.span[0], item.span[1], item.replacement)
        for item in result.references
        if item.replacement is not None
    )
    return SubstitutionResult(
        ok=True,
        text=_splice(source, splices),
        references=result.references,
        issues=[],
        server_verifiable=result.server_verifiable,
        deferred_to_post_verify=result.deferred_to_post_verify,
    )


def _splice(source: str, splices: Sequence[tuple[int, int, str]]) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end, value in sorted(splices, key=lambda item: (item[0], item[1], item[2])):
        if start < cursor:
            continue
        pieces.append(source[cursor:start])
        pieces.append(value)
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces)


def _resolve_tree(
    node: ExprNode,
    language: LanguageGrammar,
    index: Mapping[str, RegistryTarget],
    grammar: Grammar,
    field: str | None,
    expected_types: Mapping[str, Sequence[str]] | None,
) -> tuple[tuple[ResolvedReference, ...], list[ResolveIssue]]:
    found: list[ResolvedReference] = []
    issues: list[ResolveIssue] = []
    _walk(node, language, index, grammar, field, expected_types, found, issues)
    found.sort(key=lambda item: (item.reference.start, item.reference.end, item.key))
    return tuple(found), issues


def _walk(
    node: ExprNode,
    language: LanguageGrammar,
    index: Mapping[str, RegistryTarget],
    grammar: Grammar,
    field: str | None,
    expected_types: Mapping[str, Sequence[str]] | None,
    found: list[ResolvedReference],
    issues: list[ResolveIssue],
) -> None:
    if node.kind == KIND_REFERENCE:
        for reference in node.refs:
            _visit_reference(
                reference, language, index, grammar, field, expected_types, found, issues
            )
        return
    if node.kind == KIND_VARIABLE:
        name = node.name or ""
        if name not in language.variables:
            issues.append(
                ResolveIssue(
                    code=CODE_UNDECLARED_VARIABLE,
                    message=_locate(
                        field,
                        f"{name!r} is not a variable grammar {grammar.name} declares for "
                        f"{language.name} expressions",
                    ),
                    offset=node.offset,
                    key=name,
                    kind="variable",
                )
            )
        return
    for child in node.args:
        _walk(child, language, index, grammar, field, expected_types, found, issues)


def _visit_reference(
    reference: Reference,
    language: LanguageGrammar,
    index: Mapping[str, RegistryTarget],
    grammar: Grammar,
    field: str | None,
    expected_types: Mapping[str, Sequence[str]] | None,
    found: list[ResolvedReference],
    issues: list[ResolveIssue],
) -> None:
    spec = language.reference_kind(reference.kind)
    if spec is None:
        issues.append(
            ResolveIssue(
                code=CODE_UNRESOLVED_REFERENCE,
                message=_locate(
                    field,
                    f"the {reference.kind} reference is not part of the "
                    f"{language.name} language in grammar {grammar.name}",
                ),
                offset=reference.start,
                kind=reference.kind,
            )
        )
        return
    # A name the grammar reserves (``V#{eventDate}``) is a built-in, never a Registry
    # object: the same spelling registered as a variable stays a variable.
    if spec.key_source == KEY_VARIABLE_OR_REGISTRY and reference.name in language.variables:
        found.append(
            ResolvedReference(
                reference=reference,
                key=reference.name,
                kind=reference.kind,
                target=None,
                span=_span(reference, reference.segments[-1]),
                replacement=None,
            )
        )
        return
    hit = _match(reference, index)
    if hit is None:
        issues.append(
            ResolveIssue(
                code=CODE_UNRESOLVED_REFERENCE,
                message=_locate(
                    field,
                    f"{reference.name!r} is not registered as a target of a "
                    f"{reference.kind} reference",
                ),
                offset=reference.start,
                key=reference.name,
                kind=reference.kind,
            )
        )
        return
    key, segment, target = hit
    narrowing = expected_types.get(reference.kind) if expected_types is not None else None
    allowed = tuple(narrowing) if narrowing else spec.allowed_types
    if allowed and target.dhis2_type not in allowed:
        issues.append(
            ResolveIssue(
                code=CODE_REFERENCE_TYPE_MISMATCH,
                message=_locate(
                    field,
                    f"{key!r} is a {target.dhis2_type}, but a {reference.kind} reference "
                    f"accepts {_joined(allowed)}",
                ),
                offset=reference.start,
                key=key,
                kind=reference.kind,
            )
        )
        return
    status = _status(target)
    if status is RegistryEntryStatus.PROPOSED:
        issues.append(
            ResolveIssue(
                code=CODE_REFERENCE_NOT_APPROVED,
                message=_locate(
                    field,
                    f"{key!r} is still PROPOSED; only an approved Registry entry may be "
                    f"referenced (BP-VAL-031)",
                ),
                offset=reference.start,
                key=key,
                kind=reference.kind,
            )
        )
        return
    if status is None:
        issues.append(
            ResolveIssue(
                code=CODE_REFERENCE_STATUS_UNKNOWN,
                message=_locate(
                    field, f"{key!r} carries the unknown Registry status {target.status!r}"
                ),
                offset=reference.start,
                key=key,
                kind=reference.kind,
            )
        )
        return
    if not target.uid:
        issues.append(
            ResolveIssue(
                code=CODE_UID_NOT_ASSIGNED,
                message=_locate(
                    field, f"{key!r} has no UID assigned in this Blueprint version"
                ),
                offset=reference.start,
                key=key,
                kind=reference.kind,
            )
        )
        return
    found.append(
        ResolvedReference(
            reference=reference,
            key=key,
            kind=reference.kind,
            target=target,
            span=_span(reference, segment),
            replacement=target.uid,
        )
    )


def _match(
    reference: Reference, index: Mapping[str, RegistryTarget]
) -> tuple[str, SegmentSpan, RegistryTarget] | None:
    """The longest registered dotted prefix wins, so ``#{de.coc}`` also works for a
    businessKey that itself contains a dot."""
    for key, segment in reference.key_candidates():
        target = index.get(key)
        if target is not None:
            return key, segment, target
    return None


def _span(reference: Reference, through: SegmentSpan) -> tuple[int, int]:
    return (reference.segments[0].start, through.end)


def _status(target: RegistryTarget) -> RegistryEntryStatus | None:
    try:
        return RegistryEntryStatus(target.status)
    except ValueError:
        return None


def _locate(field: str | None, message: str) -> str:
    return message if field is None else f"{field}: {message}"


def _joined(values: Sequence[str]) -> str:
    return " or ".join(f"{value!r}" for value in sorted(values))
