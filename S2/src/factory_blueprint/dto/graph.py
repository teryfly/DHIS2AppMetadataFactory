"""Dependency-graph DTOs (S2 SRS 7, BP-IMP-004 / BP-CMP-026 / BP-TD-009).

The graph's nodes are Registry entries and its edges are field-level references between them,
which is what makes an edge explainable: ``(from, to, field)`` is the stored key, because two
objects can reference each other through several fields and a graph that merged them would lose
the reason the edge exists.

Two reads answer about this graph.  ``get_object_relationships`` is scoped to a project and
version and returns edges alone: the nodes it would return are the Registry that
``get_registry`` already answers with, and copying entry fields here would give the caller two
answers to keep in step.  ``get_dependency_graph`` is scoped to one *build* and returns the whole
picture, nodes included, because a build's graph is a frozen product of a compile -- including
its UIDs, which side of the managed scope an object was on and what did not resolve at the time
that build ran, none of which the live Registry can reconstruct (BP-TD-009).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: the vocabulary ``graph/model.py`` declares for how an edge came to exist.  Only ``ref`` --
#: a field that names another business key -- is produced today; the other three await the
#: descriptor, accessModel and attributeValues producers that would need them.
EDGE_KINDS = ("ref", "implicit", "sharing", "attribute")


class ObjectRelation(BaseModel):
    """One field-level reference from one Registry entry to another."""

    model_config = ConfigDict(frozen=True)

    from_business_key: str
    to_business_key: str
    field: str
    from_dhis2_type: str | None = None
    to_dhis2_type: str | None = None
    edge_kind: str = "ref"


class GraphNode(BaseModel):
    """One Registry entry as the build saw it (BP-TD-009).

    ``uid`` is the identifier that build resolved -- the environment binding for an
    environment-bound compile, the pool allocation for a baseline.  ``in_payload`` says the
    object was written into this build's metadata payload as a CREATE or UPDATE; an object
    that is only referenced is false.  The degrees are the full graph's, so filtering to one
    object type cannot turn a referenced object into an orphan: a node answers the same
    question in every view of it (BP-AC-16).
    """

    model_config = ConfigDict(frozen=True)

    business_key: str
    dhis2_type: str | None = None
    uid: str | None = None
    disposition: str | None = None
    managed: bool = False
    in_payload: bool = False
    in_degree: int = 0
    out_degree: int = 0


class UnresolvedGraphReference(BaseModel):
    """A metadata field that names a business key this build never resolved.

    The edge is absent from the graph because an edge needs both ends; naming the reference
    here is what stops a caller reading a missing edge as a resolved one (BP-AC-16).  This
    is a graph annotation, not a validation finding -- cross-section reference problems are
    the validation report's answer (BP-VAL-030), and the two must not be shown as one list.
    """

    model_config = ConfigDict(frozen=True)

    from_business_key: str
    field: str
    target: str


class GraphFilters(BaseModel):
    """How to narrow a graph read (BP-TD-009).

    Each dimension supplied narrows the nodes, and within one dimension any member matches:
    an object is kept when its type is in ``dhis2_types`` *and* its key is in
    ``business_keys``, where an omitted or empty set narrows nothing.  A kept node brings its
    one-hop edges: an edge is returned when either end is kept, so the far end may lie
    outside the view.  Neither the degrees nor the orphan and dangling-reference verdicts
    change with the filter -- they are computed on the whole graph and the filter only trims
    what is returned.
    """

    model_config = ConfigDict(frozen=True)

    dhis2_types: tuple[str, ...] | None = None
    business_keys: tuple[str, ...] | None = None


class DependencyGraph(BaseModel):
    """The dependency graph one build produced (BP-TD-006, BP-TD-009)."""

    model_config = ConfigDict(frozen=True)

    build_id: str
    project_id: str
    blueprint_version: str
    kind: str
    environment_key: str | None = None
    schema_version: int = 1
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[ObjectRelation] = Field(default_factory=list)
    #: nodes whose in- and out-degree are both zero on the whole graph
    orphans: list[str] = Field(default_factory=list)
    #: dangling metadata references, on the whole graph; not a validation finding
    unresolved_registry_refs: list[UnresolvedGraphReference] = Field(default_factory=list)
