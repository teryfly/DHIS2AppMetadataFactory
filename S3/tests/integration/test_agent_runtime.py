"""The M1 exit: the runtime drives a real Blueprint (AI-AC-01 … AI-AC-08, AI-NFR-03).

These tests are the subsystem's acceptance evidence, so they are written against **S2**
rather than a stubbed port: a patch S3 believed it submitted is only submitted when the
Blueprint accepted it, and a confirmation record only exists when S2's own schema carries it.
The model is the mock provider (AI-MOCK-001) so the runs are reproducible and no network is
touched, while every transaction, artifact, policy judgement and audit write is the
production path.

Refusals and the retry budgets that produce them live in
``test_agent_run_guards.py``; what is proven here is that a run *completes*, that its
evidence survives, and that a waiting run continues in its own persisted context.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from factory_blueprint import api as blueprint
from factory_blueprint.api import AuthorKind, DesignStage, Patch, PatchAuthor, PatchContext
from factory_foundation.api import (
    ArtifactCreate,
    ArtifactStatus,
    DataClassification,
    FixedClock,
    get_artifact,
    register_artifact,
    unit_of_work,
)
from sqlalchemy import select

from factory_intelligence import api
from factory_intelligence import artifacts as ai_artifacts
from factory_intelligence.dto.common import Answer
from factory_intelligence.dto.result import AgentRunResult
from factory_intelligence.enums import AgentType, RunState
from factory_intelligence.errors import AgentRunInProgress, PromptNotActivated
from factory_intelligence.models.call import ModelCallRow
from factory_intelligence.models.run import (
    AgentRunContextRow,
    AgentRunRow,
    ConfirmationRecordRow,
)
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration.agent_runtime import (
    add_operation,
    answer,
    audit_types,
    clear_prompt_activations,
    draft_of,
    install_mock_model,
    open_draft,
    question,
    request_for,
    requirement_item,
    script,
    seed_project,
    stage_input,
    suggested_question,
    unique_project_id,
)

MAPPING_KEY = "weight-data-element"
DECISION_ID = "MAP-001"


# --- a completed run -------------------------------------------------------------


def test_a_mock_agent_run_completes_and_s2_accepts_the_patch(
    harness: IntelligenceHarness,
) -> None:
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    input_id = stage_input(project, {"notes": "weight is recorded at every visit"})
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(operations=[add_operation("/requirements/items/-", requirement_item(input_id))]),
    )

    result = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, input_artifact_ids=[input_id]
        )
    )

    assert result.status == "COMPLETED", result.model_dump()
    assert result.patch_ids, "S2 accepted nothing"
    assert result.run_id
    assert result.agent_output_artifact_id
    assert provider.call_count == 1
    assert draft_of(draft_id).open_patch_count == 1


def test_the_completed_run_leads_with_its_own_evidence(harness: IntelligenceHarness) -> None:
    """AI-RT-001, AI-RT-014, AI-RT-022: the row, the artifact and the audit agree."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    input_id = stage_input(project, {"notes": "the nurse records a visit"})
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(operations=[add_operation("/requirements/items/-", requirement_item(input_id))]),
    )

    result = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, input_artifact_ids=[input_id]
        )
    )
    assert result.status == "COMPLETED"

    run = api.get_agent_run(str(result.run_id))
    assert run is not None
    assert run.status == "COMPLETED"
    assert run.patch_ids == result.patch_ids
    assert run.confidence == result.confidence

    events = audit_types(project)
    assert "ai.agent.started" in events
    assert "ai.agent.completed" in events
    assert "ai.model.called" in events

    artifact = get_artifact(str(result.agent_output_artifact_id))
    assert artifact.type == "AGENT_OUTPUT"
    assert artifact.build_record is not None
    assert artifact.build_record["agentRunId"] == result.run_id
    assert artifact.build_record["promptVersions"] == {"requirements.consolidate": 1}, (
        "the run reports the activated version it snapshotted (the harness seeds v1 active)"
    )
    assert artifact.build_record["modelCalls"] == 1
    assert artifact.data_classification.value == "INTERNAL"


def test_the_run_writes_one_usage_row_per_call_even_for_a_run_that_fails(
    harness: IntelligenceHarness,
) -> None:
    """AI-NFR-03, and the reason usage is committed apart: a failed run still paid for its
    calls, and deleting the run's evidence would make the estimate unauditable."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, {"summary": ""})

    result = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id)
    )

    assert result.status == "FAILED"
    rows = _model_calls(project)
    assert provider.call_count == 6, "one initial answer plus five repair turns"
    assert len(rows) == provider.call_count
    assert all(row.outcome == "OK" for row in rows)
    assert all(row.run_id == result.run_id for row in rows)


def _model_calls(project_id: str) -> list[ModelCallRow]:
    with unit_of_work() as session:
        return list(
            session.execute(
                select(ModelCallRow).where(ModelCallRow.project_id == project_id)
            ).scalars()
        )


# --- a stage walk ----------------------------------------------------------------


def test_the_three_m1_agents_each_leave_a_patch_s2_accepts(
    harness: IntelligenceHarness,
) -> None:
    """The M1 exit criterion: REQUIREMENTS, DOMAIN and MAPPING each complete on a real draft.

    One project per stage, on purpose: ``open_draft`` returns the project's *active* draft
    (``factory_blueprint/draft/service.py:262``), so a second stage on the same project would
    reuse the first stage's scope and S2 would be right to refuse it.  Advancing a project
    between stages is the Gate's freeze-and-promote, which is S2's own tested ground; what is
    tested here is that each agent's answer survives S3's ladder and S2's acceptance.
    """
    _ = harness
    provider = install_mock_model(
        AgentType.REQUIREMENTS, AgentType.DOMAIN, AgentType.MAPPING
    )
    input_id = stage_input(unique_project_id(), {"notes": "n/a"})

    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(operations=[add_operation("/requirements/items/-", requirement_item(input_id))]),
    )
    script(
        provider,
        AgentType.DOMAIN,
        answer(
            operations=[
                add_operation(
                    "/domain/entities/-",
                    {
                        "businessKey": "child-visit",
                        "displayName": {"default": "Child visit"},
                        "attributes": [
                            {
                                "name": "weightKg",
                                "label": {"default": "Weight (kg)"},
                                "valueType": "NUMBER",
                                "required": True,
                                "sensitivity": "INTERNAL",
                            }
                        ],
                        "source": {"artifactId": input_id},
                    },
                )
            ]
        ),
    )
    script(
        provider,
        AgentType.MAPPING,
        answer(
            operations=[
                add_operation(
                    "/architecture/decisions/-",
                    {
                        "decisionId": DECISION_ID,
                        "title": "Weight is a data element",
                        "context": "growth monitoring",
                        "decision": "model weight as one DataElement reused by every programme",
                        "status": "ACCEPTED",
                    },
                ),
                add_operation(
                    "/mapping/entries/-",
                    {
                        "businessKey": MAPPING_KEY,
                        "dhis2Type": "dataElement",
                        "decisionId": DECISION_ID,
                        "confidence": 1.0,
                        "source": {"decisionId": DECISION_ID},
                    },
                ),
            ],
            decisions=[
                {
                    "id": DECISION_ID,
                    "confidence": 1.0,
                    "subject": MAPPING_KEY,
                    "statement": "weight is one data element, reused by every programme",
                }
            ],
        ),
    )

    for agent, stage in (
        (AgentType.REQUIREMENTS, "REQUIREMENTS"),
        (AgentType.DOMAIN, "DOMAIN"),
        (AgentType.MAPPING, "MAPPING"),
    ):
        project = unique_project_id()
        seed_project(project)
        draft_id = open_draft(project, stage)
        # AI-INP-001: the mapping leg reads an NRR, not the raw upload — a MAPPING run handed
        # a BUSINESS_INPUT is refused, and this walk is the M1 exit criterion, so it has to
        # walk the same path a real stage run takes
        artifact = (
            stage_input(project, {"items": []}, artifact_type="NRR", creator="S3")
            if agent is AgentType.MAPPING
            else input_id
        )
        result = api.start_agent_run(
            request_for(
                project,
                agent,
                stage=stage,
                draft_id=draft_id,
                input_artifact_ids=[artifact],
                idempotency_key=f"walk:{stage}",
            )
        )
        assert result.status == "COMPLETED", f"{agent.value}: {result.model_dump()}"
        assert result.patch_ids, agent.value
    assert provider.call_count == 3


# --- waiting and resuming --------------------------------------------------------


def test_a_blocking_question_waits_and_submits_no_patch(harness: IntelligenceHarness) -> None:
    """AI-RT-015 with ruling 4: the question gates exactly the decision the patch encodes."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(
            operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))],
            questions=[question("q-1", "Which programme owns the record?", "HIGH")],
        ),
    )

    result = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id)
    )

    assert result.status == "WAITING_INPUT"
    assert result.patch_ids == []
    assert [item.local_id for item in result.questions] == ["Q1"]
    assert result.questions[0].is_blocking
    assert draft_of(draft_id).open_patch_count == 0

    with unit_of_work() as session:
        context = session.get(AgentRunContextRow, str(result.run_id))
        assert context is not None
        assert context.pending_question_local_ids == ["Q1"]
        assert context.context_artifact_id


def test_resuming_an_unactivated_run_raises_and_leaves_it_waiting(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-002 on resume: the answers arrive into a run whose agent has no activated prompt.

    Resume *raises* rather than returning a FAILED result — the WAITING_INPUT row already
    exists and must survive, so that once an operator finishes the deployment the very same
    resume can run.  A FAILED transition here would strand the answers and the stage.
    """
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(
            operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))],
            questions=[question("q-1", "Which programme owns the record?", "HIGH")],
        ),
    )
    waiting = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id)
    )
    assert waiting.status == "WAITING_INPUT"
    run_id = str(waiting.run_id)

    clear_prompt_activations()
    with pytest.raises(PromptNotActivated) as refused:
        api.resume_agent_run(
            run_id,
            [{"question_local_id": "Q1", "suggestion_id": "S1", "answered_by": "ops"}],
            f"resume:unactivated:{run_id}",
        )
    assert refused.value.code == "AI_PROMPT_NOT_ACTIVATED"

    with unit_of_work() as session:
        row = session.get(AgentRunRow, run_id)
        assert row is not None and row.status == RunState.WAITING_INPUT.value, (
            "the refusal leaves the run exactly as resumable as it was"
        )


def test_a_non_blocking_question_does_not_stall_the_stage(
    harness: IntelligenceHarness,
) -> None:
    """S1 resumes only on unanswered *blocking* questions, so a cosmetic one must not wait."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(
            operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))],
            questions=[question("q-1", "Spell the label British or American?", "LOW")],
        ),
    )

    result = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id)
    )

    assert result.status == "COMPLETED"
    assert result.patch_ids
    assert [item.local_id for item in result.questions] == ["Q1"]


def test_resuming_confirms_the_gated_decision_in_s2_itself(
    harness: IntelligenceHarness,
) -> None:
    """AI-AC-04, the acceptance half: the resumed turn reuses the run's context version chain,
    and S2 — not S3 — ends up holding the confirmation record."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "MAPPING")
    before = draft_of(draft_id)

    provider = install_mock_model(AgentType.MAPPING)
    script(
        provider,
        AgentType.MAPPING,
        answer(
            operations=[],
            questions=[
                question(
                    "q-1",
                    "Is weight one shared data element or one per programme?",
                    "HIGH",
                    affects=[f"mapping.entries.{MAPPING_KEY}"],
                )
            ],
        ),
    )
    waiting = api.start_agent_run(
        request_for(
            project,
            AgentType.MAPPING,
            stage="MAPPING",
            draft_id=draft_id,
            idempotency_key="confirm:1",
        )
    )
    assert waiting.status == "WAITING_INPUT"
    first_context = _context_row(str(waiting.run_id))
    assert first_context is not None
    first_artifact = first_context.context_artifact_id

    # a zero-second instant, because datetime.isoformat() omits the seconds there and S2's
    # dateTime pattern then fails at freeze time rather than here
    api.set_clock(FixedClock(datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)))
    script(
        provider,
        AgentType.MAPPING,
        answer(
            operations=[
                add_operation(
                    "/architecture/decisions/-",
                    {
                        "decisionId": DECISION_ID,
                        "title": "Weight is a data element",
                        "context": "growth monitoring",
                        "decision": "one DataElement reused by every programme",
                        "status": "ACCEPTED",
                    },
                ),
                add_operation(
                    "/mapping/entries/-",
                    {
                        "businessKey": MAPPING_KEY,
                        "dhis2Type": "dataElement",
                        "decisionId": DECISION_ID,
                        "confidence": 0.55,
                        "source": {"decisionId": DECISION_ID},
                    },
                ),
            ],
            decisions=[
                {
                    "id": DECISION_ID,
                    "confidence": 0.95,
                    "subject": MAPPING_KEY,
                    "statement": "one shared data element",
                    "alternatives": [{"option": "one per programme", "whyRejected": "duplicate"}],
                }
            ],
        ),
    )
    answered = api.resume_agent_run(
        str(waiting.run_id),
        [
            {
                "question_local_id": "Q1",
                "suggestion_id": "one per programme",
                "answered_by": "sol-arch",
            }
        ],
        f"resume:{waiting.run_id}",
    )

    assert answered.denial_reasons == [], answered.denial_reasons
    assert answered.status == "COMPLETED", answered.model_dump()
    assert answered.run_id == waiting.run_id
    assert answered.questions == []
    assert answered.patch_ids, "the follow-up patch never reached S2"

    second_context = _context_row(str(waiting.run_id))
    assert second_context is not None
    assert second_context.attempt == 2
    assert second_context.context_artifact_id != first_artifact
    assert get_artifact(str(second_context.context_artifact_id)).prev_version_id == first_artifact

    recorded = _confirmations(str(waiting.run_id))
    assert [item.question_local_id for item in recorded] == ["Q1"]
    assert recorded[0].target_path == f"/mapping/entries/{MAPPING_KEY}/confirmation"

    document = draft_section(project, blueprint.Section.MAPPING, draft_id)
    entry = _entry_of(document, MAPPING_KEY)
    assert entry["confirmation"]["questionId"] == "Q1"
    assert entry["confirmation"]["answerer"] == "sol-arch"
    assert entry["confirmation"]["answeredAt"] == "2026-09-28T10:00:00Z"
    assert draft_of(draft_id).content_hash != before.content_hash
    assert "ai.agent.resumed" in audit_types(project)


def test_a_waiting_run_keeps_its_own_answer_for_the_resume(
    harness: IntelligenceHarness,
) -> None:
    """AI-RT-015: the resumed turn must see what the model answered last turn.

    Without the answer in the stored transcript the model has only the question ids, not the
    decisions it wrote, so it re-emits them without their rejected alternatives and the
    resumed turn is refused with AIC-004.
    """
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "MAPPING")
    provider = install_mock_model(AgentType.MAPPING)
    script(
        provider,
        AgentType.MAPPING,
        answer(
            operations=[],
            decisions=[
                {
                    "id": "D7",
                    "subject": "weight-data-element",
                    "selected": {"option": "one shared data element"},
                    "alternatives": [
                        {"option": "one per programme", "reasonRejected": "duplicate object"}
                    ],
                    "confidence": 0.4,
                    "impactLevel": "HIGH",
                }
            ],
            questions=[
                question(
                    "q-1",
                    "Is weight one shared data element or one per programme?",
                    "HIGH",
                    affects=["D7"],
                )
            ],
        ),
    )
    waiting = api.start_agent_run(
        request_for(
            project,
            AgentType.MAPPING,
            stage="MAPPING",
            draft_id=draft_id,
            idempotency_key="transcript:1",
        )
    )
    assert waiting.status == "WAITING_INPUT"

    messages = _context_messages(str(waiting.run_id))
    assert messages[-1]["role"] == "assistant"
    stored = json.loads(messages[-1]["content"])
    assert stored["decisions"][0]["alternatives"][0]["option"] == "one per programme"


def test_an_answered_decision_is_not_asked_again_on_resume(
    harness: IntelligenceHarness,
) -> None:
    """AI-AG-012: the answer *is* the confirmation; re-listing the decision must not fail
    with AIC-005 merely because the resumed answer asks no new question about it."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "MAPPING")
    provider = install_mock_model(AgentType.MAPPING)
    decision = {
        "id": "D7",
        "subject": "weight-data-element",
        "selected": {"option": "one shared data element"},
        "alternatives": [{"option": "one per programme", "reasonRejected": "duplicate object"}],
        "confidence": 0.4,
        "impactLevel": "HIGH",
    }
    script(
        provider,
        AgentType.MAPPING,
        answer(
            operations=[],
            decisions=[decision],
            questions=[
                question(
                    "q-1",
                    "Is weight one shared data element or one per programme?",
                    "HIGH",
                    affects=["D7"],
                )
            ],
        ),
        answer(operations=[], decisions=[decision], questions=[]),
    )
    waiting = api.start_agent_run(
        request_for(
            project,
            AgentType.MAPPING,
            stage="MAPPING",
            draft_id=draft_id,
            idempotency_key="answered:1",
        )
    )
    assert waiting.status == "WAITING_INPUT"

    resumed = api.resume_agent_run(
        str(waiting.run_id),
        [
            {
                "question_local_id": "Q1",
                "suggestion_id": "one shared data element",
                "suggestion_texts": ["one shared data element"],
                "answered_by": "ops",
            }
        ],
        f"resume:{waiting.run_id}",
    )

    assert resumed.denial_reasons == [], resumed.denial_reasons
    assert resumed.status == "COMPLETED"

    # The resumed turn rebuilds the context; it must not append the stored copy of it again.
    body = _context_body(str(resumed.run_id))
    messages = list(body["messageSequence"])
    assert body["conversationStart"] == next(
        index for index, item in enumerate(messages) if item["role"] == "assistant"
    )
    assert sum("Stage: MAPPING." in item["content"] for item in messages) == 1


def draft_section(project_id: str, section: Any, draft_id: str) -> list[dict[str, Any]]:
    """The draft's own entries for one section, read the way any caller would."""
    _ = draft_id
    selected = blueprint.get_entries(
        project_id, blueprint.EntrySelector(sections=[section], source="DRAFT")
    )
    return [dict(item) for item in selected.entries]


def _context_row(run_id: str) -> AgentRunContextRow | None:
    with unit_of_work() as session:
        return session.get(AgentRunContextRow, run_id)


def _context_body(run_id: str) -> dict[str, Any]:
    row = _context_row(run_id)
    assert row is not None and row.context_artifact_id is not None
    body, _classification = ai_artifacts.read_json(str(row.context_artifact_id))
    return dict(body)


def _context_messages(run_id: str) -> list[dict[str, Any]]:
    return list(_context_body(run_id)["messageSequence"])


def _confirmations(run_id: str) -> list[ConfirmationRecordRow]:
    with unit_of_work() as session:
        return list(
            session.execute(
                select(ConfirmationRecordRow).where(ConfirmationRecordRow.run_id == run_id)
            ).scalars()
        )


def _entry_of(entries: list[dict[str, Any]], business_key: str) -> dict[str, Any]:
    for item in entries:
        wrapped = item.get("value")
        value = wrapped if isinstance(wrapped, dict) else item
        if value.get("businessKey") == business_key:
            return dict(value)
    raise AssertionError(f"no entry {business_key!r} in the draft: {entries}")


def test_a_resumed_run_replays_when_the_same_attempt_answers_again(
    harness: IntelligenceHarness,
) -> None:
    """00 doc 6.4: S1's retry of a *delivery* of the same answers must not re-run the model."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(questions=[question("q-1", "Which unit?", "HIGH")]),
        answer(operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))]),
    )
    waiting = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="re:1")
    )
    answers = [{"question_local_id": "Q1", "custom_text": "kilograms", "answered_by": "ops"}]

    first = api.resume_agent_run(str(waiting.run_id), answers, "resume:replayed")
    again = api.resume_agent_run(str(waiting.run_id), answers, "resume:replayed")

    assert first.status == "COMPLETED"
    assert again.patch_ids == first.patch_ids
    assert provider.call_count == 2


def test_a_malformed_array_index_is_repaired_not_fatal(
    harness: IntelligenceHarness,
) -> None:
    """A replace against an absent entry is a mechanical mistake: S2's error is fed back
    with the array-path rules, and the next complete answer is submitted."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(
            operations=[
                {
                    "op": "replace",
                    "path": "/requirements/items/3/statement",
                    "value": "a statement",
                }
            ]
        ),
        answer(operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))]),
    )

    result = api.start_agent_run(
        request_for(
            project,
            AgentType.REQUIREMENTS,
            draft_id=draft_id,
            idempotency_key="malformed-repair:1",
        )
    )

    assert result.status == "COMPLETED", result.model_dump()
    assert provider.call_count == 2
    repair = " ".join(item["content"] for item in _context_messages(str(result.run_id)))
    assert "array index 3 out of range" in repair
    assert "add /<container>/-" in repair


def test_a_cited_decision_is_registered_before_s2_sees_it(
    harness: IntelligenceHarness,
) -> None:
    """FR-MAP-001, BP-VAL-010: S2 resolves a decisionId only against /architecture/decisions.

    The mapping entries were cited correctly and the answer defined every decision, so
    nothing in S3 used to object — and the run died at AI-RT-013 after spending its whole
    repair budget on a mistake it never explained.  Now the ladder refuses it on the first
    answer and the repair turn says which container the decision belongs in.
    """
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "MAPPING")
    provider = install_mock_model(AgentType.MAPPING)
    decision = {
        "id": DECISION_ID,
        "confidence": 1.0,
        "subject": MAPPING_KEY,
        "statement": "weight is one data element, reused by every programme",
    }
    mapping_entry = add_operation(
        "/mapping/entries/-",
        {
            "businessKey": MAPPING_KEY,
            "dhis2Type": "dataElement",
            "decisionId": DECISION_ID,
            "confidence": 1.0,
            "source": {"note": "the approved domain model"},
        },
    )
    script(
        provider,
        AgentType.MAPPING,
        answer(operations=[mapping_entry], decisions=[decision]),
        answer(
            operations=[
                add_operation(
                    "/architecture/decisions/-",
                    {
                        "decisionId": DECISION_ID,
                        "title": "Weight is one data element",
                        "context": "growth monitoring across programmes",
                        "decision": "model weight as one DataElement reused by every programme",
                        "status": "PROPOSED",
                    },
                ),
                mapping_entry,
            ],
            decisions=[decision],
        ),
    )

    result = _mapping_run_no_discovery(project, draft_id, "decision-registered:1")

    assert result.status == "COMPLETED", result.denial_reasons
    assert provider.call_count == 2, "the ladder caught it, so S2 was never asked"
    assert result.patch_ids
    repair = " ".join(item["content"] for item in _context_messages(str(result.run_id)))
    assert "no entry in /architecture/decisions registers" in repair
    assert "references unknown decisionId" in repair


def test_a_decision_the_draft_registers_needs_no_second_record(
    harness: IntelligenceHarness,
) -> None:
    """The narrow case: an accepted decision an earlier run recorded is citable as-is, and a
    check that read only the answer would refuse it and force a duplicate record."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "MAPPING")
    _seed_registry_entry(project, draft_id, "weight_kg")
    provider = install_mock_model(AgentType.MAPPING)
    script(
        provider,
        AgentType.MAPPING,
        answer(
            operations=[
                add_operation(
                    "/mapping/entries/-",
                    {
                        "businessKey": "map-children-under-five",
                        "dhis2Type": "dataElement",
                        "decisionId": DECISION_ID,
                        "confidence": 1.0,
                        "source": {"note": "the approved domain model"},
                    },
                )
            ],
            decisions=[
                {
                    "id": DECISION_ID,
                    "confidence": 1.0,
                    "subject": "map-children-under-five",
                    "statement": "weight is one data element, reused by every programme",
                }
            ],
        ),
    )

    result = _mapping_run_no_discovery(project, draft_id, "decision-registered-draft:1")

    assert result.status == "COMPLETED", result.denial_reasons
    assert provider.call_count == 1, "an accepted decision is not re-registered"


def test_a_rejected_patch_asks_for_missing_business_facts_and_resumes(
    harness: IntelligenceHarness,
) -> None:
    """A repair that lacks a business fact becomes the same questionnaire the first
    requirements turn uses; the answers are then fed back into the resumed run."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(
            operations=[
                {
                    "op": "replace",
                    "path": "/requirements/items/3/statement",
                    "value": "a statement",
                }
            ]
        ),
        answer(operations=[], questions=[suggested_question()]),
        answer(operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))]),
    )

    waiting = api.start_agent_run(
        request_for(
            project,
            AgentType.REQUIREMENTS,
            draft_id=draft_id,
            idempotency_key="ask-after-reject:1",
        )
    )

    assert waiting.status == "WAITING_INPUT", waiting.denial_reasons
    assert waiting.patch_ids == [], "a blocking question submits no patch"
    assert len(waiting.questions) == 1
    asked = waiting.questions[0]
    assert len(asked.suggestions) == 2
    assert any(item.get("recommended") for item in asked.suggestions)
    assert provider.call_count == 2, "the malformed patch, then the questionnaire"

    resumed = api.resume_agent_run(
        str(waiting.run_id),
        [
            {
                "question_local_id": asked.local_id,
                "suggestion_id": asked.suggestions[0]["id"],
                "suggestion_texts": [asked.suggestions[0]["text"]],
                "answered_by": "ops",
            }
        ],
        f"resume:{waiting.run_id}",
    )

    assert resumed.status == "COMPLETED", resumed.denial_reasons
    assert resumed.patch_ids
    assert provider.call_count == 3, "the answer turn re-runs the model with the new facts"


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED"])
def test_resuming_a_run_that_is_not_waiting_reports_its_outcome(
    harness: IntelligenceHarness, state: str
) -> None:
    """``RunNotResumable`` is for a run that cannot continue; a finished one has an answer."""
    _ = harness, state
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    if state == "COMPLETED":
        script(
            provider,
            AgentType.REQUIREMENTS,
            answer(operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))]),
        )
    else:
        script(provider, AgentType.REQUIREMENTS, {"summary": ""})
    run = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key=f"fin:{state}")
    )
    assert run.status == state

    reported = api.resume_agent_run(
        str(run.run_id), [{"question_local_id": "Q1", "answered_by": "ops"}], f"resume:{state}"
    )
    assert reported.status == state
    assert reported.run_id == run.run_id


def test_resume_without_a_run_of_that_id_says_so(harness: IntelligenceHarness) -> None:
    _ = harness
    from factory_foundation.api import NotFoundError

    with pytest.raises(NotFoundError):
        api.resume_agent_run("arun-absent-0000", [], "resume:absent")


def test_resuming_a_run_that_is_still_executing_is_a_retryable_refusal(
    harness: IntelligenceHarness,
) -> None:
    """A second delivery of a turn already in flight must not start that turn twice.

    ``RUNNING`` is not a result and S1's status enum has no seventh value, so the answer is a
    retryable refusal — the same reasoning as ``start_agent_run``'s (AI-AC-05).
    """
    _ = harness
    from factory_foundation.api import idempotency

    from factory_intelligence.registries import IdemScope
    from factory_intelligence.runtime.resume import resume_digest, resume_key

    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(questions=[question("q", "unit?", "HIGH")]),
    )
    waiting = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="busy:1")
    )
    assert waiting.status == "WAITING_INPUT"

    answers = [{"question_local_id": "Q1", "custom_text": "kilograms", "answered_by": "ops"}]
    with unit_of_work() as session:
        idempotency.begin(
            IdemScope.AGENT_RESUME,
            resume_key(f"resume:busy:{waiting.run_id}", 2),
            resume_digest(str(waiting.run_id), 2, [Answer.model_validate(answers[0])]),
            session=session,
        )

    with pytest.raises(AgentRunInProgress):
        api.resume_agent_run(str(waiting.run_id), answers, f"resume:busy:{waiting.run_id}")
    assert provider.call_count == 1, "the refused resume must not have reached the model"


def test_resuming_a_run_that_is_still_running_refuses_as_not_resumable(
    harness: IntelligenceHarness,
) -> None:
    _ = harness
    from factory_intelligence.errors import RunNotResumable
    from factory_intelligence.models import AgentRunRow

    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, answer())
    started = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="run:9")
    )
    assert started.status == "COMPLETED"
    with unit_of_work() as session:
        row = session.get(AgentRunRow, str(started.run_id))
        assert row is not None
        row.status = RunState.RUNNING.value

    with pytest.raises(RunNotResumable):
        api.resume_agent_run(
            str(started.run_id),
            [{"question_local_id": "Q1", "answered_by": "ops"}],
            f"resume:run:9:{started.run_id}",
        )


def test_the_run_row_reaches_the_state_the_result_reports(
    harness: IntelligenceHarness,
) -> None:
    """AI-RT-017: the result is a projection of the row, never a parallel truth."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, answer(questions=[question("q", "unit?", "HIGH")]))
    result = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="row:1")
    )
    assert result.status == "WAITING_INPUT"

    with unit_of_work() as session:
        from factory_intelligence.models import AgentRunRow

        row = session.get(AgentRunRow, str(result.run_id))
        assert row is not None
        assert row.status == RunState.WAITING_INPUT.value
        assert row.model_calls == 1
        assert row.provider == "mock"
        assert row.usage["modelCalls"] == 1


# --- similar objects (AI-AG-011) -------------------------------------------------


def _discovery(project_id: str, objects: list[dict[str, Any]]) -> str:
    """A ``DISCOVERY_RESULT`` artifact — S4's product, and S3's read-only input."""
    handle = ai_artifacts.stage_json(project_id, "DISCOVERY_RESULT", {"objects": objects})
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type="DISCOVERY_RESULT",
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S4",
                source_artifact_ids=[],
                status=ArtifactStatus.APPROVED,
            ),
            session=session,
        )
    return str(info.id)


def _seed_registry_entry(project_id: str, draft_id: str, code: str) -> None:
    """A human-authored registry entry with a code, so the run has something resembling the
    design basis to decide about — the registry is where S2 records a code and a dhis2Type."""
    blueprint.prepare_draft_base(project_id, draft_id)
    operations = [
        add_operation(
            "/architecture/decisions/-",
            {
                "decisionId": DECISION_ID,
                "title": "Weight is a data element",
                "context": "growth monitoring",
                "decision": "model weight as one DataElement",
                "status": "ACCEPTED",
            },
        ),
        add_operation(
            "/registry/entries/-",
            {
                "businessKey": MAPPING_KEY,
                "dhis2Type": "dataElement",
                "code": code,
                "disposition": "REUSE",
                "status": "APPROVED",
                "managed": False,
                "decisionId": DECISION_ID,
                "sourceEnvironment": "DEV",
                "observedUid": "w1234567890",
                "observedHash": "sha256:" + "0" * 64,
                "observedLastUpdated": "2026-01-01T00:00:00Z",
                "source": {"note": "the gate mapped it"},
            },
        ),
    ]
    patch = Patch(
        patch_id=f"ptch-seed-{project_id}",
        base_version=draft_of(draft_id).base_version,
        author=PatchAuthor(kind=AuthorKind.USER, ref="sol-arch"),
        operations=operations,
        rationale="seeded by the test",
    )
    with unit_of_work() as session:
        outcome = blueprint.submit_patch(
            project_id,
            draft_id,
            patch,
            PatchContext(stage=DesignStage.MAPPING, change_request_id=None),
            idempotency_key=f"seed:{project_id}",
            session=session,
        )
    status = str(getattr(outcome.status, "value", outcome.status))
    assert status != "REJECTED", outcome.issues


def _mapping_run(project: str, draft_id: str, discovery: str, key: str) -> AgentRunResult:
    return api.start_agent_run(
        request_for(
            project,
            AgentType.MAPPING,
            stage="MAPPING",
            draft_id=draft_id,
            discovery_result_artifact_id=discovery,
            design_reference_environment_key="DEV",
            idempotency_key=key,
        )
    )


def _mapping_run_no_discovery(project: str, draft_id: str, key: str) -> AgentRunResult:
    """A brownfield-free MAPPING run: no similar objects to decide about, so the only thing
    the answer can be refused for is what it writes."""
    return api.start_agent_run(
        request_for(
            project,
            AgentType.MAPPING,
            stage="MAPPING",
            draft_id=draft_id,
            idempotency_key=key,
        )
    )


def test_an_answer_that_ignores_a_similar_object_fails_naming_it(
    harness: IntelligenceHarness,
) -> None:
    """AI-AG-011: the search is deterministic and the obligation is enforced, in rung 2 —
    before S2, and with the object named so the operator can act on it."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "MAPPING")
    _seed_registry_entry(project, draft_id, "weight_kg")
    discovery = _discovery(
        project,
        [{"kind": "dataElement", "code": "weight_kg", "name": "Weight (kg)", "uid": "w1234567890"}],
    )
    provider = install_mock_model(AgentType.MAPPING)
    script(provider, AgentType.MAPPING, answer(operations=[], decisions=[]))

    result = _mapping_run(project, draft_id, discovery, "ag011-ignored")

    assert result.status == "FAILED", result.model_dump()
    assert provider.call_count == 6, "one initial answer plus five repair turns, then the refusal"
    assert "weight_kg" in " ".join(result.denial_reasons)
    assert result.patch_ids == []
    assert draft_of(draft_id).open_patch_count == 1, "only the seeded patch is in the draft"


def test_a_similar_object_is_decided_before_the_answer_is_accepted(
    harness: IntelligenceHarness,
) -> None:
    """The same run, answered: REUSE with a reason, and the observed UID stays data."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "MAPPING")
    _seed_registry_entry(project, draft_id, "weight_kg")
    discovery = _discovery(
        project,
        [{"kind": "dataElement", "code": "weight_kg", "name": "Weight (kg)", "uid": "w1234567890"}],
    )
    provider = install_mock_model(AgentType.MAPPING)
    script(
        provider,
        AgentType.MAPPING,
        answer(operations=[], decisions=[]),
        answer(
            operations=[],
            decisions=[
                {
                    "id": DECISION_ID,
                    "confidence": 1.0,
                    "subject": "weight_kg",
                    "disposition": "REUSE",
                    "statement": "reuse the observed weight_kg rather than creating a second one",
                }
            ],
        ),
    )

    result = _mapping_run(project, draft_id, discovery, "ag011-decided")

    assert result.status == "COMPLETED", result.denial_reasons
    assert provider.call_count == 2
    assert result.rejected_artifacts == []
