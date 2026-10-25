"""``evaluate_policy`` and the reference grammar (AI-POL-006, AI-POL-004, SRS 7).

The read-only pre-check has to answer *exactly* the question the run will answer later: the
interesting failure of a policy engine is a pre-check that says yes and a run that says no.  So
these tests exercise the public function over the same ref grammar, the same effective-policy
combination and the same engine — and assert the one property that makes it safe to call from
inside S1's transaction: it reads artifact **metadata** only, and writes nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_blueprint import api as blueprint
from factory_foundation.api import (
    DataClassification,
    NotFoundError,
    ValidationError,
    is_external_io,
    unit_of_work,
)
from sqlalchemy import func, select

from factory_intelligence import api
from factory_intelligence.dto.request import ContextItemRef
from factory_intelligence.enums import PolicyResult, ProviderClass
from factory_intelligence.models.policy import PolicyDecisionRow
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration.agent_runtime import (
    seed_project,
    stage_input,
    unique_project_id,
)


def _judge(project: str, refs: list[Any], provider: Any = ProviderClass.LOCAL_ONLY, **kwargs: Any) -> Any:
    return api.evaluate_policy(project, refs, provider, **kwargs)


# --- the grammar ------------------------------------------------------------------


def test_an_artifact_reference_takes_the_class_recorded_on_the_product(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-006: an artifact carries its own classification; nothing here re-reads content."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    confidential = stage_input(
        project, {"notes": "wards in this district"}, classification=DataClassification.CONFIDENTIAL
    )

    outcome = _judge(project, [f"artifact:{confidential}"], ProviderClass.PRIVATE_NETWORK)

    assert outcome.result is PolicyResult.ALLOWED
    assert [item["classification"] for item in outcome.items] == ["CONFIDENTIAL"]
    assert outcome.provider_class is ProviderClass.PRIVATE_NETWORK


def test_an_explicit_classification_short_circuits_the_read(harness: IntelligenceHarness) -> None:
    """The context builder already classified every item it fetched; AI-NFR-01 is why the
    pre-check must not go and look them all up again."""
    _ = harness
    project = unique_project_id()
    seed_project(project)

    outcome = _judge(
        project,
        [ContextItemRef(ref="artifact:does-not-exist", classification="PUBLIC")],
        ProviderClass.LOCAL_ONLY,
    )

    assert outcome.result is PolicyResult.ALLOWED
    assert outcome.items[0]["classification"] == "PUBLIC"


def test_a_blueprint_reference_resolves_through_the_version_artifact(
    harness: IntelligenceHarness,
) -> None:
    _ = harness
    project = unique_project_id()
    version = seed_project(project)

    by_version = _judge(project, [f"blueprint:{version}"], ProviderClass.LOCAL_ONLY)
    by_latest = _judge(project, ["blueprint:latest"], ProviderClass.LOCAL_ONLY)

    assert by_version.result is PolicyResult.ALLOWED
    # the same product answers both spellings; only the reference the caller used is echoed
    assert [item["classification"] for item in by_version.items] == [
        item["classification"] for item in by_latest.items
    ]
    assert by_latest.items[0]["ref"] == "blueprint:latest"
    # the BLUEPRINT artifact of a fresh project is S2's own product, classified INTERNAL
    assert by_version.items[0]["classification"] == "INTERNAL"


def test_a_draft_reference_inherits_its_base_version_and_says_so(
    harness: IntelligenceHarness,
) -> None:
    """``DraftInfo`` reports a content hash and no artifact id, so the conservative reading is
    the base version's classification (AI-POL-006's draft case)."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    with unit_of_work() as session:
        draft_id = blueprint.open_draft(project, "REQUIREMENTS", session=session)

    outcome = _judge(project, [f"blueprint:draft:{draft_id}"], ProviderClass.LOCAL_ONLY)

    assert outcome.items[0]["classification"] == "INTERNAL"


def test_a_draft_of_another_project_is_not_readable(harness: IntelligenceHarness) -> None:
    _ = harness
    mine, theirs = unique_project_id(), unique_project_id()
    seed_project(mine)
    seed_project(theirs)
    with unit_of_work() as session:
        draft_id = blueprint.open_draft(theirs, "REQUIREMENTS", session=session)

    with pytest.raises(NotFoundError):
        _judge(mine, [f"blueprint:draft:{draft_id}"])


@pytest.mark.parametrize(
    "reference",
    ["ward-notes.txt", "blueprint:0.9.9", "artifact:", "s3://bucket/key"],
    ids=["bare-name", "unknown-version", "empty-artifact", "foreign-scheme"],
)
def test_a_reference_that_cannot_be_classified_is_refused_not_guessed(
    harness: IntelligenceHarness, reference: str
) -> None:
    """Guessing a classification would be a policy decision made on nobody's behalf."""
    _ = harness
    project = unique_project_id()
    seed_project(project)

    with pytest.raises(NotFoundError):
        _judge(project, [reference])


# --- the verdict ------------------------------------------------------------------


def test_the_default_policy_refuses_every_external_class(harness: IntelligenceHarness) -> None:
    """AI-POL-002: with nothing relaxed, INTERNAL content may not go out."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    internal = stage_input(project, {"notes": "routine"}, classification=DataClassification.INTERNAL)

    outcome = _judge(project, [f"artifact:{internal}"], ProviderClass.APPROVED_EXTERNAL)

    assert outcome.result is PolicyResult.DENIED
    assert [item.ref for item in outcome.denied_items] == [f"artifact:{internal}"]
    assert outcome.denied_items[0].allowed_classes == ["LOCAL_ONLY", "PRIVATE_NETWORK"]
    assert outcome.allowed is False


def test_phi_pii_is_denied_to_every_class_including_its_own(harness: IntelligenceHarness) -> None:
    """AC-POL-02, stated as a verdict: the allowed set is empty, so no class is a loophole."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    phi = stage_input(
        project,
        {"notes": "identifiable patient data"},
        classification=DataClassification.PHI_PII,
    )

    for provider in ProviderClass:
        outcome = _judge(project, [f"artifact:{phi}"], provider)
        assert outcome.result is PolicyResult.DENIED, provider.value
        assert outcome.denied_items[0].allowed_classes == []


def test_an_environment_policy_narrows_the_answer(harness: IntelligenceHarness) -> None:
    """AI-POL-004 / AI-AC-06 with the same combination the runtime applies."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    internal = stage_input(project, {"notes": "x"}, classification=DataClassification.INTERNAL)

    wide = _judge(
        project, [f"artifact:{internal}"], ProviderClass.PRIVATE_NETWORK, environment_policies={}
    )
    narrow = _judge(
        project,
        [f"artifact:{internal}"],
        ProviderClass.PRIVATE_NETWORK,
        environment_policies={"ward": {"allowedProviderClasses": ["LOCAL_ONLY"]}},
    )

    assert wide.result is PolicyResult.ALLOWED
    assert narrow.result is PolicyResult.DENIED
    assert narrow.effective_policy, "the matrix that decided is reported with the answer"


def test_an_unrecognised_environment_policy_is_reported_as_a_warning(
    harness: IntelligenceHarness,
) -> None:
    """The fail-safe normalisation must reach the caller, or a typo reads as an allowance."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    confidential = stage_input(
        project, {"notes": "x"}, classification=DataClassification.CONFIDENTIAL
    )

    outcome = _judge(
        project,
        [f"artifact:{confidential}"],
        ProviderClass.PRIVATE_NETWORK,
        environment_policies={"ward": {"exportPolicyEnabled": True}},
    )

    assert outcome.warnings
    assert any("env:ward" in warning for warning in outcome.warnings)
    assert outcome.result is PolicyResult.DENIED


def test_an_unknown_provider_class_is_a_validation_error(harness: IntelligenceHarness) -> None:
    _ = harness
    project = unique_project_id()
    seed_project(project)

    with pytest.raises(ValidationError):
        _judge(project, ["blueprint:latest"], "CLOUD_MAYBE")

    with pytest.raises(ValidationError):
        api.evaluate_policy(project, [], "LOCAL")


def test_a_member_and_its_string_answer_identically(harness: IntelligenceHarness) -> None:
    """``str(ProviderClass.LOCAL_ONLY)`` is ``"ProviderClass.LOCAL_ONLY"``, not the value.

    A caller holding the enum member — which is what ``enums`` hands out — must not be told its
    own constant is an unknown provider class.
    """
    _ = harness
    project = unique_project_id()
    seed_project(project)

    from_string = _judge(project, ["blueprint:latest"], "LOCAL_ONLY")
    from_member = _judge(project, ["blueprint:latest"], ProviderClass.LOCAL_ONLY)

    assert from_string.result is from_member.result is PolicyResult.ALLOWED


# --- its transactional shape -------------------------------------------------------


def test_the_pre_check_is_not_external_io_and_writes_nothing(
    harness: IntelligenceHarness,
) -> None:
    """S1 may call it with its own transaction open (it reads metadata only), and a pre-check
    that recorded a decision would be a second ledger nobody asked for (AI-POL-008)."""
    _ = harness
    project = unique_project_id()
    seed_project(project)
    assert not is_external_io(api.evaluate_policy)

    with unit_of_work() as session:
        outcome = api.evaluate_policy(project, ["blueprint:latest"], "LOCAL_ONLY", session=session)
        assert outcome.result is PolicyResult.ALLOWED
        rows = int(
            session.execute(
                select(func.count()).select_from(PolicyDecisionRow).where(
                    PolicyDecisionRow.project_id == project
                )
            ).scalar_one()
        )
        assert rows == 0
