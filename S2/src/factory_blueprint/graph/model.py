"""Dependency graph: nodes are Registry entries, edges are field-level references.

Uses are reference integrity, impact analysis, Diff display, compile diagnostics
and deletion safety (overall SRS 6.5).  It never decides import order (FR-COV-012).

``build_edges`` and ``dropped_references`` are the two halves of one walk: an edge
exists when both ends are Registry entries, and what the same walk cannot turn into
an edge is the graph's own list of unresolved references.  That list is deliberately
narrower than ``patch.businesskey.unresolved_references`` (rule BPV-BP-002), which
also reports pageKey, intentKey and decisionId references and names any key declared
anywhere in the document.  The two answers overlap but are not the same question, so
a caller reading the graph reports dangling *object* references and a caller reading
the validator report says something else.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH, EntryContainer
from factory_blueprint.patch.businesskey import (
    BUSINESS_KEY,
    KEY_FIELD_BY_CONTAINER,
    REFERENCE_RULES,
    _walk_field,
)

#: ref = direct field reference, implicit = added by a type descriptor, sharing =
#: produced from accessModel, attribute = an attributeValues entry
EDGE_REF = "ref"
EDGE_IMPLICIT = "implicit"
EDGE_SHARING = "sharing"
EDGE_ATTRIBUTE = "attribute"


@dataclass(frozen=True, order=True)
class Edge:
    from_business_key: str
    to_business_key: str
    field: str
    from_dhis2_type: str | None = None
    to_dhis2_type: str | None = None
    edge_kind: str = EDGE_REF

    def as_row(self) -> dict[str, object]:
        return {
            "from_business_key": self.from_business_key,
            "to_business_key": self.to_business_key,
            "field": self.field,
            "from_dhis2_type": self.from_dhis2_type,
            "to_dhis2_type": self.to_dhis2_type,
            "edge_kind": self.edge_kind,
        }


def registry_types(document: dict[str, object]) -> dict[str, str]:
    container = CONTAINER_BY_PATH["/registry/entries"]
    return {
        str(entry["businessKey"]): str(entry.get("dhis2Type") or "")
        for entry in BlueprintDocument(document).container_entries(container)
        if isinstance(entry.get("businessKey"), str)
    }


def _business_key_references(document: dict[str, object]) -> Iterator[tuple[str, str, str]]:
    """Every businessKey reference in the document, as ``(from, field, target)``.

    The source is kept only when it is itself a Registry entry: an object nobody can
    name owns no edge and dangling nothing, so reporting its references would be
    describing a part of the graph that does not exist.  Self-references are dropped
    because a reference to the object holding it is not a dependency.
    """
    view = BlueprintDocument(document)
    known = set(registry_types(document))
    for container_path, field_path, kind in REFERENCE_RULES:
        if kind != BUSINESS_KEY:
            continue
        container = _container(container_path)
        if container is None:
            continue
        for entry in view.container_entries(container):
            source = entry.get(KEY_FIELD_BY_CONTAINER[container_path])
            if not isinstance(source, str) or source not in known:
                continue
            for leaf, value in _walk_field(entry, field_path):
                if not isinstance(value, str) or not value or value == source:
                    continue
                yield source, leaf, value


def build_edges(document: dict[str, object]) -> list[Edge]:
    """Field-level edges between registered entries, derived from the document."""
    types = registry_types(document)
    edges = {
        Edge(
            from_business_key=source,
            to_business_key=target,
            field=field,
            from_dhis2_type=types.get(source),
            to_dhis2_type=types.get(target),
        )
        for source, field, target in _business_key_references(document)
        if target in types
    }
    return sorted(edges)


@dataclass(frozen=True, order=True)
class DroppedReference:
    """One reference a registered object makes to a business key nobody registers."""

    from_business_key: str
    field: str
    target: str


def dropped_references(document: dict[str, object]) -> list[DroppedReference]:
    """The references ``build_edges`` could not turn into edges (BP-TD-009).

    An edge needs both ends in the Registry, so what the same walk drops is exactly
    the set of dangling object references — listed beside the graph rather than
    silently missing from it, because a caller showing only the edges would show a
    build whose references all resolved.
    """
    known = set(registry_types(document))
    return sorted(
        {
            DroppedReference(from_business_key=source, field=field, target=target)
            for source, field, target in _business_key_references(document)
            if target not in known
        }
    )


def _container(path: str) -> EntryContainer | None:
    return CONTAINER_BY_PATH.get(path)
