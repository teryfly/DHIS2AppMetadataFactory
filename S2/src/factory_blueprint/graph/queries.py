"""Graph queries over ``bp.dependency_edge`` (BP-IMP-004, BP-CMP-026)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_blueprint.models.index import DependencyEdgeRow


def out_edges(
    session: Session, project_id: str, blueprint_version: str, business_key: str
) -> list[DependencyEdgeRow]:
    return list(
        session.execute(
            select(DependencyEdgeRow).where(
                DependencyEdgeRow.project_id == project_id,
                DependencyEdgeRow.blueprint_version == blueprint_version,
                DependencyEdgeRow.from_business_key == business_key,
            )
        )
        .scalars()
        .all()
    )


def in_edges(
    session: Session, project_id: str, blueprint_version: str, business_key: str
) -> list[DependencyEdgeRow]:
    return list(
        session.execute(
            select(DependencyEdgeRow).where(
                DependencyEdgeRow.project_id == project_id,
                DependencyEdgeRow.blueprint_version == blueprint_version,
                DependencyEdgeRow.to_business_key == business_key,
            )
        )
        .scalars()
        .all()
    )


def downstream(
    session: Session,
    project_id: str,
    blueprint_version: str,
    business_keys: Iterable[str],
) -> set[str]:
    """Everything that transitively references one of ``business_keys``."""
    seed = [key for key in business_keys]
    found: set[str] = set()
    queue: deque[str] = deque(seed)
    while queue:
        current = queue.popleft()
        for edge in in_edges(session, project_id, blueprint_version, current):
            if edge.from_business_key in found or edge.from_business_key in seed:
                continue
            found.add(edge.from_business_key)
            queue.append(edge.from_business_key)
    return found


def all_edges(
    session: Session,
    project_id: str,
    blueprint_version: str,
    *,
    dhis2_types: Iterable[str] | None = None,
    edge_kinds: Iterable[str] | None = None,
) -> list[DependencyEdgeRow]:
    """Every edge of one version, optionally narrowed to a set of object types.

    The narrowing is here rather than left to the caller because a 5,000-object project has
    tens of thousands of edges (BP-NFR-03): a UI that filters by object type must not have to
    read the whole graph to show a slice of it.  ``dhis2_types`` matches an edge when either end
    is one of those types, so a filtered view still shows how the kept objects hang together.
    """
    statement = select(DependencyEdgeRow).where(
        DependencyEdgeRow.project_id == project_id,
        DependencyEdgeRow.blueprint_version == blueprint_version,
    )
    if dhis2_types:
        kept = list(dhis2_types)
        statement = statement.where(
            DependencyEdgeRow.from_dhis2_type.in_(kept)
            | DependencyEdgeRow.to_dhis2_type.in_(kept)
        )
    if edge_kinds:
        statement = statement.where(DependencyEdgeRow.edge_kind.in_(list(edge_kinds)))
    return list(
        session.execute(
            statement.order_by(
                DependencyEdgeRow.from_business_key,
                DependencyEdgeRow.to_business_key,
                DependencyEdgeRow.field,
            )
        ).scalars()
    )


def references_of(
    session: Session, project_id: str, blueprint_version: str, business_key: str
) -> list[tuple[str, str]]:
    """(field, target) pairs this entry depends on, for compile diagnostics."""
    return sorted(
        (edge.field, edge.to_business_key)
        for edge in out_edges(session, project_id, blueprint_version, business_key)
    )


def is_referenced(session: Session, project_id: str, blueprint_version: str, business_key: str) -> bool:
    """Deletion safety, graph half (BP-CMP-026): nothing may still point at it."""
    return bool(in_edges(session, project_id, blueprint_version, business_key))
