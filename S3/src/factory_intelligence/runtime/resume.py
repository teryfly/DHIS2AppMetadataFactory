"""``resume_agent_run`` — a later turn of the same run (AI-RT-015, AI-RT-016, AI-AC-04).

Two integration traps are handled here rather than in the engine.

**S1 reuses one resume key for the whole run.**  It sends ``resume:{run_id}`` on every answer
turn (``factory_control/services/questions.py:258``), so successive turns arrive with the same
idempotency key and different content — which S0 answers with ``IDEMPOTENCY_CONFLICT``,
breaking exactly the multi-round waits AI-RT-015 defines.  The key is therefore scoped by
attempt internally: a retry of the *same* attempt still replays, and a new turn gets a new
key by construction.

**The answers are cumulative.**  ``_answers_for_run`` returns every answered question of the
run, not only the new ones, so a confirmation must not be written twice — which is
:func:`factory_intelligence.runtime.patching.confirmation_operations`' job, through
``ai.confirmation_record``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from factory_foundation.api import (
    CancellationToken,
    ProgressReporter,
    idempotency,
    structure_hash,
    unit_of_work,
)

from factory_intelligence.agents.spec import spec_for
from factory_intelligence.dto.common import Answer
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.dto.result import AgentRunResult
from factory_intelligence.enums import RunState
from factory_intelligence.errors import AgentRunInProgress, PromptNotActivated, RunNotResumable
from factory_intelligence.prompts import activation as prompts
from factory_intelligence.registries import IdemScope
from factory_intelligence.runtime import contextstore, engine
from factory_intelligence.runtime import state as run_state
from factory_intelligence.runtime.get import project
from factory_intelligence.runtime.progress import Progress


def _progress_sink(run_id: str) -> Callable[[str, float, dict[str, Any]], None]:
    """Bind a run's progress events to its own row (AI-RT-019)."""
    from factory_intelligence.runtime import state as run_state

    def _sink(step: str, fraction: float, data: dict[str, Any]) -> None:
        run_state.record_progress(run_id, step, fraction, data)

    return _sink


def resume(
    run_id: str,
    answers: Sequence[Answer | Mapping[str, Any]],
    idempotency_key: str,
    *,
    language: str | None = None,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> AgentRunResult:
    """Continue a waiting run in its persisted context, applying the answers given.

    ``language`` is the interface language of the person answering this turn.  It is not
    stored on the run: the same waiting run can be resumed by two people reading different
    languages, and the current turn's language is the one the new questions must follow.
    """
    parsed = [_answer(item) for item in answers]
    with unit_of_work() as session:
        row = run_state.lock_run(run_id, session=session)
        if row.status == RunState.RUNNING.value:
            raise RunNotResumable(
                f"run {run_id} is still executing and has nothing to resume",
                details={"runId": run_id, "status": row.status},
            )
        if row.status != RunState.WAITING_INPUT.value:
            # The answers arrived, the run had already finished, and a retry is legitimate:
            # report its outcome rather than failing a caller that did nothing wrong.
            return project(run_id) or AgentRunResult(run_id=run_id, status=str(row.status))
        context_row = run_state.load_context_row(run_id, session=session)
        attempt = int(context_row.attempt) + 1
        internal_key = resume_key(idempotency_key, attempt)
        begun = idempotency.begin(
            IdemScope.AGENT_RESUME,
            internal_key,
            resume_digest(run_id, attempt, parsed),
            session=session,
        )
        if not begun.created:
            # A Temporal retry of a turn that is still executing.  There is no result to
            # hand back, and S1's status enum has no seventh value, so this is the same
            # retryable refusal ``start_agent_run`` makes for the same reason (AI-AC-05).
            raise AgentRunInProgress(
                f"attempt {attempt} of run {run_id} is still executing",
                details={"runId": run_id, "attempt": attempt, "idempotencyKey": idempotency_key},
            )
        request = _request_of(row)
        if language:
            request = request.model_copy(update={"language": language})
        active = prompts.current_activation(request.agent_type, session=session)
        if active is None:
            # AI-REG-002: raise rather than return a FAILED result, because the WAITING_INPUT row
            # already exists and must stay.  The answers arrived into a transaction this refusal
            # rolls back, so nothing is spent or recorded; once an operator activates a prompt
            # the same resume can be retried and this turn runs.  Degrading to a placeholder
            # would answer the person's questions with governance switched off.
            raise PromptNotActivated(
                f"run {run_id} cannot resume: the {request.agent_type} agent has no activated "
                "prompt (AI-REG-002)",
                details={"runId": run_id, "agentType": request.agent_type},
            )
        run_state.audit_resumed(row, session=session, answers=len(parsed))

    # Reading the transcript is external I/O, so it happens after that transaction closed.
    stored = contextstore.load(run_id, request.project_id)
    unanswered = stored.unanswered(parsed)
    if unanswered:
        # S1 resumes only once nothing blocking is outstanding; answering again here is
        # cheaper than acting on an answer that was never given.
        return AgentRunResult(
            run_id=run_id,
            status="WAITING_INPUT",
            denial_reasons=[f"unanswered questions: {', '.join(unanswered)}"],
        )

    execution = engine.Execution(
        request=request.model_copy(update={"idempotency_key": internal_key}),
        spec=spec_for(request.agent_type),
        run_id=run_id,
        progress=Progress(progress, cancel, sink=_progress_sink(run_id)),
        attempt=attempt,
        active_prompt=active,
        answers=tuple(parsed),
        stored=stored,
    )
    return engine.execute_turn(execution)


def resume_key(idempotency_key: str, attempt: int) -> str:
    """The internal key for one answer turn (see the module docstring's first trap)."""
    return f"{idempotency_key}:{attempt}"


def resume_digest(run_id: str, attempt: int, answers: Sequence[Answer]) -> str:
    """Caller intent for this turn: the run, the turn number, and these answers.

    Recorded so a retry of the *same* turn replays and a turn with different answers does
    not silently reuse another turn's record (00 doc 6.4, AI-AC-05).
    """
    return structure_hash(
        {
            "runId": run_id,
            "attempt": attempt,
            "answers": [item.model_dump(mode="json") for item in answers],
        }
    )


def _answer(item: Answer | Mapping[str, Any]) -> Answer:
    """Accept S1's ``Answer`` mirror as well as a plain mapping.

    S1 builds ``factory_control.ports.dto.Answer`` objects and the adapter forwards them
    untouched, so an ``isinstance`` test against S3's class would reject every real call.
    """
    return item if isinstance(item, Answer) else Answer.model_validate(dict(item))


def _request_of(row: Any) -> AgentRunRequest:
    """Rebuild the request from the run row.

    S3 keeps no copy of S1's columns (D-14), so everything here is a field the run recorded
    for itself.  ``target_environment_policies`` is left empty on purpose: the caller supplies
    the live policies with each request, and a resumed turn must not act on a stale snapshot of
    an organisation's decision — an environment policy tightened after the first turn would
    otherwise be ignored (AI-POL-004).  The design-basis environment *is* restored, because the
    UID replenishment of AI-RT-013 needs it on any turn.
    """
    return AgentRunRequest(
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        stage_id=row.stage_id or row.id,
        stage=row.stage,
        agent_type=row.agent_type,
        draft_id=row.draft_id,
        scope=list(row.scope or []),
        input_artifact_ids=list(row.input_artifact_ids or []),
        design_reference_environment_key=row.design_reference_environment_key,
        discovery_result_artifact_id=row.discovery_result_artifact_id,
        capability_profile_artifact_id=row.capability_profile_artifact_id,
        target_environment_policies={},
        operator=row.operator,
        idempotency_key=row.idempotency_key,
    )


__all__ = ["resume", "resume_digest", "resume_key"]
