"""P2: the draft life cycle end to end — initialize, patch, freeze, promote."""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import (
    ArtifactStatus,
    AuditQuery,
    ConcurrencyError,
    TransactionMisuseError,
    get_artifact,
    query_audit,
    unit_of_work,
)

from factory_blueprint import artifacts, deps, draft
from factory_blueprint.dto.patch import Patch, PatchAuthor, PatchContext
from factory_blueprint.enums import (
    AuthorKind,
    ChangeLevel,
    DesignStage,
    DraftStatus,
    PatchStatus,
    RegistryEntryStatus,
    Section,
)
from factory_blueprint.errors import DraftNotMaterialized, NotPromotable
from factory_blueprint.registry import pool
from factory_blueprint.s4.stub import StubDhis2
from factory_blueprint.testing.harness import BlueprintHarness
from factory_blueprint.version import service as version_service
from tests.unit import helpers

PROJECT = "p1"
DESIGN_ENV = "DEV"


@pytest.fixture(autouse=True)
def _stub_dhis2() -> Any:
    deps.set_dhis2(StubDhis2())
    yield
    deps.reset_providers()


def decision_op(decision_id: str = "MAP-001") -> dict[str, Any]:
    """The architecture decision every mapping and registry entry refers to."""
    return {
        "op": "add",
        "path": "/architecture/decisions/-",
        "value": {
            "decisionId": decision_id,
            "title": "Weight is a data element",
            "context": "growth monitoring",
            "decision": "model weight as a DataElement referenced by the program",
            "status": "ACCEPTED",
        },
    }


def bootstrap(mode: Any = None) -> str:
    handle = draft.prepare_initial_blueprint(PROJECT, helpers.project_info(mode or "B"))
    with unit_of_work() as session:
        info = draft.register_initial_blueprint(PROJECT, handle, session=session)
    return info.version


def author_patch(operations: list[dict[str, Any]], *, patch_id: str, base: str) -> Patch:
    return Patch(
        patch_id=patch_id,
        base_version=base,
        author=PatchAuthor(kind=AuthorKind.USER, ref="sol-arch"),
        operations=operations,
        rationale="test patch",
    )


def test_initial_version_is_zero_one_zero(harness: BlueprintHarness) -> None:
    version = bootstrap()
    assert version == "0.1.0"
    with unit_of_work() as session:
        row = version_service.get_version(session, PROJECT, "0.1.0")
        info = draft.to_version_info(row)
    artifact = get_artifact(info.artifact_id)
    assert artifact.type == "BLUEPRINT"
    assert artifact.status is not ArtifactStatus.SUPERSEDED
    # 00 doc 6.7: the version hash is a structure hash, the artifact hash is the
    # hash of its file bytes — both describe the same document
    assert artifact.content_hash != info.content_hash
    document, _classification = artifacts.read_json(info.artifact_id)
    from factory_foundation.api import structure_hash

    assert structure_hash(document) == info.content_hash
    events = [event.event_type for event in query_audit(AuditQuery(project_id=PROJECT)).items]
    assert "bp.blueprint.initialized" in events


def test_prepare_initial_blueprint_refuses_to_run_in_a_transaction(
    harness: BlueprintHarness,
) -> None:
    with pytest.raises(TransactionMisuseError), unit_of_work():
        draft.prepare_initial_blueprint(PROJECT, helpers.project_info())


def test_open_draft_is_idempotent_and_single(harness: BlueprintHarness) -> None:
    """BP-DRF-001."""
    bootstrap()
    with unit_of_work() as session:
        first = draft.open_draft(PROJECT, DesignStage.MAPPING, session=session)
        second = draft.open_draft(PROJECT, DesignStage.DOMAIN, session=session)
    assert first == second
    with unit_of_work() as session:
        info = draft.get_draft(first, session=session)
    assert info.status is DraftStatus.OPEN
    assert Section.REGISTRY in info.stage_scope
    assert Section.PAGES not in info.stage_scope


def test_submit_patch_requires_materialized_base(harness: BlueprintHarness) -> None:
    bootstrap()
    with pytest.raises(DraftNotMaterialized), unit_of_work() as session:
        draft_id = draft.open_draft(PROJECT, DesignStage.MAPPING, session=session)
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([], patch_id="ptch-0001", base="0.1.0"),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k1",
            session=session,
        )


def _registry_at(draft_id: str) -> list[dict[str, Any]]:
    """The draft's own Registry entries, unapproved: what the operator actually authored."""
    with unit_of_work() as session:
        document = draft.draft_document(draft_id, session=session)
    return list((document.get("registry") or {}).get("entries") or [])


def _hash_of_approved_draft(draft_id: str) -> str:
    """The hash of the document freeze would stage: the draft, with entries approved."""
    from factory_blueprint.draft.service import approve_registry_entries, normalize

    with unit_of_work() as session:
        content = normalize(dict(draft.draft_document(draft_id, session=session)))
    approve_registry_entries(content)
    return _sha(content)


def _sha(content: dict[str, Any]) -> str:
    from factory_foundation.api import structure_hash

    return structure_hash(content)


def test_happy_path_patch_freeze_and_promote(harness: BlueprintHarness) -> None:
    base = bootstrap()
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=10, low_water_mark=2)
    draft_id = _open_and_materialize()

    mapping_op = {
        "op": "add",
        "path": "/mapping/entries/-",
        "value": helpers.mapping_entry("weight", "dataElement", decision_id="MAP-001"),
    }
    decision = decision_op()
    with unit_of_work() as session:
        outcome = draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([decision, mapping_op], patch_id="ptch-map", base=base),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-map",
            session=session,
        )
    assert outcome.status is PatchStatus.PROPOSED, outcome.issues
    assert outcome.draft_hash

    registry_op = {
        "op": "add",
        "path": "/registry/entries/-",
        "value": helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_WEIGHT"),
    }
    with unit_of_work() as session:
        allocated = draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([registry_op], patch_id="ptch-reg", base=base),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-reg",
            session=session,
        )
    assert allocated.status is PatchStatus.PROPOSED, [i.message for i in allocated.issues]
    assert list(allocated.assigned_uids) == ["weight"]
    assert len(allocated.assigned_uids["weight"]) == 11

    with unit_of_work() as session:
        document = draft.draft_document(draft_id, session=session)
    entry = next(item for item in document["registry"]["entries"] if item["businessKey"] == "weight")
    assert entry["uid"] == allocated.assigned_uids["weight"]
    assert entry["code"] == "MC_DE_WEIGHT"
    assert entry["status"] == "PROPOSED"

    frozen = draft.freeze_draft(draft_id, [DESIGN_ENV])
    # Freezing does not touch what was authored, **except** that a draft being frozen for
    # promotion is a draft a Gate is about to approve, so its Registry entries carry APPROVED in
    # the canonical document. That changes the hash on purpose: the stored version artifact and
    # every hash that names it must describe the same bytes, or no reader can see the approval
    # (BPV-XA-004). The invariant is therefore "freeze is stable and content-preserving apart
    # from the approval itself", not "freeze leaves the hash at whatever the patch returned".
    assert frozen.draft_hash == _hash_of_approved_draft(draft_id), frozen.draft_hash
    assert frozen.draft_hash != allocated.draft_hash or not any(
        e.get("status") == RegistryEntryStatus.PROPOSED.value
        for e in _registry_at(draft_id)
    ), "a draft with proposed Registry entries froze to an unchanged hash, so the approval is lost"
    assert frozen.promotable, [issue.message for issue in frozen.issues]

    with unit_of_work() as session:
        promoted = draft.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-promote", session=session
        )
    assert promoted.version == "0.2.0"
    assert promoted.change_level == ChangeLevel.MINOR.value
    assert DesignStage.MAPPING in promoted.affected_stages
    assert "METADATA_PACKAGE" in promoted.impact.artifact_types
    assert promoted.applied_patch_ids
    with unit_of_work() as session:
        state = draft.get_draft(draft_id, session=session)
    assert state.status is DraftStatus.PROMOTED


def threshold_op(value: float = 0.55) -> dict[str, Any]:
    """A generationPolicy edit: allowed in the MAPPING scope, always a real change."""
    return {"op": "replace", "path": "/generationPolicy/decisionConfidenceThreshold", "value": value}


def _open_and_materialize(stage: DesignStage = DesignStage.MAPPING) -> str:
    with unit_of_work() as session:
        draft_id = draft.open_draft(PROJECT, stage, session=session)
    draft.prepare_draft_base(PROJECT, draft_id)
    return draft_id



def test_cosmetic_only_change_bumps_the_revision(harness: BlueprintHarness) -> None:
    """BP-AC-04: a later draft that only touches a display-level field."""
    bootstrap()
    requirement = {
        "businessKey": "req.weight.recorded",
        "statement": "the midwife records the weight of every child under five",
        "priority": "MUST",
        "source": {"artifactId": "art-01INPUT0001", "location": "chapter 3"},
    }
    first = _open_and_materialize(DesignStage.REQUIREMENTS)
    with unit_of_work() as session:
        outcome = draft.submit_patch(
            PROJECT,
            first,
            author_patch(
                [{"op": "add", "path": "/requirements/items/-", "value": requirement}],
                patch_id="ptch-requirement",
                base="0.1.0",
            ),
            PatchContext(stage=DesignStage.REQUIREMENTS),
            idempotency_key="k-requirement",
            session=session,
        )
    assert outcome.status is PatchStatus.PROPOSED, [i.message for i in outcome.issues]
    frozen = draft.freeze_draft(first, [])
    with unit_of_work() as session:
        promoted = draft.promote_draft(
            first, frozen.draft_hash, idempotency_key="k-requirement-promote", session=session
        )
    assert promoted.version == "0.2.0"
    assert promoted.change_level == ChangeLevel.MINOR.value
    assert DesignStage.REQUIREMENTS in promoted.affected_stages

    second = _open_and_materialize(DesignStage.REQUIREMENTS)
    with unit_of_work() as session:
        outcome = draft.submit_patch(
            PROJECT,
            second,
            author_patch(
                [
                    {
                        "op": "add",
                        "path": "/requirements/items/0/rationale",
                        "value": "growth monitoring is the core of the programme",
                    },
                    {
                        "op": "add",
                        "path": "/requirements/items/0/status",
                        "value": "CONFIRMED",
                    },
                ],
                patch_id="ptch-rationale",
                base="0.2.0",
            ),
            PatchContext(stage=DesignStage.REQUIREMENTS),
            idempotency_key="k-rationale",
            session=session,
        )
    assert outcome.status is PatchStatus.PROPOSED, [i.message for i in outcome.issues]
    frozen = draft.freeze_draft(second, [])
    with unit_of_work() as session:
        promoted = draft.promote_draft(
            second, frozen.draft_hash, idempotency_key="k-rationale-promote", session=session
        )
    assert promoted.change_level == ChangeLevel.COSMETIC.value
    assert promoted.version == "0.2.1"
    assert promoted.impact.sections == [Section.REQUIREMENTS]

def test_promote_with_a_stale_hash_is_rejected(harness: BlueprintHarness) -> None:
    """BP-AC-03: freeze, then patch again, then promote with the old hash."""
    bootstrap()
    draft_id = _open_and_materialize()
    with unit_of_work() as session:
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([threshold_op(0.65)], patch_id="ptch-first-edit", base="0.1.0"),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-first-edit",
            session=session,
        )
    first = draft.freeze_draft(draft_id, [])
    with unit_of_work() as session:
        later = draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([threshold_op(0.45)], patch_id="ptch-late", base="0.1.0"),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-late",
            session=session,
        )
    assert later.status is PatchStatus.PROPOSED
    assert later.draft_hash != first.draft_hash
    with pytest.raises(ConcurrencyError), unit_of_work() as session:
        draft.promote_draft(draft_id, first.draft_hash, idempotency_key="k-stale", session=session)
def test_freeze_with_blocking_issues_is_not_promotable(harness: BlueprintHarness) -> None:
    """BP-AC-06 mechanism: an incomplete decision blocks promotion."""
    bootstrap()
    draft_id = _open_and_materialize()
    weak = helpers.mapping_entry(
        "weight", "dataElement", decision_id="MAP-001"
    )
    weak["confidence"] = 0.2
    del weak["alternatives"]
    with unit_of_work() as session:
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch(
                [decision_op(), {"op": "add", "path": "/mapping/entries/-", "value": weak}],
                patch_id="ptch-nodecision",
                base="0.1.0",
            ),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-nodecision",
            session=session,
        )
    frozen = draft.freeze_draft(draft_id, [])
    assert not frozen.promotable
    assert frozen.blocking_count
    with pytest.raises(NotPromotable), unit_of_work() as session:
        draft.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-blocked", session=session
        )


def test_reset_discards_patches_and_retires_uids(harness: BlueprintHarness) -> None:
    """BP-DRF-007 and BP-AC-07."""
    bootstrap()
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=10, low_water_mark=2)
    draft_id = _open_and_materialize()
    entry = helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_W")
    with unit_of_work() as session:
        outcome = draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch(
                [decision_op(), {"op": "add", "path": "/registry/entries/-", "value": entry}],
                patch_id="ptch-reset",
                base="0.1.0",
            ),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-reset",
            session=session,
        )
    uid = outcome.assigned_uids["weight"]
    with unit_of_work() as session:
        discarded = draft.reset_draft(draft_id, session=session)
        document = draft.draft_document(draft_id, session=session)
    assert discarded == 1
    assert document["registry"]["entries"] == []
    with unit_of_work() as session:
        from factory_blueprint.models.uid import UidAllocationRow

        row = session.get(UidAllocationRow, uid)
    assert row is not None and row.status == "RETIRED"
    # the retired UID is never handed out again
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=10, low_water_mark=2)
    assert uid not in _pool_uids()


def _pool_uids() -> list[str]:
    from sqlalchemy import select

    from factory_blueprint.models.uid import UidPoolRow

    with unit_of_work() as session:
        return list(session.execute(select(UidPoolRow.uid)).scalars())



def test_promote_is_idempotent(harness: BlueprintHarness) -> None:
    bootstrap()
    draft_id = _open_and_materialize()
    with unit_of_work() as session:
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([threshold_op()], patch_id="ptch-idem", base="0.1.0"),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-idem-patch",
            session=session,
        )
    frozen = draft.freeze_draft(draft_id, [])
    with unit_of_work() as session:
        first = draft.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-twice", session=session
        )
    with unit_of_work() as session:
        again = draft.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-twice", session=session
        )
    assert first.version == again.version
    assert first.artifact_id == again.artifact_id
def test_base_version_mismatch_returns_merge_question(harness: BlueprintHarness) -> None:
    """BP-DRF-003."""
    bootstrap()
    draft_id = _open_and_materialize()
    with unit_of_work() as session:
        outcome = draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch(
                [{"op": "add", "path": "/project/summary", "value": "x"}],
                patch_id="ptch-stale",
                base="0.9.9",
            ),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-stale-base",
            session=session,
        )
    assert outcome.status is PatchStatus.REJECTED
    assert outcome.merge_question is not None
    assert outcome.merge_question.kind == "BLUEPRINT_MERGE_REQUIRED"



def test_only_one_draft_can_be_promoted_per_base_version(harness: BlueprintHarness) -> None:
    """BP-DRF-010: after a promotion a new draft must be based on the new version."""
    bootstrap()
    draft_id = _open_and_materialize()
    with unit_of_work() as session:
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([threshold_op()], patch_id="ptch-first", base="0.1.0"),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-first",
            session=session,
        )
    frozen = draft.freeze_draft(draft_id, [])
    with unit_of_work() as session:
        draft.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-promote-first", session=session
        )
    with unit_of_work() as session:
        reopened = draft.open_draft(PROJECT, DesignStage.MAPPING, session=session)
    assert reopened != draft_id
    draft.prepare_draft_base(PROJECT, reopened)
    with unit_of_work() as session:
        info = draft.get_draft(reopened, session=session)
    assert info.base_version == "0.2.0"
def test_indexes_are_rebuilt_from_the_promoted_content(harness: BlueprintHarness) -> None:
    from sqlalchemy import select

    from factory_blueprint.models.index import BusinessKeyIndexRow
    from factory_blueprint.models.registry import RegistryEntryRow

    bootstrap()
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=6, low_water_mark=1)
    draft_id = _open_and_materialize()
    with unit_of_work() as session:
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch(
                [
                    decision_op(),
                    {
                        "op": "add",
                        "path": "/registry/entries/-",
                        "value": helpers.registry_entry(
                            "weight", "dataElement", "CREATE", code="MC_DE_WEIGHT"
                        ),
                    },
                ],
                patch_id="ptch-index",
                base="0.1.0",
            ),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-index",
            session=session,
        )
    frozen = draft.freeze_draft(draft_id, [])
    with unit_of_work() as session:
        draft.promote_draft(draft_id, frozen.draft_hash, idempotency_key="k-index-promote", session=session)
    with unit_of_work() as session:
        rows = session.execute(
            select(RegistryEntryRow).where(
                RegistryEntryRow.project_id == PROJECT,
                RegistryEntryRow.blueprint_version == "0.2.0",
            )
        ).scalars().all()
        keys = session.execute(
            select(BusinessKeyIndexRow).where(
                BusinessKeyIndexRow.project_id == PROJECT,
                BusinessKeyIndexRow.blueprint_version == "0.2.0",
            )
        ).scalars().all()
    assert [row.business_key for row in rows] == ["weight"]
    assert rows[0].status == "APPROVED"
    assert ("registry", "weight") in {(row.section, row.entry_key) for row in keys}



def test_promoted_version_artifact_is_traceable_to_its_hash(harness: BlueprintHarness) -> None:
    """AC-BP-01: the version points at exactly one artifact whose content hashes back."""
    bootstrap()
    draft_id = _open_and_materialize()
    with unit_of_work() as session:
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([threshold_op(0.42)], patch_id="ptch-trace", base="0.1.0"),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-trace",
            session=session,
        )
    frozen = draft.freeze_draft(draft_id, [])
    with unit_of_work() as session:
        result = draft.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-trace-promote", session=session
        )
    artifact = get_artifact(result.artifact_id)
    assert artifact.creator_subsystem == "S2"
    payload, classification = artifacts.read_json(result.artifact_id)
    assert classification is artifact.data_classification
    assert payload["generationPolicy"]["decisionConfidenceThreshold"] == 0.42
    from factory_foundation.api import structure_hash

    assert structure_hash(payload) == result.content_hash


def test_the_promoted_version_stores_the_entries_it_approves(harness: BlueprintHarness) -> None:
    """A Registry entry a Gate approved must read back APPROVED from the version's own bytes.

    ``approve_registry_entries`` used to run in `promote_draft` *after* `artifacts.register` had
    consumed the staging handle, so the artifact every later reader opens — the compiler,
    ``GET /blueprint/entries``, S1's review screens — went on saying PROPOSED. BPV-XA-004 then
    blocked any PAGE_DESIGN intent referencing an entry ARCHITECTURE_GATE had already approved, and
    no re-authoring by the operator could change it, because the approval was being written to a
    dict that nothing persisted afterwards.
    """
    base = bootstrap()
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=10, low_water_mark=2)
    draft_id = _open_and_materialize()
    with unit_of_work() as session:
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch([decision_op()], patch_id="ptch-dec", base=base),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-dec",
            session=session,
        )
        draft.submit_patch(
            PROJECT,
            draft_id,
            author_patch(
                [{"op": "add", "path": "/registry/entries/-",
                  "value": helpers.registry_entry("weight", "dataElement", "CREATE", code="MC_DE_WEIGHT")}],
                patch_id="ptch-reg",
                base=base,
            ),
            PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-reg",
            session=session,
        )

    # The draft itself keeps the proposed status: freezing is not yet a human approval.
    with unit_of_work() as session:
        before = draft.draft_document(draft_id, session=session)
    live = next(i for i in before["registry"]["entries"] if i["businessKey"] == "weight")
    assert live["status"] == RegistryEntryStatus.PROPOSED.value, live

    frozen = draft.freeze_draft(draft_id, [DESIGN_ENV])
    assert frozen.promotable, [issue.message for issue in frozen.issues]
    with unit_of_work() as session:
        promoted = draft.promote_draft(
            draft_id, frozen.draft_hash, idempotency_key="k-promote", session=session
        )

    stored = artifacts.read_json(promoted.artifact_id)[0]
    landed = next(i for i in stored["registry"]["entries"] if i["businessKey"] == "weight")
    assert landed["status"] == RegistryEntryStatus.APPROVED.value, (
        "the stored version still says PROPOSED: whatever approves the entries runs after the "
        "staged bytes were registered, so no reader can ever see the approval"
    )
