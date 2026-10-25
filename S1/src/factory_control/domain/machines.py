"""The remaining S1 state machines: Gate, Deployment Run, Question, Change Request.

Same contract as :mod:`factory_control.domain.stage_machine`: an illegal move is
``409 INVALID_STATE`` and nothing else may write status (CP-BR-01).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from enum import Enum

from factory_control.domain.enums import (
    ChangeRequestStatus,
    GateStatus,
    QuestionStatus,
    RunStatus,
)
from factory_control.errors import InvalidStateError

GATE_TRANSITIONS: dict[GateStatus, frozenset[GateStatus]] = {
    # A Gate is created WAITING and ends in exactly one of three ways; a change
    # of the bound content moves it to INVALIDATED (CP-GATE-005, AC-GATE-01).
    GateStatus.WAITING: frozenset(
        {GateStatus.APPROVED, GateStatus.REJECTED, GateStatus.INVALIDATED}
    ),
    GateStatus.APPROVED: frozenset({GateStatus.INVALIDATED}),
    GateStatus.REJECTED: frozenset({GateStatus.INVALIDATED}),
    GateStatus.INVALIDATED: frozenset(),
}

RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.IN_PROGRESS, RunStatus.CANCELLED}),
    RunStatus.IN_PROGRESS: frozenset(
        {
            RunStatus.WAITING_APPROVAL,
            RunStatus.COMMITTED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.MANUAL_CHECK,
        }
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        {
            RunStatus.IN_PROGRESS,
            RunStatus.COMMITTED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.MANUAL_CHECK,
        }
    ),
    RunStatus.COMMITTED: frozenset(
        {RunStatus.VERIFIED, RunStatus.FAILED, RunStatus.MANUAL_CHECK}
    ),
    RunStatus.MANUAL_CHECK: frozenset(
        {RunStatus.IN_PROGRESS, RunStatus.COMMITTED, RunStatus.VERIFIED, RunStatus.FAILED}
    ),
    # OUTDATED is a marker, not a state (CP-WF-019), so these stay terminal.
    RunStatus.VERIFIED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

QUESTION_TRANSITIONS: dict[QuestionStatus, frozenset[QuestionStatus]] = {
    QuestionStatus.OPEN: frozenset(
        {QuestionStatus.ANSWERED, QuestionStatus.WITHDRAWN, QuestionStatus.SUPERSEDED}
    ),
    # CP-Q-007: an answer is immutable; changing one requires a new question.
    QuestionStatus.ANSWERED: frozenset(),
    QuestionStatus.WITHDRAWN: frozenset(),
    QuestionStatus.SUPERSEDED: frozenset(),
}

CR_TRANSITIONS: dict[ChangeRequestStatus, frozenset[ChangeRequestStatus]] = {
    ChangeRequestStatus.DRAFT: frozenset(
        {ChangeRequestStatus.ANALYZED, ChangeRequestStatus.CANCELLED}
    ),
    ChangeRequestStatus.ANALYZED: frozenset(
        {
            ChangeRequestStatus.APPROVED,
            ChangeRequestStatus.REJECTED,
            ChangeRequestStatus.CANCELLED,
        }
    ),
    ChangeRequestStatus.APPROVED: frozenset({ChangeRequestStatus.IN_PROGRESS}),
    ChangeRequestStatus.IN_PROGRESS: frozenset({ChangeRequestStatus.COMPLETED}),
    ChangeRequestStatus.COMPLETED: frozenset(),
    ChangeRequestStatus.REJECTED: frozenset(),
    ChangeRequestStatus.CANCELLED: frozenset(),
}


def _check[E: Enum](
    table: Mapping[E, Collection[E]],
    current: E | str,
    target: E | str,
    *,
    label: str,
    object_id: str | None,
    enum_cls: type[E],
) -> None:
    cur = current if isinstance(current, enum_cls) else enum_cls(str(current))
    tgt = target if isinstance(target, enum_cls) else enum_cls(str(target))
    if tgt in table[cur]:
        return
    raise InvalidStateError(
        f"{label} transition {cur.value} -> {tgt.value} is not allowed",
        details={
            "objectId": object_id,
            "from": cur.value,
            "to": tgt.value,
            "allowed": sorted(s.value for s in table[cur]),
        },
    )


def assert_gate_transition(
    current: GateStatus | str, target: GateStatus | str, *, gate_id: str | None = None
) -> None:
    _check(GATE_TRANSITIONS, current, target, label="gate", object_id=gate_id, enum_cls=GateStatus)


def assert_run_transition(
    current: RunStatus | str, target: RunStatus | str, *, run_id: str | None = None
) -> None:
    _check(RUN_TRANSITIONS, current, target, label="deployment run", object_id=run_id, enum_cls=RunStatus)


def assert_question_transition(
    current: QuestionStatus | str,
    target: QuestionStatus | str,
    *,
    question_id: str | None = None,
) -> None:
    _check(
        QUESTION_TRANSITIONS,
        current,
        target,
        label="question",
        object_id=question_id,
        enum_cls=QuestionStatus,
    )


def assert_cr_transition(
    current: ChangeRequestStatus | str,
    target: ChangeRequestStatus | str,
    *,
    cr_id: str | None = None,
) -> None:
    _check(
        CR_TRANSITIONS, current, target, label="change request", object_id=cr_id, enum_cls=ChangeRequestStatus
    )


def gate_can_act(current: GateStatus | str) -> bool:
    """Only a WAITING Gate accepts an approval or rejection (CP-GATE-006)."""
    status = current if isinstance(current, GateStatus) else GateStatus(str(current))
    return status is GateStatus.WAITING


def open_run_statuses() -> frozenset[RunStatus]:
    """Runs that still occupy their environment (CP-BR-12, CP-PRJ-004, CP-WF-018)."""
    return frozenset(
        {
            RunStatus.CREATED,
            RunStatus.IN_PROGRESS,
            RunStatus.WAITING_APPROVAL,
            RunStatus.MANUAL_CHECK,
        }
    )


def terminal_run_statuses() -> frozenset[RunStatus]:
    return frozenset({RunStatus.VERIFIED, RunStatus.FAILED, RunStatus.CANCELLED})


__all__ = [
    "CR_TRANSITIONS",
    "GATE_TRANSITIONS",
    "QUESTION_TRANSITIONS",
    "RUN_TRANSITIONS",
    "assert_cr_transition",
    "assert_gate_transition",
    "assert_question_transition",
    "assert_run_transition",
    "gate_can_act",
    "open_run_statuses",
    "terminal_run_statuses",
]
