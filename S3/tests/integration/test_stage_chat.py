"""``api.stage_chat`` — in-stage dialogue (AI-CHAT-001/002/003, AI-AG-008, FR-UI-002).

Three things are pinned here that no other file reaches:

* **the turn is real** — a message reaches a model and comes back as an answer, through the
  same context builder, Policy Engine and output ladder a run uses (AI-RT-003, AI-CHAT-003).
  A reply that skipped the policy would be a way to send stage content to a provider the
  project denied;
* **a change needs asking for** — the patch half of AI-CHAT-002 goes through S2's own patch
  submission, so a chat answer cannot write what a run's patch could not, and once the mapping
  is frozen the turn answers without a patch rather than failing;
* **the transcript is the session** — ``ai.chat_session`` points at one versioned artifact, and
  clearing it supersedes rather than deletes (AI-CHAT-003, 00 doc 6.6).

The model is ``MockAgentProvider`` behind a ``LOCAL_ONLY`` provider, so every turn here also
proves the effective policy let the content reach it (AI-POL-002's default).
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import FactoryError, unit_of_work

from factory_intelligence import api
from factory_intelligence.dto.result import ChatReply
from factory_intelligence.enums import AgentType, ProviderClass
from factory_intelligence.errors import PromptNotActivated
from factory_intelligence.llm.mock_agent import mock_provider
from factory_intelligence.models.deferred import ChatSessionRow
from factory_intelligence.runtime import chat as chat_module
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration.agent_runtime import (
    add_operation,
    clear_prompt_activations,
    draft_of,
    install_mock_model,
    open_draft,
    requirement_item,
    script,
    seed_project,
    unique_project_id,
)
from tests.integration.agent_runtime import answer as envelope


def _chat(
    project_id: str,
    message: str,
    *,
    stage: str = "REQUIREMENTS",
    draft_id: str | None = None,
    key: str = "chat:1",
    policies: dict[str, Any] | None = None,
) -> ChatReply:
    return api.stage_chat(
        project_id, stage, draft_id, message, policies or {}, "ops", idempotency_key=key
    )


def test_a_turn_answers_the_message_and_a_repeated_key_replays_it(
    harness: IntelligenceHarness,
) -> None:
    """AI-CHAT-001 with 00 doc 6.4: one paid answer per key."""
    _ = harness
    project = unique_project_id("chat")
    seed_project(project)
    provider = install_mock_model(AgentType.REVIEW, AgentType.REQUIREMENTS)
    script(provider, AgentType.REVIEW, envelope(summary="the weight record needs a programme"))

    reply = _chat(project, "which programme owns the weight record?")

    assert reply.answer == "the weight record needs a programme"
    assert reply.patch_ids == [], "nobody asked for a change"
    assert provider.call_count == 1

    again = _chat(project, "which programme owns the weight record?")
    assert again.answer == reply.answer
    assert provider.call_count == 1, "the replayed turn costs nothing"


def test_a_turn_for_an_unactivated_agent_raises(harness: IntelligenceHarness) -> None:
    """AI-REG-002: the dialogue refuses when the agent answering has no activated prompt.

    The Review Assistant's advice is a governed answer like any run's, so an un-activated agent
    raises rather than answering from an inert placeholder — which would still cost the call and
    leave no signal on the wire that prompt governance never took effect.
    """
    _ = harness
    clear_prompt_activations()
    project = unique_project_id("chat-unactivated")
    seed_project(project)
    provider = install_mock_model(AgentType.REVIEW, AgentType.REQUIREMENTS)

    with pytest.raises(PromptNotActivated) as refused:
        _chat(project, "which programme owns the weight record?", key="chat:unactivated")
    assert refused.value.code == "AI_PROMPT_NOT_ACTIVATED"
    assert provider.call_count == 0, "the refusal happens before the model is asked"


def test_the_second_turn_carries_the_first_one_into_context(
    harness: IntelligenceHarness,
) -> None:
    """AI-CHAT-003: the history is run context, not a display convenience."""
    _ = harness
    project = unique_project_id("chat")
    seed_project(project)
    provider = install_mock_model(AgentType.REVIEW, AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REVIEW,
        envelope(summary="first answer"),
        envelope(summary="second answer"),
    )
    # what the model was *given* is what this test is about, and the Mock records answers
    # rather than requests, so the requests are captured on the way through
    given: list[list[tuple[str, str]]] = []
    inner = provider.generate_structured

    def record(request: Any) -> Any:
        given.append([(message.role, message.content) for message in request.messages])
        return inner(request)

    provider.generate_structured = record  # type: ignore[method-assign]

    _chat(project, "what does this entry mean?", key="chat:a")
    _chat(project, "and the second one?", key="chat:b")

    assert any("what does this entry mean?" in content for _role, content in given[0])
    assert any("and the second one?" in content for _role, content in given[1])
    transcript = chat_module.history(project, "REQUIREMENTS")
    assert [turn["role"] for turn in transcript.turns] == ["user", "assistant", "user", "assistant"]
    assert transcript.context_artifact_id, "the session points at the transcript it grew from"


def test_a_requested_change_is_submitted_to_the_draft(
    harness: IntelligenceHarness,
) -> None:
    """AI-CHAT-002: an asked-for change goes through S2's patch submission, not around it."""
    _ = harness
    project = unique_project_id("chat")
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REVIEW, AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        envelope(
            summary="added the weight record requirement",
            operations=[
                add_operation("/requirements/items/-", requirement_item("art-0000000000"))
            ],
        ),
    )
    before = draft_of(draft_id).content_hash

    reply = _chat(project, "add a requirement that the app records a child's weight", draft_id=draft_id)

    assert reply.patch_ids, "the patch the person asked for is submitted"
    assert "added the weight record requirement" in reply.answer
    assert draft_of(draft_id).content_hash != before, "S2's draft actually holds the entry now"


def test_a_patch_the_person_did_not_ask_for_stays_advice(
    harness: IntelligenceHarness,
) -> None:
    """A turn with no draft has nowhere to write, so the patch is dropped before validation.

    This is explain.py's rule for the frozen mapping (AI-AG-006) applied to dialogue: the
    answer still arrives, and it does not claim anything was submitted.
    """
    _ = harness
    project = unique_project_id("chat")
    seed_project(project)
    provider = install_mock_model(AgentType.REVIEW, AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REVIEW,
        envelope(
            summary="I would add a requirement for the weight record",
            operations=[
                add_operation("/requirements/items/-", requirement_item("art-0000000000"))
            ],
        ),
    )

    reply = _chat(project, "what is missing from this stage?")

    assert reply.patch_ids == []
    assert "advice only" in reply.answer
    assert "not applied" not in reply.answer


def test_a_frozen_mapping_answers_without_writing(
    harness: IntelligenceHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI-CHAT-002's second half: after ARCHITECTURE_GATE a request gets an answer, not a patch.

    The freeze itself is S2's state and is tested there; what is tested here is that this build
    *reads* it and stops offering the draft to the turn.
    """
    _ = harness
    project = unique_project_id("chat")
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REVIEW, AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REVIEW,
        envelope(summary="the mapping is frozen, so here is the reasoning"),
    )
    monkeypatch.setattr(
        chat_module,
        "_semantics",
        lambda _project: type("Semantics", (), {"mapping_frozen": True})(),
    )

    reply = _chat(project, "change the option set", draft_id=draft_id)

    assert reply.patch_ids == []
    assert "frozen" in reply.answer, "the answer is the stage's reasoning, not a write"


def test_clearing_starts_again_and_keeps_the_old_turns_auditable(
    harness: IntelligenceHarness,
) -> None:
    """AI-CHAT-003 with 00 doc 6.6: a clear supersedes, it never deletes."""
    _ = harness
    project = unique_project_id("chat")
    seed_project(project)
    provider = install_mock_model(AgentType.REVIEW, AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REVIEW,
        envelope(summary="first answer"),
        envelope(summary="after the clear"),
    )
    _chat(project, "what does this mean?")
    before = chat_module.history(project, "REQUIREMENTS").context_artifact_id

    api.clear_chat(project, "REQUIREMENTS")
    cleared = chat_module.history(project, "REQUIREMENTS")
    assert cleared.cleared is True and cleared.turns == []

    _chat(project, "new question", key="chat:after")
    after = chat_module.history(project, "REQUIREMENTS")
    assert after.cleared is False and len(after.turns) == 2
    assert after.context_artifact_id != before, "the transcript became a new version"
    with unit_of_work() as session:
        rows = list(session.query(ChatSessionRow).filter_by(project_id=project))
    assert len(rows) == 1, "one session per project stage, as the unique constraint says"


def test_a_message_no_provider_may_receive_is_refused_not_dropped(
    harness: IntelligenceHarness,
) -> None:
    """AI-CHAT-003: the policy judges the dialogue like any other context item."""
    _ = harness
    project = unique_project_id("chat")
    seed_project(project)
    provider = mock_provider()
    api.set_llm_provider(provider)
    # the only model that could answer this stage's dialogue sits with a provider class the
    # organisation policy of AI-POL-002's default does not allow to receive anything
    api.governance(
        "provider.register",
        {"name": "abroad", "providerClass": ProviderClass.UNRESTRICTED.value},
    )
    api.governance(
        "model.register",
        {
            "provider": "abroad",
            "model": "only-foreign-1",
            "capabilities": {"structuredOutput": True, "maxContextTokens": 1_000_000},
            "applicableAgents": {AgentType.REVIEW.value: 1},
        },
    )

    with pytest.raises(FactoryError) as refused:
        _chat(project, "explain this entry", key="chat:pol")

    assert refused.value.error_code == "AI_PROVIDER_UNAVAILABLE"
    assert provider.call_count == 0, "nothing was sent anywhere to find out"


def test_an_unknown_stage_has_no_dialogue_to_answer(harness: IntelligenceHarness) -> None:
    """A stage name S2 does not know is a 404, not a Review Assistant with no context."""
    _ = harness
    project = unique_project_id("chat")
    with pytest.raises(FactoryError) as refused:
        _chat(project, "hello", stage="DEPLOYMENT")
    assert refused.value.error_code == "NOT_FOUND"
