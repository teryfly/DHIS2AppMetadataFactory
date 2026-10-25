"""Project data cleanup inside the ``bp`` schema (BP-EXP-003).

CP-PRJ-007 lets S1 drop a project's Blueprint data.  The UID ledger is the one thing
that stays: a UID this project was handed a number for may never be handed to anyone
else (FR-REG-004, BP-REG-006), so active allocations are retired rather than deleted
— and ``bp.uid_allocation`` carries a database trigger that refuses the DELETE
anyway, which is what turns the rule into a structural property.
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import SystemClock, current_session
from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.orm import Session

from factory_blueprint.audit_log import record
from factory_blueprint.dto.portability import ProjectCleanupResult
from factory_blueprint.enums import UidAllocationStatus
from factory_blueprint.models.binding import EnvironmentBindingRow
from factory_blueprint.models.build import BuildRecordRow, ValidationRunRow
from factory_blueprint.models.decision import DecisionRow
from factory_blueprint.models.draft import BlueprintDraftRow, BlueprintPatchRow
from factory_blueprint.models.index import BusinessKeyIndexRow, DependencyEdgeRow
from factory_blueprint.models.portability import ImportBatchRow
from factory_blueprint.models.prepared import PreparedContentRow
from factory_blueprint.models.registry import RegistryEntryRow
from factory_blueprint.models.uid import UidAllocationRow, UidPoolRow
from factory_blueprint.models.version import BlueprintVersionRow

#: everything that belongs to a project, in deletion order
_PROJECT_TABLES: tuple[Any, ...] = (
    ValidationRunRow,
    BuildRecordRow,
    DependencyEdgeRow,
    BusinessKeyIndexRow,
    DecisionRow,
    EnvironmentBindingRow,
    RegistryEntryRow,
    BlueprintPatchRow,
    BlueprintDraftRow,
    BlueprintVersionRow,
    ImportBatchRow,
    PreparedContentRow,
    UidPoolRow,
)


def delete_project_data(project_id: str, *, session: Session | None = None) -> ProjectCleanupResult:
    """Remove every ``bp`` row of one project, keeping the UID ledger (BP-EXP-003)."""
    sess = session if session is not None else current_session()
    scope = _Scope(
        sess,
        project_id,
        draft_ids=list(
            sess.execute(
                select(BlueprintDraftRow.id).where(BlueprintDraftRow.project_id == project_id)
            ).scalars()
        ),
        build_ids=list(
            sess.execute(
                select(BuildRecordRow.id).where(BuildRecordRow.project_id == project_id)
            ).scalars()
        ),
    )
    deleted = scope.delete_all()
    retired = scope.retire_uids()
    ledger = scope.uid_ledger_size()
    record(
        "bp.project.data_deleted",
        object_type="project",
        object_id=project_id,
        project_id=project_id,
        after={"deleted": deleted, "retiredUids": retired, "retainedUidLedger": ledger},
        session=sess,
    )
    return ProjectCleanupResult(
        project_id=project_id, deleted=deleted, retired_uids=retired, retained_uids=ledger
    )


class _Scope:
    """One project's rows, seen through the predicate that selects them.

    ``blueprint_patch`` and ``validation_run`` have no project column: they hang off a
    draft and a build, so they go with their parent's keys.
    """

    def __init__(self, session: Session, project_id: str, *, draft_ids: list[str], build_ids: list[str]) -> None:
        self.session = session
        self.project_id = project_id
        self.draft_ids = draft_ids
        self.build_ids = build_ids

    def condition(self, model: Any) -> ColumnElement[bool] | None:
        """The predicate selecting this project's rows in one table."""
        column = {
            "blueprint_patch": "draft_id",
            "validation_run": "build_id",
        }.get(str(model.__tablename__), "project_id")
        values: list[str] = (
            self.draft_ids
            if column == "draft_id"
            else self.build_ids
            if column == "build_id"
            else [self.project_id]
        )
        if not values:
            return None
        element: ColumnElement[bool] = getattr(model, column).in_(values)
        return element

    def delete_all(self) -> dict[str, int]:
        deleted: dict[str, int] = {}
        for model in _PROJECT_TABLES:
            condition = self.condition(model)
            if condition is None:
                continue
            count = int(
                self.session.execute(
                    select(func.count()).select_from(model).where(condition)
                ).scalar_one()
            )
            if not count:
                continue
            self.session.execute(delete(model).where(condition))
            deleted[str(model.__tablename__)] = count
        self.session.flush()
        return {name: deleted[name] for name in sorted(deleted)}

    def retire_uids(self) -> int:
        active = list(
            self.session.execute(
                select(UidAllocationRow.uid).where(
                    UidAllocationRow.project_id == self.project_id,
                    UidAllocationRow.status == UidAllocationStatus.ACTIVE.value,
                )
            ).scalars()
        )
        if active:
            self.session.execute(
                update(UidAllocationRow)
                .where(UidAllocationRow.uid.in_(active))
                .values(
                    status=UidAllocationStatus.RETIRED.value, retired_at=SystemClock().now()
                )
            )
        self.session.flush()
        return len(active)

    def uid_ledger_size(self) -> int:
        return int(
            self.session.execute(
                select(func.count(UidAllocationRow.uid)).where(
                    UidAllocationRow.project_id == self.project_id
                )
            ).scalar_one()
        )
