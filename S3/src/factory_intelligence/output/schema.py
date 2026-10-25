"""Output schemas: the envelope, and the Blueprint item schemas drawn from S2.

AI-ASM-03 says the S2 section schema *is* the constraint on an agent's ``patch`` part, and
this module is where that is made true.  Two halves, deliberately separate:

1. :func:`envelope_schema` — the fixed top-level shape of AI-RT-010 (summary, decisions,
   patch, questions, confidence) plus the operation shape.  Authored in code because it is
   the runtime's contract, not a prompt's, and it is identical for every agent.
2. :func:`section_containers` — the *content* constraint, read from S2 at runtime.  A
   "container" is an array in a section whose items require ``businessKey``; that is
   derived by walking the schema rather than listed by hand, which is what makes it survive
   a new Blueprint section: the domain section has two containers (``entities`` and
   ``relationships``) and would break any hand-written ``section -> single container`` map.

Validating an entry against the section's own item schema — instead of against an S3 copy —
is the difference between S3's verdict and S2's agreeing by construction and merely
overlapping by luck.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.api import Section, get_section_schema

#: The identity field of a container entry.  S2 spells it differently per section —
#: ``businessKey`` for most, ``decisionId`` for architecture, ``intentKey`` for an API
#: intent, ``pageKey`` for a page — and S0's rule that only an owner may read its schema
#: means S3 cannot import S2's layout table to learn the list.  Order is preference, and
#: ``test_every_patchable_section_exposes_a_container`` is what catches a new spelling.
KEY_FIELDS: tuple[str, ...] = (
    "businessKey",
    "decisionId",
    "intentKey",
    "pageKey",
    "operationKey",
    "roleKey",
    "levelKey",
)
#: ``{op, path, value}`` — RFC 6902, the form S2 parses (``factory_blueprint/patch``).
OPERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["op", "path"],
    "additionalProperties": False,
    "properties": {
        "op": {"type": "string", "enum": ["add", "replace", "remove"]},
        "path": {"type": "string", "pattern": r"^/[A-Za-z][A-Za-z0-9_./\[\]-]*$"},
        "value": {},
        "from": {"type": "string"},
    },
}
_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "text"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 64},
        "text": {"type": "string", "minLength": 1, "maxLength": 2000},
        "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        #: SINGLE renders as a radio group, MULTIPLE as a checkbox group.  The suggested
        #: answers are what a reviewer can choose between; the console adds the free-text
        #: "Other" option itself, so the model must not spend a suggestion on it.
        "answerMode": {"type": "string", "enum": ["SINGLE", "MULTIPLE"]},
        "affects": {"type": "array", "items": {"type": "string", "maxLength": 255}, "maxItems": 40},
        "blocking": {"type": "boolean"},
        "suggestions": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["id"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "text": {"type": "string", "maxLength": 1000},
                    "rationale": {"type": "string", "maxLength": 2000},
                    #: the option the agent proposes when the question is single-select
                    "recommended": {"type": "boolean"},
                    # the SRS spells these two in camelCase, in the model's own schema
                    "changesUid": {"type": "boolean"},
                    "regenerates": {"type": "boolean"},
                },
            },
        },
    },
}
_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id"],
    "additionalProperties": True,
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 64},
        "subject": {"type": "string", "maxLength": 255},
        "selected": {"type": "object"},
        "alternatives": {"type": "array", "maxItems": 20},
        "rationale": {"type": "string", "maxLength": 4000},
        "risks": {"type": "array", "maxItems": 20},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "impactLevel": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
    },
}


def envelope_schema(*, max_operations: int = 200) -> dict[str, Any]:
    """AI-RT-010's top level, as a Draft 2020-12 schema.

    ``additionalProperties: false`` on the envelope is the cheapest defence there is
    against AI-RT-031: an injection that persuades the model to add a ``"sideEffects"`` or
    ``"instructions"`` key is refused by the shape, before any content rule runs.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "agent-output",
        "type": "object",
        "required": ["summary", "questions", "decisions", "confidence"],
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
            "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "decisions": {"type": "array", "maxItems": 200, "items": _DECISION_SCHEMA},
            "questions": {"type": "array", "maxItems": 40, "items": _QUESTION_SCHEMA},
            "patch": {
                "type": ["object", "null"],
                "required": ["baseVersion", "operations"],
                "additionalProperties": False,
                "properties": {
                    "baseVersion": {"type": "string", "minLength": 1, "maxLength": 32},
                    "operations": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": max_operations,
                        "items": OPERATION_SCHEMA,
                    },
                },
            },
        },
    }


@dataclass(frozen=True)
class Container:
    """One patchable array inside a Blueprint section.

    ``definitions`` carries the section document's definition block alongside the item
    schema, because an extracted item still points at ``#/definitions/businessKey`` and a
    validator built from the fragment alone cannot resolve it.  Dropping that block is the
    kind of error that looks like a passing check while raising on every real entry.
    """

    section: str
    property_name: str
    item_schema: dict[str, Any]
    key_field: str = "businessKey"
    definitions: Mapping[str, Any] = field(default_factory=dict)

    @property
    def pointer_prefix(self) -> str:
        """The path prefix an ``add`` operation for this container must use."""
        return f"/{self.section}/{self.property_name}"

    def standalone_schema(self) -> dict[str, Any]:
        """The item schema as a self-contained document a validator can use."""
        merged: dict[str, Any] = dict(self.definitions)
        merged.update(self.item_schema.get("definitions") or {})
        schema = dict(self.item_schema)
        if merged:
            schema["definitions"] = merged
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        return schema


def section_containers(
    section: Section | str, *, schema_version: int | None = None
) -> tuple[Container, ...]:
    """Every container in one section, discovered from S2's own schema (AI-ASM-03)."""
    document = get_section_schema(section, schema_version)
    name = str(getattr(section, "value", section))
    found: list[Container] = []
    for prop, node in _object_nodes(document.get("properties") or {}):
        # S2 declares most sections with the array written inline, but ``rules`` declares
        # each of its arrays as ``{"$ref": "#/definitions/ruleArray"}``.  Resolving the
        # property first is what lets a rule entry be validated at all: a container that
        # is invisible here is invisible to rung 1, which would then let a whole-section
        # replace through with no entry check behind it.
        found.extend(_arrays_of_keyed_items(name, prop, _resolve(node, document), document))
    return tuple(found)


def _object_nodes(properties: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(key, value) for key, value in properties.items() if isinstance(value, dict)]


def _arrays_of_keyed_items(
    section: str, prop: str, node: dict[str, Any], root: dict[str, Any]
) -> list[Container]:
    out: list[Container] = []
    if node.get("type") == "array" or "items" in node:
        items = node.get("items")
        resolved = _resolve(items, root) if isinstance(items, dict) else None
        if resolved is not None:
            key_field = _identity_field(resolved)
            if key_field is not None:
                out.append(
                    Container(
                        section=section,
                        property_name=prop,
                        item_schema=resolved,
                        key_field=key_field,
                        definitions=dict(root.get("definitions") or {}),
                    )
                )
    # one level of nesting: {"type":"object","properties":{...}} wrappers
    for child_prop, child in _object_nodes(node.get("properties") or {}):
        child_node = _resolve(child, root)
        out.extend(_arrays_of_keyed_items(section, f"{prop}/{child_prop}", child_node, root))
    return out


def _identity_field(schema: dict[str, Any]) -> str | None:
    """The field that keys a container entry, or ``None`` when the array is not one.

    An array of plain strings requires nothing and is not a container.  Where an array's
    items require two candidate keys, the first in :data:`KEY_FIELDS` wins, which is the
    same precedence S2's own pointer code applies.
    """
    required = schema.get("required") or []
    if isinstance(required, list):
        for candidate in KEY_FIELDS:
            if candidate in required:
                return candidate
    for part in schema.get("allOf") or []:
        if isinstance(part, dict):
            nested = _identity_field(part)
            if nested is not None:
                return nested
    return None


def _resolve(node: Any, root: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``$ref`` one hop; ``section_schema`` inlines everything else."""
    if not isinstance(node, dict):
        return {}
    ref = node.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return node
    target: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(target, dict):
            return node
        target = target.get(_unescape_pointer(part))
    if isinstance(target, dict):
        merged = dict(target)
        merged.update({key: value for key, value in node.items() if key != "$ref"})
        return merged
    return node


def _unescape_pointer(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


_SEGMENT = re.compile(r"^/([^/]+)(?:/([^/]+))?(?:/(.+))?$")


def classify_path(path: str) -> tuple[str | None, str | None, str | None]:
    """``(section, container_property, remainder)`` for a JSON-Pointer operation path.

    Exposed because both halves of rung 1 need it: the runtime checks the path is inside
    the agent's sections, and S2's ``locate()`` does the same on its side with the same
    result — an agent-visible disagreement between the two would show up as a patch S3
    approved and S2 rejected for a reason the model could not have inferred.
    """
    match = _SEGMENT.match(path)
    if match is None:
        return None, None, None
    section, container, remainder = match.group(1), match.group(2), match.group(3)
    return section, container, remainder


def is_within(prefix: str, path: str) -> bool:
    """Whether ``path`` addresses something inside ``prefix``."""
    return path == prefix or path.startswith(f"{prefix}/") or path.startswith(f"{prefix}-")


__all__ = [
    "OPERATION_SCHEMA",
    "Container",
    "envelope_schema",
    "section_containers",
    "classify_path",
    "is_within",
]
