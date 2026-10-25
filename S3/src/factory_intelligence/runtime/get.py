"""``get_agent_run`` — the durable state of one run, as AI-RT-019 defines the query.

Answered from the ``ai`` rows only.  Three things the spec asks for and a result does not
carry are assembled here from their own tables:

* **``RUNNING``** (AI-AC-14).  A run still executing is reported as running, with its most
  recent progress event, rather than being forced into one of AI-RT-017's six terminal
  statuses.  Reporting a still-running run as ``WAITING_INPUT`` — which is what this
  function used to do — makes S1 park a stage that nobody is waiting on.
* **Per-model denial reasons and the rejected artifacts** (AI-POL-007, AI-RT-003's
  refusal): ``ai.policy_decision`` holds exactly those two lists per attempt.
* **Budget state** (AI-COST-002): the same reading the runtime reports on a finished turn.

Question *texts* are not returned: S1 stores and displays Questions (SRS 1.3 puts that
outside S3), and reading the run-context artifact would make this read-only function
perform external I/O — which S1 is entitled to call inside its own transaction
(``cancel_agent_run`` and ``get_agent_run`` are not in its external-I/O list).  The local
ids are returned, which is what a caller joins its own rows on.
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import unit_of_work
from sqlalchemy import select

from factory_intelligence.cost import budget as budget_service
from factory_intelligence.dto.result import AgentRunResult
from factory_intelligence.enums import RunState
from factory_intelligence.models.policy import PolicyDecisionRow
from factory_intelligence.runtime import state as run_state


def project(run_id: str) -> AgentRunResult | None:
    """The run as S1's port expects it, or ``None`` when there is no such run."""
    with unit_of_work() as session:
        row = run_state.get_run(run_id, session=session)
        if row is None:
            return None
        decision = _latest_decision(session, run_id)
        running = row.status == RunState.RUNNING.value
        return AgentRunResult(
            run_id=row.id,
            # a run in flight is reported as in flight (AI-RT-019, AI-AC-14); S1 corrects
            # nothing, because there is nothing to correct
            status=RunState(row.status),
            summary=row.summary,
            confidence=row.confidence,
            patch_ids=list(row.patch_ids or []),
            agent_output_artifact_id=row.output_artifact_id,
            questions=[],
            question_local_ids=[str(item) for item in (row.question_local_ids or [])],
            last_progress=dict(row.last_progress or {}),
            denial_reasons=_reasons(row, decision, running=running),
            rejected_artifacts=_rejected(decision),
            budget_status=budget_service.view(row.project_id, session=session).as_result_field(),
            issues=[],
        )


def _latest_decision(session: Any, run_id: str) -> PolicyDecisionRow | None:
    """The judgement a caller is reading: the newest refusal, else the newest decision."""
    row: PolicyDecisionRow | None = (
        session.execute(
        select(PolicyDecisionRow)
        .where(PolicyDecisionRow.run_id == run_id)
        .order_by(PolicyDecisionRow.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    return row


def _reasons(row: Any, decision: PolicyDecisionRow | None, *, running: bool) -> list[str]:
    """AI-RT-019's 每个模型的拒绝原因 — one entry per model that was tried and refused."""
    out: list[str] = []
    if decision is not None:
        for item in decision.model_denials or []:
            if isinstance(item, dict):
                out.append(str(item.get("reason") or item))
            else:
                out.append(str(item))
    if row.error_message:
        out.append(str(row.error_message))
    if running and not out:
        progress = dict(row.last_progress or {})
        out.append(f"the run is still executing at step {progress.get('step') or 'unknown'!r}")
    return out


def _rejected(decision: PolicyDecisionRow | None) -> list[dict[str, Any]]:
    if decision is None:
        return []
    return [dict(item) for item in decision.denied_items or [] if isinstance(item, dict)]


__all__ = ["project"]
