"""Normalized indexes rebuilt from a Blueprint version's content.

BP-VER-002: the version content in the ``BLUEPRINT`` artifact is the source of
truth; these rows exist to make queries cheap and are dropped and rebuilt for a
version at any time (promote, import, or an explicit repair call).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import ENTRY_CONTAINERS, EntryContainer
from factory_blueprint.graph.model import build_edges
from factory_blueprint.models.build import BuildRecordRow
from factory_blueprint.models.decision import DecisionRow
from factory_blueprint.models.index import BusinessKeyIndexRow, DependencyEdgeRow
from factory_blueprint.models.registry import RegistryEntryRow
from factory_blueprint.patch.businesskey import (
    KEY_FIELD_BY_CONTAINER,
    containers,
)


def delete_version_indexes(session: Session, project_id: str, blueprint_version: str) -> None:
    session.execute(
        delete(RegistryEntryRow).where(
            RegistryEntryRow.project_id == project_id,
            RegistryEntryRow.blueprint_version == blueprint_version,
        )
    )
    session.execute(
        delete(DecisionRow).where(
            DecisionRow.project_id == project_id,
            DecisionRow.blueprint_version == blueprint_version,
        )
    )
    session.execute(
        delete(BusinessKeyIndexRow).where(
            BusinessKeyIndexRow.project_id == project_id,
            BusinessKeyIndexRow.blueprint_version == blueprint_version,
        )
    )
    session.execute(
        delete(DependencyEdgeRow).where(
            DependencyEdgeRow.project_id == project_id,
            DependencyEdgeRow.blueprint_version == blueprint_version,
        )
    )
    session.execute(
        delete(BuildRecordRow).where(
            BuildRecordRow.project_id == project_id,
            BuildRecordRow.blueprint_version == blueprint_version,
        )
    )


def rebuild_indexes(
    session: Session,
    *,
    project_id: str,
    blueprint_version: str,
    document: dict[str, Any],
) -> None:
    """Recreate every derived index of one version from its content."""
    delete_version_indexes(session, project_id, blueprint_version)
    view = BlueprintDocument(document)
    _index_registry(session, project_id, blueprint_version, view)
    _index_decisions(session, project_id, blueprint_version, view)
    _index_keys(session, project_id, blueprint_version, view)
    _index_edges(session, project_id, blueprint_version, view)
    session.flush()


def _registry_types(view: BlueprintDocument) -> dict[str, str]:
    container = _container("/registry/entries")
    return {
        entry["businessKey"]: str(entry.get("dhis2Type") or "")
        for entry in view.container_entries(container)
        if isinstance(entry.get("businessKey"), str)
    }


def _container(path: str) -> EntryContainer:
    return next(container for container in ENTRY_CONTAINERS if container.path == path)


def _parse_moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _index_registry(
    session: Session, project_id: str, blueprint_version: str, view: BlueprintDocument
) -> None:
    container = _container("/registry/entries")
    for entry in view.container_entries(container):
        business_key = entry.get("businessKey")
        if not isinstance(business_key, str):
            continue
        session.add(
            RegistryEntryRow(
                project_id=project_id,
                blueprint_version=blueprint_version,
                business_key=business_key,
                dhis2_type=str(entry.get("dhis2Type") or ""),
                uid=entry.get("uid"),
                code=entry.get("code"),
                disposition=str(entry.get("disposition") or ""),
                managed=bool(entry.get("managed"))
                if "managed" in entry
                else str(entry.get("disposition")) in {"CREATE", "UPDATE", "DEPRECATED"},
                controlled_fields=list(entry.get("controlledFields") or []),
                status=str(entry.get("status") or "PROPOSED"),
                decision_id=entry.get("decisionId"),
                source_environment=entry.get("sourceEnvironment"),
                observed_last_updated=_parse_moment(entry.get("observedLastUpdated")),
                observed_hash=entry.get("observedHash"),
                discovery_result_id=entry.get("discoveryResultId"),
            )
        )


def _index_decisions(
    session: Session, project_id: str, blueprint_version: str, view: BlueprintDocument
) -> None:
    architecture = _container("/architecture/decisions")
    mapping = _container("/mapping/entries")
    rows: dict[str, DecisionRow] = {}

    for decision in view.container_entries(architecture):
        decision_id = decision.get("decisionId")
        if not isinstance(decision_id, str):
            continue
        rows[decision_id] = DecisionRow(
            project_id=project_id,
            blueprint_version=blueprint_version,
            decision_id=decision_id,
            type="ARCHITECTURE",
            subject_business_key=None,
            selected={"decision": decision.get("decision")},
            alternatives=list(decision.get("alternatives") or []),
            rationale=decision.get("context"),
            risks=list(decision.get("risks") or []),
            confidence=None,
            impact_level=None,
            confirmation=decision.get("confirmation"),
            affected_artifacts=[],
        )

    for entry in view.container_entries(mapping):
        decision_id = entry.get("decisionId")
        business_key = entry.get("businessKey")
        if not isinstance(decision_id, str) or not isinstance(business_key, str):
            continue
        row = rows.get(decision_id)
        if row is None:
            row = DecisionRow(
                project_id=project_id,
                blueprint_version=blueprint_version,
                decision_id=decision_id,
                type="MAPPING",
                selected=None,
                alternatives=[],
                risks=[],
                confidence=None,
                impact_level=None,
                confirmation=None,
                affected_artifacts=[],
            )
            rows[decision_id] = row
        row.type = row.type or "MAPPING"
        row.subject_business_key = business_key
        row.selected = row.selected or {
            "dhis2Type": entry.get("dhis2Type"),
            "structure": entry.get("structure"),
        }
        if entry.get("alternatives"):
            row.alternatives = list(entry["alternatives"])
        if entry.get("risks"):
            row.risks = list(entry["risks"])
        row.confidence = entry.get("confidence", row.confidence)
        row.affected_artifacts = list(entry.get("affectedArtifacts") or [])
        highest = _highest_risk_level(entry.get("risks") or [])
        if highest:
            row.impact_level = highest

    session.add_all(rows.values())


def _highest_risk_level(risks: list[Any]) -> str | None:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    levels = [
        str(risk.get("level"))
        for risk in risks
        if isinstance(risk, dict) and risk.get("level")
    ]
    if not levels:
        return None
    return max(levels, key=lambda level: order.get(level, 0))


def _index_keys(
    session: Session, project_id: str, blueprint_version: str, view: BlueprintDocument
) -> None:
    types = _registry_types(view)
    page_of_intent: dict[str, str] = {}
    for intent in view.container_entries(_container("/apiIntents/items")):
        intent_key = intent.get("intentKey")
        page_key = intent.get("pageKey")
        if isinstance(intent_key, str) and isinstance(page_key, str):
            page_of_intent[intent_key] = page_key

    for container, entries in containers(view.data):
        key_field = KEY_FIELD_BY_CONTAINER[container.path]
        for entry in entries:
            entry_key = entry.get(key_field)
            if not isinstance(entry_key, str):
                continue
            uid = entry.get("uid") if isinstance(entry.get("uid"), str) else None
            session.add(
                BusinessKeyIndexRow(
                    project_id=project_id,
                    blueprint_version=blueprint_version,
                    section=container.section.value,
                    key_kind=container.key_field,
                    entry_key=entry_key,
                    path=f"{container.path}/{entry_key}",
                    dhis2_type=entry.get("dhis2Type") if uid else types.get(entry_key),
                    uid=uid,
                    decision_id=entry.get("decisionId")
                    if isinstance(entry.get("decisionId"), str)
                    else None,
                    page_key=_page_key_of(container, entry, page_of_intent),
                )
            )
            for name in container.secondary_keys:
                secondary = entry.get(name)
                if not isinstance(secondary, str):
                    continue
                session.add(
                    BusinessKeyIndexRow(
                        project_id=project_id,
                        blueprint_version=blueprint_version,
                        section=container.section.value,
                        key_kind=name,
                        entry_key=secondary,
                        path=f"{container.path}/{entry_key}/{name}",
                        page_key=_page_key_of(container, entry, page_of_intent),
                    )
                )


def _page_key_of(
    container: EntryContainer,
    entry: dict[str, Any],
    page_of_intent: dict[str, str],
) -> str | None:
    page_key = entry.get("pageKey")
    if isinstance(page_key, str):
        return page_key
    if container.path in ("/apiIntents/items", "/uiIntents/items"):
        intent_key = entry.get("intentKey")
        if isinstance(intent_key, str):
            return page_of_intent.get(intent_key)
    return None


def _index_edges(
    session: Session, project_id: str, blueprint_version: str, view: BlueprintDocument
) -> None:
    """Field-level reference edges between Registry entries (BP-TD-006)."""
    for edge in build_edges(view.data):
        session.add(
            DependencyEdgeRow(
                project_id=project_id,
                blueprint_version=blueprint_version,
                **edge.as_row(),
            )
        )


def rebuild_all_versions(session: Session, project_id: str, documents: dict[str, dict[str, Any]]) -> None:
    for blueprint_version, document in documents.items():
        rebuild_indexes(
            session, project_id=project_id, blueprint_version=blueprint_version, document=document
        )
