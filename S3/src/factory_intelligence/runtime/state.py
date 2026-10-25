"""``ai`` row access for the runtime (AI-RT-001, AI-RT-017, AI-AC-05/08).

Every function here takes the caller's ``Session`` when there is one, because the run row,
its policy decision, its confirmations, the submitted patch, the ``AGENT_OUTPUT`` artifact
and the audit record all commit together (00 doc 6.2, D-11) — a state change that committed
apart from its audit entry is the exact failure D-11 exists to prevent.

Locking is explicit: :func:`lock_run` uses ``FOR UPDATE``.  Two concurrent attempts of the
same idempotency key — a Temporal retry while the first attempt is still alive — must not
both decide a run is theirs to finish, and AI-AC-05's "same key, same run" is only true if
the second caller waits for the first's row rather than racing it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from factory_foundation.api import (
    NotFoundError,
    current_session,
    has_active_uow,
    new_id,
    unit_of_work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_intelligence import audit_log
from factory_intelligence.deps import get_clock
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.enums import RunState
from factory_intelligence.models.policy import PolicyDecisionRow
from factory_intelligence.models.run import (
    AgentRunContextRow,
    AgentRunRow,
    ConfirmationRecordRow,
)

T = TypeVar("T")


def new_run_id() -> str:
    return new_id("arun")


def create_run(request: AgentRunRequest, *, run_id: str, session: Session) -> AgentRunRow:
    """Insert the ``RUNNING`` row before any external work begins (AI-RT-001).

    Recorded first and updated later, rather than written at the end: a run that dies in a
    model call still has to be visible as *that* run, or the next attempt cannot tell
    "resuming" from "starting over", which is what AI-RT-020 is about.
    """
    row = AgentRunRow(
        id=run_id,
        project_id=request.project_id,
        workflow_id=request.workflow_id,
        stage=request.stage,
        stage_id=request.stage_id,
        agent_type=request.agent_type,
        scope=list(request.scope),
        draft_id=request.draft_id,
        design_reference_environment_key=request.design_reference_environment_key,
        discovery_result_artifact_id=request.discovery_result_artifact_id,
        capability_profile_artifact_id=request.capability_profile_artifact_id,
        input_artifact_ids=list(request.input_artifact_ids),
        idempotency_key=request.idempotency_key,
        operator=request.operator,
        status=RunState.RUNNING.value,
        started_at=get_clock().now(),
    )
    session.add(row)
    session.flush()
    audit_log.record(
        "ai.agent.started",
        session=session,
        object_type="agentRun",
        object_id=run_id,
        project_id=request.project_id,
        after={
            "stage": request.stage,
            "agentType": request.agent_type,
            "draftId": request.draft_id,
            "scope": list(request.scope),
            "inputArtifactIds": list(request.input_artifact_ids),
            "idempotencyKey": request.idempotency_key,
        },
        operator=request.operator,
    )
    return row


def record_progress(
    run_id: str, step: str, fraction: float, data: dict[str, Any]
) -> None:
    """Store the latest progress event on the run row (AI-RT-019's 最近进度).

    Its own short transaction, on purpose: a progress event is written while the run holds no
    transaction at all (the model call is in the gap by construction), and a run that dies
    later still leaves the last thing it said — which is exactly what an operator looks at
    when a stage has been RUNNING for too long.
    """
    from factory_foundation.api import unit_of_work

    with unit_of_work() as session:
        row = session.get(AgentRunRow, run_id)
        if row is None:
            return
        row.last_progress = {
            "step": step,
            "completedFraction": fraction,
            "data": dict(data or {}),
        }
        session.flush()


def get_run(run_id: str, *, session: Session | None = None) -> AgentRunRow | None:
    def _read(sess: Session) -> AgentRunRow | None:
        return sess.get(AgentRunRow, run_id)

    return _read_only(session, _read)


def require_run(run_id: str, *, session: Session | None = None) -> AgentRunRow:
    row = get_run(run_id, session=session)
    if row is None:
        raise NotFoundError(f"agent run {run_id} not found", details={"runId": run_id})
    return row


def lock_run(run_id: str, *, session: Session) -> AgentRunRow:
    """The row with ``FOR UPDATE``, for the branch that answers a repeated idempotency key."""
    row = session.execute(
        select(AgentRunRow).where(AgentRunRow.id == run_id).with_for_update()
    ).scalars().first()
    if row is None:
        raise NotFoundError(f"agent run {run_id} not found", details={"runId": run_id})
    return row


def find_by_idempotency_key(key: str, *, session: Session) -> AgentRunRow | None:
    """The run a repeated start request belongs to (AI-AC-05)."""
    return session.execute(
        select(AgentRunRow).where(AgentRunRow.idempotency_key == key).with_for_update()
    ).scalars().first()


def completed_page_run(
    project_id: str,
    stage: str,
    agent_type: str,
    page_key: str,
    *,
    session: Session | None = None,
) -> AgentRunRow | None:
    """The completed run of one agent for one page, if there is one (AI-AG-013).

    S1 orchestrates stages and so *may* order a page's agents itself; S3 enforces it because
    a UI Intent whose data sources were never designed cannot be checked at all
    (AI-AG-005), and the run rows that prove the order are S3's own.
    """

    def _read(sess: Session) -> AgentRunRow | None:
        return sess.execute(
            select(AgentRunRow)
            .where(
                AgentRunRow.project_id == project_id,
                AgentRunRow.stage == stage,
                AgentRunRow.agent_type == agent_type,
                AgentRunRow.status == RunState.COMPLETED.value,
                AgentRunRow.scope.contains([page_key]),
            )
            .order_by(AgentRunRow.created_at.desc())
            .limit(1)
        ).scalars().first()

    if session is not None or has_active_uow():
        return _read(session if session is not None else current_session())
    with unit_of_work() as sess:
        return _read(sess)


def cancel_requested(run_id: str, *, session: Session | None = None) -> bool:
    """Re-read the flag at a safe point; cancellation is cross-process (AI-RT-020)."""

    def _read(sess: Session) -> bool:
        value = sess.execute(
            select(AgentRunRow.cancel_requested).where(AgentRunRow.id == run_id)
        ).scalar_one_or_none()
        return bool(value)

    return _read_only(session, _read)


def request_cancel(run_id: str, *, session: Session, reason: str | None = None) -> AgentRunRow:
    """Set the flag S1's other worker will see at the next safe point (AI-AC-08).

    Nothing else happens here: the run finishes its current step — the model call already
    paid for, the patch already half-applied — and stops.  A forced stop would leave the
    draft carrying part of one agent's work, which is the outcome AI-COST-003 also refuses.
    """
    row = lock_run(run_id, session=session)
    row.cancel_requested = True
    row.cancel_reason = reason or "requested"
    return row


def record_policy_decision(
    row: AgentRunRow,
    *,
    session: Session,
    effective_policy: dict[str, Any],
    provider_class: str | None,
    model: str | None,
    items: Sequence[dict[str, Any]],
    result: str,
    denied_items: Sequence[dict[str, Any]] = (),
    model_denials: Sequence[dict[str, Any]] = (),
    warnings: Sequence[str] = (),
) -> PolicyDecisionRow:
    """One decision row per model call attempt, allowed or refused (AI-POL-008)."""
    decision = PolicyDecisionRow(
        id=new_id("apdc"),
        run_id=row.id,
        project_id=row.project_id,
        effective_policy=dict(effective_policy),
        provider_class=provider_class,
        model=model,
        items=[dict(item) for item in items],
        result=result,
        denied_items=[dict(item) for item in denied_items],
        model_denials=[dict(item) for item in model_denials],
        warnings=[str(item) for item in warnings],
    )
    session.add(decision)
    session.flush()
    row.policy_decision_id = decision.id
    if result == "DENIED":
        audit_log.record(
            "ai.policy.denied",
            session=session,
            object_type="policyDecision",
            object_id=decision.id,
            project_id=row.project_id,
            after={
                "runId": row.id,
                "providerClass": provider_class,
                "denied": [item.get("ref") for item in denied_items],
                "modelDenials": [item.get("model") for item in model_denials],
            },
            operator=row.operator,
        )
    return decision


def save_context(
    row: AgentRunRow,
    *,
    session: Session,
    context_artifact_id: str | None,
    context_hash: str | None,
    pending_question_local_ids: Sequence[str],
    question_id_map: dict[str, str],
    submitted_patch_ids: Sequence[str],
    raised_question_local_ids: Sequence[str],
    base_version: str | None,
    attempt: int,
) -> AgentRunContextRow:
    """Persist what a resume needs, including what is still unanswered (AI-RT-015)."""
    context = session.get(AgentRunContextRow, row.id)
    if context is None:
        context = AgentRunContextRow(run_id=row.id, project_id=row.project_id)
        session.add(context)
    context.context_artifact_id = context_artifact_id
    context.context_hash = context_hash
    context.pending_question_local_ids = list(pending_question_local_ids)
    context.question_id_map = dict(question_id_map)
    context.submitted_patch_ids = list(submitted_patch_ids)
    context.raised_question_local_ids = list(raised_question_local_ids)
    context.base_version = base_version
    context.attempt = attempt
    session.flush()
    return context


def load_context_row(run_id: str, *, session: Session | None = None) -> AgentRunContextRow:
    def _read(sess: Session) -> AgentRunContextRow:
        row = sess.get(AgentRunContextRow, run_id)
        if row is None:
            raise NotFoundError(
                f"run {run_id} has no persisted context to resume", details={"runId": run_id}
            )
        return row

    return _read_only(session, _read)


def record_confirmation(
    row: AgentRunRow,
    *,
    session: Session,
    question_local_id: str,
    answerer: str,
    target_path: str,
    decision_id: str | None = None,
    business_key: str | None = None,
    suggestion_id: str | None = None,
    patch_id: str | None = None,
) -> ConfirmationRecordRow | None:
    """Record that a user answered a gating question (AI-RT-016).

    Returns ``None`` when the question was already confirmed: S1 sends *all* of a run's
    answered questions on every resume (``services/questions.py:283-299``), so the same
    answer arrives again on the next turn, and re-emitting its confirmation operation would
    put a duplicate into the patch.
    """
    existing = session.execute(
        select(ConfirmationRecordRow).where(
            ConfirmationRecordRow.run_id == row.id,
            ConfirmationRecordRow.question_local_id == question_local_id,
        )
    ).scalars().first()
    if existing is not None:
        return None
    record = ConfirmationRecordRow(
        id=new_id("acnf"),
        run_id=row.id,
        project_id=row.project_id,
        question_local_id=question_local_id,
        decision_id=decision_id,
        business_key=business_key,
        target_path=target_path,
        answerer=answerer,
        suggestion_id=suggestion_id,
        patch_id=patch_id,
    )
    session.add(record)
    session.flush()
    return record


def audit_resumed(row: AgentRunRow, *, session: Session, answers: int) -> None:
    """Record that a waiting run was continued, and by whom (appendix A)."""
    audit_log.record(
        "ai.agent.resumed",
        session=session,
        object_type="agentRun",
        object_id=row.id,
        project_id=row.project_id,
        after={"answerCount": answers},
        operator=row.operator,
        operator_is_user=True,
    )


def confirmed_question_ids(run_id: str, *, session: Session) -> set[str]:
    """Already-confirmed local ids, so a resume can skip them."""
    rows = session.execute(
        select(ConfirmationRecordRow.question_local_id).where(
            ConfirmationRecordRow.run_id == run_id
        )
    ).scalars().all()
    return {str(item) for item in rows}


def finish(
    row: AgentRunRow,
    *,
    session: Session,
    state: RunState,
    summary: str | None = None,
    confidence: float | None = None,
    output_artifact_id: str | None = None,
    patch_ids: Sequence[str] = (),
    question_local_ids: Sequence[str] = (),
    provider: str | None = None,
    provider_class: str | None = None,
    model: str | None = None,
    prompt_versions: dict[str, Any] | None = None,
    blueprint_ref: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    estimated_cost: float | None = None,
    model_calls: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> AgentRunRow:
    """Move the run to its terminal state and write the matching audit event."""
    row.status = state.value
    if summary is not None:
        row.summary = summary
    if confidence is not None:
        row.confidence = confidence
    if output_artifact_id is not None:
        row.output_artifact_id = output_artifact_id
    if patch_ids:
        row.patch_ids = list(patch_ids)
    if question_local_ids:
        row.question_local_ids = list(question_local_ids)
    if provider is not None:
        row.provider = provider
    if provider_class is not None:
        row.provider_class = provider_class
    if model is not None:
        row.model = model
    if prompt_versions is not None:
        row.prompt_versions = dict(prompt_versions)
    if blueprint_ref is not None:
        row.blueprint_ref = dict(blueprint_ref)
    if usage is not None:
        row.usage = dict(usage)
    if estimated_cost is not None:
        row.estimated_cost = estimated_cost
    if model_calls is not None:
        row.model_calls = model_calls
    row.error_code = error_code
    row.error_message = _bounded(error_message)
    if state is not RunState.RUNNING:
        row.completed_at = get_clock().now()
    session.flush()
    audit_log.record(
        _AUDIT_BY_STATE[state],
        session=session,
        object_type="agentRun",
        object_id=row.id,
        project_id=row.project_id,
        after={
            "status": state.value,
            "patchIds": list(row.patch_ids),
            "questions": list(row.question_local_ids),
            "outputArtifactId": row.output_artifact_id,
            "provider": row.provider,
            "providerClass": row.provider_class,
            "model": row.model,
            "errorCode": error_code,
            "estimatedCost": row.estimated_cost,
            "modelCalls": row.model_calls,
        },
        operator=row.operator,
    )
    return row


_AUDIT_BY_STATE: dict[RunState, str] = {
    RunState.COMPLETED: "ai.agent.completed",
    RunState.WAITING_INPUT: "ai.agent.waiting_input",
    RunState.FAILED: "ai.agent.failed",
    RunState.POLICY_DENIED: "ai.agent.failed",
    RunState.BUDGET_EXCEEDED: "ai.agent.failed",
    RunState.CANCELLED: "ai.agent.cancelled",
}


def _bounded(text: str | None, limit: int = 2000) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "…"


def _read_only(session: Session | None, action: Callable[[Session], T]) -> T:
    if session is not None:
        return action(session)
    if has_active_uow():
        return action(current_session())
    with unit_of_work() as sess:
        return action(sess)


__all__ = [
    "new_run_id",
    "create_run",
    "get_run",
    "require_run",
    "lock_run",
    "find_by_idempotency_key",
    "cancel_requested",
    "request_cancel",
    "record_policy_decision",
    "save_context",
    "load_context_row",
    "record_confirmation",
    "confirmed_question_ids",
    "finish",
]
