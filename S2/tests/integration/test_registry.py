"""Registry, UID pool, environment-binding and decision guarantees (BP-REG, BP-AC).

The draft and compile paths live in ``test_draft_flow`` and ``test_compile``; this module covers
what the Registry exists to guarantee: a UID issued once and never again (BP-REG-005/006/008a), one
object managed by one project per environment (BP-REG-012), bindings resolved automatically or by a
named human without forking the version (BP-REG-010), and the Managed Scope, impact and retrieval
reads S1 and S4 ask for (BP-REG-011, BP-IMP-003/004, SRS 7.2).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import unit_of_work
from sqlalchemy import select

from factory_blueprint import artifacts, deps, draft, readmodel
from factory_blueprint.build import service as builds
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.dto.blueprint import EntrySelector
from factory_blueprint.dto.patch import Patch, PatchAuthor, PatchContext, PatchOutcome
from factory_blueprint.enums import (
    AuthorKind,
    BindingResolution,
    BuildStatus,
    ChangeLevel,
    DeliveryMode,
    DesignStage,
    Disposition,
    IssueLevel,
    PatchStatus,
    Section,
    UidAllocationStatus,
    stage_rank,
    stages_from,
)
from factory_blueprint.errors import RegistryError, UidPoolExhausted
from factory_blueprint.graph import queries as graph
from factory_blueprint.impact import impact as impact_rules
from factory_blueprint.impact.changelvl import Change
from factory_blueprint.models.uid import UidAllocationRow, UidPoolRow
from factory_blueprint.models.version import BlueprintVersionRow
from factory_blueprint.registry import binding, conflicts, pool, queries, scope
from factory_blueprint.s4.contract import DiscoveryResult
from factory_blueprint.s4.stub import StubDhis2, discovered_object, discovery_document
from factory_blueprint.testing.harness import BlueprintHarness
from factory_blueprint.validators import completeness
from factory_blueprint.version import service as version_service
from tests.integration import test_compile as scenario
from tests.unit import helpers

PROJECT, RIVAL, MATRIX, DESIGN_ENV = scenario.PROJECT, "p2", "pm", scenario.DESIGN_ENV
TARGET, STAGING = "MATURITY", "TEST"
SEX: dict[str, Any] = {"observed_uid": "sexOP000001", "code": "MC_OS_SEX", "decision_id": "MAP-001"}
FACILITY: dict[str, Any] = {"observed_uid": "OUclinic001", "code": "FACILITY", "decision_id": "MAP-001"}
CLINIC: dict[str, Any] = {"observed_uid": "depData0001", "code": "MC_DE_CLINICNAME",
          "controlled_fields": ["name"], "decision_id": "MAP-002"}
BOUND_SEX: dict[str, Any] = {"uid": "sexOP000001", "code": "MC_OS_SEX"}
BOUND_CLINIC: dict[str, Any] = {"uid": "depData0001", "code": "MC_DE_CLINICNAME", "controlled_fields": ["name"]}
LEGACY: dict[str, Any] = {"observed_uid": "deprDE00001", "code": "MC_DE_LEGACY", "decision_id": "MAP-002"}
ATTRIBUTE: dict[str, Any] = {"name": "sex", "valueType": "TEXT", "required": False,
             "sensitivity": "PHI_PII", "optionSetKey": "sex"}


@pytest.fixture(autouse=True)
def _stub(harness: BlueprintHarness) -> Iterator[StubDhis2]:
    """Fresh ``bp`` state plus a private, scriptable S4 double for every test."""
    stub = StubDhis2()
    deps.set_dhis2(stub)
    yield stub
    deps.reset_providers()


class _ReplayingStub(StubDhis2):
    """An S4 whose generation endpoint offers an already-retired UID back to us."""

    def __init__(self, replay: str) -> None:
        super().__init__()
        self._replay = replay

    def generate_uids(self, environment_key: str, count: int) -> list[str]:
        return [self._replay, *super().generate_uids(environment_key, max(count - 1, 0))]


def _bootstrap(project: str = PROJECT) -> str:
    handle = draft.prepare_initial_blueprint(project, helpers.project_info())
    with unit_of_work() as session:
        return draft.register_initial_blueprint(project, handle, session=session).version


def _draft(project: str = PROJECT, stage: DesignStage = DesignStage.MAPPING) -> tuple[str, str]:
    """A materialized draft and its base version, bootstrapping 0.1.0 when the project is new."""
    with unit_of_work() as session:
        current = version_service.get_current(session, project)
    base = current.version if current is not None else _bootstrap(project)
    with unit_of_work() as session:
        draft_id = draft.open_draft(project, stage, session=session)
    draft.prepare_draft_base(project, draft_id)
    return draft_id, base


def _submit(draft_id: str, operations: list[dict[str, Any]], patch_id: str, base: str, *,
            project: str = PROJECT, stage: DesignStage = DesignStage.MAPPING) -> PatchOutcome:
    patch = Patch(patch_id=patch_id, base_version=base,
                  author=PatchAuthor(kind=AuthorKind.USER, ref="sol-arch"),
                  operations=operations, rationale="registry coverage")
    with unit_of_work() as session:
        return draft.submit_patch(project, draft_id, patch, PatchContext(stage=stage),
                                  idempotency_key=f"reg-{patch_id}", session=session)


def _promote(draft_id: str, *, project: str = PROJECT) -> str:
    frozen = draft.freeze_draft(draft_id, [])
    assert frozen.promotable, [issue.message for issue in frozen.issues]
    with unit_of_work() as session:
        promoted = draft.promote_draft(draft_id, frozen.draft_hash,
                                       idempotency_key=f"reg-promote-{draft_id}", session=session)
        return promoted.version


def _decision_op(decision_id: str = "MAP-001") -> dict[str, Any]:
    value = {"decisionId": decision_id, "title": "Weight is a DataElement",
             "context": "growth monitoring", "decision": "model weight as a DataElement",
             "status": "ACCEPTED"}
    return {"op": "add", "path": "/architecture/decisions/-", "value": value}


def _registry_op(key: str, dhis2_type: str, disposition: str, **kwargs: Any) -> dict[str, Any]:
    value = helpers.registry_entry(key, dhis2_type, disposition, **kwargs)
    return {"op": "add", "path": "/registry/entries/-", "value": value}


def _mapping_op(key: str, dhis2_type: str, decision_id: str) -> dict[str, Any]:
    value = helpers.mapping_entry(key, dhis2_type, decision_id=decision_id)
    return {"op": "add", "path": "/mapping/entries/-", "value": value}


def _version_document(project: str, version: str) -> dict[str, Any]:
    with unit_of_work() as session:
        artifact_id = version_service.get_version(session, project, version).artifact_id
    return artifacts.read_json(artifact_id)[0]


def _version_list(project: str = PROJECT) -> list[str]:
    """Every version number the project has, oldest first (BP-VER-006)."""
    with unit_of_work() as session:
        rows = session.execute(
            select(BlueprintVersionRow).where(BlueprintVersionRow.project_id == project)
        ).scalars()
        return sorted(row.version for row in rows)


def _allocation(uid: str) -> UidAllocationRow | None:
    with unit_of_work() as session:
        return session.get(UidAllocationRow, uid)


def _pool_uids(project: str = PROJECT) -> list[str]:
    with unit_of_work() as session:
        rows = session.execute(select(UidPoolRow.uid).where(UidPoolRow.project_id == project))
        return sorted(rows.scalars())


def _registry_rows(project: str, version: str) -> list[Any]:
    with unit_of_work() as session:
        return list(queries.registry_entries(session, project, version))


def _matrix() -> tuple[str, dict[str, str]]:
    """One version holding a CREATE, UPDATE, REUSE, REFERENCE and DEPRECATED entry over two
    decisions, so scope, code and retrieval reads have something to discriminate on."""
    draft_id, base = _draft(MATRIX)
    pool.replenish(MATRIX, DESIGN_ENV, batch_size=10, low_water_mark=2)
    operations = [
        _decision_op("MAP-001"), _decision_op("MAP-002"),
        _mapping_op("weight", "dataElement", "MAP-001"), _mapping_op("clinic.name", "dataElement", "MAP-002"),
        _registry_op("weight", "dataElement", "CREATE", decision_id="MAP-001"),
        _registry_op("clinic.name", "dataElement", "UPDATE", **CLINIC),
        _registry_op("marital", "optionSet", "REUSE", observed_uid="marrOP00001", code="MC_OS_MARRIAGE"),
        _registry_op("facility", "organisationUnit", "REFERENCE", **FACILITY),
        _registry_op("legacy", "dataElement", "DEPRECATED", **LEGACY),
    ]
    outcome = _submit(draft_id, operations, "ptch-matrix", base, project=MATRIX)
    assert outcome.status is PatchStatus.PROPOSED, [issue.message for issue in outcome.issues]
    return _promote(draft_id, project=MATRIX), outcome.assigned_uids


def _graph_version() -> str:
    """The compile scenario plus the domain attribute that wires ``sex`` into the graph."""
    scenario.approved_version()
    draft_id, base = _draft(PROJECT, DesignStage.DOMAIN)
    operations = [{"op": "add", "path": "/domain/entities/0/attributes/-", "value": ATTRIBUTE}]
    outcome = _submit(draft_id, operations, "ptch-graph", base, stage=DesignStage.DOMAIN)
    assert outcome.status is PatchStatus.PROPOSED, [issue.message for issue in outcome.issues]
    return _promote(draft_id)


def _rival(uid: str, code: str) -> dict[str, Any]:
    """Another project's Registry, holding the concept ``PROJECT`` created under its own UID."""
    entry = helpers.registry_entry("pregnancy", "program", "CREATE", uid=uid, code=code, decision_id="MAP-001")
    return helpers.with_entries(helpers.empty_document(), "/registry/entries", entry)


def test_bp_ac_07_a_retired_uid_is_never_handed_out_again() -> None:
    """BP-AC-07 / BP-REG-006: a discarded patch retires its UID forever and a pool refill must
    discard that UID even when DHIS2 offers it back, because one UID may never serve a second
    business concept in the Factory (BP-REG-005)."""
    draft_id, base = _draft()
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=10, low_water_mark=2)
    operations = [_decision_op(), _registry_op("weight", "dataElement", "CREATE")]
    retired = _submit(draft_id, operations, "ptch-retire", base).assigned_uids["weight"]

    with unit_of_work() as session:
        assert draft.reset_draft(draft_id, session=session) == 1
    row = _allocation(retired)
    assert row is not None and row.status == UidAllocationStatus.RETIRED.value and row.retired_at

    deps.set_dhis2(_ReplayingStub(retired))
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=5, low_water_mark=2)
    assert retired not in _pool_uids()

    replanned, replanned_base = _draft()
    later = _submit(replanned, [_decision_op(), _registry_op("other", "dataElement", "CREATE")],
                    "ptch-after-retire", replanned_base)
    assert retired not in later.assigned_uids.values()
    with unit_of_work() as session:
        assert pool.is_reusable(session, retired) is False


def test_bp_ac_07_a_discarded_concept_can_be_planned_again() -> None:
    """BP-AC-07 / BP-REG-006 with BP-DRF-001: retirement must not lock the concept out of its
    project — the ledger keeps the retired row forever and the replanned concept goes live under a
    fresh UID, one active allocation per businessKey at a time."""
    draft_id, base = _draft()
    pool.replenish(PROJECT, DESIGN_ENV, batch_size=10, low_water_mark=2)
    operations = [_decision_op(), _registry_op("weight", "dataElement", "CREATE")]
    first = _submit(draft_id, operations, "ptch-plan-again-1", base)
    with unit_of_work() as session:
        draft.reset_draft(draft_id, session=session)
    replanned, replanned_base = _draft()
    again = _submit(replanned, operations, "ptch-plan-again-2", replanned_base)
    assert again.status is PatchStatus.PROPOSED, [issue.message for issue in again.issues]
    fresh = again.assigned_uids["weight"]
    assert fresh != first.assigned_uids["weight"]
    assert _allocation(first.assigned_uids["weight"]) is not None
    with unit_of_work() as session:
        live = session.execute(select(UidAllocationRow.uid).where(
            UidAllocationRow.project_id == PROJECT, UidAllocationRow.business_key == "weight",
            UidAllocationRow.status == UidAllocationStatus.ACTIVE.value)).scalars().all()
    assert list(live) == [fresh]


def test_bp_ac_08_and_bp_reg_005_a_second_project_claiming_the_uid_is_blocking() -> None:
    """BP-AC-08 / BP-REG-012 with BP-REG-005: two projects CREATE-ing the same businessKey and UID
    in one environment block the rival, while a rival creating its own object under its own UID
    raises nothing at all."""
    version = scenario.approved_version()
    uid = next(entry.uid for entry in _registry_rows(PROJECT, version) if entry.business_key == "pregnancy")
    assert uid is not None
    with unit_of_work() as session:
        issues = conflicts.check_all(session, RIVAL, _rival(uid, "MC_PR_PREGNANCY"), [DESIGN_ENV])
    ownership = [issue for issue in issues if issue.rule_id == conflicts.RULE_OWNERSHIP_CONFLICT]
    assert [(i.level, i.business_key, i.uid) for i in ownership] == [(IssueLevel.BLOCKING, "pregnancy", uid)]
    assert ownership[0].details["otherProjectId"] == PROJECT
    assert {issue.rule_id for issue in issues} == {
        conflicts.RULE_OWNERSHIP_CONFLICT, conflicts.RULE_UID_ALREADY_ALLOCATED}
    with unit_of_work() as session:
        clean = conflicts.check_all(session, RIVAL, _rival("othPr000001", "MC_PR_OTHER"), [DESIGN_ENV])
    assert clean == []


def test_bp_ac_08_the_ownership_conflict_is_reported_and_not_fatal() -> None:
    """BP-AC-08 / BP-REG-012: declaring another project's object must be reported, not fatal."""
    version = scenario.approved_version()
    claimed = next(entry.uid for entry in _registry_rows(PROJECT, version) if entry.business_key == "pregnancy")
    draft_id, base = _draft(RIVAL)
    operations = [_decision_op(), _mapping_op("pregnancy", "program", "MAP-001"),
                  _registry_op("pregnancy", "program", "UPDATE", observed_uid=claimed or "othPr000001",
                               controlled_fields=["name"], code="MC_PR_PREGNANCY")]
    outcome = _submit(draft_id, operations, "ptch-rival", base, project=RIVAL)
    assert outcome.status is PatchStatus.PROPOSED, [issue.message for issue in outcome.issues]
    frozen = draft.freeze_draft(draft_id, [DESIGN_ENV])
    assert not frozen.promotable
    assert conflicts.RULE_OWNERSHIP_CONFLICT in {issue.rule_id for issue in frozen.issues}


def test_bp_ac_11_a_dry_uid_pool_refuses_the_patch_retryably() -> None:
    """BP-AC-11 / BP-REG-008a.3: with an empty pool a new CREATE entry is refused by the retryable
    UID_POOL_EXHAUSTED and allocates nothing; the same submission retried after ``replenish``
    succeeds and takes its UID from the pool, not from DHIS2 inside the transaction."""
    draft_id, base = _draft()
    operations = [_decision_op(), _registry_op("weight", "dataElement", "CREATE")]
    with pytest.raises(UidPoolExhausted) as raised:
        _submit(draft_id, operations, "ptch-dry", base)
    assert raised.value.error_code == "UID_POOL_EXHAUSTED" and raised.value.retryable is True
    with unit_of_work() as session:
        status = pool.pool_status(session, PROJECT, low_water_mark=2)
        document = draft.draft_document(draft_id, session=session)
        allocated = session.execute(
            select(UidAllocationRow.uid).where(UidAllocationRow.project_id == PROJECT)).scalars().all()
    assert status.available == 0 and status.needs_replenishment
    assert document["registry"]["entries"] == [] and list(allocated) == [] and _pool_uids() == []

    pool.replenish(PROJECT, DESIGN_ENV, batch_size=5, low_water_mark=2)
    stocked = set(_pool_uids())
    retry = _submit(draft_id, operations, "ptch-dry", base)
    assert retry.status is PatchStatus.PROPOSED, [issue.message for issue in retry.issues]
    assigned = retry.assigned_uids["weight"]
    assert assigned in stocked and assigned not in _pool_uids()
    row = _allocation(assigned)
    assert row is not None and row.status == UidAllocationStatus.ACTIVE.value
    assert row.source_environment == DESIGN_ENV and row.business_key == "weight"


def test_bp_ac_10_a_manual_binding_recompiles_without_a_new_version() -> None:
    """BP-AC-10 / BP-REG-010.3: a binding that cannot be resolved automatically blocks the
    environment compile, and the human-set binding unblocks a recompile without creating a version
    — the version is environment-independent (D-02, BP-BR-14).

    The option set is there under a code the match rules miss, which is the only case a
    person is asked about: their answer names an object this environment really has, so
    the recompile also gets the observation baseline S4 checks against (BP-CMP-027a).
    """
    version = scenario.approved_version()
    profile = scenario._profile_artifact()
    basis = scenario._discovery(TARGET)
    objects = [
        {**item, "code": "GENDER_LOCAL"} if item["dhis2Type"] == "optionSet" else item
        for item in basis["objects"]
    ]
    discovery = scenario._s4_artifact(PROJECT, "DISCOVERY_RESULT", discovery_document(
        TARGET, objects, defaults=basis["defaults"]))
    bound_uid = next(
        str(item["uid"]) for item in objects if item["dhis2Type"] == "optionSet"
    )
    compile_for = builds.compile_for_environment
    first = compile_for(PROJECT, version, TARGET, discovery, profile, None, scenario.context(),
                        idempotency_key="reg-k-mat-1", design_reference_environment=DESIGN_ENV)
    unresolved = [(i.rule_id, i.level) for i in first.binding_issues if i.business_key == "sex"]
    assert unresolved == [(binding.RULE_UNRESOLVED, IssueLevel.BLOCKING)]
    assert first.status is BuildStatus.FAILED and "METADATA_PACKAGE" not in first.artifact_ids
    before = _version_list()

    with unit_of_work() as session:
        manual = binding.set_manual(session, project_id=PROJECT, blueprint_version=version,
                                    environment_key=TARGET, business_key="sex", uid=bound_uid,
                                    resolved_by="dhis2-implementer",
                                    note="this instance spells the code differently")
    assert manual.resolution == BindingResolution.MANUAL.value
    assert manual.pending_reparse is True, "a human choice waits for the next compile to confirm it"

    second = compile_for(PROJECT, version, TARGET, discovery, profile, None, scenario.context(),
                         idempotency_key="reg-k-mat-2", design_reference_environment=DESIGN_ENV)
    assert second.status is BuildStatus.COMPLETED, second.binding_issues
    assert second.binding_issues == [] and second.artifact_ids["METADATA_PACKAGE"]
    with unit_of_work() as session:
        bindings = {item.business_key: item for item in binding.load(session, PROJECT, version, TARGET)}
        current = version_service.get_current(session, PROJECT)
    assert _version_list() == before and current is not None
    assert second.version == version == current.version
    assert bindings["sex"].uid == bound_uid and bindings["sex"].resolved_by == "dhis2-implementer"
    assert bindings["sex"].resolution == BindingResolution.MANUAL.value
    assert bindings["sex"].observed_hash and bindings["sex"].discovery_result_id
    assert bindings["sex"].pending_reparse is False


def test_bp_ac_10_a_manual_binding_to_an_absent_object_stays_blocking() -> None:
    """BP-REG-010.3 hands the choice to a person, not the answer: a binding that names an
    object this environment does not have — or has as another type — would put a dangling
    reference into a payload, so the recompile refuses it instead (D-02, P-10)."""
    version = scenario.approved_version()
    discovery = scenario._discovery_artifact(TARGET)
    with unit_of_work() as session:
        binding.set_manual(session, project_id=PROJECT, blueprint_version=version,
                           environment_key=TARGET, business_key="sex", uid="sexPHNTM001",
                           resolved_by="dhis2-implementer", note="a UID out of another instance")
    result = builds.compile_for_environment(
        PROJECT, version, TARGET, discovery, scenario._profile_artifact(), None,
        scenario.context(), idempotency_key="reg-k-mat-3",
        design_reference_environment=DESIGN_ENV)
    assert result.status is BuildStatus.FAILED
    rules = {issue.rule_id for issue in result.binding_issues if issue.business_key == "sex"}
    assert rules == {binding.RULE_MANUAL_UID_ABSENT}


@pytest.mark.parametrize(
    "business_key,uid,refusal",
    [
        ("weight", "sexOPTEST01", "no Registry entry"),
        ("pregnancy", "sexCREATED1", "created by this project"),
        ("sex", "short", "not a DHIS2 UID"),
    ],
)
def test_set_manual_refuses_a_binding_that_cannot_mean_anything(
    business_key: str, uid: str, refusal: str
) -> None:
    """BP-REG-010.3 with BP-BR-14: nothing stops a person from choosing, so S2 must at
    least refuse the choices that are not about an object of this version."""
    version = scenario.approved_version()
    with unit_of_work() as session, pytest.raises(RegistryError) as caught:
        binding.set_manual(session, project_id=PROJECT, blueprint_version=version,
                           environment_key=TARGET, business_key=business_key, uid=uid,
                           resolved_by="dhis2-implementer", note="because I said so")
    assert refusal in str(caught.value), caught.value


def test_set_manual_refuses_a_uid_another_project_manages() -> None:
    """D-03: one object is owned by one project per environment, and a hand-set binding is
    still a claim on that object, so it is refused the same way the compile would."""
    claimed = next(
        entry.uid
        for entry in _registry_rows(PROJECT, scenario.approved_version())
        if entry.business_key == "pregnancy"
    )
    matrix_version, _ = _matrix()
    with unit_of_work() as session, pytest.raises(RegistryError) as caught:
        binding.set_manual(session, project_id=MATRIX, blueprint_version=matrix_version,
                           environment_key=TARGET, business_key="marital", uid=claimed,
                           resolved_by="dhis2-implementer", note="same option set, other project")
    assert PROJECT in str(caught.value) and claimed in str(caught.value)



def test_ac_reg_03_and_04_managed_scope_holds_exactly_the_managed_entries(_stub: StubDhis2) -> None:
    """AC-REG-03/04 with BP-REG-002/009/011: one environment's Managed Scope holds exactly the
    CREATE, UPDATE and DEPRECATED entries with that environment's UID and the controlled fields S4
    may write, and only those entries' codes are checked per (dhis2Type, code)."""
    version, uids = _matrix()
    with unit_of_work() as session:
        managed = scope.managed_scope(session, MATRIX, version, STAGING)
        registry = queries.registry_entries(session, MATRIX, version)
    assert {item.business_key for item in managed} == {"weight", "clinic.name", "legacy"}
    unmanaged = {entry.business_key for entry in registry if not scope.is_managed(entry.disposition.value)}
    assert unmanaged == {"marital", "facility"}
    by_key = {item.business_key: item for item in managed}
    assert by_key["weight"].uid == uids["weight"] and by_key["weight"].disposition is Disposition.CREATE
    assert by_key["clinic.name"].controlled_fields == ["name"]

    document = _version_document(MATRIX, version)
    assert conflicts.code_to_be_checked(document["registry"]["entries"]) == {
        "dataElement": {"MC_DE_WEIGHT", "MC_DE_CLINICNAME"}}
    assert conflicts.check_code_conflicts(document, [STAGING]) == []
    _stub.add_existing_code(STAGING, "dataElement", "MC_DE_CLINICNAME")
    clashes = conflicts.check_code_conflicts(document, [STAGING])
    assert [(i.rule_id, i.level, i.business_key) for i in clashes] == [
        (conflicts.RULE_CODE_CONFLICT, IssueLevel.BLOCKING, "clinic.name")]


def test_bp_reg_002_a_deprecated_entry_stays_managed() -> None:
    """BP-REG-002 / BP-REG-011: a DEPRECATED object stays inside the managed set."""
    legacy = next(entry for entry in _registry_rows(MATRIX, _matrix()[0]) if entry.business_key == "legacy")
    assert legacy.disposition is Disposition.DEPRECATED and legacy.managed is True


def test_bp_ac_06_a_low_confidence_decision_without_confirmation_blocks_freeze() -> None:
    """BP-AC-06 / BP-DEC-004: confidence below the *configured* threshold without a confirmation
    record is BLOCKING at freeze, so an Agent cannot adopt its own weak decision; recording the
    approval clears exactly that issue and the same draft becomes promotable."""
    draft_id, base = _draft()
    configured = {"op": "replace", "path": "/generationPolicy/decisionConfidenceThreshold", "value": 0.8}
    weak = helpers.mapping_entry("weight", "dataElement", decision_id="MAP-001")
    weak["confidence"] = 0.75
    operations = [configured, _decision_op(), {"op": "add", "path": "/mapping/entries/-", "value": weak}]
    assert _submit(draft_id, operations, "ptch-weak", base).status is PatchStatus.PROPOSED

    frozen = draft.freeze_draft(draft_id, [])
    blocking = [i for i in frozen.issues if i.level is IssueLevel.BLOCKING]
    assert not frozen.promotable
    assert [i.rule_id for i in blocking] == [completeness.RULE_CONFIRMATION_REQUIRED]
    assert blocking[0].business_key == "weight"

    confirmation = {"op": "add", "path": "/mapping/entries/0/approvalRef",
                    "value": "ques-0001 answered by sol-arch"}
    again = _submit(draft_id, [confirmation], "ptch-confirm", base)
    assert again.status is PatchStatus.PROPOSED, [issue.message for issue in again.issues]
    refrozen = draft.freeze_draft(draft_id, [])
    assert refrozen.promotable, [issue.message for issue in refrozen.issues]
    assert completeness.RULE_CONFIRMATION_REQUIRED not in {issue.rule_id for issue in refrozen.issues}


def test_bp_reg_010_bindings_resolve_by_code_then_uid_and_update_by_uid_only() -> None:
    """BP-REG-010.2: REUSE and REFERENCE match by code first and fall back to the design
    environment's UID, UPDATE matches by UID only — so a code that fits another object must
    not bind it (BPV-BIND-003) — and a code fitting several objects is a BLOCKING ambiguity
    (BPV-BIND-002) rather than a guess."""
    entries = [helpers.registry_entry("weight", "dataElement", "CREATE", uid="stub0000010"),
               helpers.registry_entry("facility", "organisationUnit", "REFERENCE", uid="OUfacility1",
                                      code="FACILITY"),
               helpers.registry_entry("sex", "optionSet", "REUSE", **BOUND_SEX),
               helpers.registry_entry("clinic.name", "dataElement", "UPDATE", **BOUND_CLINIC),
               helpers.registry_entry("dup", "optionSet", "REUSE", uid="dupOP000001", code="DUP_CODE")]
    found = [discovered_object("organisationUnit", "OUtest000001", code="FACILITY"),
             discovered_object("optionSet", "sexOP000001", code="OTHER_SEX_CODE"),
             discovered_object("dataElement", "depData9999", code="MC_DE_CLINICNAME"),
             discovered_object("optionSet", "dupOPa00001", code="DUP_CODE"),
             discovered_object("optionSet", "dupOPb00001", code="DUP_CODE")]
    discovery = DiscoveryResult.model_validate(discovery_document(STAGING, found))
    outcome = binding.resolve_bindings(entries, discovery, environment_key=STAGING,
                                       design_environment=DESIGN_ENV,
                                       policy=helpers.empty_document()["generationPolicy"])
    assert outcome.uid_map() == {"weight": "stub0000010", "facility": "OUtest000001", "sex": "sexOP000001"}
    assert [(i.rule_id, i.business_key) for i in outcome.issues] == [
        (binding.RULE_AMBIGUOUS, "dup"), (binding.RULE_UPDATE_UID_MISMATCH, "clinic.name")]
    assert "depData9999" not in outcome.uid_map().values(), (
        "the UPDATE was not bound to the object that merely shares its code"
    )
    assert outcome.blocking is True
    assert {item.resolution for item in outcome.bindings} == {BindingResolution.AUTO}
    override = {"environmentBindingRules": {"defaultMatch": "CODE_THEN_UID",
                                            "byType": [{"dhis2Type": "optionSet", "match": "CODE"}]}}
    assert binding.match_rule_for("optionSet", override) == binding.MATCH_CODE


def test_bp_val_010_impact_of_one_entry_reaches_later_stages_dependents_and_products() -> None:
    """BP-VAL-010 with BP-IMP-003/004/005: the impact set of one businessKey is its section's stage
    plus every later stage, every entry the dependency graph hangs off it, and the artifact types
    that must be regenerated — S1 invalidates work instead of guessing at it."""
    version = _graph_version()
    document = BlueprintDocument(_version_document(PROJECT, version))
    with unit_of_work() as session:
        dependents = graph.downstream(session, PROJECT, version, {"sex"})
    assert dependents == {"pregnancy"}

    change = [Change(ChangeLevel.MINOR, "changes the option set", Section.REGISTRY.value,
                     "/registry/entries/sex", "sex")]
    computed = impact_rules.compute_impact(document, change, ChangeLevel.MINOR, DeliveryMode.B, dependents)
    assert computed["businessKeys"] == ["pregnancy", "sex"] and computed["sections"] == ["registry"]
    stages = [DesignStage(value) for value in computed["stages"]]
    assert stages == stages_from(DesignStage.MAPPING)
    assert stage_rank(stages[0]) == stage_rank(DesignStage.MAPPING) and DesignStage.DOMAIN not in stages
    assert computed["artifactTypes"] == impact_rules.artifact_types([Section.REGISTRY], DeliveryMode.B)
    assert {"METADATA_PACKAGE", "MANIFEST", "VALIDATION_REPORT"} <= set(computed["artifactTypes"])

    earlier = impact_rules.compute_impact(document, [Change(
        ChangeLevel.MINOR, "adds an attribute", Section.DOMAIN.value,
        "/domain/entities/pregnancy", "pregnancy")], ChangeLevel.MINOR, DeliveryMode.B)
    assert DesignStage(earlier["stages"][0]) is DesignStage.DOMAIN
    assert DesignStage.MAPPING in [DesignStage(value) for value in earlier["stages"]]
    assert earlier["businessKeys"] == ["pregnancy"]


def test_bp_reg_001_minimal_selection_returns_exactly_the_selected_entries() -> None:
    """SRS 7.2 ``get_entries`` / FR-AG-001: a businessKey selector and a decisionId selector reach
    exactly the entries they address, so an agent editing one decision is not handed the whole
    Blueprint of a 5,000-object project (BP-NFR-03)."""
    version, _uids = _matrix()
    with unit_of_work() as session:
        by_key = queries.find_paths(session, MATRIX, version, keys=["weight"])
        by_decision = queries.find_paths(session, MATRIX, version, decision_ids=["MAP-002"])
    assert by_key == [("mapping", "weight", "/mapping/entries/weight"),
                      ("registry", "weight", "/registry/entries/weight")]
    assert {(section, key) for section, key, _ in by_decision} == {
        ("architecture", "MAP-002"), ("mapping", "clinic.name"),
        ("registry", "clinic.name"), ("registry", "legacy")}
    assert "weight" not in {key for _, key, _ in by_decision}

    selected = readmodel.get_entries(MATRIX, EntrySelector(business_keys=["weight"]), version)
    assert selected.version == version and selected.not_found == []
    assert {(entry["_section"], entry["businessKey"]) for entry in selected.entries} == {
        ("mapping", "weight"), ("registry", "weight")}
    decided = readmodel.get_entries(MATRIX, EntrySelector(decision_ids=["MAP-002"]), version)
    assert {entry["businessKey"] for entry in decided.entries if "businessKey" in entry} == {
        "clinic.name", "legacy"}
    missing = readmodel.get_entries(MATRIX, EntrySelector(business_keys=["ghost"]), version)
    assert missing.entries == [] and missing.not_found == ["ghost"]
