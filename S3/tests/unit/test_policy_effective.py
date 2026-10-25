"""AI-POL-004 / AI-BR-04: the effective policy is the strictest of them all.

"Most restrictive" is computed as a pointwise **set intersection** over the five
classification levels, not as a ranking of provider classes.  Intersection is
commutative, idempotent and associative, so the answer cannot drift if a caller passes
the environments in another order, passes one twice, or passes a class this build has
never seen — which is exactly what D-07 and AI-POL-004 require (SRS 3.5, SRS 4).

The algebraic consequence that matters most is the one AI-POL-005 leans on: the empty
set is the absorbing element of intersection, so a PHI_PII row of ∅ survives any number
of combinations untouched.
"""

from __future__ import annotations

from itertools import combinations, product
from typing import Final

import pytest
from factory_foundation.api import DataClassification

from factory_intelligence.enums import POLICY_MATRIX, PROVIDER_CLASS_RANK, ProviderClass
from factory_intelligence.policy.engine import evaluate
from factory_intelligence.policy.model import ContextItem, EffectivePolicy

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
_ALL: frozenset[ProviderClass] = frozenset({_LO, _PN, _AE, _UR})

#: a ladder of class sets from "nowhere" to "everywhere", for the exhaustive sweeps
_SUBSETS: Final[list[frozenset[ProviderClass]]] = [
    frozenset(),
    frozenset([_LO]),
    frozenset([_PN]),
    frozenset([_AE]),
    frozenset([_LO, _PN]),
    frozenset([_LO, _PN, _AE]),
    _ALL,
]


def _subset_id(subset: frozenset[ProviderClass]) -> str:
    return "+".join(sorted(member.value for member in subset)) or "nowhere"


def _policy(
    allowed: dict[DataClassification, frozenset[ProviderClass]],
    *,
    source: str = "p",
    warnings: tuple[str, ...] = (),
) -> EffectivePolicy:
    """A policy stated level by level; an unstated level is denied.

    That is the production rule too — :class:`EffectivePolicy` treats a level missing
    from a source policy as sendable nowhere (AI-BR-03), never as unrestricted.
    """
    full = {level: allowed.get(level, frozenset()) for level in _LEVELS}
    return EffectivePolicy(allowed=full, sources=(source,), warnings=warnings)


def _widest() -> EffectivePolicy:
    return _policy({level: _ALL for level in _LEVELS}, source="widest")


def _org_default() -> EffectivePolicy:
    return EffectivePolicy.organisation_default()


#: An organisation that has widened every level all the way to the AI-POL-001 ceiling.
#: Since AI-POL-002's default denies both external classes, a requirement whose premise is
#: "the organisation allows APPROVED_EXTERNAL" has to be stated with this policy: the
#: default no longer says it, and a test that quietly used the default would be testing a
#: different scenario than the one the SRS wrote.
_CEILING: Final[EffectivePolicy] = EffectivePolicy(allowed=dict(POLICY_MATRIX), sources=("org",))


def _speaks_about(
    level: DataClassification, classes: frozenset[ProviderClass], *, key: str = "prod-1"
) -> EffectivePolicy:
    """An environment that narrows one level and leaves the others as they were.

    Only this helper reads silence as "no opinion"; the real parser reads it as "deny",
    and :func:`test_ai_ac_06_leaves_the_levels_an_environment_says_nothing_about_denied`
    pins the difference.
    """
    return _policy(
        {target: (classes if target is level else _ALL) for target in _LEVELS},
        source=f"env:{key}",
    )


def _uniform(classes: frozenset[ProviderClass], *, source: str = "env:p") -> EffectivePolicy:
    return _policy({level: classes for level in _LEVELS}, source=source)


def _allowed(policy: EffectivePolicy) -> dict[DataClassification, frozenset[ProviderClass]]:
    return {level: policy.classes_for(level) for level in _LEVELS}


def _item(level: DataClassification) -> ContextItem:
    return ContextItem(ref=f"artifact:{level.value.lower()}", classification=level)


# --- the operation --------------------------------------------------------------


def test_combine_is_a_pointwise_intersection() -> None:
    """AI-POL-004: per level, an effective class must be allowed by *every* source."""
    org = _policy(
        {
            DataClassification.PUBLIC: frozenset([_LO, _PN, _AE]),
            DataClassification.INTERNAL: frozenset([_LO, _PN]),
            DataClassification.CONFIDENTIAL: frozenset([_LO]),
        },
        source="org",
    )
    env = _policy(
        {
            DataClassification.PUBLIC: frozenset([_PN, _AE, _UR]),
            DataClassification.INTERNAL: frozenset([_LO, _AE]),
            DataClassification.CONFIDENTIAL: frozenset([_LO, _PN]),
        },
        source="env:prod",
    )
    combined = EffectivePolicy.combine([org, env])
    for level in _LEVELS:
        assert combined.classes_for(level) == org.classes_for(level) & env.classes_for(level)
    assert combined.classes_for(DataClassification.PUBLIC) == frozenset([_PN, _AE])
    assert combined.classes_for(DataClassification.CONFIDENTIAL) == frozenset([_LO])


def test_two_environments_that_disagree_close_the_level() -> None:
    """Disagreement is resolved by refusing: no provider is allowed by both, so none may
    be used (AI-BR-03)."""
    left = _policy({DataClassification.CONFIDENTIAL: frozenset([_LO])}, source="env:a")
    right = _policy({DataClassification.CONFIDENTIAL: frozenset([_PN])}, source="env:b")
    combined = EffectivePolicy.combine([_org_default(), left, right])
    assert combined.classes_for(DataClassification.CONFIDENTIAL) == frozenset()
    for provider_class in (_LO, _PN):
        assert not evaluate([_item(DataClassification.CONFIDENTIAL)], combined, provider_class).allowed


@pytest.mark.parametrize(
    ("first", "second"), list(product(_SUBSETS, _SUBSETS))[:24],
    ids=[f"{_subset_id(a)}-vs-{_subset_id(b)}" for a, b in list(product(_SUBSETS, _SUBSETS))[:24]],
)
def test_combine_is_commutative(
    first: frozenset[ProviderClass], second: frozenset[ProviderClass]
) -> None:
    """AI-POL-004: S1 assembles the environment list from its own tables, so the answer
    may not depend on the order it arrives in."""
    left = _policy({DataClassification.INTERNAL: first}, source="a")
    right = _policy({DataClassification.INTERNAL: second}, source="b")
    forward = _allowed(EffectivePolicy.combine([left, right]))
    backward = _allowed(EffectivePolicy.combine([right, left]))
    assert forward[DataClassification.INTERNAL] == backward[DataClassification.INTERNAL]


@pytest.mark.parametrize("subset", _SUBSETS, ids=[_subset_id(sub) for sub in _SUBSETS])
def test_combine_is_idempotent(subset: frozenset[ProviderClass]) -> None:
    """Saying it twice changes nothing: an environment reported by two agents of the same
    project cannot halve the permission."""
    policy = _uniform(subset, source="env:same")
    assert _allowed(EffectivePolicy.combine([policy, policy])) == _allowed(policy)


def test_combine_is_associative() -> None:
    """Grouping the list differently — per stage, per project — gives one answer."""
    org, env_a, env_b = (
        _policy({DataClassification.INTERNAL: frozenset([_LO, _PN, _AE])}, source="org"),
        _policy({DataClassification.INTERNAL: frozenset([_PN, _AE])}, source="env:a"),
        _policy({DataClassification.INTERNAL: frozenset([_LO, _PN])}, source="env:b"),
    )
    left = _allowed(EffectivePolicy.combine([EffectivePolicy.combine([org, env_a]), env_b]))
    right = _allowed(EffectivePolicy.combine([org, EffectivePolicy.combine([env_a, env_b])]))
    assert left == right
    assert left[DataClassification.INTERNAL] == frozenset([_PN])


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_combining_n_policies_is_a_subset_of_each_one(count: int) -> None:
    """AI-AC-06 in general form: the effective policy can only ever be narrower than any
    single input, never wider, whatever the number of environments."""
    members = [
        _uniform(_SUBSETS[(index + 3) % len(_SUBSETS)], source=f"env:{index}")
        for index in range(count)
    ]
    combined = EffectivePolicy.combine(members)
    for member in members:
        for level in _LEVELS:
            assert combined.classes_for(level) <= member.classes_for(level)


def test_combining_nothing_falls_back_to_the_organisation_answer() -> None:
    """The identity of the fold is not "allow everything": a project whose environments
    report no policy is judged by the organisation's (AI-POL-002)."""
    assert EffectivePolicy.combine([]) == EffectivePolicy.organisation_default()


def test_the_widest_possible_policy_is_the_identity_of_combine() -> None:
    """∩ U = itself.  Adding an environment that allows every class must not narrow a
    thing — and must still not open PHI_PII, which the empty set absorbs."""
    org = _org_default()
    combined = EffectivePolicy.combine([org, _widest()])
    assert _allowed(combined) == _allowed(org)
    assert combined.classes_for(DataClassification.PHI_PII) == frozenset()


def test_an_unstated_level_denies_rather_than_permits() -> None:
    """AI-BR-03: a partial document is an unsure document.  ``EffectivePolicy`` was
    built with all five levels by every parser, so this is the hand-written case; the
    fold must not invent a permission for the missing rows."""
    partial = EffectivePolicy(
        allowed={DataClassification.PUBLIC: frozenset([_LO])}, sources=("env:partial",)
    )
    combined = EffectivePolicy.combine([_org_default(), partial])
    assert combined.classes_for(DataClassification.PUBLIC) == frozenset([_LO])
    for level in _LEVELS[1:]:
        assert combined.classes_for(level) == frozenset(), level.value


# --- AI-AC-06 -------------------------------------------------------------------


def test_ai_ac_06_an_environment_denying_approved_external_wins() -> None:
    """AI-AC-06: the organisation allows APPROVED_EXTERNAL for INTERNAL, one target
    environment allows only PRIVATE_NETWORK ⇒ the effective policy denies
    APPROVED_EXTERNAL.

    "The organisation allows" is a decision and not a starting state: AI-POL-002's default
    stops INTERNAL at PRIVATE_NETWORK, so the organisation half of this scenario is the
    AI-POL-001 ceiling reached by a relaxation.  What the environment wins is the half
    under test, and it is unchanged by which of the two constants the org row holds.
    """
    environment = _speaks_about(DataClassification.INTERNAL, frozenset([_PN]))
    assert not _org_default().allows(DataClassification.INTERNAL, _AE), (
        "the default cannot supply AI-AC-06's premise; this scenario needs a widened org"
    )
    effective = EffectivePolicy.combine([_CEILING, environment])
    assert _CEILING.allows(DataClassification.INTERNAL, _AE)
    assert not effective.allows(DataClassification.INTERNAL, _AE)
    assert effective.allows(DataClassification.INTERNAL, _PN)
    verdict = evaluate([_item(DataClassification.INTERNAL)], effective, _AE)
    assert not verdict.allowed
    assert "APPROVED_EXTERNAL" in verdict.denied[0].reason


def test_ai_ac_06_leaves_the_levels_an_environment_says_nothing_about_denied() -> None:
    """The real parser turns "nothing said" into "nowhere", so a partial environment
    document narrows every level at once — the fail-safe half of AI-POL-004."""
    silent_about_the_rest = EffectivePolicy(
        allowed={DataClassification.INTERNAL: frozenset([_PN])}, sources=("env:prod-1",)
    )
    effective = EffectivePolicy.combine([_org_default(), silent_about_the_rest])
    assert effective.classes_for(DataClassification.INTERNAL) == frozenset([_PN])
    for level in (
        DataClassification.PUBLIC,
        DataClassification.CONFIDENTIAL,
        DataClassification.RESTRICTED,
    ):
        assert effective.classes_for(level) == frozenset(), level.value


_ORG_VARIANTS: Final[list[EffectivePolicy]] = [
    _org_default(),
    _widest(),
    _uniform(frozenset([_LO]), source="org"),
    _policy(
        {
            DataClassification.PUBLIC: _ALL,
            DataClassification.INTERNAL: frozenset([_LO, _PN, _AE]),
            DataClassification.CONFIDENTIAL: frozenset(),
            DataClassification.RESTRICTED: frozenset(),
            DataClassification.PHI_PII: frozenset(),
        },
        source="org",
    ),
]

_ENV_VARIANTS: Final[list[EffectivePolicy]] = [
    _uniform(classes, source=f"env:{index}") for index, classes in enumerate(_SUBSETS)
]


@pytest.mark.parametrize(
    ("org_index", "env_index"),
    list(product(range(len(_ORG_VARIANTS)), range(len(_ENV_VARIANTS)))),
)
def test_the_effective_policy_is_never_wider_than_either_source(
    org_index: int, env_index: int
) -> None:
    """AI-POL-004 as a property over an exhaustive set of organisation/environment
    pairs: effective ⊆ org and effective ⊆ env, level by level."""
    org, environment = _ORG_VARIANTS[org_index], _ENV_VARIANTS[env_index]
    effective = EffectivePolicy.combine([org, environment])
    for level in _LEVELS:
        assert effective.classes_for(level) <= org.classes_for(level), level.value
        assert effective.classes_for(level) <= environment.classes_for(level), level.value


@pytest.mark.parametrize(("a", "b"), list(combinations(range(len(_ENV_VARIANTS)), 2))[:15])
def test_two_environments_never_rescue_a_denied_level(a: int, b: int) -> None:
    """An operator cannot open CONFIDENTIAL→APPROVED_EXTERNAL by adding a second
    permissive environment: the organisation's row is in the intersection too."""
    effective = EffectivePolicy.combine(
        [_org_default(), _ENV_VARIANTS[a], _ENV_VARIANTS[b], _widest()]
    )
    assert not effective.allows(DataClassification.CONFIDENTIAL, _AE)
    assert not effective.allows(DataClassification.RESTRICTED, _PN)
    assert not effective.allows(DataClassification.PHI_PII, _LO)


# --- AI-POL-005, algebraically --------------------------------------------------


@pytest.mark.parametrize("subset", _SUBSETS, ids=[_subset_id(sub) for sub in _SUBSETS])
def test_phi_pii_survives_any_number_of_widened_components(
    subset: frozenset[ProviderClass],
) -> None:
    """AI-POL-005: no combination widens PHI_PII, because the empty set absorbs
    everything and the organisation's row is empty."""
    widening = _uniform(subset, source="env:wider")
    for count in (1, 2, 3):
        # the organisation's own answer is always in a project's fold (AI-POL-004), and its
        # PHI_PII row is empty: that is what absorbs every widening attempt
        combined = EffectivePolicy.combine([_org_default(), _widest(), widening] * count)
        assert combined.classes_for(DataClassification.PHI_PII) == frozenset()
    # ... and once the organisation's own answer is in the fold, no level can be opened
    assert EffectivePolicy.combine([_org_default(), _widest()]).classes_for(
        DataClassification.CONFIDENTIAL
    ) == _org_default().classes_for(DataClassification.CONFIDENTIAL)


def test_the_empty_set_absorbs_every_subset_which_is_why_the_fold_is_an_intersection() -> None:
    """One line of arithmetic is the whole of AI-POL-005's guarantee here: ``∅ ∩ x == ∅``.
    A union would widen PHI_PII the moment one environment mentioned it, and AI-POL-004
    does not use a union."""
    for classes in _SUBSETS:
        assert frozenset() & classes == frozenset()
        assert frozenset() | classes == classes


# --- narrower_than and strictest -------------------------------------------------


def test_narrower_than_is_a_strict_subset_test() -> None:
    narrow = _policy({DataClassification.CONFIDENTIAL: frozenset([_LO])}, source="a")
    wide = _policy({DataClassification.CONFIDENTIAL: frozenset([_LO, _PN])}, source="b")
    assert narrow.narrower_than(wide)
    assert not wide.narrower_than(narrow)


def test_narrower_than_is_false_for_two_equal_policies() -> None:
    """Same permissions, so neither is narrower — "strict subset", not "subset".  A
    report that called a policy narrower than itself would invent a difference no
    operator can act on."""
    first = _org_default()
    second = EffectivePolicy.from_payload(first.to_payload())
    assert _allowed(first) == _allowed(second)
    assert not first.narrower_than(second)
    assert not second.narrower_than(first)


def test_narrower_than_is_false_for_incomparable_policies() -> None:
    """Two levels moving in opposite directions is not "stricter overall"; ranking them
    anyway is how a hand-written ordering goes wrong (AI-POL-004)."""
    left = _policy(
        {
            DataClassification.PUBLIC: frozenset([_LO]),
            DataClassification.INTERNAL: frozenset([_LO, _PN]),
        },
        source="a",
    )
    right = _policy(
        {
            DataClassification.PUBLIC: frozenset([_LO, _PN]),
            DataClassification.INTERNAL: frozenset([_LO]),
        },
        source="b",
    )
    assert not left.narrower_than(right)
    assert not right.narrower_than(left)


def test_the_combine_of_two_is_always_narrower_than_or_equal_to_each() -> None:
    """The relation the reporting helper is used for, checked against the fold itself."""
    combined = EffectivePolicy.combine(
        [_org_default(), _speaks_about(DataClassification.INTERNAL, frozenset([_PN]))]
    )
    assert combined.narrower_than(_org_default())
    assert not _org_default().narrower_than(combined)


def test_strictest_sends_everything_local_and_nothing_phi() -> None:
    """AI-POL-005, AI-BR-03: the fail-safe keeps the four levels inside the boundary and
    keeps PHI_PII out of any model."""
    narrow = EffectivePolicy.strictest()
    for level in _LEVELS[:-1]:
        assert narrow.classes_for(level) == frozenset([_LO]), level.value
    assert narrow.classes_for(DataClassification.PHI_PII) == frozenset()
    assert narrow.sources == ("fail-safe",)


def test_strictest_is_narrower_than_the_organisation_default() -> None:
    assert EffectivePolicy.strictest().narrower_than(EffectivePolicy.organisation_default())


def test_strictest_denies_both_external_classes_for_every_level() -> None:
    narrow = EffectivePolicy.strictest()
    for level in _LEVELS:
        for provider_class in (_AE, _UR):
            assert not evaluate([_item(level)], narrow, provider_class).allowed


def test_strictest_still_sends_public_data_locally() -> None:
    """The fail-safe is narrow, not empty: a run against a local model must stay
    possible, or AI-ASM-05's "manual authoring only" fallback would be the default."""
    assert evaluate(
        [_item(DataClassification.PUBLIC)], EffectivePolicy.strictest(), _LO
    ).allowed


def test_combine_of_a_source_with_the_strictest_answer_is_the_strictest_answer() -> None:
    """One unknown environment policy is enough to bring the whole project down to
    LOCAL_ONLY — which is why a normalisation failure must be *reported* (AI-POL-004)."""
    combined = EffectivePolicy.combine([_org_default(), EffectivePolicy.strictest()])
    assert _allowed(combined) == _allowed(EffectivePolicy.strictest())


# --- sources, warnings and the absence of a ranking ------------------------------


def test_combine_names_every_source_it_used() -> None:
    """AI-POL-008: the decision record says which policies were combined, so a
    surprising denial can be traced to the environment that caused it."""
    combined = EffectivePolicy.combine(
        [
            _org_default(),
            _uniform(frozenset([_LO]), source="env:prod-1"),
            _uniform(_ALL, source="env:prod-2"),
        ]
    )
    assert combined.sources == ("org", "env:prod-1", "env:prod-2")


def test_duplicate_sources_are_collapsed_but_warnings_are_not() -> None:
    """The same environment named twice is one policy; two notes about it are two
    facts the operator needs (AI-POL-004)."""
    left = EffectivePolicy(
        allowed={level: _ALL for level in _LEVELS}, sources=("env:prod-1",), warnings=("a",)
    )
    right = EffectivePolicy(
        allowed={level: frozenset([_LO]) for level in _LEVELS},
        sources=("env:prod-1",),
        warnings=("a", "b"),
    )
    combined = EffectivePolicy.combine([left, right])
    assert combined.sources == ("env:prod-1",)
    assert combined.warnings == ("a", "a", "b")


def test_the_effective_policy_is_not_computed_by_ranking_provider_classes() -> None:
    """AI-POL-004 says "most restrictive", which reads like a ranking — and is not one.

    Here the two disagree: the widest class the organisation mentions is
    APPROVED_EXTERNAL and the narrowest the environment mentions is LOCAL_ONLY, so a
    rank-based fold would answer with a single class.  The intersection answers "the one
    class both actually allow", which is the requirement.
    """
    org = _policy({DataClassification.INTERNAL: frozenset([_PN, _AE])}, source="org")
    env = _policy({DataClassification.INTERNAL: frozenset([_LO, _PN])}, source="env:a")
    combined = EffectivePolicy.combine([org, env])
    assert combined.classes_for(DataClassification.INTERNAL) == frozenset([_PN])
    assert not combined.allows(DataClassification.INTERNAL, _LO)
    # the rank table still exists, purely to describe a difference in a report
    assert PROVIDER_CLASS_RANK[_LO] > PROVIDER_CLASS_RANK[_AE]
    assert org.rank_of(_AE) != org.rank_of(_UR)


def test_classes_for_and_allows_never_disagree() -> None:
    """Two accessors, one answer — a drift here would make the audit payload disagree
    with the verdict (AI-POL-008)."""
    combined = EffectivePolicy.combine(
        [_org_default(), _speaks_about(DataClassification.CONFIDENTIAL, frozenset())]
    )
    for level in _LEVELS:
        for provider_class in ProviderClass:
            assert combined.allows(level, provider_class) is (
                provider_class in combined.classes_for(level)
            )
