"""AI-POL-007 / AC-AG-01: which model serves this run, and why not the others.

An agent type carries an ordered preference list, and the runtime takes the **first**
model that clears both halves of the rule: the effective policy permits every context
item to reach that model's provider class, *and* the model's declared capabilities cover
the request (structured output, vision when an image is present, a context window that
fits).  Nothing about a model's preference position excuses it from the policy, and
nothing about the policy excuses a model that cannot do the job.

When no model clears both, AC-AG-01 asks for one refusal line **per candidate**: "no
model available" cannot be acted on, and the operator's next move is either to relax a
policy or to register a bigger model.
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import DataClassification

from factory_intelligence.enums import ItemSourceKind, ProviderClass
from factory_intelligence.policy.engine import evaluate
from factory_intelligence.policy.model import ContextItem, EffectivePolicy
from factory_intelligence.policy.selection import CandidateModel, ModelDenial, Selection, select

_LEVELS: tuple[DataClassification, ...] = (
    DataClassification.PUBLIC,
    DataClassification.INTERNAL,
    DataClassification.CONFIDENTIAL,
    DataClassification.RESTRICTED,
    DataClassification.PHI_PII,
)

_LO = ProviderClass.LOCAL_ONLY
_PN = ProviderClass.PRIVATE_NETWORK
_AE = ProviderClass.APPROVED_EXTERNAL
_UR = ProviderClass.UNRESTRICTED


def _model(
    name: str,
    provider_class: ProviderClass,
    *,
    provider: str = "acme",
    priority: int = 100,
    **capabilities: Any,
) -> CandidateModel:
    """One entry of an agent's preference list, as the Model Registry describes it."""
    return CandidateModel(
        model=name,
        provider=provider,
        provider_class=provider_class,
        priority=priority,
        **capabilities,
    )


def _item(
    level: DataClassification, ref: str = "artifact:input-1", **kwargs: Any
) -> ContextItem:
    return ContextItem(ref=ref, classification=level, **kwargs)


def _public() -> list[ContextItem]:
    return [_item(DataClassification.PUBLIC)]


def _confidential() -> list[ContextItem]:
    return [_item(DataClassification.CONFIDENTIAL, ref="artifact:clinic-layout")]


def _org() -> EffectivePolicy:
    return EffectivePolicy.organisation_default()


# --- the walk --------------------------------------------------------------------


def test_the_first_model_satisfying_both_halves_wins() -> None:
    """AI-POL-007: the list is walked from the front and the search stops at the first
    acceptable model, so a preference order is a real order."""
    chosen = select(
        _public(),
        _org(),
        [
            _model("local-a", _LO, priority=1),
            _model("private-b", _PN, priority=2),
        ],
    )
    assert chosen.available
    assert chosen.chosen is not None and chosen.chosen.model == "local-a"
    assert chosen.denials == ()


def test_a_policy_denied_first_choice_falls_through_to_the_second() -> None:
    """CONFIDENTIAL may not reach APPROVED_EXTERNAL (AI-POL-001), so the preferred
    external model is skipped and the local one serves the run."""
    selection = select(
        _confidential(),
        _org(),
        [
            _model("fast-external", _AE, priority=1),
            _model("slow-local", _LO, priority=2),
        ],
    )
    assert selection.chosen is not None and selection.chosen.model == "slow-local"
    assert [denial.model for denial in selection.denials] == ["fast-external"]


def test_a_model_that_clears_the_policy_but_not_the_request_is_still_skipped() -> None:
    """AI-POL-007's second half: policy permission is necessary, not sufficient."""
    selection = select(
        _public(),
        _org(),
        [
            _model("text-only", _LO, priority=1, vision=False),
            _model("sees", _LO, priority=2, vision=True),
        ],
        needs_vision=True,
    )
    assert selection.chosen is not None and selection.chosen.model == "sees"
    assert selection.denials[0].reason == "no vision capability for an image input"


def test_the_walk_ignores_the_order_the_caller_happened_to_list_them_in() -> None:
    """The registry reads its rows in whatever order the planner edited them; the
    preference list is what decides."""
    candidates = [
        _model("third", _LO, priority=30),
        _model("first", _LO, priority=10),
        _model("second", _LO, priority=20),
    ]
    for order in (candidates, list(reversed(candidates))):
        selection = select(_public(), _org(), order)
        assert selection.chosen is not None and selection.chosen.model == "first"


def test_denials_are_reported_in_the_order_they_were_tried() -> None:
    selection = select(
        _confidential(),
        _org(),
        [
            _model("preferred", _AE, priority=1),
            _model("second", _UR, priority=2),
            _model("third", _LO, priority=3),
        ],
    )
    assert selection.chosen is not None and selection.chosen.model == "third"
    assert [denial.model for denial in selection.denials] == ["preferred", "second"]


def test_a_model_tried_after_the_winner_is_never_reported() -> None:
    """AC-AG-01 counts refusals *for this run*; a model that was never reached has no
    reason to show, and inventing one would send an operator to fix the wrong thing."""
    selection = select(
        _public(),
        _org(),
        [
            _model("winner", _LO, priority=1),
            _model("broken", _LO, priority=2, enabled=False),
            _model("tiny", _LO, priority=3, max_context_tokens=1),
        ],
        needed_tokens=10,
    )
    assert selection.chosen is not None and selection.chosen.model == "winner"
    assert selection.denials == ()
    assert selection.reasons == []


# --- capability refusals ---------------------------------------------------------


def test_a_disabled_model_is_skipped_and_says_so() -> None:
    """AI-REG-004 carries 是否启用; a disabled row must never be chosen, and must say why
    it was not."""
    selection = select(
        _public(),
        _org(),
        [
            _model("retired", _LO, priority=1, enabled=False),
            _model("current", _LO, priority=2),
        ],
    )
    assert selection.chosen is not None and selection.chosen.model == "current"
    denial = selection.denials[0]
    assert denial.model == "retired"
    assert "disabled" in denial.reason


@pytest.mark.parametrize(
    "level", [DataClassification.PUBLIC, DataClassification.CONFIDENTIAL],
    ids=["public", "confidential"],
)
def test_structured_output_is_required_by_default(level: DataClassification) -> None:
    """Every agent's output is Schema-checked JSON (AI-RT-010), so a model without the
    capability cannot serve a run whatever its policy position."""
    selection = select(
        [_item(level)],
        _org(),
        [_model("free-form", _LO, structured_output=False), _model("json", _LO)],
    )
    assert selection.chosen is not None and selection.chosen.model == "json"
    assert selection.denials[0].reason == "no structured-output capability"


def test_a_run_that_does_not_need_structured_output_may_use_a_model_lacking_it() -> None:
    """The flag is the caller's, not the model's: only a run whose contract is plain text
    may spend a model that cannot do JSON."""
    selection = select(
        _public(),
        _org(),
        [_model("free-form", _LO, structured_output=False)],
        needs_structured_output=False,
    )
    assert selection.chosen is not None and selection.chosen.model == "free-form"


def test_an_image_item_makes_vision_a_requirement_without_being_asked() -> None:
    """AI-INP-002 sends screenshots to a vision model; the context says so and the
    selection listens, because a non-vision model would answer about an image it never
    saw."""
    selection = select(
        [_item(DataClassification.PUBLIC, ref="artifact:ui-sketch.png", is_image=True)],
        _org(),
        [_model("blind", _LO), _model("seeing", _LO, vision=True)],
    )
    assert selection.chosen is not None and selection.chosen.model == "seeing"
    reason = selection.denials[0].reason
    assert "vision" in reason and "image" in reason


def test_a_text_only_context_does_not_require_vision() -> None:
    selection = select(_public(), _org(), [_model("blind", _LO)])
    assert selection.chosen is not None and selection.chosen.model == "blind"
    assert selection.denials == ()


def test_an_explicit_vision_request_overrides_what_the_context_happens_to_hold() -> None:
    """A caller that intends to send an image later must not be served a blind model
    because the item is not in this batch yet."""
    selection = select(
        _public(),
        _org(),
        [_model("blind", _LO), _model("seeing", _LO, vision=True)],
        needs_vision=True,
    )
    assert selection.chosen is not None and selection.chosen.model == "seeing"


def test_an_explicit_refusal_of_vision_lets_a_blind_model_serve_an_image_context() -> None:
    """``needs_vision=False`` is a decision, not an oversight — the runtime uses it when
    the image has already been extracted into text."""
    selection = select(
        [_item(DataClassification.PUBLIC, is_image=True)],
        _org(),
        [_model("blind", _LO)],
        needs_vision=False,
    )
    assert selection.chosen is not None and selection.chosen.model == "blind"


def test_a_context_larger_than_the_window_is_refused_with_both_numbers() -> None:
    """AI-POL-003 forbids truncating to fit, so the only honest answer names the needed
    and available figures for the operator to register a bigger model."""
    selection = select(
        _public(),
        _org(),
        [_model("small", _LO, max_context_tokens=8000)],
        needed_tokens=41000,
    )
    assert not selection.available
    reason = selection.reasons[0]
    assert "41000" in reason and "8000" in reason


def test_a_window_exactly_the_size_of_the_context_fits() -> None:
    """The comparison is "exceeds", not "reaches": an off-by-one here would reject the
    largest legal request."""
    selection = select(
        _public(), _org(), [_model("exact", _LO, max_context_tokens=8000)], needed_tokens=8000
    )
    assert selection.chosen is not None and selection.chosen.model == "exact"


def test_the_context_size_question_is_asked_per_model_not_once() -> None:
    """A preference list usually runs from small-and-cheap to large-and-costly; the first
    model with room is the one used."""
    selection = select(
        _public(),
        _org(),
        [
            _model("cheap", _LO, priority=1, max_context_tokens=4000),
            _model("middle", _LO, priority=2, max_context_tokens=32000),
            _model("huge", _LO, priority=3, max_context_tokens=200000),
        ],
        needed_tokens=12000,
    )
    assert selection.chosen is not None and selection.chosen.model == "middle"
    assert [denial.model for denial in selection.denials] == ["cheap"]


# --- policy refusals -------------------------------------------------------------


def test_a_policy_denied_candidate_reports_the_classification_and_the_open_classes() -> None:
    """AC-AG-01: the line has to say what was refused and what would have been allowed,
    or the operator cannot tell a policy problem from a registry problem."""
    selection = select(_confidential(), _org(), [_model("external", _AE)])
    denial = selection.denials[0]
    assert "CONFIDENTIAL" in denial.reason
    assert "APPROVED_EXTERNAL" in denial.reason
    assert "LOCAL_ONLY" in denial.reason and "PRIVATE_NETWORK" in denial.reason
    assert denial.provider_class == "APPROVED_EXTERNAL"


def test_a_second_offender_is_counted_rather_than_hidden() -> None:
    """AI-POL-003 lists every rejected artifact for the run result; the per-model line is
    one sentence, so it names the count of the rest instead of dropping them."""
    items = [
        _item(DataClassification.CONFIDENTIAL, ref="artifact:a"),
        _item(DataClassification.RESTRICTED, ref="artifact:b"),
    ]
    selection = select(items, _org(), [_model("external", _AE)])
    assert "1 more item(s)" in selection.denials[0].reason


def test_the_policy_that_decides_is_the_effective_one_not_the_organisation_default() -> None:
    """AI-POL-004 end to end: an environment that closes APPROVED_EXTERNAL changes which
    model the agent can use, with no code change."""
    narrowed = EffectivePolicy.combine(
        [
            _org(),
            EffectivePolicy(
                allowed={level: frozenset({_LO}) for level in _LEVELS},
                sources=("env:ward-prod",),
            ),
        ]
    )
    selection = select(
        [_item(DataClassification.PUBLIC)],
        narrowed,
        [_model("external", _AE, priority=1), _model("local", _LO, priority=2)],
    )
    assert selection.chosen is not None and selection.chosen.model == "local"
    assert selection.denials[0].provider_class == "APPROVED_EXTERNAL"


def test_phi_pii_in_the_context_refuses_every_model_on_the_list() -> None:
    """AC-POL-02 through AI-POL-007: there is no provider class that accepts PHI_PII, so
    no model is selectable and every candidate says the rule."""
    selection = select(
        [_item(DataClassification.PHI_PII, ref="artifact:patient-list")],
        _org(),
        [
            _model("a", _LO),
            _model("b", _PN),
            _model("c", _AE),
            _model("d", _UR),
        ],
    )
    assert not selection.available
    assert len(selection.reasons) == 4
    assert all("AC-POL-02" in line for line in selection.reasons)


def test_the_verdict_recorded_is_the_winner_and_says_which_class_it_was() -> None:
    """AI-POL-008: ``ai.policy_decision`` stores the verdict of the model actually used,
    so a caller must be able to read it back off the selection."""
    selection = select(
        _confidential(),
        _org(),
        [_model("external", _AE, priority=1), _model("private", _PN, priority=2)],
    )
    assert selection.verdict is not None
    assert selection.verdict.allowed is True
    assert selection.verdict.provider_class is _PN
    assert selection.chosen is not None and selection.chosen.provider_class is _PN


def test_the_verdict_is_absent_when_nothing_was_chosen() -> None:
    """There is no decision to persist, and storing the last candidate's refusal as if it
    were the run's verdict would misreport the audit (AI-POL-008)."""
    selection = select(_public(), _org(), [_model("off", _LO, enabled=False)])
    assert selection.verdict is None
    assert not selection.available


# --- AC-AG-01's report ------------------------------------------------------------


def test_nothing_selectable_gives_one_line_per_candidate() -> None:
    """AC-AG-01: POLICY_DENIED with 每个模型的拒绝原因 — the count is the number of
    models the agent listed, not the number of distinct reasons."""
    candidates = [
        _model("disabled", _LO, priority=1, enabled=False),
        _model("no-json", _LO, priority=2, structured_output=False),
        _model("external", _AE, priority=3),
        _model("tiny", _LO, priority=4, max_context_tokens=100),
    ]
    selection = select(_confidential(), _org(), candidates, needed_tokens=5000)
    assert not selection.available
    assert selection.chosen is None
    assert selection.reasons == [
        "acme/disabled: provider or model is disabled",
        "acme/no-json: no structured-output capability",
        "acme/external: CONFIDENTIAL may not go to APPROVED_EXTERNAL; "
        "allowed: LOCAL_ONLY, PRIVATE_NETWORK",
        "acme/tiny: context of 5000 tokens exceeds its 100 window",
    ]


def test_each_refusal_line_carries_its_own_distinct_reason() -> None:
    selection = select(
        _confidential(),
        _org(),
        [
            _model("a", _AE, priority=1),
            _model("b", _UR, priority=2),
            _model("c", _LO, priority=3, enabled=False),
        ],
    )
    assert len(selection.reasons) == 3
    assert len(set(selection.reasons)) == 3, selection.reasons


def test_an_empty_preference_list_selects_nothing_and_says_nothing() -> None:
    """AI-ASM-05: with no model at all the answer is "manual authoring", and the run
    result must not pretend a model refused."""
    selection = select(_public(), _org(), [])
    assert not selection.available
    assert selection.denials == ()
    assert selection.reasons == []
    assert selection.payloads == []


def test_the_payload_uses_the_camel_case_names_the_api_promises() -> None:
    """00 doc 6.1: S1 mirrors these keys in a DTO with ``extra="ignore"``, so a snake_case
    key here is dropped in the caller rather than reported."""
    selection = select(_confidential(), _org(), [_model("external", _AE)])
    assert selection.payloads == [
        {
            "model": "external",
            "provider": "acme",
            "providerClass": "APPROVED_EXTERNAL",
            "reason": selection.denials[0].reason,
        }
    ]
    assert all(set(payload) == {"model", "provider", "providerClass", "reason"} for payload in selection.payloads)


def test_the_line_and_the_payload_describe_the_same_refusal() -> None:
    """Two renderings of one fact: the log line a human reads and the JSON a caller
    stores.  ``ModelDenial.as_line`` prefixes provider/model, which is what ties the
    reason back to the registry row."""
    denial = select(_confidential(), _org(), [_model("external", _AE, provider="vendor-x")]).denials[0]
    assert isinstance(denial, ModelDenial)
    assert denial.as_line().startswith("vendor-x/external: ")
    assert denial.as_line().endswith(denial.reason)
    assert denial.as_payload()["provider"] == "vendor-x"


def test_the_selection_is_reproducible_for_the_same_inputs() -> None:
    """AI-NFR-01's quiet twin: the same request twice must choose the same model and
    report the same refusals, or a recorded run cannot be replayed (AI-AC-05,
    AI-LLM-006)."""
    candidates = [
        _model("zeta", _AE, priority=20),
        _model("alpha", _AE, priority=20),
        _model("mid", _LO, priority=20),
    ]
    expected = (["alpha"], "mid")
    first = select(_confidential(), _org(), candidates)
    second = select(_confidential(), _org(), list(reversed(candidates)))
    for selection in (first, second):
        assert [denial.model for denial in selection.denials] == expected[0]
        assert selection.chosen is not None and selection.chosen.model == expected[1]
    assert first.reasons == second.reasons
    assert first.payloads == second.payloads


def test_ties_are_broken_by_model_name_so_the_choice_is_deterministic() -> None:
    """Two rows with the same priority is a registry data bug, and the run must still be
    reproducible: the name decides the order, in the same order, every time."""
    candidates = [
        _model("bravo", _AE, priority=5),
        _model("charlie", _AE, priority=5),
        _model("alpha", _AE, priority=5),
    ]
    for _round in range(2):
        selection = select(_confidential(), _org(), candidates)
        assert not selection.available
        assert [denial.model for denial in selection.denials] == ["alpha", "bravo", "charlie"]
    winner_order = [
        _model("bravo", _LO, priority=5),
        _model("charlie", _LO, priority=5),
        _model("alpha", _LO, priority=5),
    ]
    selection = select(_public(), _org(), winner_order)
    assert selection.chosen is not None and selection.chosen.model == "alpha"


def test_a_selected_model_does_not_mutate_the_caller_list() -> None:
    """The runtime keeps the preference list for the retry of a Schema failure, so
    :func:`select` may only read it."""
    candidates = [
        _model("b", _LO, priority=2),
        _model("a", _LO, priority=1),
    ]
    selection = select(_public(), _org(), candidates)
    assert [candidate.model for candidate in candidates] == ["b", "a"]
    assert selection.chosen is not None and selection.chosen.model == "a"
    assert isinstance(selection, Selection)


def test_an_untrusted_kind_item_is_judged_like_any_other() -> None:
    """AI-RT-030 fences the item by kind; the policy decision does not become softer
    because the content came from a Blueprint entry rather than an upload."""
    item = ContextItem(
        ref="blueprint:0.1.0",
        classification=DataClassification.RESTRICTED,
        kind=ItemSourceKind.APPROVED_ENTRY,
        entry_key="dataElements/weight",
    )
    selection = select([item], _org(), [_model("external", _AE), _model("local", _LO)])
    assert selection.chosen is not None and selection.chosen.model == "local"
    assert "RESTRICTED" in selection.denials[0].reason
    assert selection.verdict == evaluate([item], _org(), _LO)
