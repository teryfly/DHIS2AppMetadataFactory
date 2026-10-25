"""In-stage dialogue contracts (AI-CHAT-001/002/003, FR-UI-002).

Nothing here is S1's state: the answer, the patch ids and the questions are S3's (projected),
and the context block is S1's own reading of which stage the person is standing in.  The block
is in every response rather than only in a ``GET`` because FR-UI-002 makes the header mandatory
on screen, and a turn that answers without it would let S5 draw a conversation with no way to
tell the user which draft they are talking about.
"""

from __future__ import annotations

from pydantic import Field

from factory_control.rest.schemas.common import CamelModel
from factory_control.rest.schemas.gates import SuggestionView


class StageContextView(CamelModel):
    """What the panel must show while the conversation is open (FR-UI-002)."""

    project_id: str
    project_name: str = ""
    workflow_id: str
    stage: str
    stage_status: str
    delivery_mode: str | None = None
    #: ``None`` when the project has no working draft, which is also why a requested change
    #: could not be written (AI-CHAT-002)
    active_draft_id: str | None = None
    target_environments: list[str] = Field(default_factory=list)


class ChatQuestionView(CamelModel):
    """A question the assistant raised in this turn.

    Distinct from ``QuestionView`` on purpose: a stage Question is S1's row with an id, a status
    and an answer, and the Gate can be blocked by it (CP-Q-006).  A dialogue question is part of
    one answer and is never a blocker, so giving it the same shape would imply a lifecycle it
    does not have.
    """

    local_id: str
    text: str = ""
    severity: str = "MEDIUM"
    affects: list[str] = Field(default_factory=list)
    suggestions: list[SuggestionView] = Field(default_factory=list)
    blocking: bool = False


class ChatTurnRequest(CamelModel):
    """The message the person typed (AI-CHAT-001)."""

    message: str = Field(min_length=1, max_length=8000)


class ChatTurnView(CamelModel):
    stage_id: str
    answer: str = ""
    #: the ids S2 accepted from this turn (AI-CHAT-002); empty when nothing was asked for,
    #: when the mapping is frozen, or when S3 answered without writing
    patch_ids: list[str] = Field(default_factory=list)
    questions: list[ChatQuestionView] = Field(default_factory=list)
    context: StageContextView


class ChatTurnItemView(CamelModel):
    """One stored turn of the transcript (AI-CHAT-003)."""

    role: str
    content: str = ""


class ChatTranscriptView(CamelModel):
    stage_id: str
    turns: list[ChatTurnItemView] = Field(default_factory=list)
    context: StageContextView


class ChatClearedView(CamelModel):
    stage_id: str
    cleared: bool = True
    context: StageContextView
