"""Question local ids (AI-RT-015, AI-RT-016).

S3 assigns the ids, not the model.  S1 dedupes questions on ``(agent_run_id,
source_local_id)`` and *silently skips* a repeat
(``factory_control/services/questions.py:69-73``), so two model-authored ``"Q1"``s would
lose one question outright — and a question that vanishes is worse than one that is
annoying, because the stage would then proceed as if nobody had asked anything.

The mapping from the model's own id to the canonical one is kept in the run context so that
``affects`` references and the answers that come back on resume address the same object
(AI-RT-016: an answer names a local Question ID).
"""

from __future__ import annotations

from collections.abc import Sequence

from factory_intelligence.dto.agent import ModelQuestion
from factory_intelligence.dto.common import Question
from factory_intelligence.enums import QUESTION_SEVERITY_RANK, QuestionSeverity

#: The id S1 receives and later quotes back; ``Q1``, ``Q2``… in a deterministic order.
PREFIX = "Q"


def canonicalise(
    questions: Sequence[ModelQuestion], *, attempt: int = 1
) -> tuple[list[Question], dict[str, str]]:
    """Assign canonical local ids in a stable order.

    Order is ``(-severity, first affects, text)``: the most urgent question gets ``Q1``, and
    the same set of questions always numbers the same way — which matters because a resumed
    run re-reads the stored list and must not renumber it.

    ``attempt`` keeps a later round's ids distinct from the first round's.  S1 dedupes on
    ``(agent_run_id, source_local_id)`` and *skips* a repeat, so a second round that started
    at ``Q1`` again would silently lose every question it asked.  The first turn keeps the
    plain spelling; later turns are prefixed with their attempt (``Q2-1``).
    """
    ordered = sorted(questions, key=_sort_key)
    prefix = PREFIX if attempt <= 1 else f"{PREFIX}{attempt}-"
    out: list[Question] = []
    mapping: dict[str, str] = {}
    for index, question in enumerate(ordered, start=1):
        local_id = f"{prefix}{index}"
        mapping[question.id] = local_id
        canonical = question.to_canonical(local_id)
        out.append(rewrite_affects(canonical, question.id, local_id, mapping))
    return out, mapping


def _sort_key(question: ModelQuestion) -> tuple[int, str, str]:
    rank = QUESTION_SEVERITY_RANK.get(_severity(question), 1)
    first_target = question.affects[0] if question.affects else ""
    return (-rank, first_target, question.text)


def _severity(question: ModelQuestion) -> QuestionSeverity:
    try:
        return QuestionSeverity(question.severity)
    except ValueError:
        return QuestionSeverity.MEDIUM


def rewrite_affects(
    question: Question, model_id: str, local_id: str, mapping: dict[str, str]
) -> Question:
    """Swap the model's own question id for the canonical one inside ``affects``.

    An agent that says "this question is about Q1" means its own numbering; leaving the old
    id in place would point ``affects`` at something S1 never stored, and the confirmation
    record on resume (AI-RT-016) would then be attached to nothing.
    """
    rewritten = [
        mapping.get(item, local_id if item == model_id else item) for item in question.affects
    ]
    return question.model_copy(update={"affects": rewritten})


def is_blocking(severity: str, model_claimed: bool) -> bool:
    """HIGH is blocking whatever the model said (AI-RT-015)."""
    return model_claimed or severity == QuestionSeverity.HIGH.value


def blocking_questions(questions: Sequence[Question]) -> list[Question]:
    return [question for question in questions if is_blocking(question.severity, question.blocking)]


__all__ = ["canonicalise", "rewrite_affects", "is_blocking", "blocking_questions", "PREFIX"]
