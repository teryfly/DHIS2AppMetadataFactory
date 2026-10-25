"""Constraint-check scaffolding (AI-RT-012).

A check is a function from a validated :class:`~factory_intelligence.dto.agent.AgentOutput`
plus a :class:`CheckContext` to a list of findings.  Two properties are enforced here
rather than left to each rule:

* checks are **structural**.  AI-AG-001 forbids a Requirements entry from *referencing* a
  DHIS2 object type, and the only reading that survives contact with a real model is "no
  DHIS2 type in a field that carries identity or structure" — not "the word
  ``dataElement`` may not appear in prose", which would reject a legitimate statement like
  *"each pregnancy must be recorded as a data element"* and train whoever fixes the prompt
  to route around the check instead.
* checks see the **context the run was built from** (thresholds, the similar objects S3
  found, whether the mapping is frozen), because rules like AI-AG-012 cannot be evaluated
  from the answer alone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.output.errors import Finding
from factory_intelligence.output.schema import Container

#: A patch value that may be any JSON document.
JsonValue = Any


@dataclass
class CheckContext:
    """What a rule needs beyond the answer itself."""

    agent_type: str
    stage: str
    #: section value -> its containers, resolved from S2 (AI-ASM-03)
    containers: Mapping[str, Sequence[Container]] = field(default_factory=dict)
    #: AI-AG-012: the Blueprint's own decision threshold (AI-INP-008 supplies extraction's)
    decision_confidence_threshold: float = 0.7
    mapping_frozen: bool = False
    #: AI-AG-011: keys of the similar objects S3 found, each needing a reuse decision
    similar_object_keys: Sequence[str] = ()
    #: AI-AG-005 (M3): the operation keys this page's API intent offered
    api_operation_keys: Sequence[str] = ()
    #: artifacts excluded from context as PHI_PII, for AI-AG-007's fixture ban
    phi_refs: Sequence[str] = ()
    #: base version the patch must be authored against (AI-RT-010)
    base_version: str | None = None
    #: businessKeys already present in the draft or version, for the uniqueness rules
    existing_keys: Sequence[str] = ()


@dataclass(frozen=True)
class Write:
    """One ``add`` or ``replace`` operation, with its parsed path."""

    index: int
    op: str
    path: str
    value: JsonValue
    section: str | None
    container: str | None
    entry_key: str | None


def writes(output: AgentOutput) -> list[Write]:
    """The patch's write operations, in the order the model gave them."""
    from factory_intelligence.output.schema import classify_path

    if output.patch is None:
        return []
    out: list[Write] = []
    for index, operation in enumerate(output.patch.operations):
        kind = str(operation.get("op") or "")
        if kind not in ("add", "replace"):
            continue
        path = str(operation.get("path") or "")
        section, container, remainder = classify_path(path)
        entry_key = _entry_key(remainder)
        out.append(
            Write(
                index=index,
                op=kind,
                path=path,
                value=operation.get("value"),
                section=section,
                container=container,
                entry_key=entry_key,
            )
        )
    return out


def removes(output: AgentOutput) -> list[tuple[int, str]]:
    """The ``remove`` operations: (index, path)."""
    if output.patch is None:
        return []
    return [
        (index, str(operation.get("path") or ""))
        for index, operation in enumerate(output.patch.operations)
        if str(operation.get("op") or "") == "remove"
    ]


def _entry_key(remainder: str | None) -> str | None:
    if not remainder:
        return None
    # ``/mapping/entries/<bk>`` and ``/mapping/entries/-`` both address one entry; the
    # append marker has no key yet, which is reported as ``None`` rather than guessed.
    head = remainder.split("/", 1)[0]
    return None if head == "-" else head


def walk_documents(
    value: JsonValue, *, path: str = "$"
) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Yield every mapping in a JSON document with its location.

    Used by the rules that must not be fooled by nesting: a metadata payload hidden
    inside ``structure.parts[2]`` is as final a product as one at the top level.
    """
    if isinstance(value, Mapping):
        yield path, value
        for key, child in value.items():
            yield from walk_documents(child, path=f"{path}/{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from walk_documents(child, path=f"{path}/{index}")


def entry_values(output: AgentOutput, section: str, container: str) -> list[dict[str, Any]]:
    """The entry documents this answer writes into one container."""
    prefix = f"/{section}/{container}"
    found: list[dict[str, Any]] = []
    for write in writes(output):
        if write.section != section or not write.path.startswith(prefix):
            continue
        if isinstance(write.value, dict):
            found.append(write.value)
    return found


def finding(rule_id: str, message: str, *, location: str | None = None) -> Finding:
    from factory_intelligence.output.errors import LADDER_CONSTRAINT

    return Finding(rule_id=rule_id, message=message, ladder=LADDER_CONSTRAINT, location=location)


#: ``(agent_type, entry_key)`` shaped identity of an entry a decision belongs to, used by
#: the rules that must tie a question to the thing it gates.
def affects_keys(entry: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("businessKey", "decisionId", "intentKey", "pageKey"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            out.add(value)
    return out


CheckFn = Callable[[AgentOutput, CheckContext], list[Finding]]


__all__ = [
    "CheckContext",
    "CheckFn",
    "Write",
    "writes",
    "removes",
    "walk_documents",
    "entry_values",
    "finding",
    "affects_keys",
]
