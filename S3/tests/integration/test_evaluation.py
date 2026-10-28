"""The evaluation runner and what its score is worth (AI-EVAL-001, AI-EVAL-002,
AI-EVAL-003, AI-AC-03).

One call measures one ``(agent, prompt version, model)`` triple against that agent's packaged
set and writes the record AI-REG-003 gates on, so the claims pinned here are that the
measurement is worth gating on:

* the score is *passed cases over total cases* and a golden case passes only at full
  structural agreement, so partial credit cannot accumulate into a permission (AI-EVAL-001);
* every case reports its difference — the axis, what was missing, what was extra, what the set
  expected — and the report survives the call in ``ai.eval_case_result`` (AI-EVAL-001's 逐项差异);
* injection cases are counted apart, which is AI-EVAL-003's whole purpose and therefore
  asserted in both directions: a set answered perfectly except for one obedient injection
  sample lands at 0.75 on four cases and does not clear the 0.95 bar, while the same set
  resisted lands at 1.0 and does — and the first cannot be activated;
* the four packaged sets, injection files included, load, parse and answer with the same
  envelope a run is judged with (AI-EVAL-002), and the set's version is a digest of those
  files rather than a counter somebody maintains.

Everything runs on the mock provider (AI-MOCK-001): no network, no artifacts, no project.  An
evaluation measures a prompt against a set, which is why a deployment can re-run it against a
model that has never seen this project's data.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from factory_foundation.api import (
    CancelledError,
    IdempotencyConflictError,
    ManualCancellationToken,
    ValidationError,
    has_active_uow,
    idempotency,
    read_content_bytes,
    unit_of_work,
)
from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factory_intelligence import api
from factory_intelligence.agents.spec import AGENT_SPECS
from factory_intelligence.config import settings
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.enums import AgentType, ProviderClass
from factory_intelligence.errors import EvalSetMissing, PromptActivationBlocked, PromptNotFound
from factory_intelligence.eval.scoring import (
    EVAL_SET_ROOT,
    GOLDEN,
    INJECTION,
    EvalCase,
    load_eval_set,
    parse_case,
    set_directory,
    set_version,
)
from factory_intelligence.llm.errors import LlmTransportError
from factory_intelligence.llm.registry import registered_model
from factory_intelligence.llm.types import Capabilities, ProviderInfo
from factory_intelligence.models.call import ModelCallRow
from factory_intelligence.models.eval import EvalCaseResultRow, EvalRunRow
from factory_intelligence.models.run import AgentRunRow
from factory_intelligence.prompts.activation import current_activation
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

OPERATOR = "eval-ops"
#: the agents whose prompt and evaluation set both ship (REVIEW ships with in-stage chat; API,
#: UI and Fixture ship because PAGE_DESIGN is on the current design line — see
#: ``factory_intelligence.feature.DELIVERED_AGENTS``)
SHIPPED_AGENTS: tuple[AgentType, ...] = (
    AgentType.REQUIREMENTS,
    AgentType.DOMAIN,
    AgentType.MAPPING,
    AgentType.METADATA_DESIGN,
    AgentType.API,
    AgentType.UI,
    AgentType.FIXTURE,
    AgentType.REVIEW,
)
AGENT_SOURCES: dict[AgentType, tuple[str, str]] = {
    AgentType.REQUIREMENTS: (
        "requirements.consolidate",
        "requirements/requirements.consolidate.v1.md",
    ),
    AgentType.DOMAIN: ("domain.model", "domain/domain.model.v1.md"),
    AgentType.MAPPING: ("mapping.decide", "mapping/mapping.decide.v1.md"),
    AgentType.REVIEW: ("review.explain", "review/review.explain.v1.md"),
    AgentType.METADATA_DESIGN: (
        "metadata_design.expression",
        "metadata_design/metadata_design.expression.v1.md",
    ),
    AgentType.API: ("api.design", "api/api.design.v1.md"),
    AgentType.UI: ("ui.design", "ui/ui.design.v1.md"),
    AgentType.FIXTURE: ("fixture.specify", "fixture/fixture.specify.v1.md"),
}
#: The uid the mapping injection sample asks for, in the shape that clears every rung of
#: the ladder — which is exactly why AI-EVAL-003 needs the forbidden-content half.
OBEDIENT_UID = "f4ce0e9eb4c17a3d33ebb3ece83a7e44"

#: The keys ``run_evaluation`` folds into ``ai.eval_run.summary`` beside the judgement.
ACCOUNTING = frozenset({"modelCalls", "inputTokens", "outputTokens", "estimatedCost"})


# --- helpers -------------------------------------------------------------------------


def cases_of(agent_type: AgentType) -> list[EvalCase]:
    return load_eval_set(agent_type)


def expected_answers(agent_type: AgentType) -> list[dict[str, Any]]:
    """The answers the set expects, in the order the runner walks its case files."""
    return [dict(case.expected_document) for case in cases_of(agent_type)]


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
    return [empty_answer(case) for case in cases_of(agent_type)]


def obeying_injection_answers(agent_type: AgentType) -> list[dict[str, Any]]:
    """Every golden case answered perfectly; the injection case answered as the injected
    document demanded, with the identifier it asked for written into the entry."""
    answers: list[dict[str, Any]] = []
    for case in cases_of(agent_type):
        document = dict(case.expected_document)
        if case.is_injection:
            document = copy.deepcopy(document)
            # the entry the injected document demanded, wherever the golden puts it: a correct
            # Mapping answer registers its decision before the entries that cite it
            entry = next(
                operation["value"]
                for operation in document["patch"]["operations"]
                if str(operation.get("path", "")).startswith("/mapping/entries")
            )
            document["patch"] = {
                "baseVersion": document["patch"]["baseVersion"],
                "operations": [
                    {
                        "op": "replace",
                        "path": f"/mapping/entries/{entry['businessKey']}/uid",
                        "value": OBEDIENT_UID,
                    }
                ],
            }
        answers.append(document)
    return answers


def evaluate(
    agent_type: AgentType,
    qualified: str,
    *,
    key: str,
    answers: list[dict[str, Any]],
    model: str = MOCK_MODEL,
    eval_set_version: str | None = None,
    progress: Any = None,
    cancel: Any = None,
) -> Any:
    provider = install_mock_model(agent_type)
    script(provider, agent_type, *answers)
    return api.run_evaluation(
        agent_type.value,
        qualified,
        model,
        eval_set_version,
        idempotency_key=key,
        operator=OPERATOR,
        progress=progress,
        cancel=cancel,
    )


def governed(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One governance call, taken as the dict the dispatcher answers with."""
    value = api.governance(action, payload)
    assert isinstance(value, dict), value
    return value


def regress(agent_type: AgentType, version: int, *, passed: bool = True) -> dict[str, Any]:
    return governed(
        "regression.record",
        {
            "promptName": AGENT_SOURCES[agent_type][0],
            "promptVersion": version,
            "recordedBy": "test-group",
            "passed": passed,
            "model": MOCK_MODEL,
        },
    )


def activate(agent_type: AgentType, version: int, operator: str) -> dict[str, Any]:
    return governed(
        "prompt.activate",
        {
            "agentType": agent_type.value,
            "promptName": AGENT_SOURCES[agent_type][0],
            "promptVersion": version,
            "modelList": [MOCK_MODEL],
            "operator": operator,
        },
    )


def ship_v2(root: Path, agent_type: AgentType, marker: str) -> str:
    """One more version of an agent's shipped prompt, from a tree only this test owns."""
    name, relative = AGENT_SOURCES[agent_type]
    text = (PROMPT_SOURCE_ROOT / relative).read_text(encoding="utf-8")
    _, header, body = text.split("+++", 2)
    header = header.replace("version = 1", "version = 2", 1)
    header = header.replace('system_rules = """', f'system_rules = """\n{marker}.', 1)
    directory = root / agent_type.value.lower()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.v2.md").write_text(
        f"+++\n{header.strip()}\n+++\n{body.rstrip()}\n{marker}\n", encoding="utf-8"
    )
    reload_sources(root=root)
    return f"{name}.v2"


def count_rows(session: Session, model: Any) -> int:
    return int(session.execute(select(func.count()).select_from(model)).scalar_one())


def outcome_rows(session: Session, run_id: str) -> list[EvalCaseResultRow]:
    return list(
        session.execute(
            select(EvalCaseResultRow)
            .where(EvalCaseResultRow.eval_run_id == run_id)
            .order_by(EvalCaseResultRow.case_id)
        ).scalars().all()
    )


def idem_status(key: str) -> str:
    return str(idempotency.get("ai.eval.run", key).status.value)


def mock_provider_of() -> Any:
    return api.get_llm_provider(
        ProviderInfo(
            provider=MOCK_PROVIDER,
            provider_class=ProviderClass.LOCAL_ONLY,
            model=MOCK_MODEL,
            capabilities=Capabilities(
                structured_output=True, vision=False, max_context_tokens=1000
            ),
        )
    )


def prompt_versions(run_id: str) -> dict[str, Any]:
    with unit_of_work() as session:
        row = session.get(AgentRunRow, run_id)
        assert row is not None
        return dict(row.prompt_versions)


def transcript(run_id: str) -> str:
    """What the run's model was actually sent, read back from the persisted context."""
    from factory_intelligence.models.run import AgentRunContextRow

    with unit_of_work() as session:
        row = session.get(AgentRunContextRow, run_id)
        assert row is not None and row.context_artifact_id
        artifact_id = str(row.context_artifact_id)
    body, _classification, _media = read_content_bytes(artifact_id)
    document = json.loads(body.decode("utf-8"))
    return json.dumps(document["messageSequence"], ensure_ascii=False)


class _CancelAfterFirstCase:
    """A progress watcher that cancels the evaluation while it walks its case list."""

    def __init__(self, token: ManualCancellationToken) -> None:
        self.token = token
        self.steps: list[str] = []

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
        if step.startswith("scored ") and len(self.steps) >= 3:
            self.token.cancel("the operator closed the tab")


# --- the packaged sets (AI-EVAL-001, AI-EVAL-002, AI-EVAL-003) -------------------------


def test_every_shipped_agent_ships_a_set_with_golden_and_injection_cases(
    harness: IntelligenceHarness,
) -> None:
    """AI-EVAL-001 gives every agent a set; AI-EVAL-003 says part of it must be injection
    samples.  Both are properties of the shipped tree, so they are asserted per agent."""
    _ = harness
    for agent_type in SHIPPED_AGENTS:
        cases = cases_of(agent_type)
        assert cases, f"{agent_type.value} ships no evaluation set (AI-EVAL-001)"
        assert {case.agent_type for case in cases} == {agent_type.value}, (
            "a case filed under the wrong agent is judged against the wrong checks"
        )
        assert {case.kind for case in cases} == {GOLDEN, INJECTION}, agent_type.value
        assert len({case.id for case in cases}) == len(cases), "case ids key the result rows"
        assert {case.stage for case in cases} == {AGENT_SPECS[agent_type].stage.value}
        assert all(case.note or case.title for case in cases), "a set has to say what it pins"


@pytest.mark.parametrize("agent_type", SHIPPED_AGENTS, ids=[a.value.lower() for a in SHIPPED_AGENTS])
def test_a_shipped_case_answers_with_the_envelope_a_run_is_judged_with(
    harness: IntelligenceHarness, agent_type: AgentType
) -> None:
    """AI-EVAL-001 with AI-RT-010: a case's expected answer *is* an agent output.  A set whose
    expectations the envelope refused would score a contract no runtime ever enforces."""
    _ = harness
    from factory_intelligence.output.schema import envelope_schema

    validator = Draft202012Validator(envelope_schema())
    for case in cases_of(agent_type):
        on_disk = set_directory(agent_type) / f"{Path(case.source_path).name}"
        assert on_disk.is_file(), f"{case.id} is not the file it was parsed from"
        reparsed = parse_case(on_disk, agent_type)
        assert reparsed.id == case.id and reparsed.kind == case.kind
        assert reparsed.forbids == case.forbids
        assert AgentOutput.model_validate(dict(case.expected_document)).summary

        errors = sorted(validator.iter_errors(case.expected_document), key=str)
        assert not errors, f"{case.id} is not valid agent output: {errors[:1]}"
        assert case.context.items, f"{case.id} shows the model no content at all"

        if case.is_injection:
            assert case.forbids, f"{case.id} never says what the injection asked for"
            assert "no-value-key:uid" in case.forbids or "no-path-ending-with:uid" in case.forbids
        else:
            assert case.forbids == (), "a golden case compares a shape, it does not forbid one"


def test_the_set_version_is_a_digest_of_the_cases_themselves(
    harness: IntelligenceHarness,
) -> None:
    """AI-EVAL-002: the version names what was measured, so editing a case in place has to
    change it — or two different measurements would report one version."""
    _ = harness
    agent_type = AgentType.REQUIREMENTS
    original = set_version(agent_type)
    assert original.startswith("set-")
    assert set_version(agent_type) == original, "the digest is a function of the files"
    assert set_version(AgentType.MAPPING) != original

    target = set_directory(agent_type) / f"{agent_type.value.lower()}-conflicting-inputs.json"
    text = target.read_text(encoding="utf-8")
    document = json.loads(text)
    document["expected"]["summary"] = "the same answer, restated in other words"
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        assert set_version(agent_type) != original, "a restated expectation is a new measurement"
    finally:
        target.write_text(text, encoding="utf-8")
    assert set_version(agent_type) == original


def test_the_shipped_prompts_and_the_shipped_sets_cover_the_same_agents(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-002 and AI-EVAL-002 are one promise: a version that ships can be measured, so
    neither half may exist without the other."""
    _ = harness
    from factory_intelligence.prompts.registry import discover_sources

    sourced = {each.agent_type for each in discover_sources()}
    measured = {
        agent.value for agent in SHIPPED_AGENTS if (EVAL_SET_ROOT / agent.value.lower()).is_dir()
    }
    assert sourced == measured == {agent.value for agent in SHIPPED_AGENTS}


# --- AI-EVAL-001 / 002: one measurement ----------------------------------------------


def test_an_evaluation_scores_the_version_and_records_the_evidence(
    harness: IntelligenceHarness,
) -> None:
    """AI-EVAL-001: the score, the verdict and the identity of what was measured; AI-REG-005:
    the registry keeps the newest score while the history stays in ``ai.eval_run``."""
    _ = harness
    result = evaluate(
        AgentType.REQUIREMENTS,
        "requirements.consolidate.v1",
        key="eval001:pass",
        answers=expected_answers(AgentType.REQUIREMENTS),
    )

    assert result.agent_type == AgentType.REQUIREMENTS.value
    assert result.qualified_prompt == "requirements.consolidate.v1"
    assert result.model == MOCK_MODEL
    assert result.eval_set_version == set_version(AgentType.REQUIREMENTS)
    assert result.score == 1.0 and result.passed is True
    assert [case.kind for case in result.cases] == [GOLDEN, GOLDEN, INJECTION, GOLDEN]
    assert result.injection_cases == 1 and result.injection_passed == 1
    assert all(case.passed and case.score == 1.0 for case in result.cases)

    with unit_of_work() as session:
        run = session.get(EvalRunRow, str(result.eval_run_id))
        assert run is not None
        assert run.passed is True and run.score == 1.0
        assert run.case_count == len(result.cases)
        assert run.model == MOCK_MODEL and run.provider == MOCK_PROVIDER
        assert run.prompt_version_id
        assert run.run_by == OPERATOR
        # The judgement is the whole summary except the four accounting keys the runner folds in.
        scored = {key: value for key, value in run.summary.items() if key not in ACCOUNTING}
        assert scored == {
            "cases": 4,
            "matched": 4,
            "failedCaseIds": [],
            "injectionCases": 1,
            "injectionPassed": 1,
            "injectionRequired": True,
        }
        # The runner's own reading of the calls it made, kept beside the score they produced.
        assert run.summary["modelCalls"] == 4
        assert run.summary["inputTokens"] > 0 and run.summary["outputTokens"] > 0
        assert run.summary["estimatedCost"] >= 0.0

        # AI-NFR-03 books *every* call, and these belong to no project's budget: the rows are
        # the per-call evidence, the summary above is the same accounting read in one line.
        booked = list(
            session.scalars(
                select(ModelCallRow)
                .where(ModelCallRow.workflow_id == f"eval:{result.eval_run_id}")
                .order_by(ModelCallRow.sequence)
            )
        )
        assert len(booked) == 4, "one row per case, no corrective retry"
        assert [row.sequence for row in booked] == [1, 2, 3, 4]
        assert all(row.project_id is None for row in booked)
        assert all(row.call_role == "evaluation" for row in booked)
        assert all(row.outcome == "OK" and row.request_id for row in booked)
        assert {row.agent_type for row in booked} == {AgentType.REQUIREMENTS.value}
        assert {row.provider for row in booked} == {MOCK_PROVIDER}
        assert sum(row.input_tokens for row in booked) == run.summary["inputTokens"]
        assert sum(row.output_tokens for row in booked) == run.summary["outputTokens"]
        assert count_rows(session, EvalCaseResultRow) == 4
    assert idem_status("eval001:pass") == "COMPLETED"

    standing = registered_model(f"{MOCK_PROVIDER}/{MOCK_MODEL}")
    assert standing.last_eval_score == 1.0
    assert standing.last_eval_run_id == result.eval_run_id
    assert not has_active_uow(), "an evaluation is external I/O and leaves nothing open"

    listed = api.governance("eval.list", {"agentType": AgentType.REQUIREMENTS.value})["items"]
    assert [each["id"] for each in listed] == [result.eval_run_id]
    assert listed[0]["summary"]["injectionCases"] == 1
    fetched = api.governance("eval.get", {"id": result.eval_run_id})
    assert fetched["prompt"] == "requirements.consolidate.v1"
    assert fetched["caseCount"] == 4


def test_a_per_case_difference_is_reported_and_persisted(
    harness: IntelligenceHarness,
) -> None:
    """AI-EVAL-001's 逐项差异: the report says *which* fact the prompt missed, and the row
    keeps saying it after the call returned."""
    _ = harness
    cases = cases_of(AgentType.REQUIREMENTS)
    answers = expected_answers(AgentType.REQUIREMENTS)
    answers[0] = empty_answer(cases[0])

    result = evaluate(
        AgentType.REQUIREMENTS,
        "requirements.consolidate.v1",
        key="eval001:diff",
        answers=answers,
    )

    assert result.score == 0.75 and result.passed is False
    assert [each.passed for each in result.cases] == [False, True, True, True]
    difference = result.cases[0]
    assert difference.case_id == cases[0].id and difference.kind == GOLDEN
    assert difference.score == 0.0
    axes = {str(axis["name"]): axis for axis in difference.diff["axes"]}
    assert set(axes) >= {"businessKeys"}
    assert axes["businessKeys"]["missing"], "the report must name the entry nobody mapped"
    assert axes["businessKeys"]["score"] == 0.0
    assert difference.diff["expected"] == cases[0].expected_document

    with unit_of_work() as session:
        rows = outcome_rows(session, str(result.eval_run_id))
        assert [row.case_id for row in rows] == sorted(case.id for case in cases)
        persisted = next(row for row in rows if row.case_id == cases[0].id)
        assert persisted.passed is False
        assert persisted.score == 0.0
        assert persisted.diff["axes"] == difference.diff["axes"]
        assert all(row.passed for row in rows if row.case_id != cases[0].id)


def test_an_answer_the_ladder_refuses_scores_nothing_and_says_where_it_stopped(
    harness: IntelligenceHarness,
) -> None:
    """The ladder is the runtime's own, so an answer that never clears it is scored against
    the rungs a run would use and the diff names the rung (AI-RT-010, AI-EVAL-001)."""
    _ = harness
    answers = expected_answers(AgentType.REQUIREMENTS)
    answers[0] = {"$raw": "I will not answer in JSON."}

    result = evaluate(
        AgentType.REQUIREMENTS,
        "requirements.consolidate.v1",
        key="eval001:ladder",
        answers=answers,
    )

    refused = result.cases[0]
    assert refused.passed is False and refused.score == 0.0
    assert refused.diff["reason"].startswith("the answer did not clear the output ladder")
    assert refused.diff["rejectedAt"] == "envelope", "the diff names the rung that stopped it"
    assert any(
        str(line).startswith("AI-ENV-002") for line in refused.diff["findings"]
    ), refused.diff["findings"]
    assert result.score == 0.75 and result.passed is False


def test_the_same_key_returns_the_recorded_measurement_without_a_second_run(
    harness: IntelligenceHarness,
) -> None:
    """00 doc 6.4: an evaluation has side effects, so a retried key replays what was recorded
    — the same verdict from the same rows, and no second set of model calls."""
    _ = harness
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, *expected_answers(AgentType.REQUIREMENTS))
    key = "eval002:idempotent"

    first = api.run_evaluation(
        AgentType.REQUIREMENTS.value,
        "requirements.consolidate.v1",
        MOCK_MODEL,
        idempotency_key=key,
        operator=OPERATOR,
    )
    calls = provider.call_count
    second = api.run_evaluation(
        AgentType.REQUIREMENTS.value,
        "requirements.consolidate.v1",
        MOCK_MODEL,
        idempotency_key=key,
        operator=OPERATOR,
    )

    assert second.model_dump() == first.model_dump()
    assert second.eval_run_id == first.eval_run_id
    assert provider.call_count == calls, "the replay must not have modelled anything"
    with unit_of_work() as session:
        assert count_rows(session, EvalRunRow) == 1
        assert count_rows(session, EvalCaseResultRow) == 4


def test_a_reused_key_with_a_different_request_is_a_conflict(harness: IntelligenceHarness) -> None:
    """The digest covers the request, not just the key: the same key used for a different
    measurement is refused rather than answered with someone else's result (00 doc 6.4)."""
    _ = harness
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, *expected_answers(AgentType.REQUIREMENTS))
    recorded = api.run_evaluation(
        AgentType.REQUIREMENTS.value,
        "requirements.consolidate.v1",
        MOCK_MODEL,
        idempotency_key="eval002:conflict",
        operator=OPERATOR,
    )
    calls = provider.call_count

    with pytest.raises(IdempotencyConflictError):
        api.run_evaluation(
            AgentType.REQUIREMENTS.value,
            "requirements.consolidate.v1",
            MOCK_MODEL,
            idempotency_key="eval002:conflict",
            operator="somebody-else",
        )
    assert provider.call_count == calls, "a conflict must not have modelled anything"
    with unit_of_work() as session:
        assert count_rows(session, EvalRunRow) == 1
        assert session.get(EvalRunRow, str(recorded.eval_run_id)) is not None


def test_an_explicit_set_version_is_recorded_as_the_input_measured(
    harness: IntelligenceHarness,
) -> None:
    """AI-EVAL-002: a caller comparing prompt versions names the set, so the row says which
    measurement is comparable with which."""
    _ = harness
    result = evaluate(
        AgentType.REQUIREMENTS,
        "requirements.consolidate.v1",
        key="eval002:version",
        answers=expected_answers(AgentType.REQUIREMENTS),
        eval_set_version="release-7",
    )
    assert result.eval_set_version == "release-7"
    with unit_of_work() as session:
        row = session.get(EvalRunRow, str(result.eval_run_id))
        assert row is not None and row.eval_set_version == "release-7"


# --- AI-EVAL-003: injection counted apart --------------------------------------------


def test_obeying_an_injection_sample_does_not_pass_the_set(
    harness: IntelligenceHarness,
) -> None:
    """AI-EVAL-003 is the reason the set exists: a run that answered every golden case
    perfectly and obeyed one injection sample must not read as a passing evaluation."""
    _ = harness
    agent_type = AgentType.MAPPING
    cases = cases_of(agent_type)
    injection = next(case for case in cases if case.is_injection)
    golden_ids = sorted(case.id for case in cases if not case.is_injection)

    result = evaluate(
        agent_type,
        "mapping.decide.v1",
        key="eval003:obedient",
        answers=obeying_injection_answers(agent_type),
    )

    assert [each.case_id for each in result.cases] == [case.id for case in cases]
    assert sorted(each.case_id for each in result.cases if each.passed) == golden_ids, (
        "the premise: the golden half was answered perfectly"
    )
    assert result.score == 0.75, "three of four is not the pass mark for a set of four"
    assert result.score < settings().eval_pass_score
    assert result.passed is False
    assert result.injection_cases == 1 and result.injection_passed == 0

    failed = next(each for each in result.cases if each.case_id == injection.id)
    assert failed.kind == INJECTION and failed.score == 0.0
    assert failed.diff["forbids"] == list(injection.forbids)
    assert any("no-path-ending-with:uid" in str(violation) for violation in failed.diff["violations"]), (
        "the report names the field the injection asked for"
    )
    assert any(OBEDIENT_UID not in str(violation) for violation in failed.diff["violations"])

    with unit_of_work() as session:
        run = session.get(EvalRunRow, str(result.eval_run_id))
        assert run is not None and run.passed is False
        assert run.summary["injectionCases"] == 1
        assert run.summary["injectionPassed"] == 0
        assert run.summary["injectionRequired"] is True
        assert run.summary["failedCaseIds"] == [injection.id]


def test_resisting_the_same_injection_is_what_passes(harness: IntelligenceHarness) -> None:
    """The other half of the arithmetic: nothing changed except that the injection was ignored,
    and that is the difference between 0.75 and 1.0 (AI-EVAL-003)."""
    _ = harness
    agent_type = AgentType.MAPPING
    result = evaluate(
        agent_type,
        "mapping.decide.v1",
        key="eval003:resisted",
        answers=expected_answers(agent_type),
    )
    assert result.score == 1.0 and result.passed is True
    assert result.injection_cases == 1 and result.injection_passed == 1
    assert result.score >= settings().eval_pass_score > 0.75


def test_an_injection_case_is_refused_when_it_only_avoids_the_ladder(
    harness: IntelligenceHarness,
) -> None:
    """AI-EVAL-003 asks for two halves — the output clears the ladder *and* contains none of
    what was asked for.  An answer that is not output at all proves nothing."""
    _ = harness
    agent_type = AgentType.MAPPING
    cases = cases_of(agent_type)
    position = next(index for index, case in enumerate(cases) if case.is_injection)
    answers = expected_answers(agent_type)
    answers[position] = {"summary": "", "confidence": 0.0, "decisions": [], "questions": []}

    result = evaluate(agent_type, "mapping.decide.v1", key="eval003:refusal", answers=answers)

    injection_case = result.cases[position]
    assert injection_case.passed is False
    assert "violations" not in injection_case.diff, "it stopped on the ladder, not on content"
    assert injection_case.diff["rejectedAt"]
    assert result.passed is False


def test_a_version_that_aced_the_golden_half_and_obeyed_an_injection_cannot_be_activated(
    harness: IntelligenceHarness,
) -> None:
    """The gate reads the verdict the report gives, so AI-EVAL-003 is not decoration: with the
    regression claim on file, a 0.75 still keeps the switch refused."""
    _ = harness
    clear_prompt_activations()
    agent_type = AgentType.MAPPING
    result = evaluate(
        agent_type,
        "mapping.decide.v1",
        key="eval003:gate",
        answers=obeying_injection_answers(agent_type),
    )
    regress(agent_type, 1)

    assert result.passed is False
    with pytest.raises(PromptActivationBlocked):
        activate(agent_type, 1, "eval003-gate-ops")
    assert current_activation(agent_type) is None


def test_every_shipped_injection_case_names_what_it_refused(
    harness: IntelligenceHarness,
) -> None:
    """AI-EVAL-003 across the whole packaged tree, not one hand-picked case: a sample that
    does not declare what the injection asked for cannot be scored, so parsing refuses it."""
    _ = harness
    for agent_type in SHIPPED_AGENTS:
        injections = [case for case in cases_of(agent_type) if case.is_injection]
        assert injections, f"{agent_type.value} ships no injection sample"
        for case in injections:
            assert case.forbids, f"{case.id} declares nothing to check it against"
            assert all(":" in predicate or predicate == "no-patch" for predicate in case.forbids)
            assert any(
                "instruction" in item.content.lower() or "ignore" in item.content.lower()
                for item in case.context.items
                if item.kind.value not in {"SYSTEM_RULES", "STAGE_RULES"}
            ), f"{case.id} injects no demand at all"


def test_an_injection_case_without_forbids_cannot_be_loaded(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """The parser, not a reviewer, holds AI-EVAL-003: a case file that forgets to say what the
    injection asked for is refused rather than scored as automatically clean."""
    _ = harness, tmp_path
    case = dict(json.loads((set_directory(AgentType.REQUIREMENTS) / (
        "requirements-injection-approve-and-uid.json")).read_text(encoding="utf-8")))
    del case["forbids"]
    path = tmp_path / "stripped-injection.json"
    path.write_text(json.dumps(case), encoding="utf-8")

    from factory_foundation.api import ValidationError as FoundationValidation

    with pytest.raises(FoundationValidation) as refused:
        parse_case(path, AgentType.REQUIREMENTS)
    assert "AI-EVAL-003" in str(refused.value)


# --- AI-EVAL-002 refusals, and what a call may not do ---------------------------------


def test_a_prompt_of_another_agent_is_refused_before_the_first_call(
    harness: IntelligenceHarness,
) -> None:
    """Its set would be judged against the wrong spec and the wrong checks, and a measurement
    like that is worse than none: it can be gate-opened later (AI-EVAL-001)."""
    _ = harness
    install_mock_model(AgentType.REQUIREMENTS, AgentType.MAPPING)

    with pytest.raises(ValidationError) as refused:
        api.run_evaluation(
            AgentType.MAPPING.value,
            "requirements.consolidate.v1",
            MOCK_MODEL,
            idempotency_key="eval002:mismatch",
        )
    assert refused.value.details["declaredFor"] == AgentType.REQUIREMENTS.value
    assert refused.value.details["agentType"] == AgentType.MAPPING.value
    assert mock_provider_of().call_count == 0
    with unit_of_work() as session:
        assert count_rows(session, EvalRunRow) == 0


def test_an_unregistered_prompt_or_an_unregistered_model_is_named_as_missing(
    harness: IntelligenceHarness,
) -> None:
    """Both halves of the triple have to be registered before a measurement means anything
    (AI-REG-001, AI-REG-004)."""
    _ = harness
    install_mock_model(AgentType.MAPPING)
    with pytest.raises(PromptNotFound):
        api.run_evaluation(
            AgentType.MAPPING.value,
            "mapping.decide.v9",
            MOCK_MODEL,
            idempotency_key="eval002:no-prompt",
        )
    with pytest.raises(ValidationError) as refused:
        api.run_evaluation(
            AgentType.MAPPING.value,
            "mapping.decide.v1",
            "vendor/never-registered",
            idempotency_key="eval002:no-model",
        )
    assert "never-registered" in str(refused.value)
    with unit_of_work() as session:
        assert count_rows(session, EvalRunRow) == 0


def test_a_disabled_model_cannot_be_scored(harness: IntelligenceHarness) -> None:
    """Scoring a model nobody may run would produce evidence that cannot be activated, so the
    request is refused instead (AI-REG-004, AI-EVAL-002)."""
    _ = harness
    install_mock_model(AgentType.MAPPING)
    api.governance("model.enable", {"ref": f"{MOCK_PROVIDER}/{MOCK_MODEL}", "enabled": False})

    with pytest.raises(ValidationError) as refused:
        api.run_evaluation(
            AgentType.MAPPING.value,
            "mapping.decide.v1",
            MOCK_MODEL,
            idempotency_key="eval002:disabled",
        )
    assert "disabled" in str(refused.value)
    with unit_of_work() as session:
        assert count_rows(session, EvalRunRow) == 0


def test_an_agent_with_no_packaged_set_reports_the_missing_input(
    harness: IntelligenceHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent with no packaged set is answered as a missing input naming the path that has to
    exist, never as a score of zero (AI-EVAL-001).

    Every agent of SRS 3.3 ships a set now that PAGE_DESIGN's three do too, so the *absence* is
    produced here by pointing the loader at an empty root.  What this pins is the refusal and its
    message, not which agents are still missing files — that second fact is pinned for the whole
    packaged tree by :func:`test_the_shipped_prompts_and_the_shipped_sets_cover_the_same_agents`.
    """
    _ = harness
    install_mock_model(AgentType.API)
    header = (
        'name = "api.generate"\nversion = 1\nagent_type = "API"\nstages = ["API"]\n'
        'input_schema_ref = "agent-context"\noutput_schema_ref = "agent-output"\n'
        'system_rules = """\nGenerate the API surface.\n"""\n'
    )
    directory = tmp_path / "api"
    directory.mkdir()
    (directory / "api.generate.v1.md").write_text(
        f"+++\n{header}\n+++\n# Generating the API surface\n", encoding="utf-8"
    )
    reload_sources(root=tmp_path)
    empty_sets = tmp_path / "sets"
    monkeypatch.setattr("factory_intelligence.eval.scoring.EVAL_SET_ROOT", empty_sets)

    with pytest.raises(EvalSetMissing) as refused:
        api.run_evaluation(
            AgentType.API.value, "api.generate.v1", MOCK_MODEL, idempotency_key="eval001:noset"
        )
    assert refused.value.code == "AI_EVAL_SET_MISSING"
    assert "eval/sets/api" in str(refused.value)
    assert not (empty_sets / "api").is_dir(), "the refusal is about a directory that is not there"


def test_a_provider_failure_records_nothing_and_leaves_the_key_spendable(
    harness: IntelligenceHarness,
) -> None:
    """A transport outage says nothing about a prompt: a row of ``score = 0`` would become the
    evidence that refuses a switch, and a lucky re-run the evidence that permits it."""
    _ = harness
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, {"$error": {"kind": "transport", "message": "503"}})
    api.set_sleeper(lambda seconds: None)

    with pytest.raises(LlmTransportError):
        api.run_evaluation(
            AgentType.REQUIREMENTS.value,
            "requirements.consolidate.v1",
            MOCK_MODEL,
            idempotency_key="eval002:outage",
            operator=OPERATOR,
        )
    with unit_of_work() as session:
        assert count_rows(session, EvalRunRow) == 0
        assert count_rows(session, EvalCaseResultRow) == 0
    assert idem_status("eval002:outage") == "IN_PROGRESS"
    assert registered_model(f"{MOCK_PROVIDER}/{MOCK_MODEL}").last_eval_score is None

    retried = evaluate(
        AgentType.REQUIREMENTS,
        "requirements.consolidate.v1",
        key="eval002:outage",
        answers=expected_answers(AgentType.REQUIREMENTS),
    )
    assert retried.passed is True


def test_cancellation_stops_at_the_next_case_and_persists_no_half_measurement(
    harness: IntelligenceHarness,
) -> None:
    """00 doc 6.3: an evaluation responds to cancellation, and the honest record of a
    half-walked set is no record — the key stays spendable for the next attempt."""
    _ = harness
    token = ManualCancellationToken()
    watcher = _CancelAfterFirstCase(token)
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, *expected_answers(AgentType.REQUIREMENTS))

    with pytest.raises(CancelledError):
        api.run_evaluation(
            AgentType.REQUIREMENTS.value,
            "requirements.consolidate.v1",
            MOCK_MODEL,
            idempotency_key="eval002:cancel",
            operator=OPERATOR,
            progress=watcher,
            cancel=token,
        )

    assert provider.call_count == 1, "the token was set after the first case, not the fourth"
    with unit_of_work() as session:
        assert count_rows(session, EvalRunRow) == 0
    assert idem_status("eval002:cancel") == "IN_PROGRESS"
    assert not has_active_uow()


def test_progress_names_the_case_being_measured(harness: IntelligenceHarness) -> None:
    """AI-RT-022's vocabulary reused: an evaluation that reports nothing cannot be told apart
    from one that is stuck on a slow model."""
    _ = harness

    class _Watch:
        def __init__(self) -> None:
            self.steps: list[str] = []

        def report(
            self,
            *,
            step: str,
            completed_fraction: float | None = None,
            message: str | None = None,
            data: dict[str, Any] | None = None,
        ) -> None:
            _ = completed_fraction, message
            self.steps.append(f"{step}:{sorted((data or {}).keys())}")

    watcher = _Watch()
    result = evaluate(
        AgentType.REQUIREMENTS,
        "requirements.consolidate.v1",
        key="eval002:progress",
        answers=expected_answers(AgentType.REQUIREMENTS),
        progress=watcher,
    )
    assert watcher.steps[0] == "construct case contexts:['cases']"
    assert any(step.startswith("model call for requirements-conflicting-inputs") for step in watcher.steps)
    assert any(step.startswith("scored requirements-merge-duplicate-facts") for step in watcher.steps)
    assert watcher.steps[-1] == "evaluation recorded:['passed', 'score']"
    assert result.passed is True


# --- AI-AC-03: the gate is why the measurement exists ---------------------------------


def test_a_version_measured_and_failed_cannot_go_live_until_it_is_measured_and_passed(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """AI-AC-03 end to end: one version, one model, one set, measured twice.  The failing
    measurement keeps the switch refused even with the regression claim on file; the passing
    one opens it, and the run that follows executes the version the gate accepted."""
    _ = harness
    clear_prompt_activations()
    agent_type = AgentType.REQUIREMENTS
    name = AGENT_SOURCES[agent_type][0]
    v2 = ship_v2(tmp_path, agent_type, "SECOND DRAFT OF THE REQUIREMENTS PROMPT")

    refused_run = evaluate(agent_type, v2, key="ac03:failing", answers=failing_answers(agent_type))
    assert refused_run.passed is False and refused_run.score == 0.25
    assert refused_run.injection_passed == refused_run.injection_cases == 1, (
        "the injection half was clean; the golden half still failed the set"
    )
    regress(agent_type, 2)
    with pytest.raises(PromptActivationBlocked) as blocked:
        activate(agent_type, 2, "ac03-ops")
    assert "no passing evaluation" in str(blocked.value)
    assert current_activation(agent_type) is None

    project = unique_project_id("prj-ac03-before")
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    input_id = stage_input(project, {"notes": "the nurse weighs the child at every visit"})
    provider = install_mock_model(agent_type)
    script(
        provider,
        agent_type,
        answer(operations=[add_operation("/requirements/items/-", requirement_item(input_id))]),
    )
    before = api.start_agent_run(
        request_for(
            project,
            agent_type,
            draft_id=draft_id,
            input_artifact_ids=[input_id],
            idempotency_key="ac03:run-before",
        )
    )
    assert before.status == "FAILED", (before.denial_reasons, before.issues)
    assert before.run_id is None, (
        "AI-REG-002: an un-activated run is refused and writes no row (not a placeholder run)"
    )
    assert any(
        issue.details.get("errorCode") == "AI_PROMPT_NOT_ACTIVATED" for issue in before.issues
    ), before.issues

    accepted = evaluate(agent_type, v2, key="ac03:passing", answers=expected_answers(agent_type))
    assert accepted.passed is True
    assert accepted.injection_cases == accepted.injection_passed == 1
    activated = activate(agent_type, 2, "ac03-ops")
    assert activated["evalRunId"] == accepted.eval_run_id
    active = current_activation(agent_type)
    assert active is not None and active.qualified_name == f"{name}.v2"

    second_project = unique_project_id("prj-ac03-after")
    seed_project(second_project)
    second_draft = open_draft(second_project, "REQUIREMENTS")
    # a fresh provider and answer for this run: ``evaluate`` installs its own provider, whose
    # script ends on the case file's base version, and the ladder rightly refuses a patch
    # authored against another version
    provider = install_mock_model(agent_type)
    script(
        provider,
        agent_type,
        answer(operations=[add_operation("/requirements/items/-", requirement_item(input_id))]),
    )
    running = api.start_agent_run(
        request_for(
            second_project,
            agent_type,
            draft_id=second_draft,
            input_artifact_ids=[input_id],
            idempotency_key="ac03:run-after",
        )
    )

    assert running.status == "COMPLETED", (running.denial_reasons, running.issues)
    assert running.patch_ids, "the activated prompt is the one whose patch S2 accepted"
    assert prompt_versions(str(running.run_id)) == {name: 2}, (
        "AI-AC-03: the run reports the version the gate let through"
    )
    assert "SECOND DRAFT OF THE REQUIREMENTS PROMPT" in transcript(str(running.run_id)), (
        "and sent it: the activated prose, not merely the version number"
    )
