"""Reads that answer from a product's bytes, not from the ``bp`` schema.

``readmodel.py`` answers what this subsystem's own tables know.  Everything here answers
what a *build* produced, which lives in an artifact: the dependency graph one compile wrote
into its package, and the files inside a development package.  Reading artifact content is
external I/O, so each function opens its own short unit of work for the database half and
reads the bytes once that transaction is closed (00 doc 6.2).

Both formats belong to S2, and that is the reason these functions exist at all: a caller
that unzipped the artifact and parsed the graph or the package layout would break whenever
S2 changed a file name, which is precisely the coupling BP-TD-009 and BP-HO-004 forbid.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from factory_foundation.api import (
    ArtifactError,
    FactoryError,
    NotFoundError,
    content_hash,
    external_io,
    unit_of_work,
)

from factory_blueprint import artifacts
from factory_blueprint.build import records as build_records
from factory_blueprint.compilers import files as product_files
from factory_blueprint.compilers import package as package_compiler
from factory_blueprint.dto.graph import (
    DependencyGraph,
    GraphFilters,
    GraphNode,
    ObjectRelation,
    UnresolvedGraphReference,
)
from factory_blueprint.dto.package_files import (
    PackageFileContent,
    PackageFileEntry,
    PackageFileListing,
)
from factory_blueprint.enums import BuildKind, BuildStatus

#: the artifact role each build kind files its dependency graph under, and the ZIP entry
#: name inside it.  The role names are the ones ``registries.BP_ARTIFACT_TYPES`` registers.
_GRAPH_ROLES: dict[str, str] = {
    BuildKind.BASELINE.value: "METADATA_BASELINE",
    BuildKind.ENVIRONMENT.value: "METADATA_PACKAGE",
}

#: artifact types whose internal layout this read lists (BP-HO-004).  A later milestone
#: adds the metadata package here rather than changing either public signature.
LISTABLE_PACKAGE_TYPES: tuple[str, ...] = ("FRONTEND_PACKAGE",)

#: ``CP-UI-022``: a derived resource that does not exist is reported by reason, never as a
#: 200 with an empty body, so the client can say "run this stage first" instead of "nothing".
REASON_NOT_GENERATED = "NOT_GENERATED"
REASON_NOT_EXIST = "NOT_EXIST"

#: ``BP-HO-004``: an artifact of the wrong type is the caller's error, not a resource that
#: has not been generated.  The code is S0's (FD-COM-001a), so S2 only raises it.
ARTIFACT_TYPE_MISMATCH = "ARTIFACT_TYPE_MISMATCH"

_MEDIA_BY_SUFFIX = {
    ".json": artifacts.JSON_MEDIA_TYPE,
    ".md": artifacts.MARKDOWN_MEDIA_TYPE,
    ".yaml": artifacts.YAML_MEDIA_TYPE,
}


@external_io
def get_dependency_graph(build_id: str, filters: GraphFilters | None = None) -> DependencyGraph:
    """The dependency graph one build produced (BP-TD-006, BP-TD-009).

    This is the only source of the graph: S1 forwards it and must not recompute nodes,
    degrees, orphans or dangling references itself (CP-UI-020).  Works for both compile
    kinds, because a baseline and an environment-bound compile each write the same entry
    into their own package; a HANDOFF build has no graph to answer with and is reported as
    not generated rather than as an empty graph.

    Everything is read from the frozen product, so the degrees, ``orphans`` and
    ``unresolved_registry_refs`` describe the whole graph whatever the filters say: a
    narrowing trims the returned nodes and edges only, and never turns a referenced object
    into an orphan (BP-AC-16).  A filter set that is empty or ``None`` narrows nothing, so
    the two dimensions combine as AND.
    """
    with unit_of_work() as session:
        row = build_records.load(session, build_id)
        if row is None:
            raise NotFoundError(
                f"unknown build {build_id}",
                details={"buildId": build_id, "reason": REASON_NOT_EXIST},
            )
        kind = row.kind
        status = row.status
        version = row.blueprint_version
        project_id = row.project_id
        environment_key = row.environment_key
        artifact_id = _graph_artifact_id(kind, status, dict(row.output_artifact_ids or {}), build_id)
    data = artifacts.read_bytes(artifact_id)[0]
    blob = artifacts.unpack_archive(data).get(product_files.DEPENDENCY_GRAPH_FILE)
    if blob is None:
        raise NotFoundError(
            f"build {build_id} wrote no {product_files.DEPENDENCY_GRAPH_FILE}",
            details={
                "buildId": build_id,
                "artifactId": artifact_id,
                "reason": REASON_NOT_GENERATED,
            },
        )
    document: dict[str, Any] = json.loads(blob.decode("utf-8"))
    graph = _parse_graph(document, build_id, project_id, kind, version, environment_key)
    return narrow_graph(graph, filters)


def _graph_artifact_id(
    kind: str, status: str, artifact_ids: dict[str, str], build_id: str
) -> str:
    """The package this build filed its graph under, or the not-generated answer.

    A build that did not finish is included deliberately: AC-17 reads "尚未完成" as the build's
    status, and a graph from a compile that failed validation is not something a caller should
    be shown as though it had been reviewed.  A handoff build is refused by the same answer,
    because it produces a development package and no graph at all.
    """
    role = _GRAPH_ROLES.get(kind)
    if status != BuildStatus.COMPLETED.value or role is None:
        raise NotFoundError(
            f"build {build_id} has produced no dependency graph",
            details={
                "buildId": build_id,
                "buildKind": kind,
                "buildStatus": status,
                "reason": REASON_NOT_GENERATED,
            },
        )
    artifact_id = artifact_ids.get(role)
    if not artifact_id:
        raise NotFoundError(
            f"build {build_id} produced no {role} artifact",
            details={
                "buildId": build_id,
                "buildKind": kind,
                "buildStatus": status,
                "reason": REASON_NOT_GENERATED,
            },
        )
    return str(artifact_id)


def _parse_graph(
    document: dict[str, Any],
    build_id: str,
    project_id: str,
    kind: str,
    version: str,
    environment_key: str | None,
) -> DependencyGraph:
    """The frozen ``dependency-graph.json`` as models (format owned by S2).

    The file spells nodes in camelCase and edges in snake_case because the two halves were
    written by different producers; reading it means following the file, not normalising it.
    """
    nodes = [
        GraphNode(
            business_key=str(node.get("businessKey") or ""),
            dhis2_type=_text(node.get("dhis2Type")),
            uid=_text(node.get("uid")),
            disposition=_text(node.get("disposition")),
            managed=bool(node.get("managed")),
            in_payload=bool(node.get("inPayload")),
            in_degree=int(node.get("inDegree") or 0),
            out_degree=int(node.get("outDegree") or 0),
        )
        for node in document.get("nodes") or []
    ]
    edges = [
        ObjectRelation(
            from_business_key=str(row.get("from_business_key") or ""),
            to_business_key=str(row.get("to_business_key") or ""),
            field=str(row.get("field") or ""),
            from_dhis2_type=_text(row.get("from_dhis2_type")),
            to_dhis2_type=_text(row.get("to_dhis2_type")),
            edge_kind=str(row.get("edge_kind") or "ref"),
        )
        for row in document.get("edges") or []
    ]
    unresolved = [
        UnresolvedGraphReference(
            from_business_key=str(row.get("from_business_key") or ""),
            field=str(row.get("field") or ""),
            target=str(row.get("target") or ""),
        )
        for row in document.get("unresolvedRegistryRefs") or []
    ]
    return DependencyGraph(
        build_id=build_id,
        project_id=project_id,
        blueprint_version=version,
        kind=kind,
        environment_key=environment_key,
        schema_version=int(document.get("schemaVersion") or 0),
        nodes=nodes,
        edges=edges,
        orphans=[str(key) for key in document.get("orphans") or []],
        unresolved_registry_refs=unresolved,
    )


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def narrow_graph(graph: DependencyGraph, filters: GraphFilters | None) -> DependencyGraph:
    """Apply the filters, keeping the graph's own verdict on which node is an orphan.

    The Fake in :mod:`factory_blueprint.testing` calls this same function rather than
    reimplementing it: a double that narrowed differently would let S1 test a filtered
    graph that answers something else in production.
    """
    if filters is None:
        return graph
    types = set(filters.dhis2_types or ())
    keys = set(filters.business_keys or ())
    if not types and not keys:
        return graph

    def keep(node: GraphNode) -> bool:
        # each supplied dimension narrows (AND); within one, any member matches (OR) --
        # the same shape ``graph/queries.all_edges`` gives its type and kind filters
        if types and node.dhis2_type not in types:
            return False
        return not keys or node.business_key in keys

    nodes = [node for node in graph.nodes if keep(node)]
    kept = {node.business_key for node in nodes}
    edges = [
        edge
        for edge in graph.edges
        if edge.from_business_key in kept or edge.to_business_key in kept
    ]
    return graph.model_copy(
        update={
            "nodes": nodes,
            "edges": edges,
            "orphans": [key for key in graph.orphans if key in kept],
            "unresolved_registry_refs": [
                item for item in graph.unresolved_registry_refs if item.from_business_key in kept
            ],
        }
    )


@external_io
def list_package_files(artifact_id: str) -> PackageFileListing:
    """The files inside one development package (BP-HO-004, overall SRS 17.1).

    Size and hash are computed from the same bytes, and ``read_package_file`` derives its
    answer the same way, so the listing and the content cannot disagree (BP-AC-18).
    """
    owner = _require_package(artifact_id)
    blobs = artifacts.unpack_archive(artifacts.read_bytes(artifact_id)[0])
    return PackageFileListing(
        artifact_id=artifact_id,
        project_id=owner.project_id,
        blueprint_version=owner.blueprint_version,
        files=[
            PackageFileEntry(
                path=path,
                kind=package_compiler.package_file_kind(path),
                size_bytes=len(blob),
                content_hash=content_hash(blob),
            )
            for path, blob in sorted(blobs.items())
        ],
    )


@external_io
def read_package_file(artifact_id: str, path: str) -> PackageFileContent:
    """One file's content from a development package (BP-HO-004).

    The path is refused before anything is read, and a refused path answers exactly what a
    path that is simply not in the package answers, so the answer cannot sketch the layout
    (BP-AC-20).  The hash is computed from the bytes being returned, so it equals the
    listing's entry for that path (BP-AC-18).
    """
    validate_package_path(path, artifact_id)
    _require_package(artifact_id)
    blobs = artifacts.unpack_archive(artifacts.read_bytes(artifact_id)[0])
    blob = blobs.get(path)
    if blob is None:
        raise NotFoundError(
            f"package {artifact_id} holds no file {path!r}",
            details={"artifactId": artifact_id, "path": path, "reason": REASON_NOT_EXIST},
        )
    try:
        content = blob.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArtifactError(
            f"package file {path!r} is not UTF-8 text",
            details={"artifactId": artifact_id, "path": path},
        ) from error
    return PackageFileContent(
        path=path,
        kind=package_compiler.package_file_kind(path),
        content=content,
        media_type=package_media_type(path),
        size_bytes=len(blob),
        content_hash=content_hash(blob),
    )


def package_media_type(path: str) -> str:
    """How to describe one package file's content, from its extension (BP-HO-004)."""
    for suffix, media in _MEDIA_BY_SUFFIX.items():
        if path.endswith(suffix):
            return media
    return "application/octet-stream"


class _PackageOwner(NamedTuple):
    """Which build a package belongs to, so the listing can say so."""

    project_id: str
    blueprint_version: str


def _require_package(artifact_id: str) -> _PackageOwner:
    """Check the artifact is a development package; return which build it belongs to.

    Three answers, and they mean different things to the caller (BP-HO-004, CP-UI-021):
    an unknown artifact is ``NOT_EXIST``; an artifact of another type is
    ``ARTIFACT_TYPE_MISMATCH``, because handing a commit package to this read is the
    caller's mistake rather than a resource nobody generated yet — S1 maps that one to a
    server error, since S1 is what resolves the project's package.  Whether a project has a
    package at all is S1's judgement, not S2's.  A purged artifact is not folded in here
    either: it existed, which is ``ARTIFACT_PURGED``.
    """
    with unit_of_work() as session:
        try:
            info = artifacts.read_metadata(artifact_id, session=session)
        except NotFoundError as error:
            raise NotFoundError(
                f"unknown artifact {artifact_id}",
                details={"artifactId": artifact_id, "reason": REASON_NOT_EXIST},
            ) from error
        if info.type not in LISTABLE_PACKAGE_TYPES:
            raise FactoryError(
                f"artifact {artifact_id} is a {info.type}, not a {LISTABLE_PACKAGE_TYPES[0]}",
                code=ARTIFACT_TYPE_MISMATCH,
                details={
                    "artifactId": artifact_id,
                    "expectedType": LISTABLE_PACKAGE_TYPES[0],
                    "actualType": info.type,
                },
            )
        build = build_records.find_by_artifact(session, artifact_id)
        if build is None:
            raise NotFoundError(
                f"no build record produced artifact {artifact_id}",
                details={"artifactId": artifact_id, "reason": REASON_NOT_EXIST},
            )
        return _PackageOwner(
            project_id=build.project_id, blueprint_version=build.blueprint_version
        )


def validate_package_path(path: str, artifact_id: str) -> None:
    """Refuse a path that is not a plain package-relative file name (BP-HO-004, BP-AC-20).

    The lookup below is an exact match against the archive's own entry names, so traversal
    cannot escape anything already — refusing it here is about the answer the caller gets:
    every one of these says ``NOT_EXIST``, the same answer a legal path that is not in the
    package gets, so no reply sketches what the layout might be.
    """
    rejected = (
        not path
        or path.startswith("/")
        or "\\" in path
        or "%" in path
        or "#" in path
        # control characters have no place in a file name this subsystem ever wrote
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in path)
        or any(token in {".", ".."} for token in path.split("/"))
    )
    if rejected:
        raise NotFoundError(
            f"refused package file path {path!r}",
            details={"artifactId": artifact_id, "path": path, "reason": REASON_NOT_EXIST},
        )
