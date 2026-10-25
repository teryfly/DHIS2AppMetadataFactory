"""Stage state machine — appendix A of the S1 SRS (CP-WF-003 / CP-WF-004).

Appendix A is the *only* legal set of transitions; anything else is
``409 INVALID_STATE``.  Transitions themselves are validated here, and
persisted by the services together with the event record and the audit entry
(CP-WF-005).
"""

from __future__ import annotations

from factory_control.domain.enums import StageStatus
from factory_control.errors import InvalidStateError

_TRANSITIONS: dict[StageStatus, frozenset[StageStatus]] = {
    StageStatus.LOCKED: frozenset({StageStatus.READY, StageStatus.SKIPPED, StageStatus.INVALIDATED}),
    StageStatus.READY: frozenset(
        {
            StageStatus.RUNNING,
            StageStatus.WAITING_APPROVAL,
            StageStatus.SKIPPED,
            StageStatus.INVALIDATED,
            StageStatus.LOCKED,
        }
    ),
    StageStatus.RUNNING: frozenset(
        {
            StageStatus.WAITING_INPUT,
            StageStatus.WAITING_APPROVAL,
            StageStatus.PASSED,
            StageStatus.FAILED,
            StageStatus.BLOCKED,
            StageStatus.MANUAL_CHECK,
            StageStatus.INVALIDATED,
        }
    ),
    StageStatus.WAITING_INPUT: frozenset(
        # READY is the rerun edge: a run waiting for an answer can be discarded and started
        # again, which is exactly what `stage.rerun` offers for this state.  Without it the
        # control was visible and the request answered 409.
        {StageStatus.RUNNING, StageStatus.READY, StageStatus.FAILED, StageStatus.INVALIDATED}
    ),
    StageStatus.WAITING_APPROVAL: frozenset(
        {StageStatus.PASSED, StageStatus.READY, StageStatus.INVALIDATED}
    ),
    StageStatus.PASSED: frozenset({StageStatus.RUNNING, StageStatus.INVALIDATED}),
    StageStatus.FAILED: frozenset({StageStatus.READY, StageStatus.INVALIDATED}),
    StageStatus.BLOCKED: frozenset({StageStatus.READY, StageStatus.INVALIDATED}),
    StageStatus.MANUAL_CHECK: frozenset({StageStatus.PASSED, StageStatus.FAILED}),
    StageStatus.INVALIDATED: frozenset({StageStatus.READY, StageStatus.LOCKED}),
    StageStatus.SKIPPED: frozenset({StageStatus.LOCKED, StageStatus.READY}),
}

#: States in which a stage is finished for the current pass and holds no claim on
#: the single-RUNNING slot (CP-WF-006).
SETTLED: frozenset[StageStatus] = frozenset(
    {
        StageStatus.PASSED,
        StageStatus.FAILED,
        StageStatus.BLOCKED,
        StageStatus.INVALIDATED,
        StageStatus.SKIPPED,
        StageStatus.LOCKED,
        StageStatus.READY,
    }
)

#: States that mean "a human is expected to do something".
AWAITING_HUMAN: frozenset[StageStatus] = frozenset(
    {StageStatus.WAITING_INPUT, StageStatus.WAITING_APPROVAL, StageStatus.MANUAL_CHECK}
)


def allowed_targets(current: StageStatus | str) -> frozenset[StageStatus]:
    return _TRANSITIONS[StageStatus(current if isinstance(current, str) else current.value)]


def can_transition(current: StageStatus | str, target: StageStatus | str) -> bool:
    cur, tgt = _coerce(current), _coerce(target)
    return tgt in _TRANSITIONS[cur]


def assert_transition(
    current: StageStatus | str, target: StageStatus | str, *, stage_id: str | None = None
) -> None:
    """Raise ``409 INVALID_STATE`` unless appendix A permits this move (CP-WF-004)."""
    cur, tgt = _coerce(current), _coerce(target)
    if tgt in _TRANSITIONS[cur]:
        return
    raise InvalidStateError(
        f"stage transition {cur.value} -> {tgt.value} is not allowed",
        stage=cur.value,
        details={
            "stageId": stage_id,
            "from": cur.value,
            "to": tgt.value,
            "allowed": sorted(s.value for s in _TRANSITIONS[cur]),
        },
    )


def _coerce(value: StageStatus | str) -> StageStatus:
    return value if isinstance(value, StageStatus) else StageStatus(value)


def is_terminal_for_progress(status: StageStatus | str) -> bool:
    """True when the stage cannot move forward without an explicit user action."""
    return _coerce(status) in SETTLED


__all__ = [
    "AWAITING_HUMAN",
    "SETTLED",
    "allowed_targets",
    "assert_transition",
    "can_transition",
    "is_terminal_for_progress",
]
