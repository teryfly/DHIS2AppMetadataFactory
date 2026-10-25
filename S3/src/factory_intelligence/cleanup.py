"""Project data cleanup (AI-DEL-001, CP-PRJ-007).

S1 calls this with its own live session (``factory_control/services/transfers.py:331``) and
purges artifact *content* afterwards itself, so this function performs no external I/O at all:
it deletes rows and nothing else.  That is also why it must not commit — the deletion, S1's
purge audit and the project removal are one transaction (00 doc 6.2).

The retention rule is stated per table in :data:`factory_intelligence.models.DELETABLE_TABLES`
and is not incidental: ``ai.model_call`` survives because AI-DEL-001 asks that usage be
retained as "汇总值（不含内容）", and those rows are exactly that — token counts, a model name
and a cost, with no prompt, no answer and no artifact content.  The immutability trigger on
that table would refuse the delete anyway, which is the point of the trigger.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from factory_foundation.api import (
    AiBase,
    current_session,
    has_active_uow,
    unit_of_work,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Mapper, Session

from factory_intelligence import audit_log
from factory_intelligence.models import DELETABLE_TABLES

T = TypeVar("T")

#: What the cleanup leaves behind, reported in the audit event so a reader of the trail does
#: not have to know the schema to see that the usage ledger survived.
RETAINED_TABLES: tuple[str, ...] = (
    "model_call",
    "org_policy",
    "policy_relaxation",
    "budget",
    "provider",
    "model",
    "prompt_version",
    "prompt_activation",
    "regression_ref",
    "eval_run",
    "eval_case_result",
)


def _mapped(name: str) -> Mapper[Any]:
    """The mapper for one ``ai`` table, resolved from the ORM metadata.

    Resolved rather than imported by name so a table added to ``ai`` without a decision here
    fails at once: the scaffold test asserts that every table is either deletable or retained.
    """
    table = AiBase.metadata.tables.get(f"ai.{name}")
    if table is None:
        raise KeyError(f"there is no ai table named {name!r}")
    for mapper in AiBase.registry.mappers:
        if mapper.local_table is table:
            return mapper
    raise KeyError(f"ai table {name!r} has no mapped class")


def delete_project_data(project_id: str, *, session: Session | None = None) -> dict[str, int]:
    """Delete this project's S3 rows and report how many of each went.

    Order is load-bearing: ``DELETABLE_TABLES`` lists child rows before their parents, since
    ``ai`` keeps real foreign keys within its own schema (eval case results before evaluation
    runs, run context before the run itself).
    """

    def _write(sess: Session) -> dict[str, int]:
        removed: dict[str, int] = {}
        for name in DELETABLE_TABLES:
            model = _mapped(name).class_
            column = getattr(model, "project_id", None)
            if column is None:  # pragma: no cover - guarded by the scaffold test
                raise KeyError(f"ai.{name} has no project_id column to delete by")
            counted = int(
                sess.execute(
                    select(func.count()).select_from(model).where(column == project_id)
                ).scalar_one()
            )
            removed[name] = counted
            if counted:
                sess.execute(model.__table__.delete().where(column == project_id))
        audit_log.record(
            "ai.project.data_deleted",
            session=sess,
            object_type="project",
            object_id=project_id,
            project_id=project_id,
            after={"removedRows": removed, "retained": list(RETAINED_TABLES)},
        )
        sess.flush()
        return removed

    return _in(session, _write)


def summarize_retained_usage(project_id: str, *, session: Session | None = None) -> dict[str, Any]:
    """What the ledger still says about a project whose run rows are gone (AI-COST-001)."""

    def _read(sess: Session) -> dict[str, Any]:
        from factory_intelligence.cost.usage import rollup

        rows = rollup(project_id, group_by=["stage", "agent", "model"], session=sess)
        return {
            "groups": len(rows),
            "calls": sum(row.calls for row in rows),
            "estimatedCost": round(sum(row.estimated_cost for row in rows), 6),
        }

    return _in(session, _read)


def _in(session: Session | None, action: Callable[[Session], T]) -> T:
    if session is not None:
        return action(session)
    if has_active_uow():
        return action(current_session())
    with unit_of_work() as sess:
        return action(sess)


__all__ = ["delete_project_data", "summarize_retained_usage", "RETAINED_TABLES"]
