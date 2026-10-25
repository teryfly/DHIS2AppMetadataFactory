"""The organisation policy row, its relaxations and their audit (AI-POL-002, AI-POL-004).

Three claims are pinned against the real ``ai`` and ``core`` schemas:

* with no row at all the answer is AI-POL-002's **default** — both external classes denied —
  and not an empty mapping, nor the AI-POL-001 table.  AI-POL-001 is the *ceiling* a
  relaxation may widen to; keeping the two apart is what makes a recorded decision able to
  change behaviour, which is why the widening tests here assert a difference and not just a
  row (AI-POL-002, AI-BR-03);
* a relaxation writes ``ai.org_policy``, ``ai.policy_relaxation`` and the
  ``ai.policy.relaxed`` audit event **in one transaction**, because a widening recorded
  without its audit row, or an audit row for a widening that rolled back, both defeat
  D-11 and 00 doc 6.6;
* AI-POL-001 is a ceiling and PHI_PII is untouchable, so the refusals happen before
  anything can be written — at the point where somebody might otherwise believe the
  change succeeded.
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import (
    AuditQuery,
    AuditRecord,
    DataClassification,
    ValidationError,
    has_active_uow,
    query_audit,
    unit_of_work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_intelligence.enums import POLICY_MATRIX, SYSTEM_DEFAULT_MATRIX, ProviderClass
from factory_intelligence.errors import PolicyRelaxationDenied
from factory_intelligence.models.policy import OrgPolicyRow, PolicyRelaxationRow
from factory_intelligence.policy.engine import evaluate
from factory_intelligence.policy.model import ContextItem, EffectivePolicy
from factory_intelligence.policy.orgpolicy import effective_policy, read_org_policy, relax
from factory_intelligence.testing.harness import IntelligenceHarness

_LEVELS: tuple[DataClassification, ...] = (
    DataClassification.PUBLIC,
    DataClassification.INTERNAL,
    DataClassification.CONFIDENTIAL,
    DataClassification.RESTRICTED,
    DataClassification.PHI_PII,
)

ORG_ROW_ID = "org"
EVENT = "ai.policy.relaxed"


class _Rollback(RuntimeError):
    """Raised inside a caller's transaction to prove nothing of the relaxation survived."""


def _levels_of(policy: EffectivePolicy) -> dict[str, set[str]]:
    return {
        level.value: {member.value for member in policy.classes_for(level)}
        for level in _LEVELS
    }


def _matrix_as_names() -> dict[str, set[str]]:
    """AI-POL-001's **ceiling**: the widest each level may ever be.  Not the starting state."""
    return {
        level.value: {member.value for member in POLICY_MATRIX[level]} for level in _LEVELS
    }


def _default_as_names() -> dict[str, set[str]]:
    """AI-POL-002's **default**: what a level may use before anybody has relaxed anything.

    Strictly inside the ceiling at PUBLIC and INTERNAL, and equal to it everywhere else —
    which is exactly why a CONFIDENTIAL relaxation is still a no-op while an INTERNAL one is
    not (see ``test_a_relaxation_below_the_ceiling_widens_the_stored_policy``).
    """
    return {
        level.value: {member.value for member in SYSTEM_DEFAULT_MATRIX[level]}
        for level in _LEVELS
    }


def _org_rows(session: Session) -> list[OrgPolicyRow]:
    return list(session.scalars(select(OrgPolicyRow)).all())


def _relaxation_rows(session: Session) -> list[PolicyRelaxationRow]:
    return list(session.scalars(select(PolicyRelaxationRow).order_by(PolicyRelaxationRow.id)).all())


def _audit_events(operator: str) -> list[AuditRecord]:
    page = query_audit(AuditQuery(event_type=EVENT, operator_name=operator, limit=50))
    return list(page.items)


def _state() -> tuple[int, int]:
    """Row counts of the two tables a relaxation writes."""
    with unit_of_work() as session:
        return len(_org_rows(session)), len(_relaxation_rows(session))


def _stored_org() -> dict[str, Any] | None:
    with unit_of_work() as session:
        rows = _org_rows(session)
        return dict(rows[0].allowed_classes_by_level) if rows else None


# --- the default (AI-POL-002) -----------------------------------------------------


def test_with_no_row_the_organisation_policy_is_the_system_default(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-002: "系统默认策略为拒绝外部提供方" — the default denies APPROVED_EXTERNAL and
    UNRESTRICTED at every level, and it needs no row to exist, because a deployment that
    never configures anything is still safe.

    The ceiling this test used to assert (SRS 20.1, i.e. :data:`POLICY_MATRIX`) is the row
    an organisation may *reach*, not the one it starts on: reading the two as one constant
    made every legal relaxation a no-op.
    """
    _ = harness
    policy = read_org_policy()
    assert _levels_of(policy) == _default_as_names()
    assert policy.sources == ("org",)
    assert policy.warnings == ()
    assert not policy.allows(DataClassification.CONFIDENTIAL, ProviderClass.APPROVED_EXTERNAL)
    assert not policy.allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)
    assert not policy.allows(DataClassification.PUBLIC, ProviderClass.UNRESTRICTED)
    assert _levels_of(policy) != _matrix_as_names(), "the ceiling is not the default"


def test_the_default_is_not_written_as_a_row(harness: IntelligenceHarness) -> None:
    """AI-POL-002: the absence of a row *is* the seed.  Materialising it at start-up would
    let a later migration overwrite an operator's relaxation, and would make an unconfigured
    system indistinguishable from a configured-open one."""
    _ = harness
    with unit_of_work() as session:
        assert _org_rows(session) == []
        assert _relaxation_rows(session) == []
    assert _levels_of(read_org_policy()) == _default_as_names()


def test_reading_the_organisation_policy_opens_no_transaction_of_its_own(
    harness: IntelligenceHarness,
) -> None:
    """00 doc 6.2: a read must not leave a transaction open behind it."""
    _ = harness
    read_org_policy()
    assert not has_active_uow()


# --- a legal relaxation ----------------------------------------------------------


def test_a_relaxation_writes_policy_relaxation_and_audit_together(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-002: 放宽必须记录审计 — all three facts land, in the caller's transaction."""
    _ = harness
    with unit_of_work() as session:
        record = relax(
            change={"level": "CONFIDENTIAL", "addClasses": [ProviderClass.PRIVATE_NETWORK.value]},
            reason="clinic runs its own model on the ward network",
            operator="nurse-admin",
            session=session,
        )
        assert len(_org_rows(session)) == 1
        assert len(_relaxation_rows(session)) == 1
        # visible in the caller's own session: the audit row is written *in* this
        # transaction, not after it, which is what makes the next test possible
        assert len(_audit_events("nurse-admin")) == 1
    assert record.operator == "nurse-admin"
    org_rows, relaxations = _state()
    assert (org_rows, relaxations) == (1, 1)
    assert len(_audit_events("nurse-admin")) == 1


def test_the_three_writes_share_one_transaction(harness: IntelligenceHarness) -> None:
    """FD-AUD-001 / D-11: an audit row that survives the state change it describes — or
    the other way round — is a false record either way, so all three roll back together.

    The relaxation asked for is one that genuinely widens INTERNAL, so the read-back is a
    witness to the rollback and not merely to the absence of a row: a write that had
    committed would show ``APPROVED_EXTERNAL`` here."""
    _ = harness
    with pytest.raises(_Rollback), unit_of_work() as session:
        relax(
            change={"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
            reason="rolled back",
            operator="rollback-ops",
            session=session,
        )
        raise _Rollback("the caller's transaction failed for an unrelated reason")
    assert _state() == (0, 0)
    assert _audit_events("rollback-ops") == []
    assert _stored_org() is None
    assert _levels_of(read_org_policy()) == _default_as_names()


def test_a_relaxation_outside_a_transaction_commits_its_own(
    harness: IntelligenceHarness,
) -> None:
    """S1 calls the governance entry point with no transaction of its own (00 doc 6.2);
    the function must then open and commit one, and leave nothing open."""
    _ = harness
    relax(
        change={"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
        reason="approved vendor contract signed",
        operator="self-commit-ops",
    )
    assert not has_active_uow()
    assert _state() == (1, 1)
    assert len(_audit_events("self-commit-ops")) == 1


def test_the_audit_event_reports_the_operator_as_a_user(harness: IntelligenceHarness) -> None:
    """D-16: while there is no authentication the *name* is the identity, and its kind
    must not be lost — a relaxation by a person and one by the runtime are different
    facts."""
    _ = harness
    relax(
        change={"level": "CONFIDENTIAL", "addClasses": [ProviderClass.PRIVATE_NETWORK.value]},
        reason="named operator",
        operator="sol-arch-2",
    )
    events = _audit_events("sol-arch-2")
    assert len(events) == 1
    event = events[0]
    assert event.event_type == EVENT
    assert event.operator_name == "sol-arch-2"
    assert event.operator_type == "USER"
    assert event.object_type == "OrgPolicy"
    assert event.object_id == ORG_ROW_ID
    assert event.source_subsystem == "S3"


def test_the_audit_event_carries_the_result_of_the_change(harness: IntelligenceHarness) -> None:
    """AI-POL-008's sibling: a reviewer must be able to see what the level became, not
    only that somebody asked."""
    _ = harness
    relax(
        change={"level": "RESTRICTED", "addClasses": [ProviderClass.LOCAL_ONLY.value]},
        reason="audit payload",
        operator="payload-ops",
    )
    after = _audit_events("payload-ops")[0].after
    assert after is not None
    assert after["level"] == "RESTRICTED"
    assert after["scope"] == "organization"
    assert after["reason"] == "audit payload"
    assert after["classes"] == ["LOCAL_ONLY"]
    assert after["roleChecked"] is False, "M4 introduces the administrator check"


def test_the_relaxation_row_keeps_the_change_and_the_reason(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-002, the ``ai.policy_relaxation`` half: scope, change, reason, operator."""
    _ = harness
    relax(
        change={"level": "CONFIDENTIAL", "addClasses": [ProviderClass.PRIVATE_NETWORK.value]},
        reason="   ward network only   ",
        operator="scope-ops",
        scope="env:ward-prod",
    )
    with unit_of_work() as session:
        rows = _relaxation_rows(session)
        assert len(rows) == 1
        row = rows[0]
        assert row.scope == "env:ward-prod"
        assert row.change == {
            "level": "CONFIDENTIAL",
            "addClasses": [ProviderClass.PRIVATE_NETWORK.value],
        }
        assert row.reason == "ward network only", "the reason is stripped, not padded"
        assert row.operator == "scope-ops"
        assert row.role_checked is False
        org = _org_rows(session)[0]
        assert org.updated_by == "scope-ops"
        assert org.id == ORG_ROW_ID


def test_two_relaxations_are_two_records_on_one_org_row(harness: IntelligenceHarness) -> None:
    """Relaxations accumulate; the org row stays a single "org" document, and every
    widening keeps its own audit event.

    The two asks are deliberately not alike: CONFIDENTIAL→PRIVATE_NETWORK only records what
    the default already granted, while INTERNAL→APPROVED_EXTERNAL is the one that moves the
    row.  Both keep a record, and neither overwrites the other's level.
    """
    _ = harness
    relax(
        change={"level": "CONFIDENTIAL", "addClasses": [ProviderClass.PRIVATE_NETWORK.value]},
        reason="first",
        operator="pair-ops",
    )
    relax(
        change={"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
        reason="second",
        operator="pair-ops",
    )
    assert _state() == (1, 2)
    assert len(_audit_events("pair-ops")) == 2
    stored = _stored_org()
    assert stored is not None
    assert set(stored["CONFIDENTIAL"]) == {
        ProviderClass.LOCAL_ONLY.value,
        ProviderClass.PRIVATE_NETWORK.value,
    }
    assert set(stored["INTERNAL"]) == {
        ProviderClass.LOCAL_ONLY.value,
        ProviderClass.PRIVATE_NETWORK.value,
        ProviderClass.APPROVED_EXTERNAL.value,
    }


def test_a_relaxation_below_the_ceiling_widens_the_stored_policy(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-002 with AI-POL-001 read as a ceiling: ``INTERNAL`` starts at
    LOCAL_ONLY + PRIVATE_NETWORK and the table lets it reach APPROVED_EXTERNAL, so relaxing
    it is a change of state and not a note about the status quo.

    The premise this replaces — that an INTERNAL relaxation "is recorded without changing
    the effect" — was an artefact of reading the AI-POL-001 table as the default.  It is
    still true of CONFIDENTIAL, which the default already grants PRIVATE_NETWORK; that case
    is kept as the contrast in
    :func:`test_a_relaxation_the_default_already_grants_is_recorded_as_a_no_op`.
    """
    _ = harness
    before = read_org_policy()
    assert not before.allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)
    relax(
        change={"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
        reason="approved vendor contract signed",
        operator="widen-ops",
    )
    after = read_org_policy()
    assert after.classes_for(DataClassification.INTERNAL) == POLICY_MATRIX[
        DataClassification.INTERNAL
    ]
    # the point of separating the default from the ceiling: a recorded widening changes state
    assert before.narrower_than(after)
    # one level at a time: the relaxation must not carry any other level up to its ceiling
    for level in (
        DataClassification.PUBLIC,
        DataClassification.CONFIDENTIAL,
        DataClassification.RESTRICTED,
        DataClassification.PHI_PII,
    ):
        assert after.classes_for(level) == before.classes_for(level), level.value
    assert not after.allows(DataClassification.PUBLIC, ProviderClass.UNRESTRICTED)
    # and the three facts still land together
    assert _state() == (1, 1)
    assert len(_audit_events("widen-ops")) == 1


def test_a_relaxation_the_default_already_grants_is_recorded_as_a_no_op(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-002: a widening must be recorded, AI-POL-001 fixes the ceiling, and the
    overlap between them can be empty — this is that case.

    CONFIDENTIAL's default row and its ceiling row are the *same* set, LOCAL_ONLY +
    PRIVATE_NETWORK, because AI-POL-001 never allowed that level an external provider:
    relaxing it documents a decision and changes nothing.  INTERNAL is the level where the
    two differ, and :func:`test_a_relaxation_below_the_ceiling_widens_the_stored_policy`
    pins that one.  Recording this must not silently rewrite any other level either.
    """
    _ = harness
    assert SYSTEM_DEFAULT_MATRIX[DataClassification.CONFIDENTIAL] == POLICY_MATRIX[
        DataClassification.CONFIDENTIAL
    ], "the premise of this test: nothing left to widen for CONFIDENTIAL"
    relax(
        change={"level": "CONFIDENTIAL", "addClasses": [ProviderClass.PRIVATE_NETWORK.value]},
        reason="already permitted, now on the record",
        operator="noop-ops",
    )
    assert _levels_of(read_org_policy()) == _default_as_names()
    assert _state() == (1, 1)
    assert len(_audit_events("noop-ops")) == 1


def test_a_recorded_relaxation_changes_what_a_project_may_actually_send(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-002 end to end, which is the acceptance half of the requirement: a decision an
    organisation records has to reach the verdict the engine gives.

    With no environment policy in the way — what S1 sends today — an INTERNAL context item
    aimed at APPROVED_EXTERNAL is refused before the relaxation and allowed after it.  This
    is the pair of assertions the collapsed constant hid: with :data:`POLICY_MATRIX` read as
    the default the first verdict would already have been an allowance, and no relaxation
    could have made the second any wider.
    """
    _ = harness
    items = [
        ContextItem(
            ref="artifact:ward-handover-notes", classification=DataClassification.INTERNAL
        )
    ]
    untouched = effective_policy()
    assert untouched.sources == ("org",)
    denied = evaluate(items, untouched, ProviderClass.APPROVED_EXTERNAL)
    assert not denied.allowed
    assert [item.ref for item in denied.denied] == ["artifact:ward-handover-notes"]
    relax(
        change={"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
        reason="vendor signed the data-processing addendum",
        operator="end-to-end-ops",
    )
    widened = effective_policy()
    assert widened.sources == ("org",)
    allowed = evaluate(items, widened, ProviderClass.APPROVED_EXTERNAL)
    assert allowed.allowed, "the audited relaxation did not reach the verdict"
    assert allowed.denied == ()
    # level-scoped: the row it relaxed is the only row that moved
    assert not widened.allows(DataClassification.CONFIDENTIAL, ProviderClass.APPROVED_EXTERNAL)
    assert not widened.allows(DataClassification.PUBLIC, ProviderClass.UNRESTRICTED)


def test_a_relaxation_never_narrows_even_when_asked_to(harness: IntelligenceHarness) -> None:
    """AI-POL-002: the stored form accepts ``allowedClasses`` as well as ``addClasses``, and
    both are *additive* — ``relax`` is the widening verb.  A caller who wants to narrow
    edits the organisation policy document, not this function, because the alternative would
    let a "relaxation" remove a permission nobody came to discuss.

    What production actually does with the ask below (INTERNAL to LOCAL_ONLY alone) is union
    it with what the level already had.  Since AI-POL-002's default already carries
    PRIVATE_NETWORK, the stored row ends up LOCAL_ONLY + PRIVATE_NETWORK: the ask loses
    nothing and, having nothing new to add, gains nothing either.  It is still written and
    still audited, because the operator did record a decision here — and the audit payload
    reports the set the level ended with rather than the set that was requested, which is the
    only version of the two a reviewer can act on.
    """
    _ = harness
    relax(
        change={"level": "INTERNAL", "allowedClasses": [ProviderClass.LOCAL_ONLY.value]},
        reason="attempt to narrow",
        operator="narrow-ops",
    )
    union = {ProviderClass.LOCAL_ONLY.value, ProviderClass.PRIVATE_NETWORK.value}
    assert set((_stored_org() or {})["INTERNAL"]) == union
    stored = read_org_policy()
    assert not stored.allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)
    assert stored.allows(DataClassification.INTERNAL, ProviderClass.PRIVATE_NETWORK)
    events = _audit_events("narrow-ops")
    assert len(events) == 1
    assert events[0].after is not None
    assert events[0].after["classes"] == sorted(union)
    assert _state() == (1, 1)


# --- the refusals ----------------------------------------------------------------


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"], ids=["empty", "spaces", "newline"])
def test_a_relaxation_without_a_reason_is_refused(
    harness: IntelligenceHarness, reason: str
) -> None:
    """AI-POL-002: the reason is the reviewable half of the record; an unnamed
    justification is not one."""
    _ = harness
    with pytest.raises(ValidationError):
        relax(
            change={"level": "CONFIDENTIAL", "addClasses": [ProviderClass.PRIVATE_NETWORK.value]},
            reason=reason,
            operator="reasonless-ops",
        )
    assert _state() == (0, 0)
    assert _audit_events("reasonless-ops") == []


def test_a_phi_pii_relaxation_is_refused_and_nothing_is_written(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-005 / AC-POL-02: the engine could not widen PHI_PII anyway — the empty set
    absorbs it — so the request is refused here, where somebody might otherwise believe
    it succeeded, and with no trace that it half-happened.

    AC-POL-02 is unchanged by how the default and the ceiling are split: both leave PHI_PII
    with nowhere to go, so which of the two the stored policy answers with says nothing
    about this refusal — and nothing may be written either way.
    """
    _ = harness
    with pytest.raises(PolicyRelaxationDenied) as refused:
        relax(
            change={"level": "PHI_PII", "addClasses": [ProviderClass.LOCAL_ONLY.value]},
            reason="anonymised enough",
            operator="phi-ops",
        )
    assert refused.value.code == "AI_POLICY_RELAXATION_DENIED"
    assert "PHI_PII" in str(refused.value)
    assert refused.value.details["level"] == "PHI_PII"
    assert _state() == (0, 0)
    assert _audit_events("phi-ops") == []
    assert _levels_of(read_org_policy()) == _default_as_names()
    assert not read_org_policy().allows(DataClassification.PHI_PII, ProviderClass.LOCAL_ONLY)
    assert POLICY_MATRIX[DataClassification.PHI_PII] == frozenset()


@pytest.mark.parametrize(
    ("level", "provider_class"),
    [
        ("CONFIDENTIAL", ProviderClass.APPROVED_EXTERNAL),
        ("CONFIDENTIAL", ProviderClass.UNRESTRICTED),
        ("RESTRICTED", ProviderClass.PRIVATE_NETWORK),
        ("RESTRICTED", ProviderClass.APPROVED_EXTERNAL),
        ("INTERNAL", ProviderClass.UNRESTRICTED),
    ],
    ids=[
        "confidential-to-approved-external",
        "confidential-to-unrestricted",
        "restricted-to-private-network",
        "restricted-to-approved-external",
        "internal-to-unrestricted",
    ],
)
def test_widening_beyond_the_matrix_is_refused(
    harness: IntelligenceHarness, level: str, provider_class: ProviderClass
) -> None:
    """AI-POL-001 is a ceiling, not a starting suggestion: only PUBLIC may reach
    UNRESTRICTED, and nothing above INTERNAL may leave the network.  The refusal names
    the class that is beyond it."""
    _ = harness
    with pytest.raises(PolicyRelaxationDenied) as refused:
        relax(
            change={"level": level, "addClasses": [provider_class.value]},
            reason="we would like more",
            operator=f"ceiling-ops-{level}-{provider_class.value}",
        )
    message = str(refused.value)
    assert level in message
    assert provider_class.value in message
    assert "AI-POL-001" in message
    assert _state() == (0, 0)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"addClasses": ["LOCAL_ONLY"]}, "no level named"),
        ({"level": "SECRET", "addClasses": ["LOCAL_ONLY"]}, "unknown classification"),
        ({"level": "INTERNAL", "addClasses": ["LOCAL_HOST"]}, "unknown provider class"),
        ({"level": "INTERNAL", "addClasses": []}, "no class at all"),
        ({"level": "INTERNAL", "addClasses": 5}, "classes not a list"),
        ({"level": None, "addClasses": ["LOCAL_ONLY"]}, "level not a string"),
        ({}, "an empty change"),
    ],
    ids=[
        "no-level",
        "unknown-classification",
        "unknown-provider-class",
        "no-classes",
        "classes-not-a-list",
        "level-null",
        "empty-change",
    ],
)
def test_a_malformed_change_is_a_validation_error_not_a_write(
    harness: IntelligenceHarness, change: dict[str, Any], why: str
) -> None:
    """A caller with a typo must be told, not recorded: ``ai.policy_relaxation`` is read
    by auditors as a list of decisions that took effect."""
    _ = harness, why
    with pytest.raises(ValidationError):
        relax(change=change, reason="typo", operator="malformed-ops")
    assert _state() == (0, 0)
    assert _audit_events("malformed-ops") == []


# --- the effective policy --------------------------------------------------------


def test_effective_policy_is_the_org_policy_when_no_environment_speaks(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-004: with the empty ``target_environment_policies`` S1 sends today, the
    organisation's own answer governs — which, until somebody relaxes it, is AI-POL-002's
    default and not the AI-POL-001 ceiling."""
    _ = harness
    effective = effective_policy()
    assert _levels_of(effective) == _default_as_names()
    assert effective.sources == ("org",)
    assert effective.warnings == ()
    assert not effective.allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)


def test_effective_policy_combines_the_stored_org_policy_with_environments(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-004 / AI-AC-06 with a row on file: the organisation's permission is
    intersected with each target environment's document.

    The row here is a real relaxation, so the organisation *does* allow
    INTERNAL→APPROVED_EXTERNAL before either environment speaks; being narrowed away from a
    permission the organisation granted is the case AI-AC-06 is about."""
    _ = harness
    relax(
        change={"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
        reason="vendor approved",
        operator="combine-ops",
    )
    effective = effective_policy(
        environment_policies={
            "prod-1": {
                "allowedProviderClasses": [
                    ProviderClass.LOCAL_ONLY.value,
                    ProviderClass.PRIVATE_NETWORK.value,
                ]
            },
            "ward-prod": {"providerClass": ProviderClass.PRIVATE_NETWORK.value},
        }
    )
    assert read_org_policy().allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)
    # both environments mention PRIVATE_NETWORK, so it survives; LOCAL_ONLY is contradicted
    # by ward-prod and RESTRICTED had only LOCAL_ONLY to begin with.
    assert effective.classes_for(DataClassification.PUBLIC) == frozenset(
        {ProviderClass.PRIVATE_NETWORK}
    )
    assert effective.classes_for(DataClassification.INTERNAL) == frozenset(
        {ProviderClass.PRIVATE_NETWORK}
    )
    assert effective.classes_for(DataClassification.RESTRICTED) == frozenset()
    assert effective.classes_for(DataClassification.PHI_PII) == frozenset()
    assert not effective.allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)
    assert effective.sources == ("org", "env:prod-1", "env:ward-prod")


def test_effective_policy_may_be_read_in_the_callers_transaction(
    harness: IntelligenceHarness,
) -> None:
    """00 doc 6.2: with a session passed, S3 must not open a transaction of its own.

    The relaxation asked for widens INTERNAL rather than restating CONFIDENTIAL's default,
    so "the caller reads its own write" is something this test can actually observe on both
    sides of the commit."""
    _ = harness
    with unit_of_work() as session:
        relax(
            change={"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
            reason="same transaction",
            operator="enlist-ops",
            session=session,
        )
        effective = effective_policy(session=session, environment_policies={})
        assert effective.sources == ("org",)
        assert effective.allows(DataClassification.INTERNAL, ProviderClass.APPROVED_EXTERNAL)
    assert not has_active_uow()
    after = effective_policy()
    assert after.classes_for(DataClassification.INTERNAL) == POLICY_MATRIX[
        DataClassification.INTERNAL
    ]
    assert _levels_of(after) != _default_as_names()


def test_relaxation_warnings_surface_through_effective_policy(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-004: an environment document S1 forwarded unrecognised is reported on the
    effective policy, so ``ai.policy_decision.warnings`` carries the reason the run came
    back LOCAL_ONLY."""
    _ = harness
    effective = effective_policy(
        environment_policies={"ward-prod": {"exportPolicyEnabled": True}}
    )
    assert effective.warnings
    assert any("env:ward-prod" in warning for warning in effective.warnings)
    assert any("fail-safe" in warning for warning in effective.warnings)
    assert effective.classes_for(DataClassification.CONFIDENTIAL) == frozenset(
        {ProviderClass.LOCAL_ONLY}
    )
    assert ProviderClass.PRIVATE_NETWORK not in effective.classes_for(
        DataClassification.CONFIDENTIAL
    )


def test_an_org_row_written_without_a_level_is_read_narrow_and_says_so(
    harness: IntelligenceHarness,
) -> None:
    """The row is operator-written data (M4 will edit it in a UI): a missing level is a
    hole in the document, not a licence, and the warning has to reach the caller of
    :func:`effective_policy`."""
    _ = harness
    with unit_of_work() as session:
        session.add(
            OrgPolicyRow(
                id=ORG_ROW_ID,
                allowed_classes_by_level={
                    DataClassification.PUBLIC.value: [ProviderClass.LOCAL_ONLY.value]
                },
                updated_by="hand-written",
            )
        )
    policy = read_org_policy()
    assert policy.classes_for(DataClassification.INTERNAL) == frozenset()
    assert any("org.INTERNAL" in warning for warning in policy.warnings), policy.warnings
    assert effective_policy().warnings == policy.warnings
    with_environment = effective_policy(
        environment_policies={"prod-1": {"providerClass": ProviderClass.LOCAL_ONLY.value}}
    )
    assert set(policy.warnings) <= set(with_environment.warnings), with_environment.warnings
    assert with_environment.sources == ("org", "env:prod-1")


def test_a_relaxation_cannot_widen_past_the_matrix_even_after_a_row_exists(
    harness: IntelligenceHarness,
) -> None:
    """The ceiling is checked against the *current* stored policy, not against a
    remembered one: an operator who first widens INTERNAL and then tries for
    CONFIDENTIAL→APPROVED_EXTERNAL is refused by the same rule as the first caller."""
    _ = harness
    relax(
        change={"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
        reason="step one",
        operator="sequence-ops",
    )
    with pytest.raises(PolicyRelaxationDenied):
        relax(
            change={
                "level": "CONFIDENTIAL",
                "addClasses": [ProviderClass.APPROVED_EXTERNAL.value],
            },
            reason="step two",
            operator="sequence-ops",
        )
    assert _state() == (1, 1)
    assert len(_audit_events("sequence-ops")) == 1
