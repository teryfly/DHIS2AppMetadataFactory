"""Budget state (AI-COST-002, AI-COST-003).

AI-COST-003 fixes *when* the judgement is made: at run start, against cost already
incurred.  A run that crosses the limit mid-flight finishes rather than being cut off,
because a stopped half-way would leave the draft carrying part of one agent's patch — a
worse outcome than one run's overshoot.  So this module answers a single question, "should
a new run start", and never interrupts one in progress.

Enforcement begins when a limit exists.  A project with no ``ai.budget`` row, or a row
whose ``limit`` is null, is unlimited — the rule and the switch are separable, which is why
the engine shipped in the MVP while :func:`put` (the operator switch, AI-GOV-007) is
delivered with M4: AI-AC-09 could be written against a seeded row instead of waiting.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TypeVar

from factory_foundation.api import (
    current_session,
    has_active_uow,
    unit_of_work,
)
from sqlalchemy.orm import Session

from factory_intelligence import audit_log
from factory_intelligence.cost.usage import total_spent
from factory_intelligence.dto.result import BudgetView
from factory_intelligence.enums import BudgetState
from factory_intelligence.errors import BudgetInvalid
from factory_intelligence.models.cost import BudgetRow

T = TypeVar("T")


def budget_for(project_id: str, *, session: Session | None = None) -> BudgetRow | None:
    """The project's budget row, or ``None`` when it is unlimited by absence."""

    def _read(sess: Session) -> BudgetRow | None:
        return sess.get(BudgetRow, project_id)

    return _read_only(session, _read)


def view(
    project_id: str, *, spent: float | None = None, session: Session | None = None
) -> BudgetView:
    """The project's budget position, as reported in every run result (AI-RT-017)."""
    row = budget_for(project_id, session=session)
    if row is None:
        # no row is not "zero budget": it is no budget statement at all
        return BudgetView(state=BudgetState.UNSET, spent=0.0 if spent is None else spent)
    spent_value = total_spent(project_id, session=session) if spent is None else spent
    if row.limit is None:
        return BudgetView(
            state=BudgetState.OK, spent=spent_value, limit=None, currency=row.currency
        )
    state, reached = classify(spent_value, row.limit, [float(item) for item in row.thresholds])
    return BudgetView(
        state=state,
        spent=spent_value,
        limit=row.limit,
        currency=row.currency,
        threshold_reached=reached,
    )


def classify(
    spent: float, limit: float, thresholds: Sequence[float]
) -> tuple[BudgetState, float | None]:
    """Pure so the arithmetic is testable without a database.

    Over the limit is :attr:`BudgetState.EXCEEDED`; between the highest crossed threshold
    and the limit it is :attr:`BudgetState.WARNING`, which AI-COST-002 reports *in the run
    result* rather than raising — an alert that interrupts a run would be the same
    half-finished-draft problem AI-COST-003 refuses.
    """
    if spent > limit:
        return BudgetState.EXCEEDED, None
    crossed = [float(step) for step in thresholds if 0.0 < float(step) < 1.0 and spent >= limit * float(step)]
    if crossed:
        return BudgetState.WARNING, max(crossed)
    return BudgetState.OK, None


def may_start_run(budget: BudgetView) -> bool:
    """AI-COST-002: over budget refuses a *new* run; nothing else is affected.

    Human authoring never comes through here — S3 only serves agent runs, and manual
    editing goes to S2 directly, which is how "人工编写模式不受影响" holds without S3 having
    to know about it.
    """
    return budget.state is not BudgetState.EXCEEDED


def put(
    project_id: str,
    limit: float | None,
    thresholds: Sequence[float],
    *,
    currency: str = "USD",
    operator: str,
    session: Session | None = None,
) -> BudgetView:
    """AI-COST-002 as set by an operator (AI-GOV-007, M4): write the limit, audited.

    ``limit=None`` is a statement, not a deletion: the row is written with no limit, so the
    project answers ``OK`` with the currency and the person who decided it, rather than
    falling back to ``UNSET`` and losing the decision that there is no cap here.

    Enforcement is *not* what this switches.  :func:`may_start_run` already obeys any row in
    the table, which is why AI-AC-09 could be written before the switch existed.
    """
    checked_limit = _checked_limit(limit)
    checked_steps = _checked_thresholds(thresholds)
    checked_currency = _checked_currency(currency)

    def _write(sess: Session) -> BudgetView:
        row = sess.get(BudgetRow, project_id)
        before = (
            None
            if row is None
            else {
                "limit": row.limit,
                "thresholds": [float(item) for item in row.thresholds],
                "currency": row.currency,
            }
        )
        if row is None:
            row = BudgetRow(project_id=project_id)
            sess.add(row)
        row.limit = checked_limit
        # a new list, never an in-place edit: JSONB columns report no change on mutation
        row.thresholds = checked_steps
        row.currency = checked_currency
        row.updated_by = operator
        sess.flush()
        audit_log.record(
            "ai.budget.updated",
            session=sess,
            object_type="Budget",
            object_id=project_id,
            project_id=project_id,
            before=before,
            after={
                "limit": checked_limit,
                "thresholds": checked_steps,
                "currency": checked_currency,
            },
            operator=operator,
            operator_is_user=True,
        )
        return view(project_id, session=sess)

    if session is not None or has_active_uow():
        return _write(session if session is not None else current_session())
    with unit_of_work() as sess:
        return _write(sess)


def _checked_limit(limit: float | None) -> float | None:
    if limit is None:
        return None
    value = float(limit)
    if not math.isfinite(value) or value < 0.0:
        raise BudgetInvalid(
            "a budget limit is a non-negative finite amount, or no limit at all",
            details={"limit": limit},
        )
    return value


def _checked_thresholds(thresholds: Sequence[float]) -> list[float]:
    """AI-COST-002's warning steps, as fractions of the limit.

    A step at or below zero warns on the first call and one at or above 1.0 can never be
    reached before ``EXCEEDED``, so both would be a silently useless threshold; duplicates
    only repeat the same warning.  Stored sorted, because :func:`classify` reports the
    *highest* crossed step and a caller should not have to know that.
    """
    steps: list[float] = []
    for item in thresholds:
        value = float(item)
        if not math.isfinite(value) or not 0.0 < value < 1.0:
            raise BudgetInvalid(
                "budget thresholds are fractions of the limit, strictly between 0 and 1",
                details={"thresholds": [float(entry) for entry in thresholds]},
            )
        if value not in steps:
            steps.append(value)
    return sorted(steps)


def _checked_currency(currency: str) -> str:
    """A three-letter code, upper-cased.

    ``ai.model`` prices are per-1k amounts in a single currency and carry no currency column,
    so the budget's currency is the deployment's statement about those prices; letting "usd ",
    "Dollars" or an empty string through would make ``spent > limit`` compare two different
    monies without any way to notice.
    """
    code = str(currency or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise BudgetInvalid(
            "budget currency is a three-letter ISO-4217 code",
            details={"currency": currency},
        )
    return code


def _read_only(session: Session | None, action: Callable[[Session], T]) -> T:
    if session is not None:
        return action(session)
    if has_active_uow():
        return action(current_session())
    with unit_of_work() as sess:
        return action(sess)


__all__ = ["budget_for", "classify", "may_start_run", "put", "view"]
