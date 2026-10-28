"""The persisted run context (AI-RT-015, AI-RT-016).

A run that returns ``WAITING_INPUT`` must be continuable *in the same context*, and a
Temporal activity may be retried on a different worker — so "the same context" has to be a
durable object rather than memory.  It is stored as a versioned artifact (message sequence,
submitted patches, raised questions, the question-id map) and its classification is the
maximum of everything in it, because S0 refuses a derived artifact classified below its
sources and a context that lost a level would be a way to smuggle content past the policy
engine.

Each turn supersedes the previous artifact through ``prev_version_id`` rather than mutating
it: an artifact is immutable (00 doc 6.6), and the history is what lets a reviewer see what
the model was actually told at attempt 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import ArtifactInfo, DataClassification, StagingHandle
from sqlalchemy.orm import Session

from factory_intelligence import artifacts
from factory_intelligence.context.builder import BuiltContext
from factory_intelligence.deps import get_clock
from factory_intelligence.dto.common import Answer, Question
from factory_intelligence.llm.types import ChatMessage, Role

CONTEXT_ARTIFACT_TYPE = "AGENT_OUTPUT"


@dataclass
class StoredContext:
    """What a resume needs, read from ``ai.agent_run_context`` plus its artifact."""

    run_id: str
    project_id: str
    attempt: int
    context_artifact_id: str | None
    context_hash: str | None
    pending_question_local_ids: list[str] = field(default_factory=list)
    question_id_map: dict[str, str] = field(default_factory=dict)
    submitted_patch_ids: list[str] = field(default_factory=list)
    raised_question_local_ids: list[str] = field(default_factory=list)
    #: the questions this run asked, with their text.  An answer that names only ``Q1`` is
    #: useless to a model that cannot see what ``Q1`` said, so a resumed turn re-reads the
    #: question from the transcript rather than from the database's id list (AI-RT-015).
    raised_questions: list[Question] = field(default_factory=list)
    base_version: str | None = None
    messages: list[ChatMessage] = field(default_factory=list)
    #: index in ``messages`` where the conversation starts; everything before it is the
    #: context rebuilt for the current turn and must not be appended a second time
    conversation_start: int | None = None
    #: the classification the context artifact was written at
    classification: DataClassification = DataClassification.INTERNAL

    @property
    def still_waiting(self) -> bool:
        return bool(self.pending_question_local_ids)

    @property
    def tail_start(self) -> int:
        """Where the stored conversation starts.

        Artifacts written before ``conversationStart`` existed carry the whole sent context
        followed by the dialogue.  The rebuilt context is not conversation, so appending it
        again would duplicate the prompt, rules and entries.  The first assistant answer or
        feedback message is the first thing the current build did not already send.
        """
        if self.conversation_start is not None:
            return max(0, min(self.conversation_start, len(self.messages)))
        for index, message in enumerate(self.messages):
            if index == 0 and message.role == "system":
                continue
            if message.role == "assistant":
                return index
            text = message.content.lstrip()
            if text.startswith(
                (
                    "Your previous answer was",
                    "The previous answer was truncated",
                    "Answers to the questions",
                )
            ):
                return index
        return len(self.messages)

    def unanswered(self, answers: list[Answer]) -> list[str]:
        answered = {answer.question_local_id for answer in answers}
        return [item for item in self.pending_question_local_ids if item not in answered]


def document(
    *,
    run_id: str,
    attempt: int,
    context: BuiltContext,
    submitted_patch_ids: list[str],
    raised_questions: list[Question],
    question_id_map: dict[str, str],
    base_version: str | None,
    conversation_start: int,
) -> dict[str, Any]:
    """The artifact body: the message sequence, and nothing the model only thought.

    AI-RT-014 forbids persisting a model's internal reasoning, so this records exactly what
    *was sent* and *was accepted* — the auditable transcript.  A provider that returns a
    ``reasoning_content`` field, or an answer with a chain-of-thought key, contributes
    nothing here: the ladder validated the structured output and the output is what is kept.
    """
    return {
        "runId": run_id,
        "attempt": attempt,
        "messageSequence": [
            {
                "role": message.role,
                "content": message.content,
                "images": [{"mediaType": image.media_type, "label": image.label} for image in message.images],
            }
            for message in context.messages
        ],
        "submittedPatchIds": list(submitted_patch_ids),
        "raisedQuestions": [question.model_dump(mode="json") for question in raised_questions],
        "questionIdMap": dict(question_id_map),
        "baseVersion": base_version,
        "conversationStart": conversation_start,
        "contextDigest": context.digest(),
        "recordedAt": get_clock().now().isoformat(),
    }


def stage(
    project_id: str, body: dict[str, Any], *, media_type: str = "application/json"
) -> StagingHandle:
    """External I/O: write the transcript (call this in the gap, not in a transaction)."""
    return artifacts.stage_json(project_id, CONTEXT_ARTIFACT_TYPE, body, media_type=media_type)


def register(
    handle: StagingHandle | str,
    *,
    session: Session,
    project_id: str,
    source_artifact_ids: list[str],
    workflow_id: str | None,
    classification: DataClassification,
    prev_version_id: str | None,
    run_id: str,
    attempt: int,
) -> ArtifactInfo:
    """Record the transcript inside the run's transaction (AI-RT-015)."""
    return artifacts.register_staged(
        handle,
        project_id=project_id,
        artifact_type=CONTEXT_ARTIFACT_TYPE,
        data_classification=classification,
        source_artifact_ids=source_artifact_ids,
        workflow_id=workflow_id,
        build_record={
            "kind": "agentRunContext",
            "runId": run_id,
            "attempt": attempt,
            "recordedBy": "factory_intelligence",
        },
        prev_version_id=prev_version_id,
        session=session,
    )


def load(run_id: str, project_id: str, *, session: Session | None = None) -> StoredContext:
    """Rebuild the context of a waiting run from its row and its artifact."""
    from factory_intelligence.runtime.state import load_context_row

    row = load_context_row(run_id, session=session)
    stored = StoredContext(
        run_id=run_id,
        project_id=project_id,
        attempt=row.attempt,
        context_artifact_id=row.context_artifact_id,
        context_hash=row.context_hash,
        pending_question_local_ids=[str(item) for item in row.pending_question_local_ids],
        question_id_map={str(k): str(v) for k, v in (row.question_id_map or {}).items()},
        submitted_patch_ids=[str(item) for item in row.submitted_patch_ids],
        raised_question_local_ids=[str(item) for item in row.raised_question_local_ids],
        base_version=row.base_version,
    )
    if row.context_artifact_id is None:
        return stored
    document_body, classification = artifacts.read_json(row.context_artifact_id)
    stored.classification = classification
    stored.messages = [_message(item) for item in document_body.get("messageSequence") or []]
    start = document_body.get("conversationStart")
    stored.conversation_start = int(start) if isinstance(start, int) else None
    stored.raised_questions = [
        question
        for item in document_body.get("raisedQuestions") or []
        if isinstance(item, dict)
        for question in [_question(item)]
        if question is not None
    ]
    return stored


def _question(raw: dict[str, Any]) -> Question | None:
    try:
        return Question.model_validate(raw)
    except ValueError:  # pragma: no cover - a transcript S3 wrote always validates
        return None


def _message(raw: Any) -> ChatMessage:
    if not isinstance(raw, dict):
        raise ValueError(f"a stored context message is not an object: {raw!r}")
    role: Role = raw_role(raw.get("role"))
    return ChatMessage(role=role, content=str(raw.get("content") or ""))


def raw_role(value: Any) -> Role:
    """A stored role is trusted only as far as the three roles the protocol has."""
    if value in ("system", "user", "assistant"):
        return value  # type: ignore[no-any-return]
    return "user"


__all__ = ["StoredContext", "CONTEXT_ARTIFACT_TYPE", "document", "stage", "register", "load"]
