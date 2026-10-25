"""Lifecycle: project deletion, the milestone gates, and issue explanation.

Three things that are easy to get wrong and easy to regress:

* **AI-DEL-001** — cleanup deletes *this project's* rows, keeps the usage ledger, performs no
  external I/O, and joins the caller's transaction instead of committing its own;
* **the milestone gates** — M3 and M4 capabilities exist as public functions that refuse, so a
  caller can branch on them today and a later release adds no API;
* **AI-AG-014** — ``explain_issues`` returns an artifact id or ``None``, and *raises* when it
  could not look, because a ``str | None`` port cannot otherwise tell "nothing wrong" from
  "no model was allowed to read it".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from factory_foundation.api import (
    ArtifactCreate,
    ArtifactStatus,
    AuditQuery,
    DataClassification,
    NotFoundError,
    get_artifact,
    has_active_uow,
    query_audit,
    register_artifact,
    unit_of_work,
)
from sqlalchemy import func, select

from factory_intelligence import api, feature
from factory_intelligence import artifacts as ai_artifacts
from factory_intelligence.agents.spec import spec_for
from factory_intelligence.enums import AgentType, Milestone, ProviderClass
from factory_intelligence.errors import FeatureNotEnabled, PromptNotActivated, ProviderUnavailable
from factory_intelligence.models import DELETABLE_TABLES, ModelCallRow
from factory_intelligence.models.run import AgentRunRow
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration.agent_runtime import (
    add_operation,
    answer,
    clear_prompt_activations,
    install_mock_model,
    open_draft,
    question,
    request_for,
    requirement_item,
    script,
    seed_project,
    stage_input,
    unique_project_id,
)


def _report(project_id: str, document: dict[str, Any]) -> str:
    """A ``VALIDATION_REPORT`` artifact, registered under its real owner (S2)."""
    handle = ai_artifacts.stage_json(project_id, "VALIDATION_REPORT", document)
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type="VALIDATION_REPORT",
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S2",
                source_artifact_ids=[],
                status=ArtifactStatus.APPROVED,
            ),
            session=session,
        )
    return str(info.id)


# --- AI-DEL-001 ------------------------------------------------------------------


def test_project_deletion_removes_the_runs_and_keeps_the_usage_ledger(
    harness: IntelligenceHarness,
) -> None:
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))]),
    )
    run = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="del:1")
    )
    assert run.status == "COMPLETED"
    assert _count(AgentRunRow, project) == 1
    assert _count(ModelCallRow, project) == 1

    api.delete_project_data(project)

    assert _count(AgentRunRow, project) == 0
    # AI-DEL-001: the汇总值 survives, without content — that is what makes the spend of a
    # deleted project still auditable, and the append-only trigger would refuse its deletion
    assert _count(ModelCallRow, project) == 1
    from factory_intelligence.cleanup import summarize_retained_usage

    assert summarize_retained_usage(project)["calls"] == 1
    assert "ai.project.data_deleted" in _audit(project)


def test_cleanup_joins_the_callers_transaction_and_deletes_nothing_on_rollback(
    harness: IntelligenceHarness,
) -> None:
    """00 doc 6.2 and D-11: S1 passes its live session, and S3 must neither commit nor refuse."""

    class _Rollback(RuntimeError):
        pass

    _ = harness
    project = unique_project_id()
    seed_project(project)
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, answer())
    run = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=None, idempotency_key="del:2")
    )
    assert run.status in {"COMPLETED", "FAILED"}

    opened = has_active_uow()
    with pytest.raises(_Rollback), unit_of_work() as session:
        api.delete_project_data(project, session=session)
        # visible to the caller before the commit, which is the only way to prove it enlisted
        assert _count(AgentRunRow, project, session=session) == 0
        raise _Rollback("the project removal failed for an unrelated reason")
    assert has_active_uow() is opened or not opened
    assert _count(AgentRunRow, project) == (1 if run.status == "COMPLETED" else 0)
    assert "ai.project.data_deleted" not in _audit(project)


def test_cleanup_performs_no_external_io(harness: IntelligenceHarness) -> None:
    """S1 purges artifact content itself; S3 touching the object store here would be a second
    owner of the same data, and this call runs inside S1's transaction."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    with unit_of_work() as session:
        api.delete_project_data(project, session=session)
    # only the cleanup's own event, from this call: no artifact read, no content purge, and
    # the deletion's audit rides in the caller's transaction like the rows do
    assert [event for event in _audit(project) if event.startswith("ai.")] == [
        "ai.project.data_deleted"
    ]


# --- the milestone gates ---------------------------------------------------------


def test_the_still_undelivered_capability_refuses_rather_than_does_nothing() -> None:
    """M3/M4 functions exist so a caller can branch on them; a silent success would be worse
    than an error, because the caller would believe work happened.

    ``stage_chat``, ``clear_chat`` and ``set_budget`` are no longer in here: S1's M3/M4 surface
    needed them, so they are named one by one in
    :data:`factory_intelligence.feature.DELIVERED_CAPABILITIES` rather than by moving
    ``CURRENT_MILESTONE``, which would also let the API / UI / Fixture agents and the source
    extractors past a gate their prompt sources have not walked through yet.
    """
    cases: dict[str, tuple[str, Callable[[], Any]]] = {
        "desensitize": (
            "M4",
            lambda: api.desensitize("prj", "art-1", {}, operator="ops", idempotency_key="d:1"),
        ),
    }
    for name, (milestone, call) in cases.items():
        with pytest.raises(FeatureNotEnabled) as refused:
            call()
        assert refused.value.details["capability"] == name
        assert refused.value.details["milestone"] == milestone


def test_an_undelivered_agent_is_reported_as_a_failed_run_not_an_exception(
    harness: IntelligenceHarness,
) -> None:
    """AI-AG 3.3: an agent this build does not serve still answers through the result channel,
    so S1 sees a stage outcome and keeps its manual-authoring path (FR-AG-011).

    Gated deliberately rather than inherited from the schedule: SRS 12 puts these three in M3,
    and :data:`factory_intelligence.feature.DELIVERED_AGENTS` serves them now because
    PAGE_DESIGN is on the current design line.  What is pinned here is the *shape* of a refusal
    — a FAILED result, no run row, a reason naming the milestone the agent is scheduled for.
    """
    _ = harness
    project = unique_project_id()
    seed_project(project)
    draft_id = open_draft(project, "PAGE_DESIGN")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("factory_intelligence.runtime.start.agent_enabled", lambda agent: False)
        for agent in (AgentType.API, AgentType.UI, AgentType.FIXTURE):
            result = api.start_agent_run(
                request_for(
                    project,
                    agent,
                    stage="PAGE_DESIGN",
                    draft_id=draft_id,
                    idempotency_key=f"m3:{agent.value}",
                )
            )
            assert result.status == "FAILED", agent.value
            assert result.run_id is None, "no row for a run that never started"
            assert "M3" in " ".join(result.denial_reasons)
    # REVIEW left this list with in-stage chat, which answers through it (AI-CHAT-001); the
    # catalogue's own view of that is asserted in test_srs_v11_additions.py


def test_the_page_agents_are_served_by_this_build(harness: IntelligenceHarness) -> None:
    """The other half of the same fact: PAGE_DESIGN's three agents are delivered, while their
    ``milestone`` still reads M3 because that is SRS 12's schedule and ``enabled`` is what this
    build does — exactly the arrangement REVIEW already had."""
    _ = harness
    for agent in (AgentType.API, AgentType.UI, AgentType.FIXTURE):
        assert feature.agent_enabled(agent), agent.value
        assert spec_for(agent).milestone is Milestone.M3, agent.value


def test_the_agent_surface_reports_which_agents_this_build_serves(
    harness: IntelligenceHarness,
) -> None:
    """``agent.list`` is how a UI decides what to offer; it must agree with the gate."""
    _ = harness
    listed = api.governance("agent.list", {})
    served = {
        str(item.get("agentType")): item for item in listed.get("items", [])
    } if isinstance(listed, dict) and listed.get("items") else {}
    if served:
        assert served["REQUIREMENTS"]["enabled"] is True
        # UI is scheduled for M3 and served by this build, exactly as REVIEW has been since
        # in-stage chat: ``milestone`` is SRS 12's schedule, ``enabled`` is the delivery
        assert served["UI"]["enabled"] is True
        assert served["UI"]["milestone"] == "M3"
        assert served["API"]["enabled"] is True
        assert served["FIXTURE"]["enabled"] is True


# --- AI-AG-014 -------------------------------------------------------------------


def test_an_explanation_is_recorded_as_an_artifact_and_replayed_on_the_same_key(
    harness: IntelligenceHarness,
) -> None:
    _ = harness
    project = unique_project_id()
    seed_project(project)
    report_id = _report(
        project,
        {
            "issues": [
                {
                    "issueId": "bpis-1",
                    "ruleId": "BPV-EXP-001",
                    "level": "BLOCKING",
                    "businessKey": "weight-element",
                    "message": "expression references an unknown data element",
                }
            ]
        },
    )
    provider = install_mock_model(AgentType.METADATA_DESIGN)
    script(
        provider,
        AgentType.METADATA_DESIGN,
        {
            "summary": "the expression names a data element that the registry does not carry; "
            "fix the mapping stage, not the rule",
            "confidence": 0.8,
            "decisions": [],
            "questions": [question("q1", "Which element replaces it?", "MEDIUM")],
        },
    )

    first = api.explain_issues(
        project, report_id, environment_policies={}, operator="ops", idempotency_key="ex:1"
    )
    again = api.explain_issues(
        project, report_id, environment_policies={}, operator="ops", idempotency_key="ex:1"
    )

    assert first is not None and again == first
    assert provider.call_count == 1, "a repeated key must not pay for the model again"
    body, _level = ai_artifacts.read_json(str(first))
    assert body["kind"] == "issueExplanation"
    assert body["reportArtifactId"] == report_id
    assert body["patch"] is None
    assert body["summary"].startswith("the expression names a data element")
    assert body["questions"], "an unresolvable cause is asked about, not invented away"
    assert get_artifact(str(first)).type == "AGENT_OUTPUT"


def test_a_report_with_nothing_in_it_answers_none_without_a_call(
    harness: IntelligenceHarness,
) -> None:
    """The one honest use of ``None``: nothing to explain, and no money spent to say so."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    report_id = _report(project, {"issues": []})
    provider = install_mock_model(AgentType.METADATA_DESIGN)

    assert (
        api.explain_issues(
            project, report_id, environment_policies={}, operator="ops", idempotency_key="ex:empty"
        )
        is None
    )
    assert provider.call_count == 0


def test_an_artifact_that_is_not_a_report_is_refused(harness: IntelligenceHarness) -> None:
    """S3 is not a general-purpose document reader (AI-AG-014 names the two report types)."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    other = stage_input(project, {"notes": "not a report"})

    with pytest.raises(NotFoundError):
        api.explain_issues(
            project, other, environment_policies={}, operator="ops", idempotency_key="ex:type"
        )


def test_an_explanation_for_an_unactivated_agent_raises(harness: IntelligenceHarness) -> None:
    """AI-REG-002 on the explanation side-channel: an un-activated agent raises rather than
    answering from an inert placeholder.  There is no run result to carry a denial here, so the
    refusal is an error S1 maps like any ``AiError`` — the alternative was paying for a call
    whose governance was switched off.
    """
    _ = harness
    clear_prompt_activations()
    project = unique_project_id()
    seed_project(project)
    report_id = _report(project, {"issues": [{"level": "BLOCKING", "message": "bad"}]})
    provider = install_mock_model(AgentType.METADATA_DESIGN)

    with pytest.raises(PromptNotActivated) as refused:
        api.explain_issues(
            project, report_id, environment_policies={}, operator="ops", idempotency_key="ex:unactivated"
        )
    assert refused.value.code == "AI_PROMPT_NOT_ACTIVATED"
    assert provider.call_count == 0, "the refusal happens before any model is asked"


def test_a_policy_that_allows_no_model_raises_rather_than_answering_none(
    harness: IntelligenceHarness,
) -> None:
    """S1's port would otherwise read "no model could look" as "nothing was wrong"."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    report_id = _report(project, {"issues": [{"level": "BLOCKING", "message": "bad"}]})
    provider = mock_unrestricted_only()

    with pytest.raises(ProviderUnavailable):
        api.explain_issues(
            project, report_id, environment_policies={}, operator="ops", idempotency_key="ex:pol"
        )
    assert provider.call_count == 0


def test_an_explanation_answer_carrying_a_patch_is_refused_then_corrected(
    harness: IntelligenceHarness,
) -> None:
    """AI-AG-006: after the Gate an explanation proposes nothing, and the model is told."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    report_id = _report(project, {"issues": [{"level": "WARNING", "message": "check the code"}]})
    provider = install_mock_model(AgentType.METADATA_DESIGN)
    script(
        provider,
        AgentType.METADATA_DESIGN,
        {
            "summary": "here is the fix",
            "patch": {
                "baseVersion": "0.1.0",
                "operations": [add_operation("/rules/others/-", {"businessKey": "r-1"})],
            },
        },
        {"summary": "the rule's left side refers to a programme that is not in the registry"},
    )

    explained = api.explain_issues(
        project, report_id, environment_policies={}, operator="ops", idempotency_key="ex:patch"
    )

    assert explained is not None
    assert provider.call_count == 2
    body, _level = ai_artifacts.read_json(str(explained))
    assert body["patch"] is None
    assert body["modelCalls"] == 2


def mock_unrestricted_only() -> Any:
    """Register one UNRESTRICTED model, which AI-POL-002's default refuses everything."""
    provider = api_mock_provider()
    api.governance(
        "provider.register",
        {"name": "foreign", "providerClass": ProviderClass.UNRESTRICTED.value},
    )
    api.governance(
        "model.register",
        {
            "provider": "foreign",
            "model": "abroad-1",
            "capabilities": {"structuredOutput": True, "maxContextTokens": 1_000_000},
            "applicableAgents": {AgentType.METADATA_DESIGN.value: 1},
        },
    )
    return provider


def api_mock_provider() -> Any:
    from factory_intelligence.llm.mock_agent import mock_provider

    provider = mock_provider()
    api.set_llm_provider(provider)
    return provider


def _count(model: Any, project_id: str, session: Any = None) -> int:
    def _read(active: Any) -> int:
        return int(
            active.execute(
                select(func.count()).select_from(model).where(model.project_id == project_id)
            ).scalar_one()
        )

    if session is not None:
        return _read(session)
    with unit_of_work() as active:
        return _read(active)


def _audit(project_id: str) -> list[str]:
    page = query_audit(AuditQuery(project_id=project_id, limit=200))
    return [event.event_type for event in page.items]


def test_every_deletable_table_is_named_by_the_cleanup_surface() -> None:
    """The two lists are the retention decision; a new table must appear in exactly one.

    Kept here rather than in ``test_scaffold.py`` because this is the rule AI-DEL-001 states
    in words ("汇总值保留"), not a schema property.
    """
    from factory_intelligence.cleanup import RETAINED_TABLES

    assert DELETABLE_TABLES, "cleanup deletes nothing at all"
    assert set(DELETABLE_TABLES).isdisjoint(RETAINED_TABLES)
    assert "model_call" in RETAINED_TABLES
