"""Managed Scope per project × environment (BP-REG-011, D-03)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_blueprint.dto.registry import ManagedObject
from factory_blueprint.enums import Disposition
from factory_blueprint.models.binding import EnvironmentBindingRow
from factory_blueprint.models.registry import RegistryEntryRow

#: the dispositions a project is responsible for in an environment
MANAGED_DISPOSITIONS: tuple[str, ...] = (
    Disposition.CREATE.value,
    Disposition.UPDATE.value,
    Disposition.DEPRECATED.value,
)


def managed_scope(
    session: Session, project_id: str, blueprint_version: str, environment_key: str
) -> list[ManagedObject]:
    """Entries this project creates, updates or deprecates in one environment.

    The UID is the environment's view of the object: a resolved binding wins, and
    otherwise a CREATE or UPDATE entry carries the version's own UID, which is the
    same in every environment (D-02).
    """
    bindings: dict[str, EnvironmentBindingRow] = {
        row.business_key: row
        for row in session.execute(
            select(EnvironmentBindingRow).where(
                EnvironmentBindingRow.project_id == project_id,
                EnvironmentBindingRow.blueprint_version == blueprint_version,
                EnvironmentBindingRow.environment_key == environment_key,
            )
        ).scalars()
    }
    entries = session.execute(
        select(RegistryEntryRow).where(
            RegistryEntryRow.project_id == project_id,
            RegistryEntryRow.blueprint_version == blueprint_version,
            RegistryEntryRow.disposition.in_(MANAGED_DISPOSITIONS),
        )
    ).scalars()

    scope: list[ManagedObject] = []
    for row in entries:
        binding = bindings.get(row.business_key)
        uid = binding.uid if binding is not None else row.uid
        if not uid:
            continue
        observed_at = (
            binding.observed_last_updated if binding is not None else row.observed_last_updated
        )
        observed_hash = binding.observed_hash if binding is not None else row.observed_hash
        scope.append(
            ManagedObject(
                business_key=row.business_key,
                dhis2_type=row.dhis2_type,
                uid=uid,
                disposition=Disposition(row.disposition),
                code=row.code,
                controlled_fields=list(row.controlled_fields or []),
                observed_last_updated=observed_at.isoformat() if observed_at else None,
                observed_hash=observed_hash,
            )
        )
    return sorted(scope, key=lambda item: (item.dhis2_type, item.business_key))


def is_managed(disposition: Disposition | str) -> bool:
    return Disposition(disposition).value in MANAGED_DISPOSITIONS
