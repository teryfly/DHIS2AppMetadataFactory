"""Question lifecycle (CP-Q-001 … CP-Q-007).

S1 is the only owner of questions.  Sources are an S3 agent result, an S2 patch
merge conflict, or a user asking inside a stage.

Answering is a two-phase operation because resuming an S3 run is external I/O:
the answer is committed first, then the run is resumed outside the transaction,
then the run outcome is applied in a second transaction.  A failure in phase two
leaves the answer recorded — re-running the resume is idempotent (CP-Q-005).
"""

from __future__ import annotations

from typing import Any

from factory_control.domain.enums import (
    AgentRunStatus,
    QuestionSeverity,
    QuestionSourceKind,
    QuestionStatus,
    StageStatus,
)
from factory_control.domain.machines import assert_question_transition
from factory_control.errors import InvalidStateError, NotFoundControlError
from factory_control.foundation import new_id
from factory_control.models import Question, Stage
from factory_control.ports.dto import QuestionDraft
from factory_control.progress import stage_cancel
from factory_control.runtime import ControlContext, Runtime
from factory_control.services.recording import record_change, write_audit


def _suggestion_view(raw: dict[str, Any]) -> dict[str, Any]:
    """S3 spells this key both ways (``changes_uid`` / ``changesUid``); S1 answers
    with one (S5IF-FLD08).  A column-mapping suggestion keeps its proposal."""
    changes = raw.get("changesUid")
    if changes is None:
        changes = raw.get("changes_uid")
    return {
        **{k: v for k, v in raw.items() if k not in ("changes_uid", "columnMapping")},
        "changesUid": changes,
        "regenerates": list(raw.get("regenerates") or []),
        **(
            {"columnMapping": dict(raw["columnMapping"])}
            if isinstance(raw.get("columnMapping"), dict)
            else {}
        ),
    }


def question_view(row: Question) -> dict[str, Any]:
    return {
        "id": row.id,
        "projectId": row.project_id,
        "workflowId": row.workflow_id,
        "stageId": row.stage_id,
        "source": {
            "kind": row.source_kind,
            "agentRunId": row.agent_run_id,
            "localId": row.source_local_id,
        },
        "text": row.text,
        "severity": row.severity,
        "answerMode": _answer_mode(row.answer_mode),
        "affects": list(row.affects or []),
        "suggestions": [_suggestion_view(s) for s in (row.suggestions or [])],
        "blocking": row.blocking,
        "status": row.status,
        "answer": row.answer,
        "answeredBy": row.answered_by,
        "answeredAt": row.answered_at,
        "createdAt": row.created_at,
    }


def _answer_mode(value: str | None) -> str:
    mode = str(value or "").strip().upper()
    return mode if mode in {"SINGLE", "MULTIPLE"} else "SINGLE"


def record_questions(
    ctx: ControlContext,
    *,
    stage: Stage,
    questions: list[QuestionDraft],
    source_kind: str = QuestionSourceKind.AGENT.value,
    agent_run_id: str | None = None,
) -> list[Question]:
    """Persist agent / patch-conflict / user questions, deduped (CP-Q-002 … CP-Q-004)."""
    project_id = ctx.project_id or _project_for_workflow(ctx, stage.workflow_id)
    created: list[Question] = []
    for draft in questions:
        local_id = draft.local_id or None
        if agent_run_id and local_id:
            existing = _find_by_source(ctx, agent_run_id, local_id)
            if existing is not None:
                # CP-Q-004: an activity retry must not duplicate a question.
                continue
        row = Question(
            id=new_id("qst"),
            project_id=project_id,
            workflow_id=stage.workflow_id,
            stage_id=stage.id,
            source_kind=source_kind,
            agent_run_id=agent_run_id,
            source_local_id=local_id,
            text=draft.text,
            severity=draft.severity or QuestionSeverity.MEDIUM.value,
            answer_mode=_answer_mode(draft.answer_mode),
            affects=list(draft.affects),
            suggestions=[dict(s) for s in draft.suggestions],
            blocking=bool(draft.blocking or draft.severity == QuestionSeverity.HIGH.value),
            status=QuestionStatus.OPEN.value,
        )
        ctx.session.add(row)
        created.append(row)
    if created:
        ctx.session.flush()
        for row in created:
            record_change(
                ctx.bind(stage_id=stage.id),
                event_type="question.created",
                audit_event_type="cp.question.created",
                object_type="Question",
                object_id=row.id,
                payload={
                    "questionId": row.id,
                    "stageId": stage.id,
                    "severity": row.severity,
                    "blocking": row.blocking,
                },
            )
        from factory_control.services.notifications import notify_question_created

        notify_question_created(ctx, stage_id=stage.id, questions=created)
    return created


def _project_for_workflow(ctx: ControlContext, workflow_id: str) -> str:
    from factory_control.models import Workflow

    workflow = ctx.session.get(Workflow, workflow_id)
    if workflow is None:
        raise NotFoundControlError(f"unknown workflow {workflow_id}")
    return workflow.project_id


def _find_by_source(ctx: ControlContext, agent_run_id: str, local_id: str) -> Question | None:
    from sqlalchemy import select

    return ctx.session.execute(
        select(Question).where(
            Question.agent_run_id == agent_run_id, Question.source_local_id == local_id
        )
    ).scalars().first()


def open_questions(ctx: ControlContext, stage_id: str) -> list[Question]:
    from sqlalchemy import select

    return list(
        ctx.session.execute(
            select(Question).where(Question.stage_id == stage_id, Question.status == QuestionStatus.OPEN.value)
        )
        .scalars()
        .all()
    )


def has_open_high_questions(ctx: ControlContext, stage_id: str) -> bool:
    """CP-Q-006: a HIGH open question blocks approval of the design Gate."""
    return any(
        q.severity == QuestionSeverity.HIGH.value and q.status == QuestionStatus.OPEN.value
        for q in open_questions(ctx, stage_id)
    )


def unanswered_blocking(ctx: ControlContext, stage_id: str) -> list[Question]:
    return [q for q in open_questions(ctx, stage_id) if q.blocking]


def supersede_open_questions(ctx: ControlContext, stage_id: str, *, reason: str) -> list[Question]:
    """CP-WF-012: rerunning a stage retires its open questions."""
    changed: list[Question] = []
    for question in open_questions(ctx, stage_id):
        assert_question_transition(question.status, QuestionStatus.SUPERSEDED, question_id=question.id)
        question.status = QuestionStatus.SUPERSEDED.value
        changed.append(question)
    if changed:
        ctx.session.flush()
        for question in changed:
            write_audit(
                ctx,
                event_type="cp.question.superseded",
                object_type="Question",
                object_id=question.id,
                before={"status": QuestionStatus.OPEN.value},
                after={"status": QuestionStatus.SUPERSEDED.value, "reason": reason},
            )
    return changed


def answer_question(
    runtime: Runtime,
    question_id: str,
    *,
    operator: str,
    suggestion_id: str | None = None,
    suggestion_ids: list[str] | None = None,
    custom_text: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Record an answer, then resume the agent run if that unblocks it."""
    ids = _merge_suggestion_ids(suggestion_id, suggestion_ids)
    if not ids and not (custom_text or "").strip():
        raise InvalidStateError(
            "an answer must select a suggestion or supply custom text",
            details={"questionId": question_id},
        )

    with runtime.transaction(operator=operator) as ctx:
        question = ctx.session.get(Question, question_id)
        if question is None:
            raise NotFoundControlError(f"question {question_id} not found")
        _record_answer(
            ctx,
            question,
            suggestion_ids=ids,
            custom_text=custom_text,
            operator=operator,
        )
        remaining = unanswered_blocking(ctx, question.stage_id) if question.stage_id else []
        resume_target = None
        # Only a blocking answer can be what the run was waiting for. A late answer to an
        # optional question must not resume a run that already moved on.
        if (
            not remaining
            and question.blocking
            and question.agent_run_id
            and question.stage_id is not None
        ):
            resume_target = (
                question.agent_run_id,
                question.stage_id,
                question.project_id,
                question.workflow_id,
            )

    if resume_target is None:
        return {"questionId": question_id, "status": QuestionStatus.ANSWERED.value, "resumed": False}

    return _resume_after_answers(
        runtime,
        run_id=resume_target[0],
        stage_id=resume_target[1],
        project_id=resume_target[2],
        workflow_id=resume_target[3],
        operator=operator,
        language=language,
    )


def answer_questions(
    runtime: Runtime,
    answers: list[dict[str, Any]],
    *,
    operator: str,
    language: str | None = None,
) -> dict[str, Any]:
    """Record a whole questionnaire in one transaction, then resume its run once.

    This is the review screen's single submit.  The per-question endpoint stays for clients
    that answer opportunistically, but a questionnaire must not pay one model resume per
    question: the second answer would arrive after the run had already continued past the
    first.  Every answer here is committed before any resume, and the resume happens at most
    once (CP-Q-005).
    """
    if not answers:
        raise InvalidStateError("a questionnaire submit needs at least one answer")
    question_ids = [str(item.get("questionId") or "") for item in answers]
    if any(not item for item in question_ids):
        raise InvalidStateError("every answer needs a questionId")
    if len(set(question_ids)) != len(question_ids):
        raise InvalidStateError("a questionnaire submit names one question at most once")

    with runtime.transaction(operator=operator) as ctx:
        rows: list[Question] = []
        for item in answers:
            question = ctx.session.get(Question, item["questionId"])
            if question is None:
                raise NotFoundControlError(f"question {item['questionId']} not found")
            rows.append(question)
        stage_ids = {row.stage_id for row in rows}
        if len(stage_ids) != 1 or None in stage_ids:
            raise InvalidStateError(
                "a questionnaire submit must answer questions of one stage",
                details={"stageIds": sorted(str(item) for item in stage_ids)},
            )
        project_ids = {row.project_id for row in rows}
        workflow_ids = {row.workflow_id for row in rows}
        if len(project_ids) != 1 or len(workflow_ids) != 1:
            raise InvalidStateError("a questionnaire submit must belong to one workflow")
        stage = ctx.session.get(Stage, next(iter(stage_ids)))
        if stage is None:
            raise NotFoundControlError("the questions' stage no longer exists")

        for item, question in zip(answers, rows, strict=True):
            _record_answer(
                ctx,
                question,
                suggestion_ids=_merge_suggestion_ids(
                    item.get("suggestionId"), item.get("suggestionIds")
                ),
                custom_text=item.get("customText"),
                operator=operator,
            )

        remaining = unanswered_blocking(ctx, stage.id)
        run_ids = {row.agent_run_id for row in rows if row.agent_run_id}
        answered_blocking = any(row.blocking for row in rows)
        resume_target = None
        if not remaining and answered_blocking and len(run_ids) == 1:
            resume_target = (
                next(iter(run_ids)),
                stage.id,
                next(iter(project_ids)),
                next(iter(workflow_ids)),
            )
        elif not remaining and answered_blocking and len(run_ids) > 1:
            # Two agent runs on one stage is not a state the workflow creates; refusing the
            # resume is safer than continuing an arbitrary one.
            raise InvalidStateError(
                "the stage's answered questions belong to more than one agent run",
                details={"stageId": stage.id, "runIds": sorted(run_ids)},
            )
        answered_ids = [row.id for row in rows]

    result: dict[str, Any] | None = None
    if resume_target is not None:
        result = _resume_after_answers(
            runtime,
            run_id=resume_target[0],
            stage_id=resume_target[1],
            project_id=resume_target[2],
            workflow_id=resume_target[3],
            operator=operator,
            language=language,
        )

    with runtime.transaction(
        operator=operator,
        project_id=next(iter(project_ids)),
        workflow_id=next(iter(workflow_ids)),
        stage_id=next(iter(stage_ids)),
    ) as ctx:
        open_rows = open_questions(ctx, next(iter(stage_ids)))
        return {
            "answers": [
                {"questionId": question_id, "status": QuestionStatus.ANSWERED.value}
                for question_id in answered_ids
            ],
            "resumed": result is not None,
            "runId": result.get("runId") if result else None,
            "status": result.get("status") if result else None,
            "questions": [question_view(row) for row in open_rows],
        }


def _merge_suggestion_ids(
    suggestion_id: str | None, suggestion_ids: list[str] | None
) -> list[str]:
    """One selected id (the old wire shape) and a list (the questionnaire's) as one list."""
    merged: list[str] = []
    for value in [suggestion_id, *(suggestion_ids or [])]:
        if value and value not in merged:
            merged.append(str(value))
    return merged


def _record_answer(
    ctx: ControlContext,
    question: Question,
    *,
    suggestion_ids: list[str],
    custom_text: str | None,
    operator: str,
) -> Question:
    """Validate one answer against its question and write it (CP-Q-005/007)."""
    if question.status == QuestionStatus.ANSWERED.value:
        # CP-Q-007: answers are immutable; a change needs a new question.
        raise InvalidStateError(
            "a question answer cannot be modified once recorded; raise a new question or a patch",
            details={"questionId": question.id, "status": question.status},
        )
    assert_question_transition(question.status, QuestionStatus.ANSWERED, question_id=question.id)

    mode = _answer_mode(question.answer_mode)
    ids = list(suggestion_ids)
    custom = (custom_text or "").strip() or None
    if mode == "SINGLE" and len(ids) > 1:
        raise InvalidStateError(
            "this question asks for one choice, but several suggestions were selected",
            details={"questionId": question.id, "answerMode": mode, "suggestionIds": ids},
        )
    if mode == "SINGLE" and ids and custom:
        raise InvalidStateError(
            "a single-choice question takes a suggestion or a custom answer, not both",
            details={"questionId": question.id, "answerMode": mode},
        )
    texts = _suggestion_texts(question, ids)
    if not ids and not custom:
        raise InvalidStateError(
            "an answer must select a suggestion or supply custom text",
            details={"questionId": question.id},
        )

    chosen_text = "; ".join([*texts, custom] if custom else texts)
    question.status = QuestionStatus.ANSWERED.value
    question.answer = {
        "suggestionId": ids[0] if ids else None,
        "suggestionIds": ids,
        "suggestionText": "; ".join(texts) if texts else None,
        "suggestionTexts": texts,
        "customText": custom,
        "answeredAt": None,
        "text": chosen_text or custom or "",
    }
    question.answered_by = operator
    from factory_control.foundation import utcnow

    question.answered_at = utcnow()
    question.answer["answeredAt"] = question.answered_at.isoformat()
    ctx.session.flush()

    record_change(
        ctx.bind(
            workflow_id=question.workflow_id,
            stage_id=question.stage_id,
            project_id=question.project_id,
        ),
        event_type="question.answered",
        audit_event_type="cp.question.answered",
        object_type="Question",
        object_id=question.id,
        payload={"questionId": question.id, "stageId": question.stage_id},
        before={"status": QuestionStatus.OPEN.value},
        after={"status": QuestionStatus.ANSWERED.value},
    )
    return question


def _resume_after_answers(
    runtime: Runtime,
    *,
    run_id: str,
    stage_id: str | None,
    project_id: str,
    workflow_id: str,
    operator: str,
    language: str | None = None,
) -> dict[str, Any]:
    """CP-Q-005: once every blocking question of a run is answered, continue the run."""
    with runtime.transaction(operator=operator, project_id=project_id, workflow_id=workflow_id) as ctx:
        answers = _answers_for_run(ctx, run_id)
        idempotency_key = f"resume:{run_id}"
        # The stage parked in WAITING_INPUT when the run asked its questions.  A continuation
        # is a RUNNING stage again: apply_agent_result then makes the one legal move from
        # RUNNING (back to WAITING_INPUT for a new round, or on to the approval Gate).
        from factory_control.services.workflow import reload_stage, transition

        stage = ctx.session.get(Stage, stage_id) if stage_id else None
        if stage is not None and stage.status == StageStatus.WAITING_INPUT.value:
            transition(
                ctx,
                stage=reload_stage(ctx, stage),
                target=StageStatus.RUNNING,
                event_type="stage.started",
                payload={"reason": "ANSWERS_RECEIVED", "runId": run_id},
            )

    # External I/O: no unit of work may be open here.
    result = runtime.intelligence.resume_agent_run(
        run_id,
        answers,
        idempotency_key,
        language=language,
        progress=None,
        cancel=stage_cancel(),
    )

    from factory_control.services.workflow import apply_agent_result

    applied: dict[str, Any] = {}
    with runtime.transaction(operator=operator, project_id=project_id, workflow_id=workflow_id) as ctx:
        stage = ctx.session.get(Stage, stage_id)
        if stage is not None:
            applied = apply_agent_result(
                ctx, stage=stage, result=result, idempotency_key=idempotency_key
            )
    if applied.get("readyForApproval") and stage_id is not None:
        # A continued run that completed has the same finish line as a first run: freeze the
        # draft and open the Gate (run_claimed does this for the first attempt).
        from factory_control.services.steps import submit_for_approval

        submit_for_approval(runtime, stage_id, operator=operator)
    return {
        "questionId": answers[0].question_local_id if answers else None,
        "status": result.status,
        "resumed": True,
        "runId": run_id,
    }


def _answers_for_run(ctx: ControlContext, run_id: str) -> list[Any]:
    from sqlalchemy import select

    from factory_control.ports.dto import Answer

    rows = ctx.session.execute(
        select(Question).where(
            Question.agent_run_id == run_id, Question.status == QuestionStatus.ANSWERED.value
        )
    ).scalars().all()
    out: list[Answer] = []
    for row in rows:
        answer = row.answer or {}
        suggestion_ids = [
            str(item) for item in (answer.get("suggestionIds") or []) if item
        ]
        if not suggestion_ids and answer.get("suggestionId"):
            suggestion_ids = [str(answer["suggestionId"])]
        suggestion_texts = [
            str(item) for item in (answer.get("suggestionTexts") or []) if item
        ]
        if not suggestion_texts and answer.get("suggestionText"):
            suggestion_texts = [str(answer["suggestionText"])]
        out.append(
            Answer(
                question_local_id=row.source_local_id or row.id,
                suggestion_id=suggestion_ids[0] if suggestion_ids else None,
                suggestion_ids=suggestion_ids,
                suggestion_texts=suggestion_texts,
                custom_text=answer.get("customText"),
                answered_by=row.answered_by or "unknown",
            )
        )
    return out


def _suggestion_texts(question: Question, suggestion_ids: list[str]) -> list[str]:
    """Resolve every selected id, refusing an id this question never offered."""
    by_id = {
        str(suggestion.get("id")): str(suggestion.get("text") or "")
        for suggestion in (question.suggestions or [])
        if suggestion.get("id")
    }
    texts: list[str] = []
    for suggestion_id in suggestion_ids:
        if suggestion_id not in by_id:
            raise InvalidStateError(
                f"unknown suggestion id {suggestion_id!r} for question {question.id}",
                details={"questionId": question.id, "suggestionId": suggestion_id},
            )
        texts.append(by_id[suggestion_id])
    return texts


def stage_is_waiting_input(stage: Stage) -> bool:
    return stage.status == StageStatus.WAITING_INPUT.value


def agent_status_is_waiting(status: str) -> bool:
    return status == AgentRunStatus.WAITING_INPUT.value


__all__ = [
    "agent_status_is_waiting",
    "answer_question",
    "answer_questions",
    "has_open_high_questions",
    "open_questions",
    "question_view",
    "record_questions",
    "stage_is_waiting_input",
    "supersede_open_questions",
    "unanswered_blocking",
]
