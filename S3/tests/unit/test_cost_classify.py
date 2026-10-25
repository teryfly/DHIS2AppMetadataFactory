"""The arithmetic behind a budget verdict and a cost line, with no database in sight
(AI-COST-002, AI-COST-003, AI-REG-004, AI-COST-001).

``classify`` is the whole of AI-COST-002's judgement and :meth:`RegisteredModel.price_for`
the whole of AI-COST-001's arithmetic, so both belong in a file where a boundary is a number
rather than a row:

* AI-COST-003 refuses only a *new* run, and only when the spend is **over** the limit — a run
  that lands exactly on its limit has finished honestly, so cutting it off would leave the
  draft half-patched, which is the outcome the requirement names as worse;
* a threshold of 0 warns on every call and a threshold of 1 can never warn before the limit
  is already passed, so both are ignored rather than honoured literally;
* AI-REG-004's prices are **per one thousand tokens**, split by direction, so a model nobody
  priced costs exactly 0.0 — a real zero the rollup can show, not a missing number.
"""

from __future__ import annotations

import pytest

from factory_intelligence.cost.budget import classify, may_start_run
from factory_intelligence.dto.result import BudgetView
from factory_intelligence.enums import BudgetState, ProviderClass
from factory_intelligence.llm.registry import RegisteredModel
from factory_intelligence.llm.types import Capabilities


def _model(
    unit_price_input_per_1k: float = 0.0, unit_price_output_per_1k: float = 0.0
) -> RegisteredModel:
    """A registry row as the policy engine and the ledger see it, without a session."""
    return RegisteredModel(
        model="priced-1",
        provider="vendor",
        provider_id="apvd-1",
        provider_class=ProviderClass.APPROVED_EXTERNAL,
        capabilities=Capabilities(structured_output=True, vision=False, max_context_tokens=8000),
        enabled=True,
        unit_price_input_per_1k=unit_price_input_per_1k,
        unit_price_output_per_1k=unit_price_output_per_1k,
        applicable_agents={"REQUIREMENTS": 1},
        endpoint=None,
        api_key_secret_ref=None,
        adapter="openai_compat",
        model_id="amdl-1",
    )


# --- AI-COST-002 / AI-COST-003: the state machine ----------------------------------


@pytest.mark.parametrize(
    ("spent", "limit", "thresholds", "expected"),
    [
        (0.0, 10.0, [0.8], (BudgetState.OK, None)),
        (5.0, 10.0, [], (BudgetState.OK, None)),
        (7.999, 10.0, [0.8], (BudgetState.OK, None)),
        (8.0, 10.0, [0.8], (BudgetState.WARNING, 0.8)),
        (9.999, 10.0, [0.5, 0.8], (BudgetState.WARNING, 0.8)),
        (10.0, 10.0, [0.5, 0.8], (BudgetState.WARNING, 0.8)),
        (10.0, 10.0, [], (BudgetState.OK, None)),
        (10.001, 10.0, [0.5, 0.8], (BudgetState.EXCEEDED, None)),
        (1_000.0, 10.0, [0.9], (BudgetState.EXCEEDED, None)),
        (0.0, 0.0, [0.8], (BudgetState.WARNING, 0.8)),
        (0.001, 0.0, [0.8], (BudgetState.EXCEEDED, None)),
        (5.0, 10.0, [0.0], (BudgetState.OK, None)),
        (10.0, 10.0, [1.0], (BudgetState.OK, None)),
        (5.0, 10.0, [0, 1], (BudgetState.OK, None)),
        (5.0, 10.0, [-0.5], (BudgetState.OK, None)),
        (10.0, 10.0, [1.5], (BudgetState.OK, None)),
        (8.5, 10.0, [0.9, 0.5, 0.8], (BudgetState.WARNING, 0.8)),
        (9.5, 10.0, [0.9, 0.5, 0.8], (BudgetState.WARNING, 0.9)),
    ],
    ids=[
        "nothing-spent",
        "no-thresholds-below-limit",
        "just-under-the-threshold",
        "exactly-on-the-threshold",
        "highest-crossed-wins",
        "at-the-limit-is-not-over-it",
        "at-the-limit-without-thresholds",
        "a-hair-over-the-limit",
        "far-over-the-limit",
        "zero-limit-zero-spend",
        "zero-limit-anything-is-over",
        "threshold-zero-ignored",
        "threshold-one-ignored",
        "thresholds-from-jsonb-as-integers",
        "negative-threshold-ignored",
        "threshold-above-the-limit-ignored",
        "unsorted-thresholds-mid",
        "unsorted-thresholds-high",
    ],
)
def test_the_budget_state_is_decided_by_the_numbers_alone(
    spent: float,
    limit: float,
    thresholds: list[float],
    expected: tuple[BudgetState, float | None],
) -> None:
    assert classify(spent, limit, thresholds) == expected


def test_a_limit_that_exists_is_never_answered_unset() -> None:
    """``UNSET`` is the absence of a row (AI-COST-002), not a result of the arithmetic: a
    caller that read the row must get one of the three states the operator can act on."""
    for spent in (0.0, 1.0, 8.0, 10.0, 10.5):
        for thresholds in ([], [0.5, 0.8], [0.0, 1.0]):
            assert classify(spent, 10.0, thresholds)[0] is not BudgetState.UNSET


# --- AI-COST-002: which states stop a new run -------------------------------------


@pytest.mark.parametrize(
    ("state", "startable"),
    [
        (BudgetState.UNSET, True),
        (BudgetState.OK, True),
        (BudgetState.WARNING, True),
        (BudgetState.EXCEEDED, False),
    ],
    ids=["unset", "ok", "warning", "exceeded"],
)
def test_only_over_budget_refuses_a_new_run(state: BudgetState, startable: bool) -> None:
    """AI-COST-002's alert half: a crossed threshold is *reported* in the run result and
    never interrupts, because the alternative is the half-finished draft AI-COST-003
    refuses.  The answer is a bool, not an exception — raising here would give the runtime
    no way to report the budget it is refusing."""
    view = BudgetView(state=state, spent=12.0, limit=10.0, currency="USD", threshold_reached=0.8)
    assert may_start_run(view) is startable


# --- AI-REG-004 / AI-COST-001: the price of one call ------------------------------


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "expected"),
    [
        (1_000, 1_000, 2.5),
        (500, 250, 0.75),
        (1, 0, 0.0005),
        (0, 1, 0.002),
        (0, 0, 0.0),
        (100, 0, 0.05),
        (2_000, 0, 1.0),
        (0, 2_000, 4.0),
        (12_345, 6_789, 6.1725 + 13.578),
    ],
    ids=[
        "one-thousand-each",
        "half-a-thousand-in-a-quarter-out",
        "single-input-token",
        "single-output-token",
        "empty-call",
        "hundred-tokens",
        "input-only",
        "output-only",
        "odd-counts",
    ],
)
def test_the_price_is_stated_per_one_thousand_tokens_in_each_direction(
    input_tokens: int, output_tokens: int, expected: float
) -> None:
    """AI-REG-004's "单价（输入 / 输出每千 token）": the two directions are priced apart, so
    an answer-heavy call and a prompt-heavy call of the same length cost differently."""
    priced = _model(unit_price_input_per_1k=0.5, unit_price_output_per_1k=2.0)
    assert priced.price_for(input_tokens, output_tokens) == pytest.approx(expected)


def test_an_unpriced_model_costs_exactly_nothing() -> None:
    """A model registered without prices is priced 0.0 by absence (AI-REG-004's defaults),
    which is a *real* zero rather than ``None``: the ledger column is nullable, so an absent
    number would make the rollup's total a guess instead of a sum a caller can see through."""
    unpriced = _model()
    assert unpriced.price_for(10_000, 10_000) == 0.0
    assert unpriced.price_for(0, 0) == 0.0


def test_a_price_only_on_one_direction_is_not_applied_to_the_other() -> None:
    """The two unit prices are independent columns; reading one as the other would bill the
    cheaper direction for the dearer tokens (AI-COST-001)."""
    input_only = _model(unit_price_input_per_1k=1.0)
    output_only = _model(unit_price_output_per_1k=1.0)
    assert input_only.price_for(1_000, 1_000) == pytest.approx(1.0)
    assert output_only.price_for(1_000, 1_000) == pytest.approx(1.0)
    assert input_only.price_for(1_000, 0) == pytest.approx(1.0)
    assert input_only.price_for(0, 1_000) == 0.0
