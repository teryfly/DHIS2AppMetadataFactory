"""The usage ledger and the budget it feeds (AI-COST-001, AI-COST-002, AI-COST-003, AI-NFR-03).

Cost rows here come from **real runs** through the mock provider wherever a run can produce
them, because the requirement is about what the runtime leaves behind, not about what a
hand-made row can be made to say.  ``usage.record_call`` is called directly only where a run
cannot reach the state at all: a second provider and model for one project, a NULL workflow,
an exact token count to check the arithmetic against.

Three claims the suite would otherwise lose:

* AI-COST-001's rollup groups by *any* subset of the dimensions and refuses an unknown one
  rather than quietly dropping it — a silently ignored dimension turns a bill into a
  different bill;
* the denormalised ``stage`` / ``agent_type`` / ``workflow_id`` on each row are what make the
  rollup answer after the run row is deleted with the project (AI-DEL-001, ``models/call.py``);
* AI-COST-003's judgement is made at run start on cost *already incurred*, so a run that
  lands on its limit finishes and only the next one is refused.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from factory_foundation.api import FactoryError, has_active_uow, unit_of_work
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from factory_intelligence import api
from factory_intelligence.cost import budget as budget_service
from factory_intelligence.cost import usage
from factory_intelligence.cost.usage import ROLLUP_DIMENSIONS, CallContext, record_call
from factory_intelligence.dto.result import AgentRunResult, UsageRollup
from factory_intelligence.enums import AgentType, ProviderClass
from factory_intelligence.llm.registry import RegisteredModel
from factory_intelligence.llm.types import Capabilities, ModelResponse, Usage
from factory_intelligence.models.call import ModelCallRow
from factory_intelligence.models.cost import BudgetRow
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration.agent_runtime import (
    add_operation,
    answer,
    install_mock_model,
    open_draft,
    request_for,
    requirement_item,
    script,
    seed_project,
    unique_project_id,
)

#: the prices ``install_mock_model`` registers the mock model with (AI-REG-004)
MOCK_PRICE_INPUT_PER_1K = 0.001
MOCK_PRICE_OUTPUT_PER_1K = 0.002

#: ``ROLLUP_DIMENSIONS`` names a dimension; ``UsageRollup`` spells the answer differently.
_FIELD_BY_DIMENSION = {
    "project": "project_id",
    "workflow": "workflow_id",
    "stage": "stage",
    "agent": "agent_type",
    "model": "model",
    "provider": "provider",
}
#: the five fields a single-dimension grouping must leave empty in the other rows
_GROUPABLE_FIELDS = ("workflow_id", "stage", "agent_type", "model", "provider")


# --- helpers ---------------------------------------------------------------------


def _rows(project_id: str) -> list[ModelCallRow]:
    with unit_of_work() as session:
        return list(
            session.execute(
                select(ModelCallRow)
                .where(ModelCallRow.project_id == project_id)
                .order_by(ModelCallRow.created_at, ModelCallRow.sequence)
            ).scalars()
        )


def _rollup(project_id: str, group_by: Sequence[str]) -> list[UsageRollup]:
    return api.usage_rollup(project_id, group_by=group_by)


def _budget_row(project_id: str, limit: float | None, thresholds: list[float]) -> None:
    """Write ``ai.budget`` with the ORM rather than through :func:`api.set_budget`.

    These tests are about what the *runtime* does with a limit (AI-COST-002/003), so they hold
    the row fixed instead of also exercising the operator switch the M4 delivery added — which
    has its own tests in ``test_governance.py``.
    """
    with unit_of_work() as session:
        session.add(
            BudgetRow(project_id=project_id, limit=limit, thresholds=thresholds, updated_by="ops")
        )


def _model(
    provider: str = "vendor",
    model: str = "priced-1",
    unit_price_input_per_1k: float = 0.5,
    unit_price_output_per_1k: float = 2.0,
) -> RegisteredModel:
    return RegisteredModel(
        model=model,
        provider=provider,
        provider_id=f"apvd-{provider}",
        provider_class=ProviderClass.APPROVED_EXTERNAL,
        capabilities=Capabilities(structured_output=True, vision=False, max_context_tokens=8000),
        enabled=True,
        unit_price_input_per_1k=unit_price_input_per_1k,
        unit_price_output_per_1k=unit_price_output_per_1k,
        applicable_agents={AgentType.REQUIREMENTS.value: 1},
        endpoint=None,
        api_key_secret_ref=None,
        adapter="openai_compat",
        model_id=f"amdl-{provider}-{model}",
    )


def _record(
    project_id: str | None,
    *,
    model: RegisteredModel | None = None,
    workflow_id: str | None = "wf-1",
    stage: str | None = "REQUIREMENTS",
    agent_type: str | None = AgentType.REQUIREMENTS.value,
    input_tokens: int = 1_000,
    output_tokens: int = 1_000,
    outcome: str = "OK",
    sequence: int = 1,
) -> ModelCallRow:
    """One ledger row written by the function under test, at a chosen token count."""
    response = (
        ModelResponse(
            payload={"answer": "recorded"},
            request_id=f"amcl-request-{sequence}",
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        )
        if outcome == "OK"
        else None
    )
    return record_call(
        context=CallContext(
            project_id=project_id,
            workflow_id=workflow_id,
            stage=stage,
            agent_type=agent_type,
            run_id=None,
            sequence=sequence,
        ),
        model=model or _model(),
        response=response,
        temperature=0.0,
        max_output_tokens=None,
        latency_ms=12,
        outcome=outcome,
        error="provider refused to answer" if outcome == "ERROR" else None,
    )


def _draft(project: str) -> str:
    seed_project(project)
    return open_draft(project, "REQUIREMENTS")


def _finished_run(
    project: str,
    *,
    draft_id: str,
    idempotency_key: str,
    workflow_id: str = "wf-1",
    key: str = "req-record-weight",
) -> AgentRunResult:
    """A completed Requirements run — the smallest real producer of a usage row."""
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(
            operations=[
                add_operation("/requirements/items/-", requirement_item("art-0000000000", key=key))
            ]
        ),
    )
    request = request_for(
        project,
        AgentType.REQUIREMENTS,
        draft_id=draft_id,
        idempotency_key=idempotency_key,
    ).model_copy(update={"workflow_id": workflow_id})
    result = api.start_agent_run(request)
    assert result.status == "COMPLETED", result.denial_reasons
    return result


# --- AI-COST-001: what a run leaves behind ---------------------------------------


def test_a_completed_run_leaves_one_usage_row_with_tokens_and_cost(
    harness: IntelligenceHarness,
) -> None:
    """AI-COST-001 with AI-NFR-03: the call is recorded with both token directions and an
    estimated cost, and the rollup reads exactly that row back."""
    _ = harness
    project = unique_project_id("cost")
    _finished_run(project, draft_id=_draft(project), idempotency_key="cost:one")

    rows = _rows(project)
    assert len(rows) == 1
    row = rows[0]
    assert row.input_tokens > 0 and row.output_tokens > 0
    assert row.estimated_cost == pytest.approx(
        row.input_tokens / 1000.0 * MOCK_PRICE_INPUT_PER_1K
        + row.output_tokens / 1000.0 * MOCK_PRICE_OUTPUT_PER_1K
    )
    assert row.outcome == "OK"
    assert row.error is None
    assert row.provider == "mock" and row.model == "mock-agent"

    items = _rollup(project, ["stage", "agent", "model"])
    assert len(items) == 1
    assert items[0].calls == 1
    assert items[0].failed_calls == 0
    assert items[0].input_tokens == row.input_tokens
    assert items[0].output_tokens == row.output_tokens
    assert items[0].estimated_cost == pytest.approx(row.estimated_cost or 0.0)


def test_the_rollup_keys_outlive_the_run_row(harness: IntelligenceHarness) -> None:
    """AI-COST-001 with AI-DEL-001: ``stage``, ``agent_type`` and ``workflow_id`` are copied
    onto the ledger rather than joined, so the bill still names the stage that spent it after
    the run row is deleted with the project."""
    _ = harness
    project = unique_project_id("cost")
    result = _finished_run(
        project, draft_id=_draft(project), idempotency_key="cost:keys", workflow_id="wf-42"
    )
    assert result.run_id is not None

    api.delete_project_data(project)

    items = _rollup(project, ["workflow", "stage", "agent"])
    assert [(item.workflow_id, item.stage, item.agent_type, item.calls) for item in items] == [
        ("wf-42", "REQUIREMENTS", "REQUIREMENTS", 1)
    ]


def test_a_failed_call_is_a_call_and_a_failure(harness: IntelligenceHarness) -> None:
    """AI-LLM-004 / AI-NFR-03: a provider that was reached and refused to answer is still
    recorded, and the rollup counts it apart instead of losing it."""
    _ = harness
    project = unique_project_id("cost")
    draft_id = _draft(project)
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        {"$error": {"kind": "transport", "status": 503, "message": "busy"}},
        answer(
            operations=[
                add_operation("/requirements/items/-", requirement_item("art-0000000000"))
            ]
        ),
    )
    api.set_sleeper(lambda seconds: None)

    result = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="cost:fail"
        )
    )

    assert result.status == "COMPLETED"
    outcomes = [(row.outcome, row.input_tokens, row.error) for row in _rows(project)]
    assert [item[0] for item in outcomes] == ["ERROR", "OK"]
    # the refused call answered nothing, so it carries no prompt tokens either — but the row
    # exists, with the provider's reason bounded into the ledger (AI-LLM-005: never a repr)
    assert outcomes[0][1] == 0 and "busy" in str(outcomes[0][2])
    assert outcomes[1][1] > 0

    items = _rollup(project, ["stage"])
    assert [(item.calls, item.failed_calls) for item in items] == [(2, 1)]


def test_the_ledger_survives_a_run_that_ends_failed(
    harness: IntelligenceHarness,
) -> None:
    """AI-NFR-03: every refused answer and the ``FAILED`` run still leave their rows behind,
    because each is committed in its own short transaction before the output is judged
    (``cost/usage.py``).  A ledger that rode on the run's final commit would be empty
    precisely when the attempts cost money."""
    _ = harness
    project = unique_project_id("cost")
    draft_id = _draft(project)
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(provider, AgentType.REQUIREMENTS, {"summary": ""}, {"notes": "not an envelope"})

    result = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="cost:dead"
        )
    )

    assert result.status == "FAILED"
    items = _rollup(project, ["agent"])
    assert [(item.agent_type, item.calls, item.failed_calls) for item in items] == [
        ("REQUIREMENTS", 6, 0)
    ]


# --- AI-COST-001: grouping -------------------------------------------------------


def test_the_rollup_groups_two_runs_apart_on_one_dimension_and_together_on_another(
    harness: IntelligenceHarness,
) -> None:
    """AI-COST-001 "可按项目、工作流、阶段、Agent、模型汇总查询": two runs of one project
    under different workflows are two groups by ``workflow`` and one by ``stage``."""
    _ = harness
    project = unique_project_id("cost")
    draft_id = _draft(project)
    _finished_run(project, draft_id=draft_id, idempotency_key="cost:ga", workflow_id="wf-1")
    _finished_run(
        project,
        draft_id=draft_id,
        idempotency_key="cost:gb",
        workflow_id="wf-2",
        key="req-other-weight",
    )

    by_workflow = _rollup(project, ["workflow"])
    assert [item.workflow_id for item in by_workflow] == ["wf-1", "wf-2"]
    assert [item.calls for item in by_workflow] == [1, 1]
    assert all(item.stage is None and item.model is None for item in by_workflow)

    by_stage = _rollup(project, ["stage"])
    assert [(item.stage, item.calls) for item in by_stage] == [("REQUIREMENTS", 2)]

    combined = _rollup(project, ["workflow", "stage"])
    assert [(item.workflow_id, item.stage, item.calls) for item in combined] == [
        ("wf-1", "REQUIREMENTS", 1),
        ("wf-2", "REQUIREMENTS", 1),
    ]


@pytest.mark.parametrize("dimension", list(ROLLUP_DIMENSIONS), ids=list(ROLLUP_DIMENSIONS))
def test_every_declared_dimension_can_be_grouped_on(
    harness: IntelligenceHarness, dimension: str
) -> None:
    """Each member of :data:`ROLLUP_DIMENSIONS` is a real column, and grouping on one answers
    only that one: a rollup that filled in a field it did not group by would read as a
    breakdown it is not (AI-COST-001).  ``project`` cannot split a one-project query, so it is
    the dimension that must answer a single row."""
    _ = harness
    project = unique_project_id("cost")
    _record(project, workflow_id="wf-a", stage="REQUIREMENTS", model=_model("vendor-a", "m-a"))
    _record(
        project,
        workflow_id=None,
        stage="MAPPING",
        agent_type=AgentType.MAPPING.value,
        model=_model("vendor-b", "m-b"),
        sequence=2,
    )

    field = _FIELD_BY_DIMENSION[dimension]
    distinct: dict[str, set[object]] = {
        "project": {project},
        "workflow": {"wf-a", None},
        "stage": {"REQUIREMENTS", "MAPPING"},
        "agent": {"REQUIREMENTS", "MAPPING"},
        "model": {"m-a", "m-b"},
        "provider": {"vendor-a", "vendor-b"},
    }
    items = _rollup(project, [dimension])

    assert len(items) == (1 if dimension == "project" else 2)
    assert {getattr(item, field) for item in items} == distinct[dimension]
    assert sum(item.calls for item in items) == 2
    for item in items:
        assert all(getattr(item, other) is None for other in _GROUPABLE_FIELDS if other != field)
        assert item.project_id == project, "the queried project is echoed, never grouped away"


def test_a_multi_dimension_grouping_keeps_every_key(harness: IntelligenceHarness) -> None:
    """A subset of several dimensions is one row per distinct *combination*, not one row per
    dimension (AI-COST-001)."""
    _ = harness
    project = unique_project_id("cost")
    _record(project, stage="REQUIREMENTS", agent_type=AgentType.REQUIREMENTS.value)
    _record(project, stage="REQUIREMENTS", agent_type=AgentType.MAPPING.value, sequence=2)
    _record(project, stage="MAPPING", agent_type=AgentType.MAPPING.value, sequence=3)

    items = _rollup(project, ["stage", "agent"])
    assert [(item.stage, item.agent_type, item.calls) for item in items] == [
        ("MAPPING", "MAPPING", 1),
        ("REQUIREMENTS", "MAPPING", 1),
        ("REQUIREMENTS", "REQUIREMENTS", 1),
    ]


@pytest.mark.parametrize(
    "dimension",
    ["cost", "outcome", "MODEL", "temperature", "operator", "tokens"],
    ids=["cost", "outcome", "wrong-case", "column-not-a-dimension", "identity", "aggregate"],
)
def test_an_unknown_rollup_dimension_is_refused_not_ignored(
    harness: IntelligenceHarness, dimension: str
) -> None:
    """A dimension silently dropped would return a *different* bill under the caller's feet —
    the totals would still look plausible, which is why this is a refusal and not a warning
    (AI-COST-001)."""
    _ = harness
    project = unique_project_id("cost")
    _record(project)

    for group_by in ([dimension], ["stage", dimension]):
        with pytest.raises(FactoryError) as raised:
            api.usage_rollup(project, group_by=group_by)
        assert dimension in str(raised.value)
        assert "provider" in str(raised.value), "the refusal states what is accepted"
        # AI-GOV-005 / AI-AC-11: a classified FactoryError with the allowed dimensions in
        # its details, never an unclassified exception S1 would have to render as a 500
        assert raised.value.code == "VALIDATION_ERROR"
        assert raised.value.details["allowed"] == list(usage.ROLLUP_DIMENSIONS)
    assert _rollup(project, ["stage"])[0].calls == 1, "the refusal changed nothing"


def test_a_rollup_without_a_project_spans_every_project(
    harness: IntelligenceHarness,
) -> None:
    """SRS 7's own ``project_id=None``: an operator review that is not about one project."""
    _ = harness
    first = unique_project_id("cost")
    second = unique_project_id("cost")
    _record(first)
    _record(second)

    rows = api.usage_rollup(group_by=["project", "stage"])
    seen = {row.project_id for row in rows}
    assert {first, second} <= seen, "both projects answer, each under its own name"
    assert all(row.calls == 1 for row in rows if row.project_id in seen)


def test_an_unknown_dimension_reaches_the_operator_surface_unchanged(
    harness: IntelligenceHarness,
) -> None:
    """S1 forwards the payload verbatim (CP-API-007), so the refusal has to survive the
    governance dispatch rather than be re-parsed into an empty answer."""
    _ = harness
    project = unique_project_id("cost")
    _record(project)

    with pytest.raises(FactoryError) as raised:
        api.governance("usage.rollup", {"projectId": project, "groupBy": ["cost"]})
    assert "cost" in str(raised.value)
    assert raised.value.code == "VALIDATION_ERROR", "the operator surface refuses in kind"


def test_a_project_that_never_called_a_model_has_no_rows(harness: IntelligenceHarness) -> None:
    """The negative path: an empty ledger answers *no groups*, which is not the same statement
    as a group whose cost is zero, and ``total_spent`` is the one place a zero is correct."""
    _ = harness
    project = unique_project_id("cost")
    assert _rollup(project, ["stage", "agent", "model"]) == []
    assert usage.total_spent(project) == 0.0


# --- AI-COST-001 with AI-REG-004: the arithmetic on the ledger --------------------


def test_the_price_per_one_thousand_tokens_lands_in_the_ledger_exactly(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-004's unit prices are per 1k and applied by direction, and AI-COST-001's total is
    the sum of the rows: one call of 1 500 in / 500 out at 0.5 and 2.0 per 1k is 1.75, two of
    them 3.50."""
    _ = harness
    project = unique_project_id("cost")
    _record(project, input_tokens=1_500, output_tokens=500)
    _record(project, input_tokens=1_500, output_tokens=500, sequence=2)

    items = _rollup(project, ["model"])
    assert [(item.calls, item.input_tokens, item.output_tokens) for item in items] == [
        (2, 3_000, 1_000)
    ]
    assert items[0].estimated_cost == pytest.approx(3.5)


def test_an_unpriced_model_costs_zero_and_the_rollup_shows_the_zero(
    harness: IntelligenceHarness,
) -> None:
    """AI-COST-001 with AI-REG-004: a model registered without prices is priced 0.0 by
    absence.  The ledger must not hide that — the row is present, with its tokens, and a cost
    of exactly 0.0, which is what tells an operator the model is unpriced rather than free."""
    _ = harness
    project = unique_project_id("cost")
    _record(project, model=_model(unit_price_input_per_1k=0.0, unit_price_output_per_1k=0.0))

    rows = _rows(project)
    assert len(rows) == 1
    assert rows[0].input_tokens > 0 and rows[0].output_tokens > 0
    assert rows[0].estimated_cost == 0.0

    items = _rollup(project, ["model", "provider"])
    assert [(item.calls, item.estimated_cost, item.input_tokens > 0) for item in items] == [
        (1, 0.0, True)
    ]


def test_a_call_that_belongs_to_no_project_is_never_billed_to_one(
    harness: IntelligenceHarness,
) -> None:
    """AI-NFR-03 books an evaluation's calls in this same ledger; AI-COST-001 must not let them
    into a project's bill, and AI-COST-002 must not let them trip its budget."""
    _ = harness
    project = unique_project_id("cost")
    _record(project, input_tokens=1_000, output_tokens=1_000)
    _record(
        None,
        workflow_id="eval:aeval-offline",
        stage=None,
        agent_type=None,
        input_tokens=9_000,
        output_tokens=9_000,
    )

    assert [row.project_id for row in _rows(project)] == [project]
    items = _rollup(project, ["model"])
    assert [(item.calls, item.input_tokens) for item in items] == [(1, 1_000)]
    assert usage.total_spent(project) == pytest.approx(_model().price_for(1_000, 1_000))

    with unit_of_work() as session:
        unowned = session.execute(
            select(func.count(ModelCallRow.id)).where(ModelCallRow.project_id.is_(None))
        ).scalar_one()
    assert unowned == 1, "the call is recorded — it simply answers to no project"


# --- the ledger is evidence ------------------------------------------------------


def test_a_usage_row_can_be_read_but_never_edited_or_deleted(
    harness: IntelligenceHarness,
) -> None:
    """``ai_model_call_guard`` (migration 0001): a usage row is evidence for AI-NFR-03's 100%
    and the basis of AI-COST-001's bill, so UPDATE and DELETE fail in the database rather than
    by application discipline — while a SELECT, and the rollup built on it, still work."""
    _ = harness
    project = unique_project_id("cost")
    _record(project, input_tokens=1_500, output_tokens=500)
    row_id = _rows(project)[0].id
    assert _rollup(project, ["model"])[0].calls == 1

    with pytest.raises(SQLAlchemyError) as edited, unit_of_work() as session:
        session.execute(
            text(
                "update ai.model_call set estimated_cost = 0, input_tokens = 1 where id = :id"
            ),
            {"id": row_id},
        )
    assert "immutable" in str(edited.value), "it is the guard that refused, not the SQL"
    with pytest.raises(SQLAlchemyError) as deleted, unit_of_work() as session:
        session.execute(text("delete from ai.model_call where id = :id"), {"id": row_id})
    assert "immutable" in str(deleted.value)

    assert _rows(project)[0].input_tokens == 1_500
    assert _rollup(project, ["model"])[0].estimated_cost == pytest.approx(1.75)


# --- AI-COST-002 / AI-COST-003: the budget as the ledger sees it -----------------


def test_a_project_without_a_budget_row_is_not_given_a_zero_budget(
    harness: IntelligenceHarness,
) -> None:
    """AI-COST-002: the absence of a row is *no statement*, not a limit of 0 — reading it as a
    zero would refuse every run of every project nobody has configured yet."""
    _ = harness
    project = unique_project_id("cost")
    view = budget_service.view(project)
    assert view.state.value == "UNSET"
    assert view.limit is None
    assert view.spent == 0.0
    assert budget_service.may_start_run(view) is True

    measured = budget_service.view(project, spent=99.0)
    assert measured.state.value == "UNSET", "a spend cannot invent a limit"
    assert measured.spent == 99.0


def test_a_limit_of_none_is_an_explicit_statement_of_unlimited(
    harness: IntelligenceHarness,
) -> None:
    """A row whose ``limit`` is null is a decision somebody recorded (``models/cost.py``): it
    answers OK rather than UNSET, and still reports what has been spent."""
    _ = harness
    project = unique_project_id("cost")
    _finished_run(project, draft_id=_draft(project), idempotency_key="cost:unlimited")
    _budget_row(project, None, [0.8])

    view = budget_service.view(project)
    assert view.state.value == "OK"
    assert view.limit is None
    assert view.currency == "USD"
    assert view.spent == pytest.approx(usage.total_spent(project))
    assert view.spent > 0
    assert budget_service.may_start_run(view) is True


def test_a_run_that_lands_on_its_limit_finishes_and_the_next_is_refused(
    harness: IntelligenceHarness,
) -> None:
    """AI-COST-003 exactly: the judgement is made at run start on cost already incurred, and
    ``spent == limit`` is *not* over the limit.  A run that crosses mid-flight completes rather
    than leaving the draft half-patched; only the run after it is refused."""
    _ = harness
    project = unique_project_id("cost")
    draft_id = _draft(project)
    _finished_run(project, draft_id=draft_id, idempotency_key="cost:to-limit")
    spent = usage.total_spent(project)
    assert spent > 0
    _budget_row(project, spent, [0.8])

    at_limit = budget_service.view(project)
    assert at_limit.state.value == "WARNING", "reaching a limit is an alert, not a refusal"
    assert at_limit.threshold_reached == 0.8
    assert budget_service.may_start_run(at_limit) is True

    crossing = _finished_run(
        project, draft_id=draft_id, idempotency_key="cost:crossing", key="req-second-weight"
    )
    assert crossing.status == "COMPLETED", crossing.denial_reasons
    assert crossing.budget_status["state"] == "EXCEEDED"
    assert usage.total_spent(project) > spent

    refused = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="cost:third"
        )
    )
    assert refused.status == "BUDGET_EXCEEDED"
    assert refused.budget_status["limit"] == pytest.approx(spent)


def test_an_over_budget_refusal_costs_nothing(harness: IntelligenceHarness) -> None:
    """AI-COST-002: the refusal happens before the provider is reached, so a project already
    over its limit does not accumulate the debt it was refused for."""
    _ = harness
    project = unique_project_id("cost")
    draft_id = _draft(project)
    _finished_run(project, draft_id=draft_id, idempotency_key="cost:before")
    _budget_row(project, 0.0, [0.8])

    refused = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="cost:refused"
        )
    )

    assert refused.status == "BUDGET_EXCEEDED"
    assert len(_rows(project)) == 1, "no new usage row for a run that never reached a model"
    assert "exceeded" in " ".join(refused.denial_reasons).lower()


def test_the_budget_view_is_reported_through_the_operator_surface(
    harness: IntelligenceHarness,
) -> None:
    """AI-COST-002's reporting half over CP-API-007: ``budget.get`` answers with the run
    result's own DTO, so its keys are the DTO's snake_case — and a project nobody configured
    is UNSET rather than an error or an empty object."""
    _ = harness
    project = unique_project_id("cost")
    unset = api.governance("budget.get", {"projectId": project})
    assert unset["state"] == "UNSET"
    assert unset["limit"] is None

    _finished_run(project, draft_id=_draft(project), idempotency_key="cost:reported")
    _budget_row(project, 0.000_001, [0.5, 0.9])
    reported = api.governance("budget.get", {"project_id": project})
    assert reported["state"] == "EXCEEDED"
    assert reported["spent"] == pytest.approx(usage.total_spent(project))
    assert reported["currency"] == "USD"
    assert reported["threshold_reached"] is None, "an exceeded budget reports no threshold"


def test_reading_the_ledger_leaves_no_transaction_open(harness: IntelligenceHarness) -> None:
    """00 doc 6.2: a read of the ledger commits nothing and leaves no transaction behind for
    the caller's next statement."""
    _ = harness
    project = unique_project_id("cost")
    _record(project)

    api.usage_rollup(project)
    usage.total_spent(project)
    budget_service.view(project)
    assert not has_active_uow()
