"""Mermaid input parser — ``erDiagram`` and ``flowchart``/``graph`` (AI-INP-002, AI-INP-005).

Mermaid is a *deterministic* format (AI-INP-002 row "文本图"), so this module never
imports the LLM layer at all: AI-INP-003 / AI-BR-02 are satisfied by construction, not
by discipline.  What it does instead is read the source line by line and record, for
every fact, the 1-based line it was read from — ``Location.line(n)`` — because AI-INP-009
can only list "各来源的位置" of a conflict if the parser never invents a coordinate.
A fact whose line is unknown is not emitted; it becomes an open question.

The category assignment is a deliberate reading of AI-INP-005's thirteen categories and
is stated here so a reviewer can disagree with it in one place:

=============================  ==========================================  ==========
source construct               NRR category                                why
=============================  ==========================================  ==========
``CUSTOMER { … }`` entity      ``BUSINESS_ENTITY``                         业务实体与属性
ER relationship (labelled)     ``BUSINESS_RULE``                           a cardinality is a
                                                                           constraint on data
ER relationship (vague label)  ``OPEN_QUESTION``                           AI-INP-008: the
                                                                           parser must not guess
flowchart edge / lone node     ``BUSINESS_PROCESS``                        业务流程与状态
edge out of a ``{decision}``   ``BUSINESS_RULE``                           业务规则 (branch)
``subgraph`` / ``actor``       ``PARTICIPANT``                             参与者与角色
=============================  ==========================================  ==========

Anything else that looks like a diagram header (``sequenceDiagram``, ``journey``, …) is
out of this parser's documented scope: the fragment keeps its structure, records the
diagram type in ``notes`` and asks (``needs_review`` with one open question) rather than
returning the silently empty NRR AI-INP-003a forbids.

Purity (the ``Parser`` protocol): no clock, no randomness, no database, no network, no
filesystem.  The same bytes and the same options give byte-identical entries, which is
what AI-INP-010 / AC-REQ-01 test.
"""

from __future__ import annotations

import hashlib
import re
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

#: Registered in ``factory_intelligence.registries.AI_ERROR_CODES`` (00 doc 6.5): the
#: bytes are not UTF-8 text at all, so no fact — and therefore no location — can be read.
INPUT_UNREADABLE = "AI_PARSER_INPUT_INVALID"

#: Labels that say nothing about the relationship, so the parser may not pick a category
#: for them (AI-INP-008: an unresolved fact is a question, never a confirmed statement).
#: "has" is deliberately *absent*: with the cardinality the syntax already states, "has"
#: completes an ordinary one-to-many.  A "?" or "关联" completes nothing.
AMBIGUOUS_RELATIONSHIP_LABELS: frozenset[str] = frozenset(
    {
        "?",
        "??",
        "-",
        "--",
        "n/a",
        "na",
        "tbd",
        "todo",
        "unknown",
        "relate",
        "related",
        "relation",
        "association",
        "connect",
        "connected",
        "link",
        "关联",
        "关系",
        "属于",
    }
)

#: Lines inside a ``flowchart`` that style or bind events instead of stating a fact.
_STYLE_PREFIXES: tuple[str, ...] = (
    "classDef",
    "style ",
    "click ",
    "linkStyle",
    "direction ",
    "accTitle",
    "accDescr",
)

_ER_HEADER = re.compile(r"^\s*erDiagram\b", re.IGNORECASE)
_FLOW_HEADER = re.compile(r"^\s*(?P<kind>flowchart|graph)\b(?P<rest>.*)$", re.IGNORECASE)
_ANY_HEADER = re.compile(r"^\s*(?P<kind>[A-Za-z][\w-]*)\s*$")
_COMMENT = re.compile(r"%%.*$")
_QUOTED = r'"[^"]*"'
_NAME = rf"(?:{_QUOTED}|[A-Za-z_][A-Za-z0-9_\-]*)"
_ENTITY_BLOCK_OPEN = re.compile(rf"^\s*(?P<name>{_NAME})\s*\{{\s*$")
_BLOCK_CLOSE = re.compile(r"^\s*\}\s*$")
_ATTRIBUTE = re.compile(
    rf"^\s*(?P<type>[A-Za-z_][A-Za-z0-9_\-]*)\s+(?P<name>{_NAME})\s*"
    rf"(?P<keys>(?:\s*(?:PK|FK|UK))*)\s*(?P<comment>{_QUOTED})?\s*$"
)
_BARE_ATTRIBUTE = re.compile(rf"^\s*(?P<name>{_NAME})\s*$")
_CARD = r"(?:[|{}o]{1,2})"
_RELATIONSHIP = re.compile(
    rf"^\s*(?P<left>{_NAME})\s+(?P<lcard>{_CARD})\s*--\s*(?P<rcard>{_CARD})\s*"
    rf"(?P<right>{_NAME})\s*(?::\s*(?P<label>.*?))?\s*$"
)
_SUBGRAPH = re.compile(
    rf"^\s*subgraph\s+(?P<id>[A-Za-z0-9_\-]+)?\s*(?P<label>{_QUOTED}|\[[^\]]*\]|\([^\)]*\))?\s*$"
)
_SUBGRAPH_CLOSE = re.compile(r"^\s*end\s*$")
_ACTOR = re.compile(rf"^\s*actor\s+(?P<name>{_NAME})\s*$", re.IGNORECASE)
_ARROW = r"(?:<-->|-->|--x|--o|-\.->|-\.-|==>|---)"
_EDGE_SCAN = re.compile(rf"(?:{_ARROW})(?:\s*\|\s*(?P<label>[^|]*?)\s*\|)?")
_INLINE_LABEL = re.compile(r"(?:--|-->)\s+(?P<label>[^|\-]+?)\s+-->", re.IGNORECASE)
_NODE = re.compile(r"^\s*(?P<id>[A-Za-z0-9_\-]+)\s*(?P<shape>[\[\(\{{].*)?$")
_DECISION = re.compile(r"^(?P<id>[A-Za-z0-9_\-]+)\s*\{+\s*(?P<label>.*?)\s*\}+$")
_WRAPPED = re.compile(r"^(?P<id>[A-Za-z0-9_\-]+)\s*[\[\(\{{]+\s*(?P<label>.*?)\s*[\]\)\}}]+$")

#: Mermaid's six cardinality markers, read as a *set* of characters so the mirror-image
#: spellings (``o|`` on the left of ``--``, ``|o`` on the right) mean the same thing.
_CARDINALITY: Mapping[frozenset[str], str] = {
    frozenset("|"): "exactly one",
    frozenset("|o"): "zero or one",
    frozenset("o{"): "zero or more",
    frozenset("o}"): "zero or more",
    frozenset("|{"): "one or more",
    frozenset("|}"): "one or more",
}


@dataclass
class _Entity:
    """An ER entity, collected before it is emitted so attributes and relationships of the
    same entity end up in exactly one entry (AI-INP-005: one record per fact)."""

    name: str
    line: int
    attributes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Node:
    """A flowchart node: its id, the label written in the diagram and whether ``{…}``
    marked it as a decision."""

    id: str
    label: str
    line: int
    decision: bool = False
    group: str | None = None


class MermaidParser:
    """``erDiagram`` + ``flowchart``/``graph`` → NRR fragment."""

    parser_type: ParserType = ParserType.MERMAID
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
        lines = [(_strip_comment(line), number) for number, line in enumerate(text.splitlines(), 1)]
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
                #: deterministic extraction is a measured fact, never an estimate (AI-INP-010)
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

        body = [(line, number) for line, number in lines if line.strip()]
        if not body:
            raise ParseError(INPUT_UNREADABLE, "the Mermaid source is empty")
        kind = _diagram_kind(body[0][0])
        if kind == "er":
            _parse_er(body, emit, fragment)
        elif kind == "flow":
            _parse_flow(body, emit, fragment)
        else:
            _ask_about_diagram_type(body[0], kind, emit, fragment)
        return fragment


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(
            INPUT_UNREADABLE, f"the Mermaid source is not UTF-8 text: {exc}"
        ) from exc


def _record_notes(content: bytes, options: Mapping[str, Any]) -> dict[str, Any]:
    """The AI-INP-004 fields a parse record carries besides parserType/parserVersion.

    The hash is a pure function of the bytes, so recording it here keeps the purity rule
    while giving the caller the "输入产物 ID 与哈希" pair the SRS names.
    """
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


def _strip_comment(line: str) -> str:
    return _COMMENT.sub("", line).rstrip()


def _diagram_kind(first_line: str) -> str:
    if _ER_HEADER.match(first_line):
        return "er"
    if _FLOW_HEADER.match(first_line):
        return "flow"
    match = _ANY_HEADER.match(first_line)
    return match.group("kind").lower() if match else "unknown"


def _diagram_type_label(line: str) -> str:
    parts = line.strip().split()
    return parts[0] if parts else line.strip()


def _ask_about_diagram_type(
    header: tuple[str, int],
    kind: str,
    emit: Any,
    fragment: Fragment,
) -> None:
    line, number = header
    location = Location.line(number)
    diagram = _diagram_type_label(line)
    question = {
        "question": (
            f"Mermaid diagram type '{diagram}' (line {number}) is outside the deterministic "
            "parser's documented scope (erDiagram, flowchart, graph). Which business facts "
            "should be taken from it, and should it be re-supplied as one of those types?"
        ),
        "severity": QuestionSeverity.MEDIUM.value,
    }
    fragment.open_questions.append(question)
    fragment.needs_review = True
    fragment.review_reason = (
        f"the diagram declares type '{diagram}', which this parser reads without guessing"
    )
    fragment.notes["diagramType"] = diagram
    emit(
        NrrCategory.OPEN_QUESTION,
        f"diagram type {diagram}",
        question["question"],
        location,
        payload={"diagramType": diagram, "line": number},
        questions=(question,),
    )


def _quoted(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _parse_er(body: list[tuple[str, int]], emit: Any, fragment: Fragment) -> None:
    """Entities first, then relationships: an entity referenced only by a relationship
    still gets exactly one entry, at the line where it first appeared."""
    entities: dict[str, _Entity] = {}
    relationships: list[tuple[int, str, str, str, str, str]] = []
    inside: _Entity | None = None

    for line, number in body:
        if _ER_HEADER.match(line):
            continue
        if inside is not None:
            if _BLOCK_CLOSE.match(line):
                inside = None
                continue
            inside.attributes.append(_attribute(line, number, inside.name))
            continue
        block = _ENTITY_BLOCK_OPEN.match(line)
        if block:
            name = _quoted(block.group("name"))
            #: an entity declared in a block after it was named by a relationship keeps the
            #: line where it first appeared: the fact is one fact, read from one place
            entity = entities.setdefault(name, _Entity(name=name, line=number))
            inside = entity
            continue
        relationship = _RELATIONSHIP.match(line)
        if relationship:
            relationships.append(
                (
                    number,
                    _quoted(relationship.group("left")),
                    relationship.group("lcard"),
                    relationship.group("rcard"),
                    _quoted(relationship.group("right")),
                    _quoted(relationship.group("label") or ""),
                )
            )
            for name in (
                _quoted(relationship.group("left")),
                _quoted(relationship.group("right")),
            ):
                entities.setdefault(name, _Entity(name=name, line=number))
            continue
        fragment.notes["unparsedLines"] = int(fragment.notes.get("unparsedLines", 0)) + 1

    for entity in sorted(entities.values(), key=lambda item: (item.line, item.name)):
        emit(
            NrrCategory.BUSINESS_ENTITY,
            entity.name,
            _entity_statement(entity),
            Location.line(entity.line),
            payload={"entity": entity.name, "attributes": list(entity.attributes)},
        )
    for number, left, lcard, rcard, right, label in relationships:
        _emit_relationship(left, lcard, rcard, right, label, number, emit, fragment)


def _attribute(line: str, number: int, owner: str) -> dict[str, Any]:
    match = _ATTRIBUTE.match(line)
    if match:
        keys = [token for token in (match.group("keys") or "").split() if token]
        return {
            "name": _quoted(match.group("name")),
            "type": match.group("type"),
            "keys": keys,
            "comment": _quoted(match.group("comment")) if match.group("comment") else None,
            "line": number,
            "entity": owner,
        }
    bare = _BARE_ATTRIBUTE.match(line)
    name = _quoted(bare.group("name")) if bare else _quoted(line)
    return {
        "name": name,
        "type": None,
        "keys": [],
        "comment": None,
        "line": number,
        "entity": owner,
    }


def _entity_statement(entity: _Entity) -> str:
    if not entity.attributes:
        return f"Business entity '{entity.name}' appears in the data model."
    rendered = ", ".join(
        f"{item['name']} ({_describe_type(item)})" for item in entity.attributes
    )
    return f"Business entity '{entity.name}' has attributes: {rendered}."


def _describe_type(attribute: Mapping[str, Any]) -> str:
    parts = [str(attribute["type"])] if attribute.get("type") else []
    parts.extend(str(key) for key in attribute.get("keys") or [])
    if attribute.get("comment"):
        parts.append(f'"{attribute["comment"]}"')
    return ", ".join(parts) if parts else "type not stated"


def _cardinality(token: str) -> str:
    return _CARDINALITY.get(frozenset(token), "an unspecified number of")


def _emit_relationship(
    left: str,
    lcard: str,
    rcard: str,
    right: str,
    label: str,
    number: int,
    emit: Any,
    fragment: Fragment,
) -> None:
    location = Location.line(number)
    payload = {
        "from": left,
        "to": right,
        "leftCardinality": lcard,
        "rightCardinality": rcard,
        "label": label,
    }
    if _is_ambiguous(label):
        question = {
            "question": (
                f"The relationship between '{left}' and '{right}' (line {number}) is labelled "
                f"'{label}', which does not state what connects them. What is the meaning of "
                "this link — ownership, reference or workflow?"
            ),
            "severity": QuestionSeverity.MEDIUM.value,
        }
        fragment.open_questions.append(question)
        emit(
            NrrCategory.OPEN_QUESTION,
            f"{left} to {right}",
            question["question"],
            location,
            payload=payload,
            questions=(question,),
            rationale="the diagram states a link but not its meaning (AI-INP-008)",
        )
        return
    statement = (
        f"'{left}' relates to '{right}': {_cardinality(lcard)} '{left}' to "
        f"{_cardinality(rcard)} '{right}', labelled '{label}'."
    )
    emit(
        NrrCategory.BUSINESS_RULE,
        f"{left} {label} {right}",
        statement,
        location,
        payload=payload,
        rationale="a cardinality is a constraint on the data, so it is read as a rule",
    )


def _is_ambiguous(label: str) -> bool:
    text = label.strip().strip('"').strip()
    return not text or text.lower() in AMBIGUOUS_RELATIONSHIP_LABELS


def _parse_flow(body: list[tuple[str, int]], emit: Any, fragment: Fragment) -> None:
    nodes: dict[str, _Node] = {}
    edges: list[tuple[int, str, str, str]] = []
    used_arrows: list[str] = []
    group: str | None = None
    header = True

    for line, number in body:
        if header:
            header = False
            continue
        if any(line.strip().startswith(prefix) for prefix in _STYLE_PREFIXES):
            continue
        if _SUBGRAPH_CLOSE.match(line):
            group = None
            continue
        subgraph = _SUBGRAPH.match(line)
        if subgraph:
            label = _label_of(subgraph.group("label")) or _label_of(subgraph.group("id"))
            if not label:
                continue
            group = label
            continue
        actor = _ACTOR.match(line)
        if actor:
            _remember(nodes, _label_of(actor.group("name")), number, group)
            continue
        normalised = _INLINE_LABEL.sub(lambda match: f"-->|{match.group('label')}|", line)
        arrow_positions = list(_EDGE_SCAN.finditer(normalised))
        if not arrow_positions:
            #: a lone node statement: ``A[Start]`` on its own line
            node = _node(normalised, number, group)
            if node is not None:
                _remember(nodes, node.id, number, group, node.label, node.decision)
            else:
                fragment.notes["unparsedLines"] = int(fragment.notes.get("unparsedLines", 0)) + 1
            continue
        segments = _segments(normalised, arrow_positions)
        for index, match in enumerate(arrow_positions):
            label = (match.group("label") or "").strip()
            used_arrows.append(match.group(0).split("|", 1)[0].strip())
            source_node = _node(segments[index], number, group)
            target_node = _node(segments[index + 1], number, group)
            if source_node is None or target_node is None:
                fragment.notes["unparsedLines"] = (
                    int(fragment.notes.get("unparsedLines", 0)) + 1
                )
                continue
            _remember(nodes, source_node.id, number, group, source_node.label, source_node.decision)
            _remember(nodes, target_node.id, number, group, target_node.label, target_node.decision)
            edges.append((number, source_node.id, target_node.id, label))

    for label in _group_labels(body):
        emit(
            NrrCategory.PARTICIPANT,
            label,
            f"Participant or role grouping '{label}' owns the steps drawn inside it.",
            Location.line(_first_group_line(body, label)),
            payload={"group": label, "source": "subgraph"},
        )
    for node in sorted(nodes.values(), key=lambda item: (item.line, item.id)):
        if node.decision or any(edge[1] == node.id or edge[2] == node.id for edge in edges):
            continue
        emit(
            NrrCategory.BUSINESS_PROCESS,
            node.label or node.id,
            f"Process step '{node.label or node.id}'.",
            Location.line(node.line),
            payload={"node": node.id, "label": node.label, "group": node.group},
            actors=_actors_for(node.group),
        )
    for number, source, target, label in edges:
        _emit_edge(nodes, source, target, label, number, emit, fragment)
    if used_arrows:
        fragment.notes["arrows"] = sorted(set(used_arrows))


def _emit_edge(
    nodes: Mapping[str, _Node],
    source: str,
    target: str,
    label: str,
    number: int,
    emit: Any,
    fragment: Fragment,
) -> None:
    location = Location.line(number)
    src = nodes[source]
    dst = nodes[target]
    group = src.group or dst.group
    payload = {
        "from": src.id,
        "to": dst.id,
        "fromLabel": src.label,
        "toLabel": dst.label,
        "branch": label,
        "group": group,
    }
    if src.decision:
        question: dict[str, Any] | None = None
        if _is_ambiguous(label):
            question = {
                "question": (
                    f"The branch from decision '{src.label or src.id}' to "
                    f"'{dst.label or dst.id}' (line {number}) has no condition written on it. "
                    "When is this branch taken?"
                ),
                "severity": QuestionSeverity.MEDIUM.value,
            }
            fragment.open_questions.append(question)
        statement = (
            f"Decision '{src.label or src.id}' takes the branch to '{dst.label or dst.id}'"
            + (f" when '{label}'." if question is None else " (condition not stated).")
        )
        emit(
            NrrCategory.BUSINESS_RULE if question is None else NrrCategory.OPEN_QUESTION,
            f"{src.label or src.id} to {dst.label or dst.id}",
            statement,
            location,
            payload=payload,
            actors=_actors_for(group),
            questions=(question,) if question else (),
        )
        return
    emit(
        NrrCategory.BUSINESS_PROCESS,
        f"{src.label or src.id} to {dst.label or dst.id}",
        f"Process step '{src.label or src.id}' leads to '{dst.label or dst.id}'"
        + (f" via '{label}'." if label else "."),
        location,
        payload=payload,
        actors=_actors_for(group),
    )


def _actors_for(group: str | None) -> tuple[str, ...]:
    return (business_key(NrrCategory.PARTICIPANT, group),) if group else ()


def _remember(
    nodes: dict[str, _Node],
    identifier: str,
    number: int,
    group: str | None,
    label: str = "",
    decision: bool = False,
) -> _Node:
    node = nodes.get(identifier)
    if node is None:
        node = _Node(id=identifier, label=label, line=number, decision=decision, group=group)
        nodes[identifier] = node
        return node
    #: first mention wins for the line (the fact was first read there), but a later
    #: mention carrying a label or a decision marker enriches the same node
    if label and not node.label:
        node.label = label
    node.decision = node.decision or decision
    node.group = node.group or group
    return node


def _node(text: str, number: int, group: str | None) -> _Node | None:
    stripped = text.strip()
    if not stripped:
        return None
    decision = _DECISION.match(stripped)
    if decision:
        return _Node(
            id=decision.group("id"),
            label=_quoted(_clean_label(decision.group("label"))),
            line=number,
            decision=True,
            group=group,
        )
    wrapped = _WRAPPED.match(stripped)
    if wrapped:
        return _Node(
            id=wrapped.group("id"),
            label=_quoted(_clean_label(wrapped.group("label"))),
            line=number,
            group=group,
        )
    plain = _NODE.match(stripped)
    if plain is not None and plain.group("shape") is None:
        #: a bare node id, already mentioned or standing alone
        return _Node(id=plain.group("id"), label="", line=number, group=group)
    return None


def _clean_label(label: str | None) -> str:
    text = (label or "").strip()
    #: ``:::cssClass`` and an HTML ``<br/>`` are presentation, not fact
    text = re.sub(r":::[\w\-]+$", "", text)
    return text.replace("<br/>", " ").strip()


def _segments(line: str, arrows: list[re.Match[str]]) -> list[str]:
    out: list[str] = []
    start = 0
    for arrow in arrows:
        out.append(line[start : arrow.start()].strip())
        start = arrow.end()
    out.append(line[start:].strip())
    return out


def _label_of(text: str | None) -> str:
    """Strip the ``[ … ]`` / ``( … )`` / ``" … "`` wrapper Mermaid puts around a label."""
    raw = (text or "").strip()
    if len(raw) >= 2 and raw[0] in "[(" and raw[-1] in "])":
        raw = raw[1:-1]
    return _quoted(raw)


def _group_labels(body: list[tuple[str, int]]) -> list[str]:
    seen: list[str] = []
    for line, _number in body:
        match = _SUBGRAPH.match(line)
        if not match:
            continue
        label = _label_of(match.group("label")) or _label_of(match.group("id"))
        if label and label not in seen:
            seen.append(label)
    return seen


def _first_group_line(body: list[tuple[str, int]], label: str) -> int:
    for line, number in body:
        match = _SUBGRAPH.match(line)
        if match and (_label_of(match.group("label")) or _label_of(match.group("id"))) == label:
            return number
    return 1


__all__ = ["MermaidParser", "AMBIGUOUS_RELATIONSHIP_LABELS", "INPUT_UNREADABLE"]
