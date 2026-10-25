"""PlantUML input parser — use cases, actors, entity/class blocks (AI-INP-002, AI-INP-005).

PlantUML is the second deterministic text-diagram format of AI-INP-002, so this module
imports nothing from :mod:`factory_intelligence.llm`: AI-INP-003 / AI-BR-02 hold by
construction.  Like :mod:`.mermaid` it reads line by line and records the 1-based source
line of every fact (``Location.line(n)``), because an AI-INP-009 conflict is only listable
if the parser reports where each side said it.

Category assignment, stated so a reviewer can disagree in one place:

===================================  ==========================  =========================
source construct                     NRR category                why
===================================  ==========================  =========================
``actor "Doctor" as doc``            ``PARTICIPANT``             参与者与角色
``usecase "…" as UC1`` / ``(Use)``   ``USE_CASE``                Use Case
``class``/``entity``/``object``/…    ``BUSINESS_ENTITY``         业务实体与属性
``A --> B``                          ``BUSINESS_PROCESS``        业务流程 (an interaction)
``A ..> B : <<include>>``            ``BUSINESS_RULE``           a dependency is a rule
``A "1" -- "0..*" B``                ``BUSINESS_RULE``           multiplicity = constraint
label that says nothing              ``OPEN_QUESTION``           AI-INP-008, never a guess
===================================  ==========================  =========================

``!theme``, ``skinparam``, ``'`` comments and the other presentation directives are
ignored, as required: they state no business fact, and turning them into entries would
inflate the NRR with noise a human then has to reject.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import DataClassification

from factory_intelligence.enums import NrrStatus, ParserType, QuestionSeverity
from factory_intelligence.parsing.base import (
    Fragment,
    Location,
    NrrCategory,
    NrrEntry,
    ParseError,
    business_key,
    disambiguate,
)
from factory_intelligence.parsing.textdiagrams.mermaid import AMBIGUOUS_RELATIONSHIP_LABELS

#: Registered in ``factory_intelligence.registries.AI_ERROR_CODES`` (00 doc 6.5).
INPUT_UNREADABLE = "AI_PARSER_INPUT_INVALID"

#: Directives that carry no business fact and are therefore skipped, not asked about.
IGNORED_DIRECTIVES: tuple[str, ...] = (
    "!theme",
    "!include",
    "!define",
    "!procedure",
    "!$",
    "!pragma",
    "!unprototype",
    "skinparam",
    "title",
    "header",
    "footer",
    "caption",
    "legend",
    "endlegend",
    "note",
    "autonote",
    "hnote",
    "box",
    "endbox",
    "package",
    "namespace",
    "separator",
    "newpage",
    "create",
    "destroy",
    "hide",
    "show",
    "left to right",
    "top to bottom",
)

DECLARABLE_KINDS: tuple[str, ...] = (
    "actor",
    "usecase",
    "class",
    "entity",
    "interface",
    "object",
    "enum",
    "database",
)
ENTITY_KINDS = frozenset({"class", "entity", "object", "database", "enum", "interface"})
ACTOR_KINDS = frozenset({"actor"})
USE_CASE_KINDS = frozenset({"usecase"})

#: Field key markers PlantUML writes after a type, in ``{ … }`` or ``<< … >>``.
FIELD_KEYS = frozenset({"PK", "FK", "UK", "NOT NULL", "NULL", "AUTOINCREMENT"})

_KINDS_PATTERN = "|".join(DECLARABLE_KINDS)
_BLOCK_OPEN = re.compile(
    rf"^\s*(?P<kind>{_KINDS_PATTERN})\s+(?P<rest>[^{{]*?)\s*\{{\s*$", re.IGNORECASE
)
_DECLARATION = re.compile(rf"^\s*(?P<kind>{_KINDS_PATTERN})\s+(?P<rest>.+?)\s*$", re.IGNORECASE)
_BLOCK_CLOSE = re.compile(r"^\s*\}\s*$")
_AS_ALIAS = re.compile(
    r'^(?P<name>"[^"]*"|[A-Za-z_][A-Za-z0-9_.:]*)'
    r"(?:\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_.:]*))?"
    r"\s*(?:<<\s*(?P<stereotype>[^>]*?)\s*>>)?\s*$"
)
_PAREN_USECASE = re.compile(
    r"^\s*\(\s*(?P<name>[^)]+?)\s*\)(?:\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_.:]*))?\s*$"
)
_BRACKET_CLASS = re.compile(
    r"^\s*\[\s*(?P<name>[^\]]+?)\s*\](?:\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_.:]*))?\s*$"
)
_STEREOTYPE = re.compile(r"<<\s*(?P<stereotype>[^>]*?)\s*>>")
_BRACED_KEYS = re.compile(r"\{\s*(?P<keys>[^}]*)\}")
_MULTIPLICITY = re.compile(r'"([^"]*)"')
_ARROW_IN = re.compile(r"(?:-+>|\:+>|\.\.+>|-{2,}|\.{2,})")
_RELATIONSHIP = re.compile(
    r"^\s*(?P<left>.+?)\s*(?P<kind>--+>|\.\.>+|-{2,}|\.{2,})\s*(?P<right>.+?)\s*"
    r"(?::\s*(?P<label>.*?))?\s*$"
)
_STARTUML = re.compile(r"^\s*@start\w*\b", re.IGNORECASE)
_ENDUML = re.compile(r"^\s*@end\w*\b", re.IGNORECASE)


@dataclass
class _Symbol:
    """A declared thing: an actor, a use case or a class/entity block."""

    identifier: str
    display: str
    kind: str
    line: int
    stereotype: str | None = None
    fields: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Link:
    """One relationship line, kept aside so declarations are emitted before links."""

    line: int
    left: str
    right: str
    arrow: str
    label: str
    left_multiplicity: str | None
    right_multiplicity: str | None


class PlantUMLParser:
    """``@startuml``/``@enduml`` use cases, actors and entity/class blocks → NRR fragment."""

    parser_type: ParserType = ParserType.PLANTUML
    parser_version: str = "1.0.0"

    def parse(
        self,
        content: bytes,
        *,
        source_artifact_id: str,
        classification: DataClassification,
        options: Mapping[str, Any],
    ) -> Fragment:
        fragment = Fragment(
            parser_type=self.parser_type,
            parser_version=self.parser_version,
            source_artifact_id=source_artifact_id,
            classification=classification,
        )
        text = _decode(content)
        fragment.notes.update(_record_notes(content, options))
        keys: list[str] = []

        def emit(
            category: NrrCategory,
            label: str,
            statement: str,
            location: Location,
            *,
            payload: dict[str, Any] | None = None,
            actors: tuple[str, ...] = (),
            questions: tuple[dict[str, Any], ...] = (),
            rationale: str | None = None,
        ) -> None:
            entry = NrrEntry(
                category=category,
                business_key=disambiguate(keys, business_key(category, label), seed=str(location)),
                statement=statement,
                location=location,
                confidence=1.0,
                status=NrrStatus.CONFIRMED,
                source_artifact_id=source_artifact_id,
                rationale=rationale,
                actors=actors,
                open_questions=questions,
                payload=payload or {},
            )
            keys.append(entry.business_key)
            fragment.entries.append(entry)

        symbols: dict[str, _Symbol] = {}
        links: list[_Link] = []
        _read(text, symbols, links, fragment)
        if not symbols and not links:
            raise ParseError(INPUT_UNREADABLE, "no PlantUML declaration or relationship was found")

        for symbol in _declared_symbols(symbols):
            emit(
                _category_of(symbol),
                symbol.display,
                _declaration_statement(symbol),
                Location.line(symbol.line),
                payload=_declaration_payload(symbol),
            )
        for link in links:
            _emit_link(link, symbols, emit, fragment)
        return fragment


def _declared_symbols(symbols: Mapping[str, _Symbol]) -> list[_Symbol]:
    """One entry per declaration, even though ``_declare`` indexes a symbol by both its
    alias and its display name."""
    seen: set[int] = set()
    out: list[_Symbol] = []
    for symbol in symbols.values():
        if id(symbol) not in seen:
            seen.add(id(symbol))
            out.append(symbol)
    return sorted(out, key=lambda item: (item.line, item.identifier))


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(
            INPUT_UNREADABLE, f"the PlantUML source is not UTF-8 text: {exc}"
        ) from exc


def _record_notes(content: bytes, options: Mapping[str, Any]) -> dict[str, Any]:
    """The AI-INP-004 fields a parse record carries besides parserType/parserVersion."""
    notes: dict[str, Any] = {
        "inputHash": "sha256:" + hashlib.sha256(content).hexdigest(),
        "inputBytes": len(content),
        "usedModel": False,
    }
    if options:
        notes["options"] = {str(key): _plain(value) for key, value in sorted(options.items())}
    return notes


def _plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("'")


def _is_ignored(line: str) -> bool:
    lowered = line.strip().lower()
    return any(lowered.startswith(prefix) for prefix in IGNORED_DIRECTIVES)


def _read(text: str, symbols: dict[str, _Symbol], links: list[_Link], fragment: Fragment) -> None:
    current: _Symbol | None = None
    diagrams = 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or _is_comment(line):
            continue
        if _STARTUML.match(line):
            diagrams += 1
            continue
        if _ENDUML.match(line):
            current = None
            continue
        if _is_ignored(line):
            continue
        if current is not None:
            if _BLOCK_CLOSE.match(line):
                current = None
                continue
            current.fields.append(_field_of(line, number, current.display))
            continue
        opened = _BLOCK_OPEN.match(line)
        if opened:
            name, alias, stereotype = _declaration_parts(opened.group("rest"))
            current = _declare(symbols, opened.group("kind"), name, alias, stereotype, number)
            continue
        declared = _DECLARATION.match(line)
        if declared is not None and not _ARROW_IN.search(declared.group("rest")):
            name, alias, stereotype = _declaration_parts(declared.group("rest"))
            _declare(symbols, declared.group("kind"), name, alias, stereotype, number)
            continue
        usecase = _PAREN_USECASE.match(line)
        if usecase:
            _declare(symbols, "usecase", usecase.group("name"), usecase.group("alias"), None, number)
            continue
        klass = _BRACKET_CLASS.match(line)
        if klass:
            _declare(symbols, "class", klass.group("name"), klass.group("alias"), None, number)
            continue
        relationship = _RELATIONSHIP.match(line)
        if relationship:
            links.append(_link_of(relationship, number))
            continue
        fragment.notes["unparsedLines"] = int(fragment.notes.get("unparsedLines", 0)) + 1
    fragment.notes["diagrams"] = diagrams
    fragment.notes["declarations"] = len(_declared_symbols(symbols))
    fragment.notes["relationships"] = len(links)


def _declaration_parts(rest: str) -> tuple[str, str | None, str | None]:
    match = _AS_ALIAS.match(rest.strip())
    if match is None:
        return _unquote(rest.strip()), None, None
    return (
        _unquote(match.group("name")),
        match.group("alias"),
        (match.group("stereotype") or "").strip() or None,
    )


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _declare(
    symbols: dict[str, _Symbol],
    kind: str,
    name: str,
    alias: str | None,
    stereotype: str | None,
    number: int,
) -> _Symbol:
    normalised = kind.lower()
    identifier = alias or name
    existing = symbols.get(identifier)
    if existing is not None:
        #: a block opened later for the same symbol enriches it; the fact was first read
        #: where it was first declared, and AI-INP-009 needs one location per fact
        if stereotype:
            existing.stereotype = stereotype
        return existing
    symbol = _Symbol(
        identifier=identifier, display=name, kind=normalised, line=number, stereotype=stereotype
    )
    symbols[identifier] = symbol
    if name != identifier:
        #: a relationship may refer to either the alias or the display name
        symbols.setdefault(name, symbol)
    return symbol


def _field_of(line: str, number: int, owner: str) -> dict[str, Any]:
    """A class/entity field: ``-id : Long``, ``String name``, ``id : uuid {pk}``."""
    body = line.strip()
    visibility: str | None = None
    if body[:1] in {"-", "+", "#", "~"}:
        visibility = body[0]
        body = body[1:].strip()
    keys: list[str] = []
    stereotype = _stereotype_of(body)
    if stereotype:
        keys.extend(_keys_of(stereotype))
        body = _STEREOTYPE.sub("", body).strip()
    braced = _BRACED_KEYS.search(body)
    if braced:
        keys.extend(_keys_of(braced.group("keys")))
        body = body[: braced.start()].strip()
    body = body.rstrip(";").strip()
    name_part, separator, type_part = body.partition(":")
    if separator:
        field_name, field_type = name_part.strip(), type_part.strip()
    else:
        pieces = name_part.split()
        #: ``type name`` and a bare ``name`` are both written; the ambiguous spelling is
        #: recorded with the last word as the name and the rest as the type
        if len(pieces) > 1:
            field_type, field_name = " ".join(pieces[:-1]), pieces[-1]
        else:
            field_type, field_name = None, name_part.strip()
    return {
        "name": _unquote(field_name),
        "type": _unquote(field_type) if field_type else None,
        "keys": keys,
        "visibility": visibility,
        "line": number,
        "entity": owner,
    }


def _stereotype_of(text: str) -> str | None:
    match = _STEREOTYPE.search(text)
    return match.group("stereotype").strip() if match else None


def _keys_of(value: str) -> list[str]:
    out: list[str] = []
    for token in re.split(r",", value.strip()):
        candidate = token.strip().upper()
        if candidate and candidate in FIELD_KEYS and candidate not in out:
            out.append(candidate)
    return out


def _category_of(symbol: _Symbol) -> NrrCategory:
    if symbol.kind in ACTOR_KINDS:
        return NrrCategory.PARTICIPANT
    if symbol.kind in USE_CASE_KINDS:
        return NrrCategory.USE_CASE
    return NrrCategory.BUSINESS_ENTITY


def _declaration_statement(symbol: _Symbol) -> str:
    if symbol.kind in ACTOR_KINDS:
        role = f" (role '{symbol.stereotype}')" if symbol.stereotype else ""
        return f"Actor '{symbol.display}' takes part in the process{role}."
    if symbol.kind in USE_CASE_KINDS:
        return f"Use case '{symbol.display}' is performed by the actors linked to it."
    if symbol.fields:
        rendered = ", ".join(f"{item['name']} ({_describe_type(item)})" for item in symbol.fields)
        return f"Business entity '{symbol.display}' has attributes: {rendered}."
    return f"Business entity '{symbol.display}' appears in the data model."


def _declaration_payload(symbol: _Symbol) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": symbol.kind, "name": symbol.display}
    if symbol.stereotype:
        payload["stereotype"] = symbol.stereotype
    if symbol.fields:
        payload["attributes"] = list(symbol.fields)
    return payload


def _describe_type(attribute: Mapping[str, Any]) -> str:
    parts = [str(attribute["type"])] if attribute.get("type") else []
    parts.extend(str(key) for key in attribute.get("keys") or [])
    return ", ".join(parts) if parts else "type not stated"


def _link_of(match: re.Match[str], number: int) -> _Link:
    left, left_multiplicity = _side(match.group("left"))
    right, right_multiplicity = _side(match.group("right"))
    return _Link(
        line=number,
        left=left,
        right=right,
        arrow=match.group("kind"),
        label=_unquote((match.group("label") or "").strip()),
        left_multiplicity=left_multiplicity,
        right_multiplicity=right_multiplicity,
    )


def _side(text: str) -> tuple[str, str | None]:
    """Split a relationship side into the object's name and its quoted multiplicity."""
    stripped = text.strip()
    quoted = _MULTIPLICITY.findall(stripped)
    remainder = _MULTIPLICITY.sub(" ", stripped)
    remainder = re.sub(
        rf"^(?:{_KINDS_PATTERN})\s+", "", remainder, count=1, flags=re.IGNORECASE
    )
    name = remainder.strip()
    if not name and quoted:
        return quoted[0], None
    return name, (quoted[-1] if quoted else None)


def _display(symbols: Mapping[str, _Symbol], identifier: str) -> str:
    symbol = symbols.get(identifier)
    return symbol.display if symbol else identifier


def _actor_keys(symbols: Mapping[str, _Symbol], *identifiers: str) -> tuple[str, ...]:
    out: list[str] = []
    for identifier in identifiers:
        symbol = symbols.get(identifier)
        if symbol is not None and symbol.kind in ACTOR_KINDS:
            candidate = business_key(NrrCategory.PARTICIPANT, symbol.display)
            if candidate not in out:
                out.append(candidate)
    return tuple(out)


def _pair_label(display_a: str, identifier_a: str, display_b: str, identifier_b: str) -> str:
    """A businessKey label that survives a CJK display name.

    ``business_key()`` keeps no characters from a Chinese label and falls back to a
    digest, which would make every edge of one diagram read "…to…"; the identifiers are
    used instead when that would happen.  Both spellings are pure functions of the input.
    """
    if _has_ascii_slug(display_a) or _has_ascii_slug(display_b):
        return f"{display_a} to {display_b}"
    return f"{identifier_a} to {identifier_b}"


def _has_ascii_slug(value: str) -> bool:
    ascii_only = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return any(character.isalnum() for character in ascii_only)


def _emit_link(
    link: _Link,
    symbols: Mapping[str, _Symbol],
    emit: Any,
    fragment: Fragment,
) -> None:
    location = Location.line(link.line)
    left = _display(symbols, link.left)
    right = _display(symbols, link.right)
    left_symbol = symbols.get(link.left)
    right_symbol = symbols.get(link.right)
    stereotype = _stereotype_of(link.label)
    actors = _actor_keys(symbols, link.left, link.right)
    payload: dict[str, Any] = {
        "from": link.left,
        "to": link.right,
        "fromLabel": left,
        "toLabel": right,
        "arrow": link.arrow,
        "label": link.label,
    }
    if link.left_multiplicity or link.right_multiplicity:
        payload["multiplicities"] = [link.left_multiplicity, link.right_multiplicity]
    if stereotype:
        payload["stereotype"] = stereotype
    if actors:
        payload["actors"] = list(actors)

    structural = "-" in link.arrow and not link.arrow.endswith(">")
    dependency = link.arrow.startswith(".")
    #: multiplicities carry the meaning a label might have lacked, so a declared
    #: ``Child "1" -- "0..*" Visit`` is a confirmed constraint rather than a question
    has_multiplicity = bool(link.left_multiplicity or link.right_multiplicity)
    #: an interaction needs no label to be a fact, but a label that *is* written and says
    #: nothing ("?", "关联") is an explicit ambiguity, so it is asked, never confirmed
    ambiguous = (
        (structural or dependency or bool(link.label))
        and not has_multiplicity
        and _is_ambiguous_label(link.label)
    )
    if ambiguous and stereotype is None:
        question = {
            "question": (
                f"The '{link.arrow}' link between '{left}' and '{right}' (line {link.line}) "
                "carries no label that says what connects them. What is the meaning of this "
                "link, and does it constrain the data or describe a step of the process?"
            ),
            "severity": QuestionSeverity.MEDIUM.value,
        }
        fragment.open_questions.append(question)
        emit(
            NrrCategory.OPEN_QUESTION,
            _pair_label(left, link.left, right, link.right),
            question["question"],
            location,
            payload=payload,
            actors=actors,
            questions=(question,),
            rationale="the diagram states a link but not its meaning (AI-INP-008)",
        )
        return

    if structural or dependency:
        if stereotype:
            statement = (
                f"Composition rule: '{left}' {stereotype} '{right}' (declared as "
                f"'{link.label}')."
            )
        elif structural and (link.left_multiplicity or link.right_multiplicity):
            statement = (
                f"Structural link: '{left}' (\"{link.left_multiplicity or 'not stated'}\") "
                f"relates to '{right}' (\"{link.right_multiplicity or 'not stated'}\")"
                + (f", labelled '{link.label}'." if link.label else ".")
            )
        else:
            statement = (
                f"'{left}' depends on '{right}'"
                + (f", labelled '{link.label}'." if link.label else " (no label was written).")
            )
        emit(
            NrrCategory.BUSINESS_RULE,
            _pair_label(left, link.left, right, link.right),
            statement,
            location,
            payload=payload,
            actors=actors,
            rationale="a dependency or a multiplicity constrains the model, so it is read as a rule",
        )
        return

    if left_symbol is not None and left_symbol.kind in ACTOR_KINDS:
        actor, other = left, right
    elif right_symbol is not None and right_symbol.kind in ACTOR_KINDS:
        actor, other = right, left
    else:
        actor, other = "", ""
    if actor:
        statement = (
            f"Actor '{actor}' interacts with '{other}'"
            + (f" as '{link.label}'." if link.label else ".")
        )
    else:
        statement = (
            f"Process step '{left}' leads to '{right}'"
            + (f" as '{link.label}'." if link.label else ".")
        )
    emit(
        NrrCategory.BUSINESS_PROCESS,
        _pair_label(left, link.left, right, link.right),
        statement,
        location,
        payload=payload,
        actors=actors,
    )


def _is_ambiguous_label(label: str) -> bool:
    text = _STEREOTYPE.sub("", label).strip().strip('"').strip()
    return not text or text.lower() in AMBIGUOUS_RELATIONSHIP_LABELS


__all__ = ["PlantUMLParser", "AMBIGUOUS_RELATIONSHIP_LABELS", "IGNORED_DIRECTIVES", "INPUT_UNREADABLE"]
