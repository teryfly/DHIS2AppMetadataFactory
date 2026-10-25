"""The chapter 7 reads that S1 and S5 call and ``bp`` answers (SRS 7.1–7.4).

Each test here is an acceptance criterion the SRS states in the shape a caller sees it:
the section catalog before a form is built (BP-SCH-008), the project's live draft
(BP-AC-15), a draft compared against its base (BP-AC-14) and a binding that carries the
observation a drift check needs (BP-AC-13).
"""

from __future__ import annotations

import pytest
from factory_foundation.api import unit_of_work

from factory_blueprint import api
from factory_blueprint.enums import DesignStage, Section
from factory_blueprint.testing.harness import BlueprintHarness
from tests.integration import test_compile as scenario
from tests.integration import test_draft_flow as flows
from tests.unit import helpers

PROJECT = flows.PROJECT


@pytest.fixture
def approved(harness: BlueprintHarness) -> str:
    """One project with a promoted version carrying a single domain entity."""
    flows.bootstrap()
    draft_id = flows._open_and_materialize(DesignStage.DOMAIN)
    with unit_of_work() as session:
        api.submit_patch(
            PROJECT,
            draft_id,
            flows.author_patch(
                [
                    {
                        "op": "add",
                        "path": "/domain/entities/-",
                        "value": helpers.domain_entity("pregnancy"),
                    }
                ],
                patch_id="ptch-pregnancy",
                base="0.1.0",
            ),
            api.PatchContext(stage=DesignStage.DOMAIN),
            idempotency_key="k-pregnancy",
            session=session,
        )
    frozen = api.freeze_draft(draft_id, [])
    with unit_of_work() as session:
        return api.promote_draft(
            draft_id,
            expected_hash=frozen.draft_hash,
            idempotency_key="k-promote-pregnancy",
            session=session,
        ).version


# --- BP-SCH-008: the section catalog -------------------------------------


def test_list_sections_answers_with_versions_and_containers() -> None:
    catalog = api.list_sections()
    assert catalog.current_schema_version == api.current_schema_version()
    assert catalog.schema_version == catalog.current_schema_version
    assert catalog.available_schema_versions == list(api.available_versions())

    by_name = {item.name: item for item in catalog.sections}
    assert set(by_name) == {section.value for section in Section}, "no section is hidden"
    assert by_name["registry"].entry_containers == ["/registry/entries"]
    assert by_name["registry"].key_fields == ["businessKey"]
    # a section with several lists reports each one, in document order
    assert by_name["domain"].entry_containers == ["/domain/entities", "/domain/relationships"]
    assert by_name["pages"].key_fields == ["pageKey"]


def test_whole_object_sections_are_marked_keyless() -> None:
    by_name = {item.name: item for item in api.list_sections().sections}
    for name in ("project", "generationPolicy"):
        assert by_name[name].entry_containers == [], f"{name} is not a row grid"
        assert by_name[name].key_fields == [None], f"{name} must read as having no key"


def test_list_sections_refuses_an_unknown_schema_version() -> None:
    with pytest.raises(ValueError):
        api.list_sections(9999)


# --- BP-DRF-011 / BP-AC-15: the project's live draft ---------------------


def test_get_active_draft_is_empty_until_a_draft_opens(approved: str) -> None:
    with unit_of_work() as session:
        assert api.get_active_draft(PROJECT, session=session) is None


def test_get_active_draft_returns_id_base_stage_and_hash(approved: str) -> None:
    draft_id = flows._open_and_materialize(DesignStage.DOMAIN)
    with unit_of_work() as session:
        live = api.get_active_draft(PROJECT, session=session)
    assert live is not None
    assert live.draft_id == draft_id
    assert live.base_version == approved
    assert live.stage is DesignStage.DOMAIN
    assert Section.DOMAIN in live.stage_scope
    assert live.status.value == "OPEN"
    assert live.content_hash.startswith("sha256:")
    assert live.promotable is None, "nothing has frozen it yet"
    assert live.open_patch_count == 0


def test_get_active_draft_counts_the_proposed_patches(approved: str) -> None:
    draft_id = flows._open_and_materialize(DesignStage.DOMAIN)
    with unit_of_work() as session:
        api.submit_patch(
            PROJECT,
            draft_id,
            flows.author_patch(
                [
                    {
                        "op": "add",
                        "path": "/domain/entities/-",
                        "value": helpers.domain_entity("immunization"),
                    }
                ],
                patch_id="ptch-immunization",
                base=approved,
            ),
            api.PatchContext(stage=DesignStage.DOMAIN),
            idempotency_key="k-immunization",
            session=session,
        )
        live = api.get_active_draft(PROJECT, session=session)
    assert live is not None and live.open_patch_count == 1


def test_get_active_draft_is_gone_once_the_draft_is_promoted(approved: str) -> None:
    draft_id = flows._open_and_materialize(DesignStage.DOMAIN)
    with unit_of_work() as session:
        api.submit_patch(
            PROJECT,
            draft_id,
            flows.author_patch(
                [
                    {
                        "op": "add",
                        "path": "/domain/entities/-",
                        "value": helpers.domain_entity("immunization"),
                    }
                ],
                patch_id="ptch-to-promote",
                base=approved,
            ),
            api.PatchContext(stage=DesignStage.DOMAIN),
            idempotency_key="k-to-promote",
            session=session,
        )
    frozen = api.freeze_draft(draft_id, [])
    with unit_of_work() as session:
        api.promote_draft(
            draft_id,
            expected_hash=frozen.draft_hash,
            idempotency_key="k-promote-immunization",
            session=session,
        )
        assert api.get_active_draft(PROJECT, session=session) is None


# --- BP-DIFF-003 / BP-AC-14: the draft against its base ------------------


def test_diff_draft_reports_two_added_and_one_updated(approved: str) -> None:
    """BP-AC-14 exactly: two entries added and one changed must read back that way."""
    draft_id = flows._open_and_materialize(DesignStage.DOMAIN)
    with unit_of_work() as session:
        api.submit_patch(
            PROJECT,
            draft_id,
            flows.author_patch(
                [
                    {
                        "op": "add",
                        "path": "/domain/entities/-",
                        "value": helpers.domain_entity("immunization"),
                    },
                    {
                        "op": "add",
                        "path": "/domain/entities/-",
                        "value": helpers.domain_entity("referral"),
                    },
                    {
                        "op": "replace",
                        "path": "/domain/entities/0/displayName",
                        "value": {"default": "Pregnancy episode"},
                    },
                ],
                patch_id="ptch-three-changes",
                base=approved,
            ),
            api.PatchContext(stage=DesignStage.DOMAIN),
            idempotency_key="k-three-changes",
            session=session,
        )
    report = api.diff_draft(draft_id)

    assert report.kind == "DRAFT_VS_BASE"
    assert report.from_version == approved
    assert report.to_version == draft_id
    states = {item.business_key: item.state for item in report.objects}
    assert states["immunization"] == "ADDED"
    assert states["referral"] == "ADDED"
    assert states["pregnancy"] == "UPDATED"
    updated = next(item for item in report.objects if item.business_key == "pregnancy")
    assert [change.field for change in updated.changes] == ["displayName/default"], "field level"
    assert (updated.changes[0].before, updated.changes[0].after) == (
        "Pregnancy",
        "Pregnancy episode",
    )
    assert report.counts == {"ADDED": 2, "UPDATED": 1}


def test_diff_draft_can_narrow_to_sections(approved: str) -> None:
    draft_id = flows._open_and_materialize(DesignStage.DOMAIN)
    report = api.diff_draft(draft_id, sections=["registry"])
    assert report.objects == [], "the draft touched no registry entry"


def test_diff_draft_refuses_an_unmaterialized_draft(approved: str) -> None:
    with unit_of_work() as session:
        draft_id = api.open_draft(PROJECT, DesignStage.DOMAIN, session=session)
    with pytest.raises(api.DraftNotMaterialized):
        api.diff_draft(draft_id)


# --- BP-REG-015 / BP-AC-13: bindings across several environments ---------


def test_bindings_carry_the_observation_and_accept_several_environments(
    harness: BlueprintHarness,
) -> None:
    """BP-REG-015: one call reads several environments, a bare key reads one.

    The version comes from the compile scenario because a binding is only ever about an
    entry that version defines (BP-REG-010.4): setting one for a businessKey the version
    does not have is refused rather than stored as a fact about nothing.
    """
    version = scenario.approved_version()
    with unit_of_work() as session:
        for environment_key in ("TEST", "UAT"):
            api.set_environment_binding(
                session,
                project_id=PROJECT,
                blueprint_version=version,
                environment_key=environment_key,
                business_key="facility",
                uid="ou000000001",
                note="matched by hand against the legacy code",
                resolved_by="architect",
            )

    with unit_of_work() as session:
        both = api.get_bindings(PROJECT, version, ["TEST", "UAT"], session=session)
        one = api.get_bindings(PROJECT, version, "TEST", session=session)
        all_environments = api.get_bindings(PROJECT, version, session=session)

    assert [item.environment_key for item in both] == ["TEST", "UAT"], "one call, two environments"
    assert [item.environment_key for item in one] == ["TEST"], "a bare key still works"
    assert len(all_environments) == 2
    assert {item.uid for item in both} == {"ou000000001"}
    assert all(item.note and item.resolved_by for item in both)


def test_binding_fields_are_the_ones_a_drift_check_needs(harness: BlueprintHarness) -> None:
    """BP-AC-13: ``observedHash`` and ``discoveryResultId`` must be present, not implied.

    A hand-set binding carries no observation, because nothing has observed anything yet;
    the first environment compile against that environment's Discovery Result supplies it
    and clears 待重新解析, which is what makes the row safe for S4 to diff against.
    """
    version = scenario.approved_version()
    with unit_of_work() as session:
        binding = api.set_environment_binding(
            session,
            project_id=PROJECT,
            blueprint_version=version,
            environment_key=scenario.TEST_ENV,
            business_key="facility",
            uid="OUTEST00001",
            note="this instance names its facility differently",
            resolved_by="architect",
        )
    fields = set(api.BindingInfo.model_fields)
    assert {
        "environment_key",
        "business_key",
        "uid",
        "observed_last_updated",
        "observed_hash",
        "discovery_result_id",
        "resolution",
        "resolved_by",
        "note",
        "pending_reparse",
    } <= fields
    assert binding.observed_hash is None, "a hand-set binding has no observation until a compile runs"
    assert binding.pending_reparse is True

    with unit_of_work() as session:
        listed = api.get_bindings(PROJECT, version, [scenario.TEST_ENV], session=session)
    assert [(item.observed_hash, item.discovery_result_id, item.observed_last_updated) for item in listed] == [
        (None, None, None)
    ]
    assert listed[0].resolution.value == "MANUAL"

    built = api.compile_for_environment(
        PROJECT,
        version,
        scenario.TEST_ENV,
        scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(),
        None,
        scenario.context(),
        idempotency_key="k-new-reads-confirm",
        design_reference_environment=scenario.DESIGN_ENV,
    )
    assert built.status is api.BuildStatus.COMPLETED, built.binding_issues
    with unit_of_work() as session:
        confirmed = api.get_bindings(PROJECT, version, scenario.TEST_ENV, session=session)
    row = next(item for item in confirmed if item.business_key == "facility")
    assert row.observed_hash and row.discovery_result_id
    assert row.observed_last_updated and row.resolution.value == "MANUAL"
    assert row.pending_reparse is False
