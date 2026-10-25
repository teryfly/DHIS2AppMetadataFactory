"""AI-NFR-01: the policy judgement on a hundred context items stays under 50 ms at P95.

SRS 9 sets the number; :mod:`tests.unit.test_policy_purity` keeps the *reason* it is met
(the engine is a pure pass over plain data, so the cost is a hundred set lookups and
nothing else).  This file is the observable half: it measures per call and refuses to
accept a mean — a policy check sits on the critical path of every model call, so the tail
is what an operator feels.

Run it explicitly, like S2's performance smoke tests::

    FACTORY_PERF=1 .venv/bin/pytest -q -m perf tests/perf

The budget is deliberately generous next to the real cost (single-digit microseconds per
item), which keeps the guard meaningful without making it weather-dependent: what it
catches is a change that puts an I/O call, a copy or a quadratic scan into ``evaluate``.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Sequence
from typing import TypeVar

import pytest
from factory_foundation.api import DataClassification

from factory_intelligence.enums import ItemSourceKind, ProviderClass
from factory_intelligence.policy.engine import evaluate
from factory_intelligence.policy.model import ContextItem, EffectivePolicy, Verdict

_T = TypeVar("_T")

#: AI-NFR-01's two numbers, taken from the requirement row
ITEMS = 100
P95_BUDGET_MS = 50.0

#: enough samples that "95th percentile" is a percentile and not a guess
RUNS = 1_000

SKIP_REASON = "set FACTORY_PERF=1 to run the performance smoke tests"

#: the marker is registered in ``pyproject.toml`` and the environment guard is S2's
#: convention, so the default suite skips this file and ``-m perf`` with ``FACTORY_PERF=1``
#: is what runs it
pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(os.environ.get("FACTORY_PERF") != "1", reason=SKIP_REASON),
]


def _context(level: DataClassification = DataClassification.INTERNAL) -> list[ContextItem]:
    """A context of exactly :data:`ITEMS` items, each with a distinct reference and a
    distinct source kind, as a real MAPPING run's context looks."""
    kinds = list(ItemSourceKind)
    return [
        ContextItem(
            ref=f"artifact:item-{index}",
            classification=level,
            kind=kinds[index % len(kinds)],
            label=f"input {index}",
            size_bytes=1024 + index,
            entry_key=None if index % 3 else f"dataElements/field-{index}",
        )
        for index in range(ITEMS)
    ]


def _mixed_context() -> list[ContextItem]:
    """The shape that costs the engine most: a fifth of every classification, so four
    fifths of the items are refused and reported."""
    levels = list(DataClassification)
    return [
        ContextItem(ref=f"artifact:mixed-{index}", classification=levels[index % len(levels)])
        for index in range(ITEMS)
    ]


def _p95(samples: Sequence[float]) -> float:
    """Nearest-rank percentile, computed here rather than imported: no numpy in the test
    dependencies, and no mean pretending to describe a tail."""
    assert samples, "nothing was measured"
    ordered = sorted(samples)
    rank = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[rank]


def _measure(run: Callable[[], _T]) -> tuple[list[float], _T]:
    """Per-call durations in seconds, and one result to assert the work was the work
    intended.  The untimed call up front keeps a cold-cache first iteration out of the
    samples."""
    result = run()
    durations: list[float] = []
    for _ in range(RUNS):
        started = time.perf_counter()
        result = run()
        durations.append(time.perf_counter() - started)
    assert len(durations) >= 1_000, "AI-NFR-01 asks for a percentile over a thousand calls"
    return durations, result


def _assert_within_budget(label: str, durations: Sequence[float], denied: int) -> float:
    p95_ms = _p95(durations) * 1000
    assert p95_ms > 0, "a zero-duration sample means the clock or the loop is broken"
    assert p95_ms < P95_BUDGET_MS, (
        f"AI-NFR-01: {label} over {ITEMS} context items ({denied} denied) took "
        f"p95 {p95_ms:.3f} ms against a {P95_BUDGET_MS} ms budget; "
        f"median {sorted(durations)[len(durations) // 2] * 1000:.3f} ms, "
        f"worst {max(durations) * 1000:.3f} ms"
    )
    return p95_ms


def test_an_allowed_hundred_item_context_decides_within_budget() -> None:
    """AI-NFR-01: the allowed path — every item checked, nothing refused."""
    items = _context()
    policy = EffectivePolicy.organisation_default()
    assert len(items) == ITEMS, "the scenario really is a hundred items"

    durations, verdict = _measure(lambda: evaluate(items, policy, ProviderClass.PRIVATE_NETWORK))
    assert isinstance(verdict, Verdict)
    assert verdict.allowed and verdict.denied == ()
    assert verdict.evaluated_items == ITEMS
    _assert_within_budget("evaluate (allowed)", durations, 0)


def test_a_denied_hundred_item_context_decides_within_budget() -> None:
    """AI-NFR-01 on its most expensive shape: a refusal builds one ``DeniedItem`` per
    offender, each carrying a sorted tuple of the classes that *were* open (AI-POL-003's
    "list them all", AI-RT-017's ``rejected_artifacts``).  That is the number that has to
    hold, not the trivially fast allowed case."""
    items = _mixed_context()
    policy = EffectivePolicy.organisation_default()
    probe = evaluate(items, policy, ProviderClass.UNRESTRICTED)
    assert not probe.allowed
    assert len(probe.denied) >= ITEMS - 25, (
        f"the scenario must exercise the denial path, got {len(probe.denied)} refusals"
    )

    durations, verdict = _measure(lambda: evaluate(items, policy, ProviderClass.UNRESTRICTED))
    assert len(verdict.denied) == len(probe.denied)
    _assert_within_budget("evaluate (denied)", durations, len(verdict.denied))


def test_the_cost_grows_with_the_context_rather_than_hiding_a_scan_behind_it() -> None:
    """The measurement has to be reaching the loop it claims to time: a hundred items may
    not cost the same as one, or the sample is measuring call overhead and the requirement
    is unverified (AI-NFR-01's premise is that the cost is per item)."""
    policy = EffectivePolicy.organisation_default()
    small = _context()[:1]
    large = _context()

    small_durations, small_verdict = _measure(
        lambda: evaluate(small, policy, ProviderClass.LOCAL_ONLY)
    )
    large_durations, large_verdict = _measure(
        lambda: evaluate(large, policy, ProviderClass.LOCAL_ONLY)
    )
    assert small_verdict.evaluated_items == 1
    assert large_verdict.evaluated_items == ITEMS

    small_p95, large_p95 = _p95(small_durations), _p95(large_durations)
    assert large_p95 > small_p95, (
        f"a hundred items decided in {large_p95:.6f}s against one item's {small_p95:.6f}s: "
        "the loop is not being measured"
    )
    _assert_within_budget("evaluate (scaling)", large_durations, 0)


def test_reading_the_policy_back_from_its_stored_form_costs_nothing_extra() -> None:
    """AI-POL-008 writes ``to_payload()`` and a replay reads it back; the judgement itself
    must not become a serialisation exercise (AI-NFR-01)."""
    items = _context()
    stored = EffectivePolicy.organisation_default().to_payload()

    durations, verdict = _measure(
        lambda: evaluate(
            items, EffectivePolicy.from_payload(stored), ProviderClass.PRIVATE_NETWORK
        )
    )
    assert verdict.allowed
    _assert_within_budget("evaluate (from_payload)", durations, 0)

    round_trip = _p95([_time(lambda: EffectivePolicy.from_payload(stored)) for _ in range(RUNS)])
    assert round_trip * 1000 < P95_BUDGET_MS, f"policy reload p95 {round_trip * 1000:.3f} ms"


def _time(run: Callable[[], object]) -> float:
    started = time.perf_counter()
    run()
    return time.perf_counter() - started


@pytest.mark.skipif(os.environ.get("FACTORY_PERF") != "1", reason=SKIP_REASON)
def test_a_full_provider_matrix_sweep_for_one_context_stays_useable() -> None:
    """AI-POL-007 makes the runtime judge each candidate model in turn, so one run pays up
    to four judgements.  Four is the number of provider classes, and this is the shape the
    selection code actually produces — still inside the same budget, because the budget is
    per decision and the run makes a small constant number of them."""
    items = _mixed_context()
    policy = EffectivePolicy.organisation_default()

    durations, results = _measure(
        lambda: [evaluate(items, policy, member) for member in ProviderClass]
    )
    assert len(results) == len(list(ProviderClass))
    assert any(not verdict.allowed for verdict in results)
    assert all(verdict.evaluated_items == ITEMS for verdict in results)
    _assert_within_budget("evaluate (all four classes)", durations, len(results[-1].denied))
