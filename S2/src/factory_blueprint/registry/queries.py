"""Reading the Registry, decisions and minimal entry sets (S2 SRS 7.2, 7.4)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_blueprint.dto.registry import DecisionInfo, ObservationBaseline, RegistryEntry
from factory_blueprint.enums import (
    BindingResolution,
    Disposition,
    RegistryEntryStatus,
    Section,
)
from factory_blueprint.models.binding import EnvironmentBindingRow
from factory_blueprint.models.decision import DecisionRow
from factory_blueprint.models.index import BusinessKeyIndexRow
from factory_blueprint.models.registry import RegistryEntryRow


def registry_entries(
    session: Session,
    project_id: str,
    blueprint_version: str,
    *,
    environment_key: str | None = None,
) -> list[RegistryEntry]:
    """The version's Registry, optionally annotated with one environment's UIDs."""
    bindings: dict[str, str] = {}
    if environment_key is not None:
        bindings = {
            row.business_key: row.uid
            for row in session.execute(
                select(EnvironmentBindingRow).where(
                    EnvironmentBindingRow.project_id == project_id,
                    EnvironmentBindingRow.blueprint_version == blueprint_version,
                    EnvironmentBindingRow.environment_key == environment_key,
                )
            ).scalars()
        }
    rows = session.execute(
        select(RegistryEntryRow)
        .where(
            RegistryEntryRow.project_id == project_id,
            RegistryEntryRow.blueprint_version == blueprint_version,
        )
        .order_by(RegistryEntryRow.dhis2_type, RegistryEntryRow.business_key)
    ).scalars()
    entries: list[RegistryEntry] = []
    for row in rows:
        entries.append(
            RegistryEntry(
                business_key=row.business_key,
                dhis2_type=row.dhis2_type,
                uid=row.uid,
                code=row.code,
                disposition=Disposition(row.disposition),
                managed=row.managed,
                controlled_fields=list(row.controlled_fields or []),
                status=RegistryEntryStatus(row.status),
                decision_id=row.decision_id,
                observed=ObservationBaseline(
                    source_environment=row.source_environment,
                    observed_last_updated=(
                        row.observed_last_updated.isoformat() if row.observed_last_updated else None
                    ),
                    observed_hash=row.observed_hash,
                    discovery_result_id=row.discovery_result_id,
                ),
                environment_bindings=({environment_key: bindings[row.business_key]}
                                     if environment_key and row.business_key in bindings
                                     else {}),
            )
        )
    return entries


def decisions(
    session: Session, project_id: str, blueprint_version: str, decision_ids: list[str]
) -> list[DecisionInfo]:
    """BP-DEC-006: decision detail for a Gate summary (CP-GATE-019)."""
    statement = select(DecisionRow).where(
        DecisionRow.project_id == project_id, DecisionRow.blueprint_version == blueprint_version
    )
    if decision_ids:
        statement = statement.where(DecisionRow.decision_id.in_(decision_ids))
    rows = session.execute(statement.order_by(DecisionRow.decision_id)).scalars()
    return [
        DecisionInfo(
            decision_id=row.decision_id,
            type=row.type,
            subject=row.subject_business_key,
            selected=row.selected,
            alternatives=list(row.alternatives or []),
            rationale=row.rationale,
            risks=list(row.risks or []),
            confidence=row.confidence,
            impact_level=row.impact_level,
            confirmation=row.confirmation,
            affected_artifacts=list(row.affected_artifacts or []),
        )
        for row in rows
    ]


def find_paths(
    session: Session,
    project_id: str,
    blueprint_version: str,
    *,
    keys: list[str] | None = None,
    decision_ids: list[str] | None = None,
    page_keys: list[str] | None = None,
    sections: list[Section | str] | None = None,
) -> list[tuple[str, str, str]]:
    """(section, entry key, document path) for a minimal retrieval set (FR-AG-001)."""
    statement = select(BusinessKeyIndexRow).where(
        BusinessKeyIndexRow.project_id == project_id,
        BusinessKeyIndexRow.blueprint_version == blueprint_version,
    )
    if keys:
        statement = statement.where(BusinessKeyIndexRow.entry_key.in_(keys))
    if decision_ids:
        statement = statement.where(BusinessKeyIndexRow.decision_id.in_(decision_ids))
    if page_keys:
        statement = statement.where(BusinessKeyIndexRow.page_key.in_(page_keys))
    if sections:
        names = [Section(section).value for section in sections]
        statement = statement.where(BusinessKeyIndexRow.section.in_(names))
    return sorted(
        (row.section, row.entry_key, row.path) for row in session.execute(statement).scalars()
    )


def binding_rows(
    session: Session, project_id: str, blueprint_version: str
) -> list[dict[str, Any]]:
    return [
        {
            "environmentKey": row.environment_key,
            "businessKey": row.business_key,
            "uid": row.uid,
            "resolution": BindingResolution(row.resolution).value,
            "observedHash": row.observed_hash,
            "discoveryResultId": row.discovery_result_id,
            "resolvedBy": row.resolved_by,
            "note": row.note,
            "pendingReparse": row.pending_reparse,
        }
        for row in session.execute(
            select(EnvironmentBindingRow)
            .where(
                EnvironmentBindingRow.project_id == project_id,
                EnvironmentBindingRow.blueprint_version == blueprint_version,
            )
            .order_by(EnvironmentBindingRow.environment_key, EnvironmentBindingRow.business_key)
        ).scalars()
    ]
