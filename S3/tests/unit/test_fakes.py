"""``FakeIntelligenceApi`` must be a faithful stand-in (AI-API-001, 00 doc 6.1).

A Fake is only useful while it agrees with the thing it fakes.  Three ways it can drift are
each caught here rather than by a caller's failing production run:

* a **changed or missing parameter** — S1 forwards ``*args, **kwargs`` verbatim
  (``factory_control/adapters/delegating.py:101``), so a name that drifts breaks the caller
  that used the real S3 and the one that used the Fake in opposite directions;
* a **result shape that is not S3's DTO** — S1 coerces with ``model_validate(dump)``, and a
  dict-shaped fake hides a renamed field behind ``extra="ignore"``;
* **silence where the real API refuses** — a Fake that answers ``desensitize`` successfully
  lets a caller ship against M4 work this build does not contain.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

import pytest
from factory_foundation.api import is_external_io

from factory_intelligence import api
from factory_intelligence.dto.common import Answer, Question
from factory_intelligence.dto.governance import wire
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.dto.result import (
    AgentRunResult,
    ChatReply,
    EvalResult,
    ParsedInput,
    PolicyOutcome,
    UsageRollup,
)
from factory_intelligence.enums import (
    AgentRunStatus,
    BudgetState,
    ParseStatus,
    PolicyResult,
    ProviderClass,
    RunState,
)
from factory_intelligence.errors import BudgetInvalid, FeatureNotEnabled
from factory_intelligence.testing.fakes import FakeIntelligenceApi
from tests.unit.test_contract import (
    S1_AGENT_RUN_RESULT_FIELDS,
    S1_EXTERNAL_IO_METHODS,
    S1_METHOD_MAP,
    S1_PARSED_INPUT_FIELDS,
)

#: every public S3 function a caller may reach, gated ones included
COVERED = (
    "parse_input",
    "start_agent_run",
    "resume_agent_run",
    "cancel_agent_run",
    "get_agent_run",
    "explain_issues",
    "governance",
    "delete_project_data",
    "run_evaluation",
    "list_activations",
    "evaluate_policy",
    "usage_rollup",
    "list_llm_exchanges",
    "get_llm_exchange",
    "purge_llm_exchanges",
    # governance, one typed function per capability (AI-GOV-001, AI-API-001)
    "list_providers",
    "register_provider",
    "update_provider",
    "set_provider_enabled",
    "check_provider_health",
    "list_models",
    "register_model",
    "update_model",
    "set_model_enabled",
    "list_prompts",
    "get_prompt",
    "activate_prompt",
    "record_regression",
    "list_regressions",
    "reload_prompt_sources",
    "get_org_policy",
    "relax_org_policy",
    "get_effective_policy",
    "list_evaluations",
    "get_evaluation",
    "get_budget",
    "list_agents",
    # parse records and NRR queries (AI-INP-013/014/015)
    "list_input_parses",
    "get_column_mapping_proposal",
    "query_nrr",
    "stage_chat",
    "get_chat",
    "clear_chat",
    "desensitize",
    "set_budget",
)

#: names the Fake implements that ``api`` does not export yet.  Empty is the definition of
#: done, and a name that reappears here fails the coverage test below.
NOT_YET_IN_API: set[str] = set()


def _parameters(func: Any) -> list[tuple[str, inspect.Parameter]]:
    """Name, kind and default of every parameter except ``self``."""
    signature = inspect.signature(func)
    return [
        (name, parameter)
        for name, parameter in signature.parameters.items()
        if name != "self"
    ]


def _shape(parameters: list[tuple[str, inspect.Parameter]]) -> list[tuple[str, Any, bool]]:
    return [
        (name, parameter.kind, parameter.default is not inspect.Parameter.empty)
        for name, parameter in parameters
    ]


def _request(**overrides: Any) -> AgentRunRequest:
    payload: dict[str, Any] = {
        "project_id": "prj-1",
        "workflow_id": "wf-1",
        "stage_id": "stg-1",
        "stage": "REQUIREMENTS",
        "agent_type": "REQUIREMENTS",
        "draft_id": None,
        "scope": [],
        "input_artifact_ids": [],
        "design_reference_environment_key": None,
        "target_environment_policies": {},
        "capability_profile_artifact_id": None,
        "discovery_result_artifact_id": None,
        "operator": "ops",
        "idempotency_key": "run:1",
    }
    payload.update(overrides)
    return AgentRunRequest.model_validate(payload)


@pytest.fixture()
def fake() -> FakeIntelligenceApi:
    return FakeIntelligenceApi()


def test_every_public_function_has_a_fake() -> None:
    absent = [name for name in COVERED if not callable(getattr(FakeIntelligenceApi, name, None))]
    assert not absent, f"the Fake is missing: {absent}"
    missing = [name for name in COVERED if name not in api.__all__]
    assert missing == sorted(NOT_YET_IN_API), f"api.__all__ changed: {missing}"


def test_the_api_exports_no_function_the_fake_does_not_cover() -> None:
    """Adding a public function without a Fake is the drift this test exists to make loud."""
    exported_functions = {
        name
        for name in api.__all__
        if callable(getattr(api, name))
        and not isinstance(getattr(api, name), type)
        and getattr(api, name).__module__.startswith("factory_intelligence")
    }
    internals = {
        "initialize",
        "settings",
        "configure",
        "reset_settings",
        "set_llm_provider",
        "set_llm_provider_factory",
        "get_llm_provider",
        "set_blueprint",
        "get_blueprint",
        "set_clock",
        "get_clock",
        "set_sleeper",
        "set_http_transport",
        "reset_providers",
    }
    uncovered = exported_functions - set(COVERED) - internals
    assert not uncovered, f"public functions without a Fake: {sorted(uncovered)}"


@pytest.mark.parametrize("name", [name for name in COVERED if name not in NOT_YET_IN_API])
def test_the_fake_signatures_are_the_real_ones(name: str) -> None:
    assert _shape(_parameters(getattr(api, name))) == _shape(
        _parameters(getattr(FakeIntelligenceApi, name))
    ), f"api.{name} and the Fake take different parameters"


def test_the_fake_covers_every_function_s1_resolves() -> None:
    assert set(COVERED) >= S1_METHOD_MAP


@pytest.mark.parametrize("name", sorted(S1_EXTERNAL_IO_METHODS - NOT_YET_IN_API))
def test_the_real_function_is_still_marked_external_io(name: str) -> None:
    """The Fake cannot prove this, so the assertion belongs to the real module: a caller
    that switches from the Fake to S3 must not gain a transaction constraint it did not
    test against (00 doc 6.2)."""
    assert is_external_io(getattr(api, name))


def test_start_returns_s3s_own_dto_not_a_dict(fake: FakeIntelligenceApi) -> None:
    fake.queue_run(AgentRunResult(summary="done", patch_ids=["bpch-1"], confidence=0.9))
    result = fake.start_agent_run(_request())
    assert isinstance(result, AgentRunResult)
    assert result.patch_ids == ["bpch-1"]
    assert result.run_id is not None


def test_a_repeated_start_key_replays_the_first_run(fake: FakeIntelligenceApi) -> None:
    """AI-AC-05: the caller's retry must not become a second model call."""
    fake.queue_run(AgentRunResult(summary="first"), AgentRunResult(summary="second"))
    first = fake.start_agent_run(_request())
    again = fake.start_agent_run(_request())
    assert again.run_id == first.run_id
    assert again.summary == "first"
    assert fake.calls.count("start_agent_run:run:1") == 2


def test_start_accepts_s1s_own_request_object(fake: FakeIntelligenceApi) -> None:
    """S1 hands over its mirror instance, not a dict (``delegating.py`` forwards it whole)."""

    class Mirror:
        project_id = "prj-2"
        workflow_id = "wf-2"
        stage_id = "stg-2"
        stage = "DOMAIN"
        agent_type = "DOMAIN"
        draft_id = None
        scope: list[str] = []
        input_artifact_ids: list[str] = []
        design_reference_environment_key = None
        target_environment_policies: dict[str, Any] = {}
        capability_profile_artifact_id = None
        discovery_result_artifact_id = None
        operator = "ops"
        idempotency_key = "run:mirror"

    mirror: Any = Mirror()
    result = fake.start_agent_run(mirror)
    assert result.run_id is not None
    assert fake.calls[-1] == "start_agent_run:run:mirror"


def test_results_carry_every_field_s1_reads(fake: FakeIntelligenceApi) -> None:
    """The Fake's dump must satisfy S1's mirror; a field the Fake forgot is invisible
    there because the mirror ignores unknown keys."""
    fake.queue_run(AgentRunResult())
    dumped = fake.start_agent_run(_request()).model_dump()
    assert set(S1_AGENT_RUN_RESULT_FIELDS) <= set(dumped)
    parsed = fake.parse_input("prj-1", "art-1", operator="ops", idempotency_key="parse:1")
    assert set(S1_PARSED_INPUT_FIELDS) <= set(parsed.model_dump())
    assert isinstance(parsed, ParsedInput)
    assert parsed.status is ParseStatus.PARSED


def test_parse_is_idempotent_per_key(fake: FakeIntelligenceApi) -> None:
    first = fake.parse_input("prj-1", "art-1", operator="ops", idempotency_key="parse:1")
    again = fake.parse_input("prj-1", "art-1", operator="ops", idempotency_key="parse:1")
    assert again.nrr_artifact_id == first.nrr_artifact_id


def test_resume_drops_the_questions_it_was_answered(fake: FakeIntelligenceApi) -> None:
    waiting = AgentRunResult(
        status=AgentRunStatus.WAITING_INPUT,
        questions=[Question(local_id="Q1", text="which?", severity="HIGH", blocking=True)],
    )
    fake.queue_run(waiting)
    run = fake.start_agent_run(_request())
    resumed = fake.resume_agent_run(
        run.run_id or "",
        [Answer(question_local_id="Q1", custom_text="answer", answered_by="ops")],
        "resume:1",
    )
    assert resumed.run_id == run.run_id
    assert resumed.questions == []


def test_cancel_marks_the_recorded_run(fake: FakeIntelligenceApi) -> None:
    run = fake.start_agent_run(_request())
    fake.cancel_agent_run(run.run_id or "")
    cancelled = fake.get_agent_run(run.run_id or "")
    assert cancelled is not None, "the run the fake recorded is still there"
    assert cancelled.status is RunState.CANCELLED, "the fake reports the field the real path fills"


def test_unknown_run_reads_as_absent(fake: FakeIntelligenceApi) -> None:
    assert fake.get_agent_run("arun-nope") is None


def test_governance_records_a_copy_of_the_payload(fake: FakeIntelligenceApi) -> None:
    payload: dict[str, Any] = {"name": "mapping.decide", "version": 3}
    fake.governance("prompt.activate", payload)
    payload["version"] = 4
    assert fake.governance_log == [("prompt.activate", {"name": "mapping.decide", "version": 3})]
    # AI-GOV-008: the catalogue names each agent's stage, enablement and effective prompt,
    # so a caller can align a stage with its agent without a database
    agents = fake.governance("agent.list", {})
    assert {item["agentType"] for item in agents["items"]} == {
        "REQUIREMENTS",
        "DOMAIN",
        "MAPPING",
        "METADATA_DESIGN",
        "API",
        "UI",
        "FIXTURE",
        "REVIEW",
    }
    assert {"stage", "milestone", "enabled", "promptName", "activePrompt", "perPage", "patchMode"} <= set(
        agents["items"][0]
    )


def test_an_activation_the_fake_was_told_about_is_listed(fake: FakeIntelligenceApi) -> None:
    """S1's ``S5IF-GOV10`` reads activations through the fake too, so activate-then-list has
    to work here exactly as it does against S3: one row per agent, same fields as the answer."""
    answer = fake.governance(
        "prompt.activate",
        {
            "agentType": "MAPPING",
            "promptName": "mapping.decide",
            "promptVersion": 3,
            "modelList": ["local/mock-1"],
            "operator": "ops",
        },
    )
    listed = fake.list_activations()

    # the façade answers in its wire spelling and the typed function in its model; the row
    # behind both is one row, which is what S5IF-GOV10 reads
    assert [wire(row) for row in listed] == [answer]
    assert set(answer) == {
        "agentType",
        "promptVersionId",
        "name",
        "version",
        "modelList",
        "evalRunId",
        "regressionPassedRef",
        "activatedBy",
    }
    assert answer["promptVersionId"] == "mapping.decide.v3"

    fake.governance(
        "prompt.activate",
        {
            "agentType": "MAPPING",
            "promptName": "mapping.decide",
            "promptVersion": 4,
            "modelList": [],
            "operator": "ops",
        },
    )
    assert [row.prompt_version_id for row in fake.list_activations()] == ["mapping.decide.v4"]
    assert fake.list_activations()[0].name == "mapping.decide"


def test_evaluate_policy_can_be_scripted(fake: FakeIntelligenceApi) -> None:
    fake.policy_verdicts["art-1|art-2"] = PolicyOutcome(result=PolicyResult.DENIED)
    outcome = fake.evaluate_policy("prj-1", ["art-1", "art-2"], "LOCAL_ONLY")
    assert outcome.result is PolicyResult.DENIED
    default = fake.evaluate_policy("prj-1", ["art-3"], "LOCAL_ONLY")
    assert default.result is PolicyResult.ALLOWED
    assert default.provider_class is ProviderClass.LOCAL_ONLY
    assert isinstance(default, PolicyOutcome)


def test_run_evaluation_returns_a_real_result(fake: FakeIntelligenceApi) -> None:
    result = fake.run_evaluation(
        "REQUIREMENTS", "requirements.consolidate.v1", "prov/model", idempotency_key="eval:1"
    )
    assert isinstance(result, EvalResult)
    assert result.prompt_name == "requirements.consolidate"
    assert result.prompt_version == 1
    assert result.qualified_prompt == "requirements.consolidate.v1"


def test_usage_rollup_is_filtered_to_the_project(fake: FakeIntelligenceApi) -> None:
    fake.usage.append(UsageRollup(project_id="prj-1", calls=2, estimated_cost=0.5))
    fake.usage.append(UsageRollup(project_id="prj-2", calls=1))
    rows = fake.usage_rollup("prj-1")
    assert [row.project_id for row in rows] == ["prj-1"]


def test_gated_capabilities_refuse_the_way_the_real_api_does(fake: FakeIntelligenceApi) -> None:
    calls: Mapping[str, Any] = {
        "desensitize": lambda: fake.desensitize("prj-1", "art-1", {}, operator="ops", idempotency_key="d:1"),
    }
    for name, call in calls.items():
        with pytest.raises(FeatureNotEnabled) as caught:
            call()
        assert caught.value.details["capability"] == name


def test_the_delivered_m3_m4_capabilities_answer_for_real(fake: FakeIntelligenceApi) -> None:
    """A Fake that refuses what the live API now does would hide S1's own bug.

    These three left :data:`GATED_CAPABILITIES` with S1's M3/M4 delivery, so the Fake owes the
    same answers, including the same refusals for a payload the real path rejects.
    """
    reply = fake.stage_chat(
        "prj-1", "REQUIREMENTS", None, "what does this NRR mean?", {}, "ops", idempotency_key="c:1"
    )
    assert reply.answer, "an empty answer would let S1 ship a chat bubble with no text"
    assert fake.chat_turns == [("prj-1", "REQUIREMENTS", "what does this NRR mean?")]
    scripted = ChatReply(answer="scripted", patch_ids=["ptch-1"])
    fake.chat_replies["prj-1/REQUIREMENTS"] = scripted
    assert fake.stage_chat(
        "prj-1", "REQUIREMENTS", None, "add it", {}, "ops", idempotency_key="c:2"
    ) is scripted
    fake.clear_chat("prj-1", "REQUIREMENTS")
    assert ("prj-1", "REQUIREMENTS") in fake.chat_cleared

    assert fake.set_budget("prj-1", 10.0, [0.8], operator="ops").limit == 10.0
    assert fake.get_budget("prj-1").state is BudgetState.OK
    refusals: list[tuple[dict[str, Any], str]] = [
        ({"limit": -1.0}, "a negative limit"),
        ({"thresholds": [1.0]}, "a threshold that can never warn"),
        ({"currency": "Dollars"}, "a currency that is not three letters"),
    ]
    for change, why in refusals:
        with pytest.raises(BudgetInvalid) as refused:
            fake.set_budget(
                "prj-2",
                change.get("limit", 10.0),
                change.get("thresholds", [0.8]),
                currency=change.get("currency", "USD"),
                operator="ops",
            )
        assert refused.value.error_code == "AI_BUDGET_INVALID", why


def test_armed_errors_propagate(fake: FakeIntelligenceApi) -> None:
    fake.raises["start_agent_run"] = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        fake.start_agent_run(_request())
    fake.raises["parse_input"] = FeatureNotEnabled
    with pytest.raises(FeatureNotEnabled):
        fake.parse_input("prj-1", "art-1", operator="ops", idempotency_key="parse:9")


def test_explain_answers_with_an_artifact_id_or_nothing(fake: FakeIntelligenceApi) -> None:
    explained = fake.explain_issues(
        "prj-1", "art-report", environment_policies={}, operator="ops"
    )
    assert isinstance(explained, str)
    fake.explain_nothing = True
    assert (
        fake.explain_issues("prj-1", "art-report", environment_policies={}, operator="ops")
        is None
    )


def test_reset_forgets_script_and_recordings(fake: FakeIntelligenceApi) -> None:
    fake.queue_run(AgentRunResult())
    fake.governance("model.list", {})
    fake.reset()
    assert fake.calls == [] and fake.governance_log == [] and fake.run_results == []


def test_delete_project_data_forgets_the_project(fake: FakeIntelligenceApi) -> None:
    run = fake.start_agent_run(_request())
    fake.delete_project_data("prj-1")
    assert fake.deleted_projects == ["prj-1"]
    assert fake.get_agent_run(run.run_id or "") is None
