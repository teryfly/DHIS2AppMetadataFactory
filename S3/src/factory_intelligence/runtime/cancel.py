"""``cancel_agent_run`` — request a stop, do not force one (AI-RT-020, AI-AC-08).

Sets a flag and returns.  The run stops at its next safe point — before a model call, after
one, before the commit — because anything coarser would leave the draft carrying half of one
agent's patch, which is the same partial-state outcome AI-COST-003 refuses for a budget.

The flag is in the database rather than in memory because S1's cancellation arrives on a
different Temporal worker than the activity that is running (S1 also passes ``cancel=None``,
so no in-process token exists to trip).
"""

from __future__ import annotations

from factory_foundation.api import unit_of_work

from factory_intelligence.enums import RunState
from factory_intelligence.runtime import state as run_state


def request_cancel(run_id: str, *, reason: str | None = None) -> None:
    """Ask for cancellation; ``None`` returns when nothing is running to cancel.

    Cancelling a finished run is a no-op rather than an error: S1's activity cancellation is
    not synchronised with the run reaching its own end, and an error there would surface as a
    failed cancellation for a run that simply finished first.
    """
    with unit_of_work() as session:
        row = run_state.get_run(run_id, session=session)
        if row is None or row.status != RunState.RUNNING.value:
            return
        run_state.request_cancel(run_id, session=session, reason=reason)


__all__ = ["request_cancel"]
