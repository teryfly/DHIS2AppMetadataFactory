"""``AgentOutput`` — the binding of AI-RT-010's answer into a type.

The envelope is what an untrusted process is asked to produce, so two things are checked
here rather than assumed:

* **the spellings the model actually uses are accepted.**  AI-RT-010 writes ``baseVersion``
  and ``declaredImpact`` in camelCase, and S1 coerces every result with
  ``dto.model_validate(value.model_dump())`` — a field that only bound the snake_case form
  would arrive as ``None`` and the run would proceed on a missing value;
* **the two invariants S1 depends on are refused rather than repaired.**  Duplicated
  question ids are dropped silently by S1's dedupe on
  ``(agentRunId, sourceLocalId)``, so one question would simply vanish, and an operation
  without ``op``/``path`` would reach S2 as a patch nobody can apply.

The operations themselves stay raw mappings: interpreting them is S2's job
(``AI-RT-013``), and reparsing them here would create a second, drifting idea of what a
patch means.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from factory_intelligence.dto.agent import AgentOutput, ModelQuestion, PatchEnvelope
from factory_intelligence.dto.common import Question, Suggestion


def _answer(**changes: Any) -> dict[str, Any]:
    """The smallest answer that binds, in the SRS's own camelCase."""
    answer: dict[str, Any] = {
        "summary": "consolidated the input artifact",
        "decisions": [{"id": "MAP-001"}, {"decisionId": "MAP-002"}],
        "patch": {
            "baseVersion": "7",
            "operations": [
                {"op": "add", "path": "/requirements/items/-", "value": {"businessKey": "req.a"}}
            ],
        },
        "questions": [{"id": "Q1", "text": "which org unit owns the code?"}],
        "confidence": 0.82,
    }
    answer.update(changes)
    return answer


def _question(**changes: Any) -> dict[str, Any]:
    question: dict[str, Any] = {
        "id": "Q1",
        "text": "which of these is the real source?",
        "severity": "HIGH",
        "affects": ["MAP-001", "map.demo.001"],
        "blocking": True,
        "suggestions": [
            {
                "id": "S1",
                "text": "reuse the published code list",
                "rationale": "it already exists in the reference environment",
                "changesUid": False,
                "regenerates": True,
            }
        ],
        "details": {"source": "discovery"},
    }
    question.update(changes)
    return question


# --- the camelCase contract --------------------------------------------------------


def test_the_patch_accepts_the_srs_camel_case_base_version() -> None:
    """AI-RT-010: ``patch{baseVersion, operations}`` is the SRS's own spelling."""
    output = AgentOutput.model_validate(_answer())
    assert output.patch is not None
    assert output.patch.base_version == "7"


def test_the_patch_also_accepts_the_snake_case_spelling() -> None:
    """AI-RT-010: a model that writes ``base_version`` is not wrong enough to refuse."""
    output = AgentOutput.model_validate(
        {**_answer(), "patch": {"base_version": "9", "operations": []}}
    )
    assert output.patch is not None and output.patch.base_version == "9"


def test_declared_impact_is_accepted_in_camel_case() -> None:
    """AI-RT-010: the advisory impact a model declares, in the spelling the SRS uses."""
    envelope = PatchEnvelope.model_validate(
        {"baseVersion": "7", "operations": [], "declaredImpact": {"level": "HIGH"}}
    )
    assert envelope.declared_impact == {"level": "HIGH"}


def test_declared_impact_is_optional_and_never_authoritative() -> None:
    """AI-RT-010: it is intent, so an answer that omits it is complete."""
    envelope = PatchEnvelope.model_validate({"baseVersion": "7", "operations": []})
    assert envelope.declared_impact is None


def test_the_output_serialises_back_to_the_models_spelling() -> None:
    """AI-RT-014: the AGENT_OUTPUT artifact stores what the model said, aliased as the
    envelope names it, so a replay re-validates."""
    dumped = AgentOutput.model_validate(_answer()).model_dump(mode="json", by_alias=True)
    assert dumped["patch"]["baseVersion"] == "7"
    assert dumped["questions"][0]["id"] == "Q1"
    assert AgentOutput.model_validate(dumped).patch is not None


# --- the two invariants S1 relies on ---------------------------------------------


def test_an_operation_without_op_is_refused_by_name_and_index() -> None:
    """AI-RT-013: a patch S2 could not apply must not be constructible at all."""
    with pytest.raises(ValidationError) as caught:
        AgentOutput.model_validate(
            {**_answer(), "patch": {"baseVersion": "7", "operations": [{"path": "/requirements/items/-"}]}}
        )
    message = str(caught.value)
    assert "patch.operations[0] needs 'op' and 'path'" in message
    assert "['path']" in message


def test_an_operation_without_a_path_is_refused() -> None:
    """AI-RT-013: the same, the other way round."""
    with pytest.raises(ValidationError, match="needs 'op' and 'path'"):
        PatchEnvelope.model_validate({"baseVersion": "7", "operations": [{"op": "remove"}]})


def test_the_second_operation_is_the_one_named() -> None:
    """AI-RT-011: the corrective feedback has to say which operation broke."""
    operations = [
        {"op": "add", "path": "/requirements/items/-", "value": {}},
        {"op": "add", "value": {}},
    ]
    with pytest.raises(ValidationError, match=r"patch\.operations\[1\]"):
        PatchEnvelope.model_validate({"baseVersion": "7", "operations": operations})


def test_duplicated_question_ids_are_refused_naming_the_duplicate() -> None:
    """AI-RT-015: S1 dedupes on ``(agentRunId, sourceLocalId)`` and *skips* a repeat, so
    two questions keyed ``Q1`` would silently lose one of them."""
    questions = [
        {"id": "Q1", "text": "first?"},
        {"id": "Q2", "text": "second?"},
        {"id": "Q1", "text": "third?"},
    ]
    with pytest.raises(ValidationError) as caught:
        AgentOutput.model_validate({**_answer(), "questions": questions})
    assert "questions must have distinct ids; duplicated: Q1" in str(caught.value)


def test_distinct_question_ids_are_accepted() -> None:
    """AI-RT-015: three questions is the normal case for a contested stage."""
    questions = [{"id": "Q1", "text": "a?"}, {"id": "Q2", "text": "b?"}, {"id": "Q3", "text": "c?"}]
    output = AgentOutput.model_validate({**_answer(), "questions": questions})
    assert [question.id for question in output.questions] == ["Q1", "Q2", "Q3"]


def test_only_one_duplicate_is_named_even_when_there_are_two() -> None:
    """AI-RT-015: the message lists each duplicated id once, in order, so the model can
    act on it."""
    questions = [
        {"id": "Q1", "text": "a?"},
        {"id": "Q1", "text": "b?"},
        {"id": "Q2", "text": "c?"},
        {"id": "Q2", "text": "d?"},
    ]
    with pytest.raises(ValidationError, match="duplicated: Q1, Q2"):
        AgentOutput.model_validate({**_answer(), "questions": questions})


def test_a_question_needs_both_an_id_and_a_text() -> None:
    """AI-RT-010: an unanchored question cannot be answered or displayed."""
    for question in ([{"text": "no id"}], [{"id": "Q1", "text": ""}]):
        with pytest.raises(ValidationError):
            AgentOutput.model_validate({**_answer(), "questions": question})


# --- decisions and patch presence --------------------------------------------------


def test_decision_ids_reads_every_spelling_the_models_use() -> None:
    """AI-RT-016: S2 resolves a confirmation record by the decision's id, whichever of the
    three names the answer gave it."""
    decisions = [
        {"decisionId": "MAP-001"},
        {"decision_id": "MAP-002"},
        {"id": "MAP-003"},
    ]
    output = AgentOutput.model_validate({**_answer(), "decisions": decisions})
    assert output.decision_ids == ["MAP-001", "MAP-002", "MAP-003"]


def test_decision_ids_skips_entries_that_name_nothing() -> None:
    """AI-RT-010: an empty or non-string id is not a decision S2 can resolve."""
    decisions = [{"id": ""}, {"id": 7}, {"other": "MAP-009"}, {}, {"decisionId": "MAP-010"}]
    output = AgentOutput.model_validate({**_answer(), "decisions": decisions})
    assert output.decision_ids == ["MAP-010"]


def test_decision_ids_keeps_the_order_the_answer_gave() -> None:
    """AI-RT-014: the audit record is the answer, not a sorted copy of it."""
    decisions = [{"id": "MAP-002"}, {"id": "MAP-001"}]
    output = AgentOutput.model_validate({**_answer(), "decisions": decisions})
    assert output.decision_ids == ["MAP-002", "MAP-001"]


def test_has_patch_is_false_for_a_patch_with_no_operations() -> None:
    """AI-AG-006: an explain-only answer may carry an empty patch and must still be
    recognised as touching nothing."""
    output = AgentOutput.model_validate({**_answer(), "patch": {"baseVersion": "7", "operations": []}})
    assert output.patch is not None
    assert not output.has_patch


def test_has_patch_is_false_when_the_answer_carries_no_patch() -> None:
    """AI-AG-008: the Review Assistant's answer has no patch part at all."""
    answer = _answer()
    answer.pop("patch")
    output = AgentOutput.model_validate(answer)
    assert output.patch is None and not output.has_patch


def test_confidence_accepts_the_short_alias_and_null() -> None:
    """AI-RT-010: ``confidence`` may be absent, null, or written as ``conf``."""
    aliased = _answer()
    aliased.pop("confidence")
    aliased["conf"] = 0.3
    assert AgentOutput.model_validate(aliased).confidence == 0.3
    assert AgentOutput.model_validate({**_answer(), "confidence": None}).confidence is None
    absent = _answer()
    absent.pop("confidence")
    assert AgentOutput.model_validate(absent).confidence is None


def test_a_blank_summary_is_refused_after_stripping() -> None:
    """AI-RT-014: whitespace is not a decision record."""
    with pytest.raises(ValidationError, match="at least 1 character"):
        AgentOutput.model_validate({**_answer(), "summary": "   "})


def test_surrounding_whitespace_is_stripped_from_the_summary() -> None:
    """AI-RT-014: what is persisted is what the audit will show."""
    output = AgentOutput.model_validate({**_answer(), "summary": "  consolidated  "})
    assert output.summary == "consolidated"


def test_fields_the_envelope_does_not_name_are_ignored_not_stored() -> None:
    """AI-RT-031: the DTO is lenient because the ladder's JSON Schema is what refuses an
    extra key; a smuggled field must not survive into the artifact either way."""
    output = AgentOutput.model_validate({**_answer(), "sideEffects": {"execute": True}})
    assert not hasattr(output, "sideEffects")
    assert "sideEffects" not in output.model_dump()


# --- ModelQuestion.to_canonical ----------------------------------------------------


def test_to_canonical_keeps_the_models_own_id_under_details() -> None:
    """AI-RT-016: S3 assigns the run-unique local id, and the model's own id has to stay
    readable so a later answer quoting it can be traced back."""
    canonical = ModelQuestion.model_validate(_question()).to_canonical("Q3")
    assert canonical.local_id == "Q3"
    assert canonical.details["modelId"] == "Q1"


def test_to_canonical_carries_the_existing_details_alongside_the_model_id() -> None:
    """AI-INP-009: provenance of a conflict is stored in ``details`` and must survive."""
    canonical = ModelQuestion.model_validate(_question()).to_canonical("Q3")
    assert canonical.details["source"] == "discovery"


def test_to_canonical_carries_severity_affects_and_suggestions() -> None:
    """AI-RT-010: every field the SRS names on a question reaches S1 intact."""
    canonical = ModelQuestion.model_validate(_question()).to_canonical("Q3")
    assert isinstance(canonical, Question)
    assert canonical.severity == "HIGH"
    assert canonical.affects == ["MAP-001", "map.demo.001"]
    assert canonical.text == "which of these is the real source?"
    assert len(canonical.suggestions) == 1


def test_to_canonical_writes_suggestions_in_s3s_snake_case() -> None:
    """AI-RT-010: the model spells ``changesUid``; S1's mirror reads ``changes_uid``, and a
    name it cannot read is dropped in silence.  ``regenerates`` reaches S1 as the artifact
    list S5IF-FLD08 wants — here coerced from the bool AI-RT-010 spells."""
    suggestion = ModelQuestion.model_validate(_question()).to_canonical("Q3").suggestions[0]
    assert suggestion["changes_uid"] is False
    assert suggestion["regenerates"] == ["*"]
    assert suggestion["id"] == "S1"
    assert "changesUid" not in suggestion


def test_suggestion_regenerates_is_an_artifact_list() -> None:
    """S5IF-FLD08: ``regenerates`` is a list of artifact references, not a boolean.

    A named list passes through; the bool an older prompt or a stored row still produces is
    coerced rather than refused, because S1's view does ``len(regenerates)`` to count them.
    """
    named = Suggestion.model_validate(
        {"id": "S1", "regenerates": ["dataElement:age", "optionSet:sex"]}
    )
    assert named.regenerates == ["dataElement:age", "optionSet:sex"]
    assert Suggestion.model_validate({"id": "S1", "regenerates": True}).regenerates == ["*"]
    assert Suggestion.model_validate({"id": "S1", "regenerates": False}).regenerates == []
    assert Suggestion.model_validate({"id": "S1"}).regenerates == []


def test_answer_mode_and_recommended_survive_into_the_canonical_question() -> None:
    """The review screen renders a radio or a checkbox group from these two fields."""
    canonical = ModelQuestion.model_validate(
        {
            "id": "Q1",
            "text": "which facilities?",
            "answerMode": "MULTIPLE",
            "suggestions": [{"id": "S1", "text": "Kenyatta", "recommended": True}],
        }
    ).to_canonical("Q1")
    assert canonical.answer_mode == "MULTIPLE"
    assert canonical.suggestions[0]["recommended"] is True


def test_an_unknown_answer_mode_falls_back_to_single() -> None:
    """A UI must still render an answerable control, so an invented mode is normalised."""
    canonical = ModelQuestion.model_validate(
        {"id": "Q1", "text": "which?", "answerMode": "checklist"}
    ).to_canonical("Q1")
    assert canonical.answer_mode == "SINGLE"


def test_a_later_round_gets_ids_that_cannot_collide_with_the_first() -> None:
    """S1 skips a repeated ``(run, localId)``; a second round must not start at Q1 again."""
    from factory_intelligence.runtime.questions import canonicalise

    question = ModelQuestion.model_validate({"id": "Q1", "text": "only one?"})
    first, _ = canonicalise([question], attempt=1)
    second, mapping = canonicalise([question], attempt=2)
    assert first[0].local_id == "Q1"
    assert second[0].local_id == "Q2-1"
    assert mapping["Q1"] == "Q2-1"


@pytest.mark.parametrize(
    "severity,claimed,blocking,is_blocking",
    [("HIGH", False, False, True), ("LOW", True, True, True), ("LOW", None, False, False)],
    ids=["high-claims-not", "low-claims-yes", "low-claims-nothing"],
)
def test_the_models_blocking_claim_is_kept_and_high_blocks_anyway(
    severity: str, claimed: bool | None, blocking: bool, is_blocking: bool
) -> None:
    """AI-RT-015: the claim is recorded as given; the recomputation that makes HIGH
    blocking happens in ``is_blocking``, so nothing is silently overwritten."""
    question = ModelQuestion.model_validate(_question(severity=severity, blocking=claimed))
    canonical = question.to_canonical("Q3")
    assert canonical.blocking is blocking
    assert canonical.is_blocking is is_blocking


def test_an_unknown_severity_is_coerced_to_medium_rather_than_refused() -> None:
    """AI-RT-015: a model inventing CRITICAL must still get its question in front of a
    human, at medium priority."""
    canonical = ModelQuestion.model_validate(_question(severity="CRITICAL")).to_canonical("Q3")
    assert canonical.severity == "MEDIUM"


def test_a_question_without_suggestions_or_affects_binds_to_empty_lists() -> None:
    """AI-RT-010: both are optional parts of the SRS's question shape."""
    canonical = ModelQuestion.model_validate({"id": "Q1", "text": "only a question"}).to_canonical("Q9")
    assert canonical.affects == [] and canonical.suggestions == []
    assert canonical.severity == "MEDIUM"


def test_a_one_character_question_id_is_enough() -> None:
    """AI-RT-010: the only bound the DTO puts on an id is that it is not empty — length
    beyond that is rung 1's schema, not a second opinion here."""
    assert ModelQuestion.model_validate({"id": "Q", "text": "t"}).id == "Q"
    with pytest.raises(ValidationError):
        ModelQuestion.model_validate({"id": "", "text": "t"})
