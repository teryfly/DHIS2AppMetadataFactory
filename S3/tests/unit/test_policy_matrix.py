"""AI-POL-001: the classification × provider-class table, read as code.

SRS 3.5's table is reproduced literally below as :data:`SRS_20_1` and every cell is
then asserted through :func:`evaluate`, so the requirement is pinned at *both* ends:
:data:`factory_intelligence.enums.POLICY_MATRIX` must equal the table, and the engine
must apply the table.  Asserting ``evaluate`` against ``POLICY_MATRIX`` alone would be
tautological — it would pass unchanged if somebody edited the matrix.

The two acceptance criteria that live here are the ones M1 is judged on:

* **AC-POL-01** — a ``CONFIDENTIAL`` item aimed at ``APPROVED_EXTERNAL`` under the
  default policy is refused, and the refusal names the artifact (AI-RT-017's
  ``rejected_artifacts``).
* **AC-POL-02** — a ``PHI_PII`` item is refused against *all four* classes, including
  ``LOCAL_ONLY``, and including under a policy somebody tried to widen.
"""

from __future__ import annotations

from itertools import product

import pytest
from factory_foundation.api import DataClassification

from factory_intelligence.enums import POLICY_MATRIX, ProviderClass
from factory_intelligence.policy.engine import evaluate, highest_classification
from factory_intelligence.policy.model import ContextItem, EffectivePolicy, Verdict

_LEVELS: tuple[DataClassification, ...] = (
    DataClassification.PUBLIC,
    DataClassification.INTERNAL,
    DataClassification.CONFIDENTIAL,
    DataClassification.RESTRICTED,
    DataClassification.PHI_PII,
)

_CLASSES: tuple[ProviderClass, ...] = (
    ProviderClass.LOCAL_ONLY,
    ProviderClass.PRIVATE_NETWORK,
    ProviderClass.APPROVED_EXTERNAL,
    ProviderClass.UNRESTRICTED,
)

#: SRS 3.5 AI-POL-001 (overall SRS 20.1), transcribed by hand from the requirement row
#: and deliberately not derived from the code under test.
SRS_20_1: dict[DataClassification, frozenset[ProviderClass]] = {
    DataClassification.PUBLIC: frozenset(_CLASSES),  # "任意已启用的提供方"
    DataClassification.INTERNAL: frozenset(
        {
            ProviderClass.LOCAL_ONLY,
            ProviderClass.PRIVATE_NETWORK,
            ProviderClass.APPROVED_EXTERNAL,
        }
    ),
    DataClassification.CONFIDENTIAL: frozenset(
        {ProviderClass.LOCAL_ONLY, ProviderClass.PRIVATE_NETWORK}
    ),
    DataClassification.RESTRICTED: frozenset({ProviderClass.LOCAL_ONLY}),
    DataClassification.PHI_PII: frozenset(),  # "禁止发送给任何模型"
}

_CELLS: list[tuple[DataClassification, ProviderClass]] = list(product(_LEVELS, _CLASSES))
_CELL_IDS = [f"{level.value}-{provider_class.value}" for level, provider_class in _CELLS]


def _item(
    classification: DataClassification,
    ref: str = "artifact:input-1",
    *,
    label: str = "",
    is_image: bool = False,
) -> ContextItem:
    """One context item; ``ref`` defaults to a plain input artifact."""
    return ContextItem(
        ref=ref, classification=classification, label=label, is_image=is_image
    )


def _single(
    classification: DataClassification,
    provider_class: ProviderClass,
    policy: EffectivePolicy | None = None,
) -> Verdict:
    """Judge one item, against the organisation default unless a policy is given."""
    return evaluate(
        [_item(classification)],
        EffectivePolicy.organisation_default() if policy is None else policy,
        provider_class,
    )


# --- the table ------------------------------------------------------------------


def test_the_matrix_constant_is_the_srs_table() -> None:
    """AI-POL-001: ``POLICY_MATRIX`` *is* the requirement, so it is compared with a
    transcription of the SRS row rather than with itself."""
    assert POLICY_MATRIX == SRS_20_1


#: AI-POL-001 read as what an organisation may widen *to*.  The system default is
#: narrower (AI-POL-002 denies both external classes), and keeping the two apart is what
#: makes a relaxation able to change behaviour; the sweep below therefore runs against the
#: ceiling, while the default gets its own tests.
_CEILING = EffectivePolicy(allowed=dict(POLICY_MATRIX), sources=("ai-pol-001-ceiling",))


def test_the_matrix_covers_every_level_and_nothing_else() -> None:
    """A sixth classification or a fifth provider class would silently widen the
    default answer (AI-BR-03: the default answer is no)."""
    assert set(POLICY_MATRIX) == set(_LEVELS)
    assert {member for classes in POLICY_MATRIX.values() for member in classes} == set(
        _CLASSES
    )


@pytest.mark.parametrize(("level", "provider_class"), _CELLS, ids=_CELL_IDS)
def test_each_cell_of_the_ai_pol_001_ceiling_is_the_srs_cell(
    level: DataClassification, provider_class: ProviderClass
) -> None:
    """AI-POL-001, one test per cell: the ceiling sends a level exactly where the SRS
    table says it goes.  The default is deliberately narrower; see
    ``test_the_default_policy_denies_both_external_classes_above_internal``."""
    expected = provider_class in SRS_20_1[level]
    verdict = _single(level, provider_class, policy=_CEILING)
    assert verdict.allowed is expected
    assert verdict.evaluated_items == 1
    assert verdict.provider_class is provider_class


def test_the_default_policy_is_strictly_inside_the_ceiling() -> None:
    """AI-POL-002 only means something if there is room between the default and the
    AI-POL-001 ceiling: if the two were equal, every recorded relaxation would be a no-op
    and an organisation could never actually widen anything."""
    default = EffectivePolicy.organisation_default()
    assert default.narrower_than(_CEILING)
    for level in _LEVELS:
        assert default.classes_for(level) <= _CEILING.classes_for(level), level.value
    widened = {
        level: _CEILING.classes_for(level) - default.classes_for(level) for level in _LEVELS
    }
    assert widened[DataClassification.PUBLIC] == frozenset(
        {ProviderClass.APPROVED_EXTERNAL, ProviderClass.UNRESTRICTED}
    )
    assert widened[DataClassification.INTERNAL] == frozenset(
        {ProviderClass.APPROVED_EXTERNAL}
    )
    # nothing to widen for the levels the table already denies externally, and never PHI_PII
    assert widened[DataClassification.CONFIDENTIAL] == frozenset()
    assert widened[DataClassification.RESTRICTED] == frozenset()
    assert widened[DataClassification.PHI_PII] == frozenset()


def test_the_default_policy_denies_both_external_classes_above_internal() -> None:
    """AI-POL-002: APPROVED_EXTERNAL and UNRESTRICTED are unavailable by default for
    everything above INTERNAL, and RESTRICTED — the level below PHI_PII — keeps only the
    provider that cannot take data anywhere at all."""
    default = EffectivePolicy.organisation_default()
    for level in (
        DataClassification.CONFIDENTIAL,
        DataClassification.RESTRICTED,
        DataClassification.PHI_PII,
    ):
        for provider_class in (ProviderClass.APPROVED_EXTERNAL, ProviderClass.UNRESTRICTED):
            assert not default.allows(level, provider_class), f"{level.value} -> {provider_class.value}"
    assert default.classes_for(DataClassification.RESTRICTED) == frozenset(
        {ProviderClass.LOCAL_ONLY}
    )


def test_only_public_reaches_unrestricted() -> None:
    """AI-POL-001's widest row is PUBLIC; the other four stop short of UNRESTRICTED.

    Asserted against the ceiling: under the default nothing reaches UNRESTRICTED at all,
    which is the AI-POL-002 rule rather than this table's.
    """
    assert _CEILING.allows(DataClassification.PUBLIC, ProviderClass.UNRESTRICTED)
    for level in _LEVELS[1:]:
        assert not _CEILING.allows(level, ProviderClass.UNRESTRICTED), level.value


def test_the_default_policy_names_the_policy_it_came_from() -> None:
    """AI-POL-008: a decision is reported with the policies behind it, so the baseline
    has to say it is the organisation's."""
    assert EffectivePolicy.organisation_default().sources == ("org",)


# --- AC-POL-01 ------------------------------------------------------------------


def test_ac_pol_01_confidential_to_approved_external_is_denied() -> None:
    """AC-POL-01: default policy, CONFIDENTIAL item, APPROVED_EXTERNAL target ⇒
    POLICY_DENIED, and the denied item is listed rather than dropped."""
    verdict = _single(DataClassification.CONFIDENTIAL, ProviderClass.APPROVED_EXTERNAL)
    assert verdict.allowed is False
    assert len(verdict.denied) == 1
    denied = verdict.denied[0]
    assert denied.ref == "artifact:input-1"
    assert denied.classification is DataClassification.CONFIDENTIAL
    assert "CONFIDENTIAL" in denied.reason
    assert "APPROVED_EXTERNAL" in denied.reason


def test_ac_pol_01_denial_lists_what_is_allowed_instead() -> None:
    """The refusal has to be actionable: the operator learns which classes were open."""
    denied = _single(
        DataClassification.CONFIDENTIAL, ProviderClass.APPROVED_EXTERNAL
    ).denied[0]
    assert denied.allowed_classes == (ProviderClass.LOCAL_ONLY, ProviderClass.PRIVATE_NETWORK)
    assert "allowed: LOCAL_ONLY, PRIVATE_NETWORK" in denied.reason


def test_ac_pol_01_denied_item_serialises_for_the_run_result() -> None:
    """AI-RT-017 / AI-POL-008: ``rejected_artifacts`` is built from this payload, and S1
    reads camelCase keys."""
    payload = _single(
        DataClassification.CONFIDENTIAL, ProviderClass.APPROVED_EXTERNAL
    ).denied_payload[0]
    assert set(payload) == {"ref", "classification", "reason", "allowedClasses", "label"}
    assert payload["ref"] == "artifact:input-1"
    assert payload["classification"] == "CONFIDENTIAL"
    assert payload["allowedClasses"] == ["LOCAL_ONLY", "PRIVATE_NETWORK"]


def test_ac_pol_01_still_denies_when_only_one_item_of_a_hundred_is_confidential() -> None:
    """AC-POL-01 with a realistic context: one confidential artifact among public ones
    refuses the whole call (AI-POL-003, AI-BR-03)."""
    items = [
        _item(DataClassification.PUBLIC, ref=f"artifact:public-{index}")
        for index in range(99)
    ] + [_item(DataClassification.CONFIDENTIAL, ref="artifact:secret-plan")]
    verdict = evaluate(items, _CEILING, ProviderClass.APPROVED_EXTERNAL)
    assert verdict.allowed is False
    assert [denied.ref for denied in verdict.denied] == ["artifact:secret-plan"]
    assert verdict.evaluated_items == 100


# --- AC-POL-02 ------------------------------------------------------------------


@pytest.mark.parametrize("provider_class", _CLASSES, ids=[c.value for c in _CLASSES])
def test_ac_pol_02_phi_pii_is_denied_against_every_provider_class(
    provider_class: ProviderClass,
) -> None:
    """AC-POL-02 / AI-POL-005: PHI_PII goes nowhere, and LOCAL_ONLY is not an exception —
    the item may not enter a model context at all."""
    verdict = _single(DataClassification.PHI_PII, provider_class)
    assert verdict.allowed is False
    assert verdict.denied[0].classification is DataClassification.PHI_PII


def test_ac_pol_02_reason_names_the_rule_it_enforces() -> None:
    """An operator reading ``ai.policy_decision.denied_items`` must be able to tell a
    PHI refusal apart from a level/class mismatch, because no relaxation fixes it."""
    reason = _single(
        DataClassification.PHI_PII, ProviderClass.LOCAL_ONLY
    ).denied[0].reason
    assert "AC-POL-02" in reason
    assert "PHI_PII" in reason


@pytest.mark.parametrize("provider_class", _CLASSES, ids=[c.value for c in _CLASSES])
def test_ac_pol_02_holds_under_the_fail_safe_policy_too(
    provider_class: ProviderClass,
) -> None:
    """The narrowest policy in the system still sends PHI_PII nowhere (AI-POL-005)."""
    assert not evaluate(
        [_item(DataClassification.PHI_PII)],
        EffectivePolicy.strictest(),
        provider_class,
    ).allowed


def test_ac_pol_02_a_widened_policy_cannot_open_phi_pii() -> None:
    """AI-POL-005: "放宽策略也不能改变这一点" — combining with an artificially widest
    possible policy leaves PHI_PII empty, because the empty set absorbs it."""
    widest = EffectivePolicy(
        allowed={level: frozenset(ProviderClass) for level in _LEVELS},
        sources=("widest",),
    )
    combined = EffectivePolicy.combine([widest, EffectivePolicy.organisation_default()])
    assert combined.classes_for(DataClassification.PHI_PII) == frozenset()
    verdict = evaluate(
        [_item(DataClassification.PHI_PII, ref="artifact:patient-list")],
        combined,
        ProviderClass.LOCAL_ONLY,
    )
    assert verdict.allowed is False
    assert "AC-POL-02" in verdict.denied[0].reason


def test_ac_pol_02_phi_pii_needs_no_other_offender_to_be_reported() -> None:
    """A context of public data plus one PHI_PII item is refused for the PHI_PII item
    alone, and the public items are not sent either (no partial send)."""
    verdict = evaluate(
        [
            _item(DataClassification.PUBLIC, ref="artifact:catalogue"),
            _item(DataClassification.PHI_PII, ref="artifact:patient-list"),
        ],
        EffectivePolicy.organisation_default(),
        ProviderClass.LOCAL_ONLY,
    )
    assert verdict.allowed is False
    assert [denied.ref for denied in verdict.denied] == ["artifact:patient-list"]


# --- every offender, not the first (AI-POL-003, AI-RT-017) ----------------------


def test_a_denial_reports_every_offending_item_not_just_the_first() -> None:
    """AI-RT-017: the run result carries the whole rejected set in one round trip, so a
    caller that fixes one artifact does not meet a second refusal it could not see."""
    offenders = [
        _item(DataClassification.CONFIDENTIAL, ref=f"artifact:secret-{index}")
        for index in range(3)
    ]
    verdict = evaluate(
        [_item(DataClassification.PUBLIC, ref="artifact:ok"), *offenders],
        _CEILING,
        ProviderClass.APPROVED_EXTERNAL,
    )
    assert verdict.allowed is False
    assert [denied.ref for denied in verdict.denied] == [
        "artifact:secret-0",
        "artifact:secret-1",
        "artifact:secret-2",
    ]
    assert verdict.evaluated_items == 4


def test_nothing_is_dropped_so_the_rest_may_proceed() -> None:
    """AI-POL-003 forbids 静默截断: a denial is a refusal of the call, not a filtered
    context, so the verdict keeps counting every item it was given."""
    items = [
        _item(DataClassification.RESTRICTED, ref="artifact:salary-band"),
        _item(DataClassification.PUBLIC, ref="artifact:notice"),
    ]
    verdict = evaluate(
        items, EffectivePolicy.organisation_default(), ProviderClass.PRIVATE_NETWORK
    )
    assert verdict.allowed is False
    assert verdict.evaluated_items == len(items)
    assert len(verdict.denied) == 1


def test_denied_items_carry_their_label_for_the_ui() -> None:
    """AI-RT-017's list is shown to a human, so the label travels with the ref."""
    verdict = evaluate(
        [_item(DataClassification.RESTRICTED, ref="artifact:hr", label="HR payroll export")],
        EffectivePolicy.organisation_default(),
        ProviderClass.APPROVED_EXTERNAL,
    )
    assert verdict.denied[0].label == "HR payroll export"
    assert verdict.denied_payload[0]["label"] == "HR payroll export"


def test_an_allowed_context_denies_nothing() -> None:
    verdict = evaluate(
        [_item(DataClassification.INTERNAL), _item(DataClassification.PUBLIC)],
        EffectivePolicy.organisation_default(),
        ProviderClass.PRIVATE_NETWORK,
    )
    assert verdict.allowed is True
    assert verdict.denied == ()
    assert verdict.denied_payload == []


def test_an_empty_context_is_allowed_against_every_class() -> None:
    """There is nothing to send, so nothing can be refused — and nothing is sent."""
    for provider_class in _CLASSES:
        verdict = evaluate([], EffectivePolicy.organisation_default(), provider_class)
        assert verdict.allowed is True
        assert verdict.evaluated_items == 0


def test_evaluate_accepts_the_provider_class_as_the_string_the_registry_stores() -> None:
    """``ai.agent_run.provider_class`` is a column, so a caller has the name, not the
    member."""
    verdict = evaluate(
        [_item(DataClassification.CONFIDENTIAL)],
        EffectivePolicy.organisation_default(),
        "APPROVED_EXTERNAL",
    )
    assert verdict.allowed is False
    assert verdict.provider_class is ProviderClass.APPROVED_EXTERNAL


def test_an_unknown_provider_class_name_is_refused_loudly() -> None:
    """A typo must not be read as "the most restrictive class", because that would let a
    run through against a provider nobody approved."""
    with pytest.raises(ValueError):
        evaluate(
            [_item(DataClassification.PUBLIC)],
            EffectivePolicy.organisation_default(),
            "SOMEWHERE_OVER_THE_CLOUD",
        )


# --- reasons --------------------------------------------------------------------


def test_deny_reason_is_none_for_an_allowed_pairing() -> None:
    default = EffectivePolicy.organisation_default()
    assert default.deny_reason(DataClassification.INTERNAL, ProviderClass.LOCAL_ONLY) is None


def test_deny_reason_for_a_level_with_no_open_provider_says_so() -> None:
    """RESTRICTED narrowed to nowhere is a different message from "use this class
    instead": there is no class to suggest."""
    narrowed = EffectivePolicy.strictest()
    emptied = EffectivePolicy.combine(
        [narrowed, EffectivePolicy(allowed={DataClassification.RESTRICTED: frozenset()}, sources=("env:x",))]
    )
    reason = emptied.deny_reason(
        DataClassification.RESTRICTED, ProviderClass.APPROVED_EXTERNAL
    )
    assert reason is not None
    assert "may not be sent to any provider" in reason


@pytest.mark.parametrize("level", _LEVELS, ids=[level.value for level in _LEVELS])
def test_every_denial_reason_is_a_non_empty_sentence(level: DataClassification) -> None:
    """A denial the operator cannot read is a denial they cannot clear; every refused
    pairing must explain itself, and every allowed one must say nothing."""
    for provider_class in _CLASSES:
        reason = _CEILING.deny_reason(level, provider_class)
        if provider_class in SRS_20_1[level]:
            assert reason is None
            continue
        assert reason is not None and len(reason) > 20
        assert level.value in reason


# --- highest classification -----------------------------------------------------


def test_highest_classification_of_no_items_is_public() -> None:
    """AI-POL-006: an empty context has no sensitivity to inherit, and PUBLIC is S0's
    floor rather than an error."""
    assert highest_classification([]) is DataClassification.PUBLIC


@pytest.mark.parametrize("level", _LEVELS, ids=[level.value for level in _LEVELS])
def test_highest_classification_of_one_item_is_that_item(
    level: DataClassification,
) -> None:
    assert highest_classification([_item(level)]) is level


def test_highest_classification_takes_the_sensitive_item_over_the_volume() -> None:
    """Ninety-nine public pages and one PHI_PII list make a PHI_PII derived artifact."""
    items = [_item(DataClassification.PUBLIC) for _ in range(99)] + [
        _item(DataClassification.PHI_PII)
    ]
    assert highest_classification(items) is DataClassification.PHI_PII


def test_highest_classification_is_order_independent() -> None:
    ascending = [
        _item(level) for level in (_LEVELS[0], _LEVELS[2], _LEVELS[1], _LEVELS[3])
    ]
    assert highest_classification(ascending) is DataClassification.RESTRICTED
    assert highest_classification(list(reversed(ascending))) is DataClassification.RESTRICTED


# --- the stored form (AI-POL-008) -----------------------------------------------


@pytest.mark.parametrize("level", _LEVELS, ids=[level.value for level in _LEVELS])
def test_the_payload_records_every_level_even_the_denied_ones(
    level: DataClassification,
) -> None:
    """``ai.policy_decision.effective_policy`` must be readable on its own: an omitted
    level would be ambiguous between "denied" and "not recorded"."""
    payload = _CEILING.to_payload()
    assert set(payload["allowed"]) == {item.value for item in _LEVELS}
    assert payload["allowed"][level.value] == sorted(
        member.value for member in SRS_20_1[level]
    )


def test_to_payload_from_payload_round_trips_the_default_losslessly() -> None:
    default = EffectivePolicy.organisation_default()
    assert EffectivePolicy.from_payload(default.to_payload()) == default


def test_to_payload_from_payload_round_trips_warnings_and_sources() -> None:
    """The notes a decision was made on are part of the decision (AI-POL-004, AI-POL-008):
    a report that loses which environments were combined cannot be defended."""
    widened_env = EffectivePolicy(
        allowed={level: frozenset({ProviderClass.LOCAL_ONLY}) for level in _LEVELS},
        sources=("env:prod-1",),
        warnings=("env:prod-1: unrecognised keys ['nope']",),
    )
    policy = EffectivePolicy.combine([EffectivePolicy.organisation_default(), widened_env])
    assert policy.sources == ("org", "env:prod-1")
    assert policy.warnings == ("env:prod-1: unrecognised keys ['nope']",)
    restored = EffectivePolicy.from_payload(policy.to_payload())
    assert restored == policy
    assert restored.sources == policy.sources
    assert restored.warnings == policy.warnings
    assert restored.to_payload() == policy.to_payload()


def test_unknown_class_names_in_a_stored_payload_are_dropped() -> None:
    """A payload is data that came back out of a database.  A class name this build does
    not know must not be treated as a permission (AI-BR-03)."""
    restored = EffectivePolicy.from_payload(
        {"allowed": {"PUBLIC": ["LOCAL_ONLY", "APPROVED_EXTERNAL_BETA"]}}
    )
    assert restored.classes_for(DataClassification.PUBLIC) == frozenset(
        {ProviderClass.LOCAL_ONLY}
    )
    assert not restored.allows(DataClassification.PUBLIC, ProviderClass.UNRESTRICTED)


def test_a_payload_naming_an_unknown_level_leaves_it_denied() -> None:
    """The five levels are the whole vocabulary; a sixth name cannot smuggle a rule."""
    restored = EffectivePolicy.from_payload(
        {"allowed": {"PUBLIC": ["LOCAL_ONLY"], "SECRET_SAUCE": ["UNRESTRICTED"]}}
    )
    assert restored.classes_for(DataClassification.PUBLIC) == frozenset(
        {ProviderClass.LOCAL_ONLY}
    )
    assert restored.classes_for(DataClassification.CONFIDENTIAL) == frozenset()


def test_an_empty_or_missing_payload_denies_everything() -> None:
    """The reader is fail-closed like the parser: nothing recorded means nowhere to go."""
    payloads: list[dict[str, object]] = [{}, {"allowed": {}}, {"allowed": None}]
    for payload in payloads:
        restored = EffectivePolicy.from_payload(payload)
        assert all(not restored.classes_for(level) for level in _LEVELS)
        assert not restored.allows(DataClassification.PUBLIC, ProviderClass.LOCAL_ONLY)


def test_a_payload_with_non_string_class_entries_is_not_trusted() -> None:
    restored = EffectivePolicy.from_payload(
        {"allowed": {"PUBLIC": ["LOCAL_ONLY", None, 7, {"value": "PRIVATE_NETWORK"}]}}
    )
    assert restored.classes_for(DataClassification.PUBLIC) == frozenset(
        {ProviderClass.LOCAL_ONLY}
    )


# --- KNOWN DEFECT: reported, not worked around ----------------------------------


def test_the_ceiling_is_readable_as_a_policy_of_its_own() -> None:
    """AI-POL-002 keeps the default and the ceiling apart; a form needs both at once.

    :meth:`EffectivePolicy.ceiling` exists so S1 can hand a caller the two matrices in the
    same shape: subtracting the effective one from it is what leaves the widenings a
    relaxation may actually make, and an offering built from the default alone would invite
    a request S3 answers with ``AI_POLICY_RELAXATION_DENIED``.
    """
    ceiling = EffectivePolicy.ceiling()
    assert ceiling.to_payload()["allowed"] == {
        level.value: sorted(member.value for member in classes)
        for level, classes in POLICY_MATRIX.items()
    }
    default = EffectivePolicy.organisation_default()
    levels = [DataClassification(level) for level in default.to_payload()["allowed"]]
    assert all(default.classes_for(level) <= ceiling.classes_for(level) for level in levels), (
        "the default policy must sit inside the ceiling it can be widened toward"
    )
    # the payload round trip keeps the ceiling a policy, not a special case: a caller that
    # reads it back gets the same table
    assert EffectivePolicy.from_payload(ceiling.to_payload()).to_payload()["allowed"] == ceiling.to_payload()["allowed"]


def test_ac_pol_02_a_reloaded_policy_cannot_open_phi_pii() -> None:
    """AC-POL-02 / AI-POL-005: *no* policy — including one read back from the database —
    may make PHI_PII sendable.

    This was the bug report, and it is the regression test now that ``from_payload``
    forces ``PHI_PII`` empty on the way in (``policy/model.py``), the way
    ``POLICY_MATRIX``, :meth:`EffectivePolicy.strictest` and
    ``normalize._from_levels`` already did.  It stays because the row it reads is data:
    a migration, a bug or a hand-edited database can set anything, and the round trip
    this file pins above cannot catch that, because no policy S3 *writes* has a
    non-empty PHI_PII row.
    """
    reloaded = EffectivePolicy.from_payload(
        {"allowed": {"PHI_PII": ["LOCAL_ONLY"], "PUBLIC": ["LOCAL_ONLY"]}}
    )
    verdict = evaluate(
        [_item(DataClassification.PHI_PII, ref="artifact:patient-list")],
        reloaded,
        ProviderClass.LOCAL_ONLY,
    )
    assert not verdict.allowed, (
        "from_payload reconstructed a policy that sends PHI_PII to a model: AC-POL-02 "
        "says that is impossible under any policy"
    )
