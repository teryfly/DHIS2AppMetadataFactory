"""The activation gate, the run's snapshot and the registry that feeds them (AI-REG-002,
AI-REG-003, AI-AC-07).

A registered prompt version is not a running one.  These tests are the evidence that the
difference is enforced rather than intended: every refusal path of AI-REG-003 is walked, and
each is asserted *as a refusal that wrote nothing*, because a gate that blocks while leaving a
half-made activation behind would be worse than no gate at all.  The audit event is asserted
inside the caller's transaction on purpose — D-11 and 00 doc 6.6 make the state change and its
record one fact or none.

AI-REG-002's other half is the snapshot: ``runtime/start.py`` reads the active version once,
in the run's first transaction, so an operator switching versions after that moment cannot
change what that run sends, reports or persists.  That is tested against a run in flight, not
against the function in isolation.

AI-AC-07 is the registry half of the same story: providers and models are registered through
``governance`` — the surface S1 forwards verbatim — and the candidate list an agent walks is
what a deployment ends up with after an operator switches the only qualified model off.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from factory_foundation.api import (
    AuditQuery,
    AuditRecord,
    FactoryError,
    NotFoundError,
    ValidationError,
    get_artifact,
    has_active_uow,
    query_audit,
    read_content_bytes,
    unit_of_work,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factory_intelligence import api
from factory_intelligence.dto.governance import wire
from factory_intelligence.dto.result import EvalResult
from factory_intelligence.enums import AgentType, ProviderClass
from factory_intelligence.errors import PromptActivationBlocked, PromptNotFound
from factory_intelligence.eval.scoring import EvalCase, load_eval_set
from factory_intelligence.llm.registry import candidates_for_agent, registered_model
from factory_intelligence.models.eval import EvalRunRow
from factory_intelligence.models.registry import (
    PromptActivationRow,
    RegressionRefRow,
)
from factory_intelligence.models.run import AgentRunContextRow, AgentRunRow
from factory_intelligence.prompts.activation import current_activation, list_activations
from factory_intelligence.prompts.registry import PROMPT_SOURCE_ROOT, reload_sources
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration.agent_runtime import (
    MOCK_MODEL,
    MOCK_PROVIDER,
    add_operation,
    answer,
    clear_prompt_activations,
    install_mock_model,
    open_draft,
    request_for,
    requirement_item,
    script,
    seed_project,
    stage_input,
    unique_project_id,
)

ACTIVATION_EVENT = "ai.prompt.activated"
REGISTRY_EVENT = "ai.model.registered"
#: One shipped source per agent, so a new version differs only in the parts a test names.
SHIPPED_SOURCES: dict[AgentType, str] = {
    AgentType.REQUIREMENTS: "requirements/requirements.consolidate.v1.md",
    AgentType.DOMAIN: "domain/domain.model.v1.md",
    AgentType.MAPPING: "mapping/mapping.decide.v1.md",
    AgentType.METADATA_DESIGN: "metadata_design/metadata_design.expression.v1.md",
}
PROMPT_NAMES: dict[AgentType, str] = {
    AgentType.REQUIREMENTS: "requirements.consolidate",
    AgentType.DOMAIN: "domain.model",
    AgentType.MAPPING: "mapping.decide",
    AgentType.METADATA_DESIGN: "metadata_design.expression",
}
REQUIREMENTS_HEADLINE = "You are the Requirements Agent of the DHIS2 App Studio"
#: The authored task prompt is the *body*, not the system rules; before AI-RT-001's second half
#: was sent it reached only the evaluation, never a run.  This line is unique to the shipped
#: ``requirements.consolidate.v1`` body, so its presence in a run's transcript is the proof.
REQUIREMENTS_TASK_BODY_HEADLINE = "# Consolidating the normalised requirements"
#: What the runtime substitutes for an un-activated prompt -- the generic role sentence in
#: ``render._ROLE_TEMPLATE`` -- so the contrast between the two states is visible on the wire.
PLACEHOLDER_ROLE_HEADLINE = "You are the REQUIREMENTS agent of the DHIS2 App Studio"


class _Rollback(RuntimeError):
    """Raised inside a caller's transaction to prove the gate wrote nothing durable."""


class _SwitchMidRun:
    """A progress watcher that activates another version while the run is in flight."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.steps: list[str] = []
        self.switched_at: str | None = None
        self.open_transaction_at_switch = True

    def report(
        self,
        *,
        step: str,
        completed_fraction: float | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        _ = completed_fraction, message, data
        self.steps.append(step)
        if self.switched_at is not None or step != "model_call":
            return
        api.governance("prompt.activate", self.payload)
        self.switched_at = step
        self.open_transaction_at_switch = has_active_uow()


def expected_answers(agent_type: AgentType) -> list[dict[str, Any]]:
    """What a model would send if it answered every case the way the set expects."""
    return [dict(case.expected_document) for case in load_eval_set(agent_type)]


def empty_answer(case: EvalCase) -> dict[str, Any]:
    """Valid output that maps and asks nothing: the ladder accepts it, the set does not."""
    return {
        "summary": "Nothing was mapped and nothing was asked.",
        "confidence": 0.9,
        "decisions": [],
        "questions": [],
        "patch": {"baseVersion": case.context.base_version, "operations": []},
    }


def failing_answers(agent_type: AgentType) -> list[dict[str, Any]]:
    return [empty_answer(case) for case in load_eval_set(agent_type)]


def ship_version(root: Path, agent_type: AgentType, version: int, marker: str) -> str:
    """Write and register one more version of an agent's shipped prompt, distinguishable in
    both halves: a marker sentence in the prose and in the system rules."""
    text = (PROMPT_SOURCE_ROOT / SHIPPED_SOURCES[agent_type]).read_text(encoding="utf-8")
    _, header, body = text.split("+++", 2)
    header = header.replace("version = 1", f"version = {version}", 1)
    header = header.replace('system_rules = """', f'system_rules = """\n{marker}.', 1)
    name = PROMPT_NAMES[agent_type]
    directory = root / agent_type.value.lower()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.v{version}.md").write_text(
        f"+++\n{header.strip()}\n+++\n{body.rstrip()}\n{marker}\n", encoding="utf-8"
    )
    reload_sources(root=root)
    return f"{name}.v{version}"


def evaluate(
    agent_type: AgentType,
    qualified: str,
    *,
    key: str,
    answers: list[dict[str, Any]],
    model: str = MOCK_MODEL,
) -> EvalResult:
    """One measurement of one version, on a mock that answers every case as scripted."""
    provider = install_mock_model(agent_type)
    script(provider, agent_type, *answers)
    return api.run_evaluation(agent_type.value, qualified, model, idempotency_key=key)


def governed(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One governance call, taken as the dict the dispatcher answers with."""
    value = api.governance(action, payload)
    assert isinstance(value, dict), value
    return value


def regress(agent_type: AgentType, version: int, *, passed: bool = True) -> dict[str, Any]:
    return governed(
        "regression.record",
        {
            "promptName": PROMPT_NAMES[agent_type],
            "promptVersion": version,
            "recordedBy": "test-group",
            "passed": passed,
            "model": MOCK_MODEL,
        },
    )


def activation_payload(
    agent_type: AgentType, version: int, operator: str, **changes: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agentType": agent_type.value,
        "promptName": PROMPT_NAMES[agent_type],
        "promptVersion": version,
        "modelList": [MOCK_MODEL],
        "operator": operator,
    }
    payload.update(changes)
    return payload


def activate(
    agent_type: AgentType, version: int, operator: str, **changes: Any
) -> dict[str, Any]:
    """``operator`` is per test on purpose: ``core`` keeps audit rows between tests, so a
    shared name would make another test's activation count towards this one's assertion."""
    return governed(
        "prompt.activate", activation_payload(agent_type, version, operator, **changes)
    )


def activation_rows(session: Session) -> list[PromptActivationRow]:
    return list(session.execute(select(PromptActivationRow)).scalars().all())


def count_rows(model: Any) -> int:
    with unit_of_work() as session:
        return int(session.execute(select(func.count()).select_from(model)).scalar_one())


def activation_audit(operator: str) -> list[AuditRecord]:
    return list(
        query_audit(AuditQuery(event_type=ACTIVATION_EVENT, operator_name=operator, limit=50)).items
    )


def run_prompt_versions(run_id: str) -> dict[str, Any]:
    with unit_of_work() as session:
        row = session.get(AgentRunRow, run_id)
        assert row is not None
        return dict(row.prompt_versions)


def context_messages(run_id: str) -> str:
    """The transcript the run persisted — what the model was actually sent (AI-RT-014)."""
    with unit_of_work() as session:
        row = session.get(AgentRunContextRow, run_id)
        assert row is not None and row.context_artifact_id
        artifact_id = str(row.context_artifact_id)
    body, _classification, _media = read_content_bytes(artifact_id)
    document = json.loads(body.decode("utf-8"))
    return "\n".join(str(item.get("content") or "") for item in document["messageSequence"])


@pytest.fixture(autouse=True)
def _start_from_no_activation(harness: IntelligenceHarness) -> None:
    """Undo the harness's baseline activation seeding for this whole module.

    Every test here reasons about activation *from an empty table* — a blocked gate that wrote
    nothing, a snapshot a run takes, a switch that replaces one row — and the run tests activate
    the version they need before they run it.  The generic harness seeds an activation so a
    normal run is not refused (AI-REG-002); these tests establish their own state instead, so
    they start clean.
    """
    _ = harness
    clear_prompt_activations()


# --- AI-REG-003: every refusal path -------------------------------------------------


def test_a_version_with_no_evaluation_cannot_be_activated(harness: IntelligenceHarness) -> None:
    """AI-REG-003, AI-AC-07: switching to a version nobody measured is refused, and the answer
    names the run that has to happen first."""
    _ = harness
    install_mock_model(AgentType.MAPPING)

    with pytest.raises(PromptActivationBlocked) as refused:
        activate(AgentType.MAPPING, 1, "gate-noeval-ops")

    message = str(refused.value)
    assert refused.value.code == "AI_PROMPT_ACTIVATION_BLOCKED"
    assert "mapping.decide.v1" in message
    assert "run_evaluation" in message and "AI-REG-003" in message
    assert refused.value.details["agentType"] == AgentType.MAPPING.value
    assert count_rows(PromptActivationRow) == 0
    assert activation_audit("gate-noeval-ops") == []
    assert current_activation(AgentType.MAPPING) is None


def test_a_failing_evaluation_does_not_open_the_gate_even_with_a_regression(
    harness: IntelligenceHarness,
) -> None:
    """The regression half is on file here, so the only thing missing is the measurement —
    which makes this the evaluation gate rather than an absence of both (AI-REG-003)."""
    _ = harness
    run = evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=failing_answers(AgentType.MAPPING),
        key="reg003:fail",
    )
    assert run.passed is False
    assert run.score < 0.5
    regress(AgentType.MAPPING, 1)

    with pytest.raises(PromptActivationBlocked) as refused:
        activate(AgentType.MAPPING, 1, "gate-fail-ops")
    assert "no passing evaluation" in str(refused.value)
    assert count_rows(PromptActivationRow) == 0
    assert activation_audit("gate-fail-ops") == []
    assert count_rows(EvalRunRow) == 1, "the failure stays on the record (AI-EVAL-001)"


def test_an_evaluation_recorded_below_the_current_bar_is_refused_by_its_score(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-003 compares the *live* pass mark with the recorded score: a measurement taken
    while a deployment held a laxer bar is not evidence for a switch after the bar moved."""
    _ = harness
    api.configure(eval_pass_score=0.2)
    lenient = evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=failing_answers(AgentType.MAPPING),
        key="reg003:lenient",
    )
    assert lenient.passed is True, "the premise: the lax bar recorded it as passed"
    assert lenient.score < 0.95
    api.reset_settings()
    regress(AgentType.MAPPING, 1)

    with pytest.raises(PromptActivationBlocked) as refused:
        activate(AgentType.MAPPING, 1, "gate-bar-ops")
    assert "below the 0.95 pass mark" in str(refused.value), str(refused.value)
    assert refused.value.details["evalRunId"] == lenient.eval_run_id
    # AI-GOV-003: which gate refused, and against what number, in machine-readable form
    assert refused.value.details["missing"] == "EVAL_SCORE"
    assert refused.value.details["actualScore"] == lenient.score
    assert refused.value.details["requiredScore"] == 0.95
    assert count_rows(PromptActivationRow) == 0
    assert activation_audit("gate-bar-ops") == []


def test_an_evaluation_of_another_model_does_not_activate_this_list(
    harness: IntelligenceHarness,
) -> None:
    """A prompt that passed evaluation against one model says nothing about another, so the
    model list is part of what is being activated (AI-REG-003, AI-POL-007)."""
    _ = harness
    evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:wrong-model",
    )
    api.governance(
        "model.register",
        {
            "provider": MOCK_PROVIDER,
            "model": "other-mock",
            "capabilities": {"structuredOutput": True, "maxContextTokens": 1_000_000},
            "applicableAgents": {AgentType.MAPPING.value: 2},
        },
    )
    regress(AgentType.MAPPING, 1)

    with pytest.raises(PromptActivationBlocked) as refused:
        activate(AgentType.MAPPING, 1, "gate-model-ops", modelList=["other-mock"])
    assert "other-mock" in str(refused.value), "the refusal names the list it looked at"
    assert count_rows(PromptActivationRow) == 0

    assert activate(AgentType.MAPPING, 1, "gate-model-ops")["promptVersionId"]
    active = current_activation(AgentType.MAPPING)
    assert active is not None and active.model_list == [MOCK_MODEL]


def test_a_passing_evaluation_without_a_registered_regression_is_refused(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-003's second half is the Golden Project claim the test group registers; S3
    cannot produce it, so it refuses the absence (AI-AC-07)."""
    _ = harness
    run = evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:no-regression",
    )
    assert run.passed is True

    with pytest.raises(PromptActivationBlocked) as refused:
        activate(AgentType.MAPPING, 1, "gate-noreg-ops")
    message = str(refused.value)
    assert "regression" in message and "AI-REG-003" in message
    assert refused.value.details["promptVersionId"]
    assert count_rows(PromptActivationRow) == 0
    assert count_rows(RegressionRefRow) == 0
    assert activation_audit("gate-noreg-ops") == []


def test_a_regression_recorded_as_failed_does_not_open_the_gate(
    harness: IntelligenceHarness,
) -> None:
    """The claim is evidence only when it says the regression passed; a registered failure
    must not read as the missing half (AI-REG-003)."""
    _ = harness
    assert evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:reg-failed",
    ).passed
    regress(AgentType.MAPPING, 1, passed=False)

    with pytest.raises(PromptActivationBlocked) as refused:
        activate(AgentType.MAPPING, 1, "gate-regfail-ops")
    assert "regression" in str(refused.value)
    assert count_rows(PromptActivationRow) == 0


def test_activation_without_a_model_list_is_refused_before_the_gate(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-007 walks the list, so an empty one would activate an agent every run refuses.
    That is a malformed request, not an unmet gate, and it is answered without reading the
    evaluation tables (AI-REG-003)."""
    _ = harness
    evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:no-models",
    )
    regress(AgentType.MAPPING, 1)

    with pytest.raises(ValidationError) as refused:
        activate(AgentType.MAPPING, 1, "gate-nomodels-ops", modelList=[])
    assert "model list" in str(refused.value)
    assert count_rows(PromptActivationRow) == 0
    assert activation_audit("gate-nomodels-ops") == []


@pytest.mark.parametrize(
    ("agent_type", "name", "version", "expected"),
    [
        (AgentType.MAPPING, "mapping.decide", 99, PromptNotFound),
        (AgentType.MAPPING, "requirements.consolidate", 1, ValidationError),
    ],
    ids=["unregistered-version", "prompt-of-another-agent"],
)
def test_a_request_the_gate_cannot_answer_is_refused_by_name(
    harness: IntelligenceHarness,
    agent_type: AgentType,
    name: str,
    version: int,
    expected: type[FactoryError],
) -> None:
    """A typo in a version, and a prompt declared for another agent: both are answered before
    the gate, because no evaluation could justify either (AI-REG-003)."""
    _ = harness
    install_mock_model(AgentType.MAPPING, AgentType.REQUIREMENTS)

    with pytest.raises(expected) as refused:
        activate(agent_type, version, "gate-typo-ops", promptName=name)
    if expected is ValidationError:
        assert refused.value.details["declaredFor"] == AgentType.REQUIREMENTS.value
        assert refused.value.details["agentType"] == AgentType.MAPPING.value
    else:
        assert refused.value.details == {"name": name, "version": version}
    assert count_rows(PromptActivationRow) == 0


# --- AI-REG-003: the path that opens ------------------------------------------------


def test_two_gates_and_a_written_activation_are_one_fact(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-003: an evaluation pass plus a registered regression activates the version, and
    ``current_activation`` — what a run reads — reports that version with the evidence ids the
    gate accepted."""
    _ = harness
    run = evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:pass",
    )
    regression = regress(AgentType.MAPPING, 1)

    result = activate(AgentType.MAPPING, 1, "sol-arch-9")

    assert result["agentType"] == AgentType.MAPPING.value
    assert result["evalRunId"] == run.eval_run_id
    assert result["regressionPassedRef"] == regression["id"]
    assert result["activatedBy"] == "sol-arch-9"
    assert result["modelList"] == [MOCK_MODEL]

    active = current_activation(AgentType.MAPPING)
    assert active is not None
    assert active.qualified_name == "mapping.decide.v1"
    assert active.prompt_version_id == result["promptVersionId"]
    assert active.model_list == [MOCK_MODEL]
    assert active.activated_by == "sol-arch-9"
    assert active.eval_run_id == run.eval_run_id
    assert active.regression_passed_ref == regression["id"]
    assert active.body.startswith("# Deciding the mapping"), "the prose half is what runs"
    assert active.system_rules.startswith("You are the Mapping Agent")
    assert {str(item["id"]) for item in active.prohibitions} == {
        "no_dhis2_payload",
        "uid_ban",
        "decision_coverage",
        "low_confidence_question",
        "reuse_decision_per_similar_object",
    }
    assert [each.agent_type for each in list_activations()] == [AgentType.MAPPING.value]
    assert [wire(row) for row in api.list_activations()] == [
        result
    ], "S1's activation list answers with the same row ``prompt.activate`` returned"
    # S5IF-GOV10: the row names the prompt, not only its id, so the UI needs no second call
    assert result["name"] == "mapping.decide"
    assert result["version"] == 1

    events = activation_audit("sol-arch-9")
    assert len(events) == 1
    event = events[0]
    assert event.event_type == ACTIVATION_EVENT
    assert event.operator_type == "USER"
    assert event.source_subsystem == "S3"
    assert event.object_type == "promptActivation"
    assert event.object_id == AgentType.MAPPING.value
    assert event.before is None, "nothing was active before this switch"
    assert event.after is not None
    assert event.after["prompt"] == "mapping.decide.v1"
    assert event.after["evalRunId"] == run.eval_run_id
    assert event.after["evalScore"] == run.score
    assert event.after["regressionRef"] == regression["id"]

    listed = api.governance("prompt.list", {"agentType": AgentType.MAPPING.value})["items"]
    assert [each["qualifiedName"] for each in listed] == ["mapping.decide.v1"]
    assert listed[0]["active"] is True


def _run_and_transcript(project_tag: str, idem: str) -> str:
    """One REQUIREMENTS agent run, scripted to a valid answer, returned as its transcript.

    The transcript is the persisted message sequence (AI-RT-014), which is what the model was
    actually sent — so asserting against it proves the prompt reached the wire rather than only
    the database.
    """
    project = unique_project_id(project_tag)
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    input_id = stage_input(project, {"notes": "a child is weighed at every visit"})
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(operations=[add_operation("/requirements/items/-", requirement_item(input_id))]),
    )
    result = api.start_agent_run(
        request_for(
            project,
            AgentType.REQUIREMENTS,
            draft_id=draft_id,
            input_artifact_ids=[input_id],
            idempotency_key=idem,
        )
    )
    assert result.status == "COMPLETED", result.model_dump()
    return context_messages(str(result.run_id))


def test_an_unactivated_prompt_is_refused_rather_than_degraded(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-002, asserted on the wire: a run whose agent has no activated prompt is refused —
    a ``FAILED`` result, no run row, an error the caller can read — instead of degrading to an
    empty placeholder that would still spend the model call and report success while prompt
    governance never took effect.  Finishing the deployment (activating the version the gate
    lets through) turns the same run green, so the refusal is the missing step and nothing else.
    """
    _ = harness
    clear_prompt_activations()
    install_mock_model(AgentType.REQUIREMENTS)
    assert current_activation(AgentType.REQUIREMENTS) is None

    project = unique_project_id("prj-unactivated")
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    input_id = stage_input(project, {"notes": "a child is weighed at every visit"})
    refused = api.start_agent_run(
        request_for(
            project,
            AgentType.REQUIREMENTS,
            draft_id=draft_id,
            input_artifact_ids=[input_id],
            idempotency_key="wire:unactivated",
        )
    )
    assert refused.status == "FAILED", refused.model_dump()
    assert refused.run_id is None, "a refused run writes no row to appear in the usage view"
    assert not refused.patch_ids and refused.agent_output_artifact_id is None
    # the caller gets both the requirement that failed and the machine-readable error code
    assert any(issue.rule_id == "AI-REG-002" for issue in refused.issues), refused.issues
    assert any(
        issue.details.get("errorCode") == "AI_PROMPT_NOT_ACTIVATED" for issue in refused.issues
    ), refused.issues

    # activating the version through the gate opens exactly this run
    evaluate(
        AgentType.REQUIREMENTS,
        "requirements.consolidate.v1",
        answers=expected_answers(AgentType.REQUIREMENTS),
        key="wire:eval-after-refusal",
    )
    regress(AgentType.REQUIREMENTS, 1)
    activate(AgentType.REQUIREMENTS, 1, "wire-unactivated-ops")
    transcript = _run_and_transcript("prj-unactivated", "wire:unactivated-retry")
    assert REQUIREMENTS_TASK_BODY_HEADLINE in transcript, "the authored prompt now reaches the wire"


def test_an_activated_prompt_sends_its_authored_task_body_to_the_model(
    harness: IntelligenceHarness,
) -> None:
    """AI-RT-001/003 + AI-REG-002's whole point: after the gate opens, a run sends the prompt the
    version was authored with -- both the system rules and the task body -- so prompt governance
    is actually in force on the wire, not just in the registry."""
    _ = harness
    evaluate(
        AgentType.REQUIREMENTS,
        "requirements.consolidate.v1",
        answers=expected_answers(AgentType.REQUIREMENTS),
        key="wire:eval",
    )
    regress(AgentType.REQUIREMENTS, 1)
    activate(AgentType.REQUIREMENTS, 1, "wire-body-ops")

    active = current_activation(AgentType.REQUIREMENTS)
    assert active is not None and active.body.strip(), "the snapshot carries the authored body"
    assert active.body.startswith(REQUIREMENTS_TASK_BODY_HEADLINE)

    transcript = _run_and_transcript("prj-activated", "wire:activated")
    assert active.body[:60] in transcript, "the exact authored task prompt reached the model"
    assert REQUIREMENTS_TASK_BODY_HEADLINE in transcript
    assert REQUIREMENTS_HEADLINE in transcript, "and so did the authored system rules"


def test_the_activation_row_and_its_audit_share_the_callers_transaction(
    harness: IntelligenceHarness,
) -> None:
    """D-11 / 00 doc 6.6: an activation whose audit row rolled back — or an audit row for an
    activation that never landed — is a false record either way, so both are written together
    and both disappear together."""
    _ = harness
    evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:transaction",
    )
    regress(AgentType.MAPPING, 1)

    with pytest.raises(_Rollback), unit_of_work() as session:
        api.governance("prompt.activate", activation_payload(AgentType.MAPPING, 1, "gate-tx-ops"))
        assert len(activation_rows(session)) == 1
        assert len(activation_audit("gate-tx-ops")) == 1, "the audit row is written in, not after"
        raise _Rollback("the caller's transaction failed for an unrelated reason")

    with unit_of_work() as session:
        assert activation_rows(session) == []
    assert activation_audit("gate-tx-ops") == []
    assert current_activation(AgentType.MAPPING) is None


def test_a_switch_replaces_the_one_row_per_agent_and_keeps_both_sides(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """One active version per agent type (AI-REG-002), and the second audit event reports what
    it replaced — the only form in which a reviewer can undo the switch."""
    _ = harness
    evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:switch:1",
    )
    regress(AgentType.MAPPING, 1)
    onto_v1 = activate(AgentType.MAPPING, 1, "gate-switch-ops")

    ship_version(tmp_path, AgentType.MAPPING, 2, "SECOND DRAFT OF THE MAPPING PROMPT")
    evaluate(
        AgentType.MAPPING,
        "mapping.decide.v2",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:switch:2",
    )
    regress(AgentType.MAPPING, 2)
    onto_v2 = activate(AgentType.MAPPING, 2, "gate-switch-ops")

    with unit_of_work() as session:
        rows = activation_rows(session)
        assert len(rows) == 1, "one active version per agent type (AI-REG-002)"
        assert rows[0].prompt_version_id == onto_v2["promptVersionId"]
        assert rows[0].prompt_version_id != onto_v1["promptVersionId"]
    active = current_activation(AgentType.MAPPING)
    assert active is not None and active.version == 2

    events = activation_audit("gate-switch-ops")
    assert len(events) == 2, "both switches are on the record, not only the last one"
    onto = {
        str(dict(each.after or {}).get("prompt")): each for each in events
    }
    assert onto["mapping.decide.v1"].before is None
    assert onto["mapping.decide.v2"].before is not None
    assert onto["mapping.decide.v2"].before["promptVersionId"] == onto_v1["promptVersionId"]
    assert onto["mapping.decide.v2"].before["modelList"] == [MOCK_MODEL]


def test_a_newly_registered_version_does_not_take_effect(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """AI-REG-002 is the distinction between a version that exists and the version in use:
    loading a new file into the registry reaches a run only through the gate."""
    _ = harness
    evaluate(
        AgentType.MAPPING,
        "mapping.decide.v1",
        answers=expected_answers(AgentType.MAPPING),
        key="reg003:stale",
    )
    regress(AgentType.MAPPING, 1)
    activation_first = activate(AgentType.MAPPING, 1, "gate-stale-ops")

    assert ship_version(tmp_path, AgentType.MAPPING, 2, "SHIPPED BUT NOT ACTIVATED") == (
        "mapping.decide.v2"
    )

    active = current_activation(AgentType.MAPPING)
    assert active is not None and active.qualified_name == "mapping.decide.v1"
    assert active.prompt_version_id == activation_first["promptVersionId"]
    listed = api.governance("prompt.list", {"agentType": AgentType.MAPPING.value})["items"]
    assert [each["qualifiedName"] for each in listed] == ["mapping.decide.v2", "mapping.decide.v1"]
    assert [each["active"] for each in listed] == [False, True], (
        "the registry can say which version is in use (AI-REG-002)"
    )


# --- AI-REG-002: the snapshot a run takes -------------------------------------------


def test_an_operator_switching_mid_run_cannot_change_what_the_run_uses(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """AI-REG-002 with AI-RT-001: the prompt set is frozen when the run starts.  The switch
    below lands after the run's first transaction and before its model call, and this run
    still reports, persists and sends what it snapshotted, while the run after it gets the new
    version."""
    _ = harness
    name = PROMPT_NAMES[AgentType.REQUIREMENTS]
    answers = expected_answers(AgentType.REQUIREMENTS)
    for version in (1, 2):
        qualified = f"{name}.v{version}"
        if version == 2:
            qualified = ship_version(tmp_path, AgentType.REQUIREMENTS, 2, "MIDRUN SWITCH")
        evaluate(
            AgentType.REQUIREMENTS,
            qualified,
            answers=answers,
            key=f"reg002:eval:{version}",
        )
        regress(AgentType.REQUIREMENTS, version)
    activate(AgentType.REQUIREMENTS, 1, "gate-midrun-ops")

    project = unique_project_id("prj-midrun")
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    input_id = stage_input(project, {"notes": "a child is weighed at every visit"})
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(operations=[add_operation("/requirements/items/-", requirement_item(input_id))]),
    )
    watcher = _SwitchMidRun(activation_payload(AgentType.REQUIREMENTS, 2, "gate-midrun-ops"))

    result = api.start_agent_run(
        request_for(
            project,
            AgentType.REQUIREMENTS,
            draft_id=draft_id,
            input_artifact_ids=[input_id],
            idempotency_key="reg002:run",
        ),
        progress=watcher,
    )

    assert result.status == "COMPLETED", result.model_dump()
    assert watcher.switched_at == "model_call", "the switch had to land while the run was live"
    assert not watcher.open_transaction_at_switch, (
        "the operator's write must not have enlisted in the run's own transaction"
    )
    active = current_activation(AgentType.REQUIREMENTS)
    assert active is not None and active.version == 2, (
        "the gate did open mid-run; the run must still be on the version it snapshotted"
    )

    run_id = str(result.run_id)
    assert run_prompt_versions(run_id) == {name: 1}, (
        "AI-REG-002: the row persists the snapshot, not the live switch"
    )
    record = get_artifact(str(result.agent_output_artifact_id)).build_record
    assert record is not None
    assert record["promptVersions"] == {name: 1}

    transcript = context_messages(run_id)
    assert REQUIREMENTS_HEADLINE in transcript, "v1's own system rules reached the model"
    assert "MIDRUN SWITCH" not in transcript, (
        "AI-REG-002: a version activated after the snapshot must not reach this run"
    )

    second_project = unique_project_id("prj-after")
    seed_project(second_project)
    second_draft = open_draft(second_project, "REQUIREMENTS")
    following = api.start_agent_run(
        request_for(
            second_project,
            AgentType.REQUIREMENTS,
            draft_id=second_draft,
            input_artifact_ids=[input_id],
            idempotency_key="reg002:run-after",
        )
    )
    assert following.status == "COMPLETED", following.model_dump()
    assert run_prompt_versions(str(following.run_id)) == {name: 2}, (
        "and the run that starts after the switch picks it up (AI-REG-002)"
    )
    assert "MIDRUN SWITCH" in context_messages(str(following.run_id))


# --- AI-AC-07: the registry half -----------------------------------------------------


def test_a_provider_and_a_model_registered_through_governance_are_listed(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-004 on the operator surface S1 forwards: the class is declared, the credential
    is only ever a reference, and the per-agent preference order is stored with the model."""
    _ = harness
    provider = api.governance(
        "provider.register",
        {
            "name": "ward-llm",
            "providerClass": ProviderClass.PRIVATE_NETWORK.value,
            "endpoint": "https://ward.internal/v1",
            "apiKeySecretRef": "env://WARD_MODEL_KEY",
            "adapter": "openai_compat",
            "operator": "registry-ops",
        },
    )
    assert provider["providerClass"] == ProviderClass.PRIVATE_NETWORK.value
    assert provider["apiKeySecretRef"] == "env://WARD_MODEL_KEY"
    assert provider["enabled"] is True

    model = api.governance(
        "model.register",
        {
            "provider": "ward-llm",
            "model": "ward-7b",
            "capabilities": {"structuredOutput": True, "vision": False, "maxContextTokens": 64_000},
            "applicableAgents": {AgentType.MAPPING.value: 1, AgentType.REQUIREMENTS.value: 2},
            "unitPriceInputPer1k": 0.4,
            "unitPriceOutputPer1k": 0.8,
            "operator": "registry-ops",
        },
    )
    assert model["ref"] == "ward-llm/ward-7b"
    assert model["providerClass"] == ProviderClass.PRIVATE_NETWORK.value
    assert model["applicableAgents"] == {
        AgentType.MAPPING.value: 1,
        AgentType.REQUIREMENTS.value: 2,
    }

    listed = api.governance("provider.list", {})["items"]
    assert [each["name"] for each in listed] == ["ward-llm"]
    assert set(listed[0]) == {
        "id",
        "name",
        "providerClass",
        "endpoint",
        "enabled",
        "adapter",
        "apiKeySecretRef",
    }, "the reference is governance-visible, the value never is (AI-LLM-005)"

    assert [
        each["ref"] for each in api.governance("model.list", {"agentType": "MAPPING"})["items"]
    ] == ["ward-llm/ward-7b"]
    assert api.governance("model.list", {"agentType": AgentType.DOMAIN.value})["items"] == []

    candidates = candidates_for_agent(AgentType.MAPPING)
    assert [(each.model, each.priority, each.enabled) for each in candidates] == [
        ("ward-7b", 1, True)
    ]
    events = query_audit(
        AuditQuery(event_type=REGISTRY_EVENT, operator_name="registry-ops", limit=50)
    ).items
    assert len(events) == 2
    assert {str(each.object_type) for each in events} == {"provider", "model"}


def test_disabling_the_only_qualified_model_leaves_it_visible_and_unusable(
    harness: IntelligenceHarness,
) -> None:
    """AI-AC-07's registry half: a disabled model is not deleted, so the candidate list still
    names it — flagged, and refused by the selection that reads the flag.  A disabled
    *provider* is the operator's own decision, so its models leave the list entirely."""
    _ = harness
    api.governance(
        "provider.register",
        {"name": "ward-llm", "providerClass": ProviderClass.PRIVATE_NETWORK.value},
    )
    api.governance(
        "model.register",
        {
            "provider": "ward-llm",
            "model": "ward-7b",
            "applicableAgents": {AgentType.MAPPING.value: 1},
        },
    )

    disabled = api.governance(
        "model.enable", {"ref": "ward-llm/ward-7b", "enabled": False, "operator": "registry-ops"}
    )
    assert disabled == {"ref": "ward-llm/ward-7b", "enabled": False}

    candidates = candidates_for_agent(AgentType.MAPPING)
    assert [each.model for each in candidates] == ["ward-7b"]
    assert candidates[0].enabled is False, "the row survives the switch-off (AI-REG-004)"
    assert candidates[0].fits(0, needs_vision=False, needs_structured=True) == (
        "provider or model is disabled"
    )
    assert api.governance("model.list", {"includeDisabled": False})["items"] == []
    assert [each["enabled"] for each in api.governance("model.list", {})["items"]] == [False]
    assert registered_model("ward-llm/ward-7b").enabled is False

    api.governance("provider.enable", {"name": "ward-llm", "enabled": False})
    assert candidates_for_agent(AgentType.MAPPING) == [], (
        "a provider the operator switched off is not offered as merely unavailable (AI-POL-007)"
    )

    api.governance("provider.enable", {"name": "ward-llm", "enabled": True})
    revived = candidates_for_agent(AgentType.MAPPING)
    assert [each.model for each in revived] == ["ward-7b"]
    assert revived[0].enabled is False, "the model is still the one that was switched off"

    api.governance("model.enable", {"ref": "ward-llm/ward-7b", "enabled": True})
    assert candidates_for_agent(AgentType.MAPPING)[0].enabled is True
    assert candidates_for_agent(AgentType.DOMAIN) == [], (
        "an agent nobody declared a preference for has no candidate to walk"
    )


@pytest.mark.parametrize(
    ("action", "payload", "expected"),
    [
        (
            "provider.register",
            {"name": "sneaky", "providerClass": "UNRESTRICTED", "apiKeySecretRef": "sk-abcdef"},
            ValidationError,
        ),
        ("provider.register", {"name": "sneaky", "providerClass": "NOT_A_CLASS"}, ValidationError),
        ("model.register", {"provider": "never-registered", "model": "orphan"}, NotFoundError),
    ],
    ids=["literal-api-key", "unknown-provider-class", "model-without-a-provider"],
)
def test_a_registration_that_cannot_be_judged_is_refused(
    harness: IntelligenceHarness,
    action: str,
    payload: dict[str, Any],
    expected: type[FactoryError],
) -> None:
    """A provider with no class cannot be policy-evaluated, a literal key in a row other
    subsystems can read is a secret leak, and a model needs the provider it belongs to
    (AI-LLM-002, AI-LLM-005, AI-REG-004)."""
    _ = harness
    with pytest.raises(expected):
        api.governance(action, payload)
    assert api.governance("provider.list", {})["items"] == []
    assert api.governance("model.list", {})["items"] == []
    assert count_rows(RegressionRefRow) == 0
