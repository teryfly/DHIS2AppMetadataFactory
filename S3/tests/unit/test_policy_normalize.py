"""The defensive parser: S1's untyped document in, one policy out (AI-POL-004).

``normalize_one`` is the highest-leverage defensive code in S3.  S1 forwards whatever
S4's ``data_export_policy`` happens to be — today that is S4's protection document
(``llmDataPolicy`` plus bookkeeping), and tomorrow another subsystem may spell it
differently — so the shape is not something S3 may rely on.  The
two rules the module states are pinned here, in both directions:

1. a document is honoured only when it says which provider classes it allows, in one of
   the spellings below;
2. anything unrecognised, ambiguous or empty becomes the **strictest** policy *with a
   warning that says which scope was not understood* — never the widest reading, never a
   silent pass.

Rule 2 is what keeps a schema change in another subsystem from becoming a privacy
incident here (AI-BR-03: the default answer is no).
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import DataClassification

from factory_intelligence.enums import POLICY_MATRIX, SYSTEM_DEFAULT_MATRIX, ProviderClass
from factory_intelligence.policy.model import EffectivePolicy
from factory_intelligence.policy.normalize import (
    normalize_environment_policies,
    normalize_one,
    org_policy_from_payload,
)

_LEVELS: tuple[DataClassification, ...] = (
    DataClassification.PUBLIC,
    DataClassification.INTERNAL,
    DataClassification.CONFIDENTIAL,
    DataClassification.RESTRICTED,
    DataClassification.PHI_PII,
)

LOCAL_ONLY = ProviderClass.LOCAL_ONLY.value
PRIVATE_NETWORK = ProviderClass.PRIVATE_NETWORK.value
APPROVED_EXTERNAL = ProviderClass.APPROVED_EXTERNAL.value
UNRESTRICTED = ProviderClass.UNRESTRICTED.value

_INNER_LEVELS: dict[str, list[str]] = {
    DataClassification.PUBLIC.value: [LOCAL_ONLY, PRIVATE_NETWORK],
    DataClassification.INTERNAL.value: [LOCAL_ONLY, PRIVATE_NETWORK],
    DataClassification.CONFIDENTIAL.value: [LOCAL_ONLY],
    DataClassification.RESTRICTED.value: [LOCAL_ONLY],
}


def _levels_of(policy: EffectivePolicy) -> dict[str, set[str]]:
    """The parsed result in the same shape the documents arrive in."""
    return {
        level.value: {member.value for member in policy.classes_for(level)}
        for level in _LEVELS
    }


def _expected(**by_level: list[str]) -> dict[str, set[str]]:
    """An expectation for the levels named; every other level is expected to be denied."""
    stated = {key.upper(): set(value) for key, value in by_level.items()}
    return {level.value: stated.get(level.value, set()) for level in _LEVELS}


def _all_levels(classes: list[str]) -> dict[str, set[str]]:
    return _expected(
        **{level.value: list(classes) for level in _LEVELS if level is not DataClassification.PHI_PII}
    )


def _assert_fail_safe(policy: EffectivePolicy, scope: str) -> None:
    """The strictest non-degenerate reading, the scope named, and a note that says why."""
    assert _levels_of(policy) == {
        level.value: ({LOCAL_ONLY} if level is not DataClassification.PHI_PII else set())
        for level in _LEVELS
    }
    assert policy.warnings, "a fail-safe that says nothing is a silent downgrade"
    assert any(scope in warning for warning in policy.warnings), policy.warnings
    assert any("fail-safe" in warning for warning in policy.warnings), policy.warnings


# --- the accepted spellings ------------------------------------------------------

#: (label, document, expected per-level result).  Every entry is a spelling some real
#: system has used or will use; the point of the table is that a new spelling may only
#: ever *narrow* the answer, and that these ones are understood at all.
ACCEPTED_FORMS: list[tuple[str, Any, dict[str, set[str]]]] = [
    (
        "per-level dict",
        dict(_INNER_LEVELS, **{DataClassification.PHI_PII.value: [LOCAL_ONLY]}),
        _expected(
            PUBLIC=[LOCAL_ONLY, PRIVATE_NETWORK],
            INTERNAL=[LOCAL_ONLY, PRIVATE_NETWORK],
            CONFIDENTIAL=[LOCAL_ONLY],
            RESTRICTED=[LOCAL_ONLY],
        ),
    ),
    (
        "level-keyed lowercase",
        {key.lower(): value for key, value in _INNER_LEVELS.items()},
        _expected(
            public=[LOCAL_ONLY, PRIVATE_NETWORK],
            internal=[LOCAL_ONLY, PRIVATE_NETWORK],
            confidential=[LOCAL_ONLY],
            restricted=[LOCAL_ONLY],
        ),
    ),
    (
        "allowedProviderClasses",
        {"allowedProviderClasses": [LOCAL_ONLY, PRIVATE_NETWORK]},
        _all_levels([LOCAL_ONLY, PRIVATE_NETWORK]),
    ),
    (
        "allowed_provider_classes",
        {"allowed_provider_classes": [LOCAL_ONLY]},
        _all_levels([LOCAL_ONLY]),
    ),
    (
        "allowedClasses alias",
        {"allowedClasses": [PRIVATE_NETWORK]},
        _all_levels([PRIVATE_NETWORK]),
    ),
    (
        "single providerClass string",
        {"providerClass": PRIVATE_NETWORK},
        _all_levels([PRIVATE_NETWORK]),
    ),
    (
        "maxProviderClass",
        {"maxProviderClass": LOCAL_ONLY},
        _all_levels([LOCAL_ONLY]),
    ),
    (
        "class",
        {"class": APPROVED_EXTERNAL},
        _all_levels([APPROVED_EXTERNAL]),
    ),
    (
        "bare class name as the whole document",
        LOCAL_ONLY,
        _all_levels([LOCAL_ONLY]),
    ),
    (
        "nested dataExportPolicy wrapper",
        {"dataExportPolicy": {"allowedClassesByLevel": _INNER_LEVELS}},
        _expected(
            PUBLIC=[LOCAL_ONLY, PRIVATE_NETWORK],
            INTERNAL=[LOCAL_ONLY, PRIVATE_NETWORK],
            CONFIDENTIAL=[LOCAL_ONLY],
            RESTRICTED=[LOCAL_ONLY],
        ),
    ),
    (
        "snake data_export_policy wrapper",
        {"data_export_policy": {"allowedProviderClasses": [LOCAL_ONLY]}},
        _all_levels([LOCAL_ONLY]),
    ),
    (
        "allowedClassesByLevel",
        {"allowedClassesByLevel": _INNER_LEVELS},
        _expected(
            PUBLIC=[LOCAL_ONLY, PRIVATE_NETWORK],
            INTERNAL=[LOCAL_ONLY, PRIVATE_NETWORK],
            CONFIDENTIAL=[LOCAL_ONLY],
            RESTRICTED=[LOCAL_ONLY],
        ),
    ),
    (
        "byLevel",
        {"byLevel": _INNER_LEVELS},
        _expected(
            PUBLIC=[LOCAL_ONLY, PRIVATE_NETWORK],
            INTERNAL=[LOCAL_ONLY, PRIVATE_NETWORK],
            CONFIDENTIAL=[LOCAL_ONLY],
            RESTRICTED=[LOCAL_ONLY],
        ),
    ),
    (
        "levels",
        {"levels": _INNER_LEVELS},
        _expected(
            PUBLIC=[LOCAL_ONLY, PRIVATE_NETWORK],
            INTERNAL=[LOCAL_ONLY, PRIVATE_NETWORK],
            CONFIDENTIAL=[LOCAL_ONLY],
            RESTRICTED=[LOCAL_ONLY],
        ),
    ),
    (
        "one class named for a level, as a string not a list",
        {"allowedClassesByLevel": {DataClassification.INTERNAL.value: LOCAL_ONLY}},
        _expected(INTERNAL=[LOCAL_ONLY]),
    ),
    (
        "a set instead of a list",
        {"allowedProviderClasses": {LOCAL_ONLY, PRIVATE_NETWORK}},
        _all_levels([LOCAL_ONLY, PRIVATE_NETWORK]),
    ),
]


@pytest.mark.parametrize(
    "label,document,expected",
    ACCEPTED_FORMS,
    ids=[name for name, _d, _e in ACCEPTED_FORMS],
)
def test_a_documented_spelling_is_understood(
    label: str, document: Any, expected: dict[str, set[str]]
) -> None:
    """AI-POL-004: S1 hands over an untyped dict, and the understood spellings are the
    contract — each one must produce the permission it states, not a narrower guess."""
    _ = label
    policy = normalize_one(document, scope="env:prod-1")
    assert _levels_of(policy) == expected
    assert policy.sources == ("env:prod-1",)


def test_a_document_that_states_all_five_levels_needs_no_commentary() -> None:
    """The one input that is fully understood is the only one that should come back
    silent: warnings are for the operator, and a warning per successful parse teaches
    nobody to read them."""
    complete = {level.value: sorted(member.value for member in POLICY_MATRIX[level]) for level in _LEVELS}
    policy = normalize_one({"allowedClassesByLevel": complete}, scope="env:prod-1")
    assert policy.warnings == ()
    assert _levels_of(policy) == {
        level.value: {member.value for member in POLICY_MATRIX[level]} for level in _LEVELS
    }


def test_the_wrapper_is_reported_as_a_descent_not_a_guess() -> None:
    """A document one level down is understood, and the note says so, because the
    alternative — quietly reading a nested object — is how a schema change goes unseen."""
    policy = normalize_one(
        {"dataExportPolicy": {"allowedClassesByLevel": _INNER_LEVELS}}, scope="env:prod-1"
    )
    assert any("descended" in warning for warning in policy.warnings), policy.warnings


def test_an_envelope_with_nothing_inside_it_fails_safe() -> None:
    """``{"dataExportPolicy": null}`` is S1's "the environment told us nothing", and the
    answer to that is LOCAL_ONLY, not the organisation's permission."""
    _assert_fail_safe(
        normalize_one({"dataExportPolicy": None}, scope="env:prod-1"), "env:prod-1"
    )


def test_an_empty_envelope_denies_every_level_rather_than_falling_back() -> None:
    """A per-level document that states *no* level is understood — and says nothing may
    go anywhere.  That is narrower than the fail-safe, and correct: the caller claimed to
    speak per level and then withheld every level (AI-BR-03)."""
    policy = normalize_one({"allowedClassesByLevel": {}}, scope="env:prod-1")
    assert _levels_of(policy) == {level.value: set() for level in _LEVELS}
    assert policy.warnings and all("env:prod-1" in warning for warning in policy.warnings)


def test_a_level_map_wins_over_a_flat_list_when_both_are_present() -> None:
    """An ambiguous document is read by the most specific half of it; the flat list would
    have opened RESTRICTED to PRIVATE_NETWORK."""
    policy = normalize_one(
        {
            "allowedProviderClasses": [LOCAL_ONLY, PRIVATE_NETWORK, APPROVED_EXTERNAL],
            "allowedClassesByLevel": {DataClassification.RESTRICTED.value: [LOCAL_ONLY]},
        },
        scope="env:prod-1",
    )
    assert policy.classes_for(DataClassification.RESTRICTED) == frozenset({LOCAL_ONLY})
    assert policy.classes_for(DataClassification.PUBLIC) == frozenset()


@pytest.mark.parametrize(
    "level", _LEVELS, ids=[level.value for level in _LEVELS]
)
def test_phi_pii_is_never_opened_by_a_flat_allow_list(level: DataClassification) -> None:
    """AI-POL-005: a flat allow-list is one set for every level *by construction except
    PHI_PII*, so the shape cannot become a way to send patient data out."""
    policy = normalize_one({"allowedProviderClasses": [UNRESTRICTED]}, scope="env:prod-1")
    if level is DataClassification.PHI_PII:
        assert policy.classes_for(level) == frozenset()
    else:
        assert policy.classes_for(level) == frozenset({UNRESTRICTED})


def test_a_stated_phi_allowance_is_dropped() -> None:
    """A per-level document that lists a class for PHI_PII is refused that row
    (AI-POL-005), whatever the caller believed."""
    policy = normalize_one(
        {"allowedClassesByLevel": {DataClassification.PHI_PII.value: [LOCAL_ONLY]}},
        scope="env:prod-1",
    )
    assert policy.classes_for(DataClassification.PHI_PII) == frozenset()


def test_a_single_class_document_cannot_open_phi_pii_either() -> None:
    policy = normalize_one({"providerClass": LOCAL_ONLY}, scope="env:prod-1")
    assert policy.classes_for(DataClassification.PHI_PII) == frozenset()
    assert policy.classes_for(DataClassification.RESTRICTED) == frozenset({LOCAL_ONLY})


def test_s4s_policy_document_is_read_as_the_ceiling_it_states() -> None:
    """The exact document S4's environment protection answers (DX-ENV-001), forwarded verbatim by S1.

    Until ``llmDataPolicy`` was recognised this arrived as ``unrecognised keys ['environmentClass',
    'llmDataPolicy', …]`` and collapsed to the fail-safe — which is doubly wrong: the operator's
    setting decided nothing, and a *wider* ceiling denied the *stricter* providers by naming one class.
    """
    document = {
        "llmDataPolicy": APPROVED_EXTERNAL,
        "rank": 1,
        "environmentClass": "TEST",
        "refusedWhen": "外部 LLM 提供方类别严于该策略时不得外发（D-07 由调用方合成）",
        "outsideCommitWindowReason": None,
    }
    policy = normalize_one(document, scope="env:dev")
    assert policy.classes_for(DataClassification.INTERNAL) == {
        ProviderClass.APPROVED_EXTERNAL,
        ProviderClass.PRIVATE_NETWORK,
        ProviderClass.LOCAL_ONLY,
    }
    assert ProviderClass.UNRESTRICTED not in policy.classes_for(DataClassification.CONFIDENTIAL), (
        "the ceiling is the ceiling"
    )
    assert policy.classes_for(DataClassification.PHI_PII) == frozenset(), (
        "AI-POL-005: no ceiling is a licence to send patient data"
    )
    assert any("read as a ceiling" in warning for warning in policy.warnings)


def test_an_unknown_ceiling_still_fails_safe() -> None:
    policy = normalize_one({"llmDataPolicy": "CLOUD_ANYWHERE"}, scope="env:dev")
    assert policy.classes_for(DataClassification.PUBLIC) == frozenset({ProviderClass.LOCAL_ONLY})
    assert any("unknown provider class" in warning for warning in policy.warnings)


def test_a_document_wider_than_the_matrix_is_parsed_as_stated() -> None:
    """The parser reports what a document says; AI-POL-001's ceiling is enforced by the
    intersection with the organisation's policy, which is where it belongs — an
    environment cannot be the only judge of its own data."""
    policy = normalize_one({"allowedProviderClasses": [UNRESTRICTED]}, scope="env:prod-1")
    assert policy.classes_for(DataClassification.CONFIDENTIAL) == frozenset({UNRESTRICTED})
    combined = EffectivePolicy.combine([EffectivePolicy.organisation_default(), policy])
    assert not combined.allows(DataClassification.CONFIDENTIAL, ProviderClass.UNRESTRICTED)
    assert not combined.allows(DataClassification.CONFIDENTIAL, ProviderClass.APPROVED_EXTERNAL)


# --- the fail-safe ---------------------------------------------------------------

UNUNDERSTOOD: list[tuple[str, Any]] = [
    ("None", None),
    ("empty mapping", {}),
    ("unrecognised key", {"provider_policy": [LOCAL_ONLY]}),
    ("unrecognised key only", {"maxClass": "LOCAL"}),
    ("empty flat list", {"allowedProviderClasses": []}),
    ("flat list of nonsense", {"allowedProviderClasses": ["ON_PREMISE_MAYBE"]}),
    ("unknown provider class name", {"providerClass": "CLOUD_ANYWHERE"}),
    ("a list", [LOCAL_ONLY]),
    ("a bare non-class string", "PRIVATE"),
    ("an integer", 7),
    ("a boolean", True),
    ("the wrapper with nothing in it", {"dataExportPolicy": {}}),
    ("a policy key holding a string", {"levels": "PUBLIC"}),
    ("nested nonsense", {"config": {"allowedProviderClasses": [LOCAL_ONLY]}}),
    ("a decision payload handed to the org reader", {"allowed": {}}),
]


@pytest.mark.parametrize("label,document", UNUNDERSTOOD, ids=[name for name, _d in UNUNDERSTOOD])
def test_an_ununderstood_document_becomes_the_strictest_policy(
    label: str, document: Any
) -> None:
    """AI-BR-03 / AI-POL-004 rule 2: unsure means narrow.  Anything this parser cannot
    read becomes LOCAL_ONLY for the four levels and nowhere for PHI_PII — and says so."""
    _ = label
    _assert_fail_safe(normalize_one(document, scope="env:prod-1"), "env:prod-1")


@pytest.mark.parametrize("label,document", UNUNDERSTOOD, ids=[name for name, _d in UNUNDERSTOOD])
def test_the_fail_safe_warning_names_the_scope_and_the_reason(
    label: str, document: Any
) -> None:
    """A denied run must be explainable to the operator who reads the log line: the
    warning carries the environment it is about, never a bare "policy invalid"."""
    _ = label
    policy = normalize_one(document, scope="env:ward-prod")
    assert policy.warnings
    for warning in policy.warnings:
        assert warning.startswith("env:ward-prod") or "descended" in warning, warning
    assert any(
        "env:ward-prod" in warning and "fail-safe" in warning for warning in policy.warnings
    )


def test_a_default_scope_is_recorded() -> None:
    """``scope`` has a default because callers may not have an environment key; the
    warning still has to say which policy it means."""
    policy = normalize_one({})
    assert policy.sources == ("env",)
    assert any(warning.startswith("env:") for warning in policy.warnings)


@pytest.mark.parametrize(
    "level", [level for level in _LEVELS if level is not DataClassification.PHI_PII],
    ids=[level.value for level in _LEVELS if level is not DataClassification.PHI_PII],
)
def test_an_omitted_level_denies_everything_for_that_level(
    level: DataClassification,
) -> None:
    """AI-BR-03: a per-level document that stays silent about a level is unsure about it,
    and unsure is nowhere — never the organisation's own permission."""
    stated = {
        other.value: [LOCAL_ONLY]
        for other in _LEVELS
        if other is not level and other is not DataClassification.PHI_PII
    }
    policy = normalize_one({"allowedClassesByLevel": stated}, scope="env:prod-1")
    assert policy.classes_for(level) == frozenset()
    warning = f"env:prod-1.{level.value}: not stated, treated as no provider"
    assert warning in policy.warnings


def test_a_partial_document_is_reported_level_by_level() -> None:
    """One warning per silent level, naming each: "we guessed" is not actionable, "we
    guessed about CONFIDENTIAL" is."""
    policy = normalize_one(
        {"allowedClassesByLevel": {DataClassification.PUBLIC.value: [LOCAL_ONLY]}},
        scope="env:prod-1",
    )
    reported = [warning for warning in policy.warnings if "not stated" in warning]
    assert len(reported) == 3, "PHI_PII is nowhere in every policy, so it is not reported"
    assert any("INTERNAL" in warning for warning in reported)
    assert any("CONFIDENTIAL" in warning for warning in reported)
    assert any("RESTRICTED" in warning for warning in reported)
    assert not any("PHI_PII" in warning for warning in reported)


def test_the_fail_safe_is_narrower_than_the_organisation_default() -> None:
    """The whole point of the fallback: it may only ever cost the operator a run, never
    cost a project its data."""
    policy = normalize_one({"nonsense": True}, scope="env:prod-1")
    assert policy.narrower_than(EffectivePolicy.organisation_default())


def test_a_normalisation_warning_survives_onto_the_combined_policy() -> None:
    """AI-POL-004: notes are *reported*, not silently applied — a policy that quietly
    became LOCAL_ONLY would read as a broken model integration, not as a bad document."""
    env = normalize_one({"provider_policy": [LOCAL_ONLY]}, scope="env:prod-1")
    combined = EffectivePolicy.combine([EffectivePolicy.organisation_default(), env])
    assert combined.warnings == env.warnings
    assert any("env:prod-1" in warning for warning in combined.warnings)
    assert "org" in combined.sources and "env:prod-1" in combined.sources


# --- the environment list --------------------------------------------------------


def test_no_documents_at_all_is_not_a_policy() -> None:
    """What S1 sends today, because S4 does not exist: nothing.  The caller is then
    judged by the organisation's policy alone, with no invented environment warning."""
    assert normalize_environment_policies(None) == []
    assert normalize_environment_policies({}) == []
    assert normalize_environment_policies([]) == []


def test_each_environment_becomes_one_policy_named_by_its_key() -> None:
    """AI-POL-004: "全部目标环境" — one entry per target environment, and the scope in
    every warning is that environment's key."""
    policies = normalize_environment_policies(
        {
            "prod-1": {"allowedProviderClasses": [LOCAL_ONLY]},
            "ward-prod": None,
        }
    )
    assert [policy.sources for policy in policies] == [("env:prod-1",), ("env:ward-prod",)]
    assert policies[1].warnings[0].startswith("env:ward-prod")


def test_an_ordered_pair_list_is_accepted_because_that_is_what_a_caller_holds() -> None:
    policies = normalize_environment_policies(
        [("a", {"providerClass": LOCAL_ONLY}), ("b", {"providerClass": PRIVATE_NETWORK})]
    )
    assert [policy.sources[0] for policy in policies] == ["env:a", "env:b"]
    assert policies[0].classes_for(DataClassification.PUBLIC) == frozenset({LOCAL_ONLY})
    assert policies[1].classes_for(DataClassification.PUBLIC) == frozenset({PRIVATE_NETWORK})


def test_a_pair_sequence_keeps_its_order() -> None:
    """Two environments in the other order must yield the same list order the caller
    used, so a report can be read alongside the request."""
    forward = normalize_environment_policies([("e1", {}), ("e2", {})])
    backward = normalize_environment_policies([("e2", {}), ("e1", {})])
    assert [policy.sources[0] for policy in forward] == ["env:e1", "env:e2"]
    assert [policy.sources[0] for policy in backward] == ["env:e2", "env:e1"]


def test_a_non_string_environment_key_is_still_named() -> None:
    """S1 builds the map from its own environment table; a key that is not a string is
    still a scope somebody must be able to find in the warning."""
    documents: dict[Any, Any] = {1: None}
    policies = normalize_environment_policies(documents)
    assert policies[0].sources == ("env:1",)
    assert policies[0].warnings[0].startswith("env:1:")


def test_several_environments_combine_into_the_project_answer() -> None:
    """AI-BR-04 end to end at the parser boundary: the strictest of the lot."""
    policies = normalize_environment_policies(
        {
            "prod-1": {"allowedProviderClasses": [LOCAL_ONLY, PRIVATE_NETWORK]},
            "prod-2": {"providerClass": LOCAL_ONLY},
        }
    )
    effective = EffectivePolicy.combine([EffectivePolicy.organisation_default(), *policies])
    assert effective.classes_for(DataClassification.INTERNAL) == frozenset({LOCAL_ONLY})
    assert effective.classes_for(DataClassification.CONFIDENTIAL) == frozenset({LOCAL_ONLY})
    assert effective.classes_for(DataClassification.PHI_PII) == frozenset()
    assert effective.sources == ("org", "env:prod-1", "env:prod-2")


def test_one_unreadable_environment_narrows_the_project_without_narrowing_the_others_notes() -> None:
    """Every environment's warning is kept, so the operator can tell *which* document
    broke; and the unreadable one drags the whole project to LOCAL_ONLY."""
    policies = normalize_environment_policies({"good": {"providerClass": LOCAL_ONLY}, "bad": {}})
    effective = EffectivePolicy.combine([EffectivePolicy.organisation_default(), *policies])
    assert effective.classes_for(DataClassification.PUBLIC) == frozenset({LOCAL_ONLY})
    assert sum("env:bad" in warning and "fail-safe" in warning for warning in effective.warnings) == 1
    assert not any("env:good" in warning and "fail-safe" in warning for warning in effective.warnings)
    assert sum("env:good" in warning for warning in effective.warnings) == 1


# --- the stored organisation policy ----------------------------------------------


def test_no_stored_organisation_policy_is_the_system_default_matrix() -> None:
    """AI-POL-002: the system's starting point is :data:`SYSTEM_DEFAULT_MATRIX` — both
    external classes denied — and not :data:`POLICY_MATRIX`, which is only the ceiling a
    later relaxation may widen *to*.

    The assertion against :data:`POLICY_MATRIX` this test used to make is the collapse
    itself: it read the AI-POL-001 table as the default, which left nothing below the ceiling
    to widen and made every legal relaxation a recorded no-op.
    """
    baseline = org_policy_from_payload(None)
    assert _levels_of(baseline) == {
        level.value: {member.value for member in SYSTEM_DEFAULT_MATRIX[level]}
        for level in _LEVELS
    }
    assert baseline.sources == ("org",)
    assert baseline.warnings == ()
    # the two constants are different constants, or AI-POL-002 says nothing
    assert _levels_of(baseline) != {
        level.value: {member.value for member in POLICY_MATRIX[level]} for level in _LEVELS
    }
    assert not baseline.allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)
    assert not baseline.allows(DataClassification.PUBLIC, ProviderClass.UNRESTRICTED)


def test_an_absent_payload_is_the_system_default_too() -> None:
    """An empty row is "nobody has set an organisation policy", which is the same fact as
    having no row — and must not be read as "everything is allowed"."""
    payloads: list[dict[str, Any] | None] = [None, {}]
    for payload in payloads:
        assert _levels_of(org_policy_from_payload(payload)) == _levels_of(
            EffectivePolicy.organisation_default()
        )


def test_a_decision_payload_is_not_an_organisation_document() -> None:
    """Two JSON shapes, deliberately different: ``ai.policy_decision.effective_policy``
    stores :meth:`EffectivePolicy.to_payload`, while ``ai.org_policy`` stores the document
    this parser reads.  Handing the first to the second is not a silent no-op — it is the
    fail-safe, with a warning naming the key nobody recognised."""
    decision = EffectivePolicy.organisation_default().to_payload()
    policy = org_policy_from_payload(decision)
    assert policy.classes_for(DataClassification.PUBLIC) == frozenset({LOCAL_ONLY})
    assert any(
        "unrecognised keys" in warning and "'allowed'" in warning for warning in policy.warnings
    ), policy.warnings


def test_a_stored_organisation_policy_is_read_in_the_same_form_it_is_written() -> None:
    """AI-POL-002: the row an operator's relaxation leaves behind is read back as the
    widening it records.

    The document below is the ceiling, i.e. the most a legal sequence of relaxations can
    store, and it is deliberately *not* the default: the two constants differ at PUBLIC and
    INTERNAL, and those are exactly the two rows that would silently prove nothing if this
    round trip were asserted against the default.
    """
    payload = {
        "allowedClassesByLevel": {
            level.value: sorted(member.value for member in POLICY_MATRIX[level])
            for level in _LEVELS
        }
    }
    stored = org_policy_from_payload(payload)
    assert _levels_of(stored) == {
        level.value: {member.value for member in POLICY_MATRIX[level]} for level in _LEVELS
    }
    assert stored.sources == ("org",)
    assert stored.warnings == ()
    # the distinction the round trip has to preserve
    assert stored.allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)
    assert stored.allows(DataClassification.PUBLIC, ProviderClass.UNRESTRICTED)
    assert _levels_of(stored) != _levels_of(org_policy_from_payload(None))
    assert org_policy_from_payload(None).narrower_than(stored)
    # ... and PHI_PII stays empty whatever the row claims (AI-POL-005)
    assert stored.classes_for(DataClassification.PHI_PII) == frozenset()


def test_an_organisation_document_nobody_understood_does_not_become_open() -> None:
    """AI-POL-002: "组织或环境可显式放宽" — a garbled organisation document is not an
    implicit widening; it is the fail-safe, loudly."""
    policy = org_policy_from_payload({"organisationPolicy": "permissive"})
    _assert_fail_safe(policy, "org")
    assert policy.sources == ("org",)


def test_the_organisation_policy_cannot_open_phi_pii_by_storing_it() -> None:
    """AI-POL-005, from the other side: even the row an operator writes by hand goes
    through the same construction that denies PHI_PII."""
    policy = org_policy_from_payload(
        {"allowedClassesByLevel": {DataClassification.PHI_PII.value: [LOCAL_ONLY]}}
    )
    assert policy.classes_for(DataClassification.PHI_PII) == frozenset()
