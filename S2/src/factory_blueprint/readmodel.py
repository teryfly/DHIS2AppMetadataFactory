"""The read-only query surface of S2 (S2 SRS 7.2 and 7.4).

Reads that answer from ``bp`` alone — the version index, the mirrored project
semantics, the Registry, the Managed Scope, the pool status, key locations, impact —
take a session and are safe inside S1's transaction.  The two that must touch
artifact bytes are external I/O and say so with the decorator, because S0 forbids a
content read inside a transaction (00 doc 6.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from factory_foundation.api import Page, external_io, unit_of_work
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_blueprint import artifacts
from factory_blueprint.config import settings
from factory_blueprint.content.document import BlueprintDocument, entries_of
from factory_blueprint.content.layout import (
    CONTAINER_BY_PATH,
    ENTRY_CONTAINERS,
    WHOLE_OBJECT_SECTIONS,
)
from factory_blueprint.descriptors import registry as descriptor_registry
from factory_blueprint.draft.service import to_version_info
from factory_blueprint.dto.blueprint import (
    CurrentVersion,
    EntrySelector,
    ProjectSemantics,
    SelectedEntries,
    VersionInfo,
)
from factory_blueprint.dto.graph import ObjectRelation
from factory_blueprint.dto.manifest import (
    ROLE_DOCUMENT,
    ROLE_GRAPH,
    ROLE_MANIFEST,
    ROLE_PAYLOAD,
    ROLE_REPORT,
    ROLE_VIEW,
    PackageFile,
    PackageManifest,
)
from factory_blueprint.dto.patch import ImpactSet
from factory_blueprint.dto.registry import (
    BindingInfo,
    DecisionInfo,
    ManagedObject,
    RegistryEntry,
    UidPoolStatus,
)
from factory_blueprint.enums import (
    BindingResolution,
    ChangeLevel,
    DeliveryMode,
    DesignStage,
    ProjectType,
    Section,
)
from factory_blueprint.graph import queries as graph_queries
from factory_blueprint.impact import impact as impact_rules
from factory_blueprint.impact.changelvl import Change
from factory_blueprint.models.build import BuildRecordRow
from factory_blueprint.models.index import BusinessKeyIndexRow
from factory_blueprint.models.version import BlueprintVersionRow
from factory_blueprint.registry import binding as binding_rules
from factory_blueprint.registry import pool as uid_pool
from factory_blueprint.registry import queries as registry_queries
from factory_blueprint.registry import scope as managed_scope
from factory_blueprint.version import numbering
from factory_blueprint.version import service as version_service


def get_current_version(project_id: str, *, session: Session) -> CurrentVersion:
    """The version a build starts from: number, content hash, artifact and level."""
    row = version_service.require_current(session, project_id)
    return CurrentVersion(
        version=row.version,
        content_hash=row.content_hash,
        artifact_id=row.artifact_id,
        change_level=ChangeLevel(row.change_level),
        schema_version=row.schema_version,
    )


def get_version(project_id: str, version: str, *, session: Session) -> VersionInfo:
    return to_version_info(version_service.get_version(session, project_id, version))


def list_versions(
    project_id: str, page: int = 1, page_size: int = 50, *, session: Session
) -> Page[VersionInfo]:
    """Newest first.  BP-VER-006 keeps the whole history, so nothing here is truncated."""
    offset = (max(1, page) - 1) * page_size
    rows, total = version_service.list_versions(session, project_id, limit=page_size, offset=offset)
    items = sorted(
        (to_version_info(row) for row in rows),
        key=lambda item: numbering.Version.parse(item.version),
        reverse=True,
    )
    return Page(items=items, total=total, limit=page_size, offset=offset)


def get_project_semantics(
    project_id: str, version: str | None = None, *, session: Session
) -> ProjectSemantics:
    """Mode, type, languages and whether the mapping is already frozen (D-15).

    Answered from the mirrored columns of the version row, so no artifact is read.
    """
    row = (
        version_service.get_version(session, project_id, version)
        if version
        else version_service.require_current(session, project_id)
    )
    return ProjectSemantics(
        project_id=project_id,
        version=row.version,
        delivery_mode=DeliveryMode(row.delivery_mode),
        project_type=ProjectType(row.project_type),
        default_language=row.default_language,
        translation_languages=sorted(str(item) for item in row.translation_languages or []),
        target_dhis2_version_constraint=row.target_dhis2_version_constraint,
        has_fixture_specifications=row.has_fixture_specifications,
        mapping_frozen=is_mapping_frozen(row),
        promoted_from_stage=(
            DesignStage(row.promoted_from_stage) if row.promoted_from_stage else None
        ),
    )


def is_mapping_frozen(row: BlueprintVersionRow) -> bool:
    """Frozen from the version promoted at the Mapping Gate onwards (BP-DRF-005.5)."""
    if row.promoted_from_stage is None:
        return False
    stages = list(DesignStage)
    return stages.index(DesignStage(row.promoted_from_stage)) >= stages.index(DesignStage.MAPPING)


def locate_entries(
    project_id: str, selector: EntrySelector, *, session: Session, version: str | None = None
) -> list[tuple[str, str, str]]:
    """Where the selected entries live: ``(section, entry key, document path)``."""
    target = version or version_service.require_current(session, project_id).version
    return registry_queries.find_paths(
        session,
        project_id,
        target,
        keys=list(selector.business_keys) or None,
        decision_ids=list(selector.decision_ids) or None,
        page_keys=list(selector.page_keys) or None,
        sections=list(selector.sections) or None,
    )


@external_io
def get_entries(
    project_id: str, selector: EntrySelector, version: str | None = None
) -> SelectedEntries:
    """The smallest entry set that answers the selector, contents included.

    ``source="DRAFT"`` reads the open draft's working copy instead of the approved
    version, so a Gate can show what an Agent proposed that nobody has approved yet.
    The approved view comes from the index; the draft view is a scan of the draft
    document, because a proposed entry has no index row until it is promoted.
    """
    with unit_of_work() as session:
        target = version or version_service.require_current(session, project_id).version
        paths = locate_entries(project_id, selector, session=session, version=target)
        artifact_id, draft_content = _content_choice(session, project_id, target, selector.source)
    if draft_content is not None:
        document = draft_content
        paths = _scan_paths(document, selector)
    else:
        document = artifacts.read_json(artifact_id or "")[0]
    entries = _entries_at(document, paths)
    found = {
        str(entry["businessKey"])
        for entry in entries
        if isinstance(entry.get("businessKey"), str)
    }
    whole_objects = [
        {"section": section.value, "value": document.get(section.value)}
        for section in sorted(WHOLE_OBJECT_SECTIONS)
        if section in selector.sections
    ]
    return SelectedEntries(
        project_id=project_id,
        version=target,
        source=selector.source,
        entries=entries + whole_objects,
        not_found=sorted(set(selector.business_keys) - found),
    )


def _scan_paths(document: dict[str, Any], selector: EntrySelector) -> list[tuple[str, str, str]]:
    """The draft's answer to :func:`locate_entries`: same shape, no index involved."""
    sections = {section.value for section in selector.sections}
    keys = set(selector.business_keys)
    decisions = set(selector.decision_ids)
    pages = set(selector.page_keys)
    found: list[tuple[str, str, str]] = []
    for container in ENTRY_CONTAINERS:
        if sections and container.section.value not in sections:
            continue
        for entry in entries_of(document, container):
            if not isinstance(entry, dict):
                continue
            own = str(entry.get(container.key_field) or "")
            if keys and own not in keys:
                continue
            if decisions and str(entry.get("decisionId") or "") not in decisions:
                continue
            if pages and own not in pages and str(entry.get("pageKey") or "") not in pages:
                continue
            found.append((container.section.value, own, f"{container.path}/{own}"))
    return sorted(found)


def _content_choice(
    session: Session, project_id: str, version: str, source: str
) -> tuple[str | None, dict[str, Any] | None]:
    from factory_blueprint.draft import service as draft_service

    if source.upper() == "DRAFT":
        content = draft_service.active_draft_document(session, project_id)
        if content is not None:
            return None, content
    return version_service.get_version(session, project_id, version).artifact_id, None


def _entries_at(document: dict[str, Any], paths: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    collected: dict[tuple[str, str], dict[str, Any]] = {}
    for section, key, path in paths:
        if (section, key) in collected:
            continue
        entry = _locate(document, path)
        if isinstance(entry, dict):
            collected[(section, key)] = {**entry, "_section": section, "_path": path}
    return sorted(
        collected.values(),
        key=lambda item: (str(item["_section"]), str(item.get("businessKey") or "")),
    )


def _locate(document: dict[str, Any], path: str) -> Any:
    """Follow a stored document path.

    Index rows address an array element by its entry key (``/mapping/entries/pregnancy``),
    not by position, so a list step is resolved through the container's key field.  That
    is what makes the paths survive a re-order of the array between versions.
    """
    tokens = [item for item in path.lstrip("/").split("/") if item]
    node: Any = document
    for index, token in enumerate(tokens):
        if isinstance(node, dict):
            node = node.get(token)
        elif isinstance(node, list):
            node = _array_item(node, token, "/".join(tokens[:index]))
        else:
            return None
        if node is None:
            return None
    return node


def _array_item(items: list[Any], token: str, container_path: str) -> Any:
    container = CONTAINER_BY_PATH.get(f"/{container_path}")
    key_field = container.key_field if container is not None else "businessKey"
    if token.isdigit() and int(token) < len(items):
        return items[int(token)]
    for item in items:
        if isinstance(item, dict) and str(item.get(key_field)) == token:
            return item
    return None


def get_decisions(
    project_id: str,
    decision_ids: list[str],
    version: str | None = None,
    *,
    session: Session,
) -> list[DecisionInfo]:
    target = version or version_service.require_current(session, project_id).version
    return registry_queries.decisions(session, project_id, target, decision_ids)


def get_registry(
    project_id: str,
    version: str,
    environment_key: str | None = None,
    *,
    session: Session,
) -> list[RegistryEntry]:
    """The Metadata Registry, optionally carrying one environment's UIDs (BP-REG-010)."""
    return registry_queries.registry_entries(
        session, project_id, version, environment_key=environment_key
    )


def get_object_relationships(
    project_id: str,
    version: str | None = None,
    dhis2_types: Sequence[str] | None = None,
    edge_kinds: Sequence[str] | None = None,
    *,
    session: Session,
) -> list[ObjectRelation]:
    """The dependency graph's edges (BP-IMP-004, BP-CMP-026).

    Nodes are not returned: they are the Registry, which ``get_registry`` already answers with,
    and a graph read that copied entry fields would give the caller two answers to keep in step.
    The type and kind narrowing exists because an unfiltered graph of a 5,000-object project is
    tens of thousands of rows (BP-NFR-03) — a view that filters by object type reads the slice.
    """
    target = version or version_service.require_current(session, project_id).version
    return [
        ObjectRelation(
            from_business_key=row.from_business_key,
            to_business_key=row.to_business_key,
            field=row.field,
            from_dhis2_type=row.from_dhis2_type,
            to_dhis2_type=row.to_dhis2_type,
            edge_kind=row.edge_kind,
        )
        for row in graph_queries.all_edges(
            session, project_id, target, dhis2_types=dhis2_types, edge_kinds=edge_kinds
        )
    ]


def get_managed_scope(
    project_id: str, version: str, environment_key: str, *, session: Session
) -> list[ManagedObject]:
    """The objects this project owns in one environment (BP-REG-009)."""
    return managed_scope.managed_scope(session, project_id, version, environment_key)


#: the build-record prefixes whose keys are package files rather than artifact roles
_PACKAGE_PREFIXES = ("METADATA_PACKAGE/", "METADATA_BASELINE/")


def _file_role(path: str) -> str:
    """Which role a file name plays in the package; the names come from ``compilers/files.py``."""
    if path == "metadata.json" or path == "metadata-baseline.json":
        return ROLE_PAYLOAD
    if path == "metadata_manifest.json":
        return ROLE_MANIFEST
    if path == "dependency-graph.json":
        return ROLE_GRAPH
    if path.startswith("views/"):
        return ROLE_VIEW
    if path.endswith(".json"):
        return ROLE_REPORT
    return ROLE_DOCUMENT


def get_package_manifest(
    project_id: str,
    version: str | None = None,
    environment_key: str | None = None,
    *,
    session: Session,
) -> PackageManifest:
    """The files one build put in the metadata package (BP-CMP-021; S5 FR-UI-007's by-file view).

    Read from ``bp.build_record.output_hashes`` rather than from the package artifact: the record
    already names every file it produced with its hash, so this answers without an artifact read
    and stays safe inside S1's transaction (00 doc 6.2).  A version that was never built answers
    with no files and the caller says so — inventing a layout would show files nobody produced.

    Object *identity* stays the Registry's answer (``get_registry``): this counts the objects a
    file carries, it does not restate them, so the two views of one build cannot drift.
    """
    target = version or version_service.require_current(session, project_id).version
    statement = select(BuildRecordRow).where(
        BuildRecordRow.project_id == project_id,
        BuildRecordRow.blueprint_version == target,
    )
    if environment_key:
        statement = statement.where(BuildRecordRow.environment_key == environment_key)
    row = session.execute(statement.order_by(BuildRecordRow.created_at.desc())).scalars().first()
    if row is None:
        return PackageManifest(
            project_id=project_id, blueprint_version=target, build_id="", kind="NONE"
        )

    counts = _objects_per_dhis2_type(session, project_id, target)
    # the payload file carries every object; a view file carries its type's
    payload_total = sum(counts.values())
    files: list[PackageFile] = []
    for key, digest in sorted((row.output_hashes or {}).items()):
        prefix = next((found for found in _PACKAGE_PREFIXES if key.startswith(found)), None)
        if prefix is None:
            continue
        path = key[len(prefix) :]
        role = _file_role(path)
        dhis2_type: str | None = None
        object_count: int | None = None
        if role == ROLE_PAYLOAD:
            object_count = payload_total
        elif role == ROLE_VIEW:
            dhis2_type = _type_of_view(path)
            object_count = counts.get(dhis2_type or "", 0)
        files.append(
            PackageFile(
                path=path,
                role=role,
                content_hash=str(digest),
                dhis2_type=dhis2_type,
                object_count=object_count,
            )
        )

    return PackageManifest(
        project_id=project_id,
        blueprint_version=target,
        build_id=row.id,
        kind=row.kind,
        environment_key=row.environment_key,
        package_hash=row.package_hash,
        files=files,
    )


def _objects_per_dhis2_type(
    session: Session, project_id: str, version: str
) -> dict[str, int]:
    """How many Registry objects of each DHIS2 type the version holds."""
    tally: dict[str, int] = {}
    for entry in registry_queries.registry_entries(session, project_id, version):
        key = str(entry.dhis2_type or "")
        tally[key] = tally.get(key, 0) + 1
    return tally


def _type_of_view(path: str) -> str | None:
    """The DHIS2 type a ``views/<payloadKey>.json`` file holds.

    The file is named by the descriptor's payload key, which is DHIS2's own collection name and
    not the type name, so the mapping is looked up rather than guessed from the file name.
    """
    payload_key = path.removeprefix("views/").removesuffix(".json")
    for dhis2_type in descriptor_registry.known_types():
        found = descriptor_registry.descriptor(dhis2_type)
        if found is not None and found.payload_key == payload_key:
            return dhis2_type
    return None


def get_bindings(
    project_id: str,
    version: str,
    environment_keys: Sequence[str] | str | None = None,
    *,
    session: Session,
) -> list[BindingInfo]:
    """Every binding field BP-REG-015 names, for one, several or all environments.

    ``observedHash`` and ``discoveryResultId`` are the ones a drift check is built on:
    S4 compares them against the instance, so leaving them out would make the binding
    look complete while telling nobody that the object moved.
    """
    return [
        BindingInfo(
            environment_key=row.environment_key,
            business_key=row.business_key,
            uid=row.uid,
            resolution=BindingResolution(row.resolution),
            observed_last_updated=_iso(row.observed_last_updated),
            observed_hash=row.observed_hash,
            discovery_result_id=row.discovery_result_id,
            resolved_by=row.resolved_by,
            note=row.note,
            pending_reparse=row.pending_reparse,
        )
        for row in binding_rules.load(session, project_id, version, environment_keys)
    ]


def _iso(value: Any) -> str | None:
    """A manual binding may carry the text DHIS2 reported rather than a timestamp."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def get_uid_pool_status(project_id: str, *, session: Session) -> UidPoolStatus:
    """Available UIDs against the low-water mark (BP-REG-008a).

    The project's own mark lives in its ``generationPolicy`` section, which is artifact
    content; a query must not read that, so the configured default decides here and a
    caller that knows the project value passes it through ``pool_status`` directly.
    """
    return uid_pool.pool_status(session, project_id, low_water_mark=settings().uid_low_water_mark)


def set_environment_binding(
    session: Session,
    *,
    project_id: str,
    blueprint_version: str,
    environment_key: str,
    business_key: str,
    uid: str,
    note: str,
    resolved_by: str,
) -> BindingInfo:
    """A human fixes one binding: audited, and it makes no new version (BP-BR-14)."""
    row = binding_rules.set_manual(
        session,
        project_id=project_id,
        blueprint_version=blueprint_version,
        environment_key=environment_key,
        business_key=business_key,
        uid=uid,
        note=note,
        resolved_by=resolved_by,
    )
    return BindingInfo(
        environment_key=row.environment_key,
        business_key=row.business_key,
        uid=row.uid,
        resolution=BindingResolution(row.resolution),
        observed_hash=row.observed_hash,
        resolved_by=row.resolved_by,
        note=row.note,
        pending_reparse=row.pending_reparse,
    )


@external_io
def analyze_impact(
    project_id: str,
    business_keys: list[str],
    description: str | None = None,
    version: str | None = None,
) -> ImpactSet:
    """What changing these entries would reach: stages, entries, pages, artifact types.

    A planning question has no decided change level yet, so the answer is computed for
    the widest case, Major; ``promote_draft`` reports the real set once the level is
    known from the diff.  The impact set needs the page and intent sections, which are
    not mirrored in ``bp``, so the version content is read outside the transaction.
    """
    with unit_of_work() as session:
        target = version or version_service.require_current(session, project_id).version
        row = version_service.get_version(session, project_id, target)
        delivery_mode = DeliveryMode(row.delivery_mode)
        artifact_id = row.artifact_id
        indexed = list(
            session.execute(
                select(BusinessKeyIndexRow).where(
                    BusinessKeyIndexRow.project_id == project_id,
                    BusinessKeyIndexRow.blueprint_version == target,
                    BusinessKeyIndexRow.entry_key.in_(business_keys),
                )
            ).scalars()
        )
        downstream = graph_queries.downstream(session, project_id, target, business_keys)
    document = BlueprintDocument(artifacts.read_json(artifact_id)[0])
    changes = [
        Change(
            level=ChangeLevel.MAJOR,
            reason=description or "impact question raised by the caller",
            section=entry.section,
            path=entry.path,
            business_key=entry.entry_key,
        )
        for entry in indexed
    ]
    computed = impact_rules.compute_impact(
        document, changes, ChangeLevel.MAJOR, delivery_mode, downstream
    )
    return ImpactSet(
        sections=[Section(name) for name in computed["sections"]],
        stages=[DesignStage(name) for name in computed["stages"]],
        business_keys=computed["businessKeys"],
        page_keys=computed["pageKeys"],
        operation_keys=computed["operationKeys"],
        artifact_types=computed["artifactTypes"],
    )


__all__ = [
    "analyze_impact",
    "get_bindings",
    "get_current_version",
    "get_decisions",
    "get_entries",
    "get_managed_scope",
    "get_project_semantics",
    "get_registry",
    "get_uid_pool_status",
    "get_version",
    "is_mapping_frozen",
    "list_versions",
    "locate_entries",
    "set_environment_binding",
]
