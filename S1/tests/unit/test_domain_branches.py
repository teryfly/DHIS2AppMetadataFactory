"""Branch coverage for the pure domain layer: actions, derivation, invalidation.

These run without a database, which is what lets CP-NFR-08's stricter 90% bar for
state machines and Gate rules be enforced on every commit.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from factory_control.domain import actions as A
from factory_control.domain import invalidation as INV
from factory_control.domain import structure as ST
from factory_control.domain import workflow_derive as WD
from factory_control.domain.enums import (
    ChangeLevel,
    GateStatus,
    RunStatus,
    StageStatus,
    StageType,
)

pytestmark = pytest.mark.unit


def context(**changes: object) -> A.ActionContext:
    base: dict[str, object] = {
        "stages": [A.StageSnapshot("s1", StageType.REQUIREMENTS, StageStatus.READY)],
        "gates": [],
        "runs": [],
        "environments": [A.EnvironmentFacts("dev", "TEST")],
    }
    base.update(changes)
    return A.ActionContext(**base)  # type: ignore[arg-type]


class TestProjectAndWorkflowActions:
    def test_every_project_action_is_blocked_when_archived(self) -> None:
        """CP-PRJ-006."""
        blocked = {a.name for a in A.project_actions(context(archived=True)) if not a.available}
        assert {"project.edit", "project.upgrade_mode", "change_request.create", "input.upload"} <= blocked
        assert "project.archive" in blocked
        # CP-PRJ-007: archiving is only the first half of the deletion precondition.
        assert "project.delete" in blocked

    def test_delete_requires_archive(self) -> None:
        blocked = [a for a in A.project_actions(context()) if a.name == "project.delete"]
        assert blocked[0].available is False
        assert blocked[0].details["requires"] == "ARCHIVED"

    def test_archived_project_still_needs_an_export_to_be_deletable(self) -> None:
        """CP-PRJ-007: the button must not light up for a DELETE that answers 409."""
        without = [a for a in A.project_actions(context(archived=True)) if a.name == "project.delete"]
        assert without[0].available is False
        assert without[0].reason_code == A.EXPORT_NOT_TAKEN
        assert without[0].details["requires"] == "PROJECT_EXPORT"

        with_export = [
            a
            for a in A.project_actions(context(archived=True, project_export_available=True))
            if a.name == "project.delete"
        ]
        assert with_export[0].available is True

    def test_pause_resume_cancel_when_cancelled(self) -> None:
        actions = A.workflow_actions(context(cancelled=True))
        assert all(a.available is False for a in actions)
        assert {a.reason_code for a in actions} == {A.WORKFLOW_CANCELLED}

    def test_pause_is_offered_once(self) -> None:
        paused = {a.name: a.available for a in A.workflow_actions(context(paused=True))}
        assert paused == {"workflow.pause": False, "workflow.resume": True, "workflow.cancel": True}

    def test_running_workflow_offers_pause_and_cancel(self) -> None:
        running = {a.name: a.available for a in A.workflow_actions(context())}
        assert running == {"workflow.pause": True, "workflow.resume": False, "workflow.cancel": True}


class TestStageActions:
    def test_waiting_approval_stage_offers_rerun_not_run(self) -> None:
        """A stage whose Gate is still WAITING cannot be re-submitted.

        CP-GATE-010 requires a resubmission to bind *different* content, and the way
        to change what is bound is a patch (which invalidates the Gate and returns
        the stage to READY) or a rerun — not another submission of the same state.
        """
        ctx = context(stages=[A.StageSnapshot("s1", StageType.MAPPING, StageStatus.WAITING_APPROVAL)])
        by_name = {a.name: a for a in A.stage_actions(ctx)}
        assert by_name["stage.run"].available is False
        assert by_name["stage.rerun"].available is True
        assert by_name["stage.submit_for_approval"].available is False
        assert by_name["stage.submit_for_approval"].reason_code == A.STAGE_NOT_READY
        assert by_name["patch.submit"].available is True

    def test_blocked_stage_rerun_uses_the_run_permission_the_route_checks(self) -> None:
        """The rerun endpoint authorizes with ``stage.run``; the action list must ask that too."""
        asked: list[str] = []

        def evaluate(names: object) -> dict[str, SimpleNamespace]:
            asked.extend(str(name) for name in names)  # type: ignore[union-attr]
            return {name: SimpleNamespace(allowed=True, reason_code="", details={}) for name in asked}

        payload = A.compute_actions(
            context(
                stages=[A.StageSnapshot("s1", StageType.REQUIREMENTS, StageStatus.BLOCKED)],
                evaluate=evaluate,
            )
        )
        allowed = {a["action"] for a in payload["allowed"]}
        assert "stage.rerun" in allowed
        assert "stage.run" in asked
        assert "stage.rerun" not in asked

    def test_blocked_design_stage_can_be_fixed_and_resubmitted(self) -> None:
        """A freeze-blocked design stage is a content verdict, so the recovery is a patch plus
        a resubmit — offering only rerun would loop, because the naming policy reproduces the
        exact conflict that blocked it."""
        ctx = context(stages=[A.StageSnapshot("s1", StageType.MAPPING, StageStatus.BLOCKED)])
        by_name = {a.name: a for a in A.stage_actions(ctx)}
        assert by_name["stage.submit_for_approval"].available is True
        assert by_name["patch.submit"].available is True
        assert by_name["stage.rerun"].available is True

    def test_blocked_run_step_offers_no_resubmit(self) -> None:
        """A Deployment Run step is blocked by conditions outside the draft, so it gets neither
        the submit nor the patch affordance the design stages use to recover."""
        ctx = context(stages=[A.StageSnapshot("s1", StageType.COMMIT, StageStatus.BLOCKED)])
        by_name = {a.name: a for a in A.stage_actions(ctx)}
        assert by_name["stage.submit_for_approval"].available is False
        assert "patch.submit" not in by_name

    def test_manual_check_offers_resolution_only(self) -> None:
        """附录 A: MANUAL_CHECK leaves via a user verdict."""
        ctx = context(stages=[A.StageSnapshot("s1", StageType.COMMIT, StageStatus.MANUAL_CHECK)])
        by_name = {a.name: a for a in A.stage_actions(ctx) if a.available}
        assert "stage.resolve_manual_check" in by_name
        assert "stage.run" not in by_name

    def test_locked_stage_offers_nothing_actionable(self) -> None:
        ctx = context(stages=[A.StageSnapshot("s1", StageType.HANDOFF, StageStatus.LOCKED)])
        assert all(a.available is False for a in A.stage_actions(ctx))

    def test_mapping_patch_after_architecture_gate_needs_change_request(self) -> None:
        """CP-WF-017 / CP-BR-09."""
        ctx = context(
            architecture_gate_approved=True,
            stages=[A.StageSnapshot("s1", StageType.MAPPING, StageStatus.READY)],
        )
        patch = next(a for a in A.stage_actions(ctx) if a.name == "patch.submit")
        assert patch.available is False
        assert patch.reason_code == A.MAPPING_FROZEN_BY_GATE

    def test_mapping_patch_before_gate_is_allowed(self) -> None:
        ctx = context(
            architecture_gate_approved=False,
            stages=[A.StageSnapshot("s1", StageType.MAPPING, StageStatus.READY)],
        )
        patch = next(a for a in A.stage_actions(ctx) if a.name == "patch.submit")
        assert patch.available is True

    def test_awaiting_answer_stage_offers_question_answer(self) -> None:
        ctx = context(
            stages=[
                A.StageSnapshot(
                    "s1", StageType.REQUIREMENTS, StageStatus.WAITING_INPUT, has_open_question=True
                )
            ]
        )
        assert any(a.name == "question.answer" and a.available for a in A.stage_actions(ctx))

    def test_a_medium_blocking_question_still_offers_question_answer(self) -> None:
        """The answer action is gated on *blocking*, not on severity.

        A MEDIUM-severity question raised as blocking parks the stage in WAITING_INPUT and the
        run resumes only once it is answered — so the review screen must be able to submit an
        answer. Gating on HIGH only left that questionnaire with no enabled submit button.
        """
        ctx = context(stages=[A.StageSnapshot("s1", StageType.MAPPING, StageStatus.WAITING_INPUT, has_open_question=True)])
        answer = next(a for a in A.stage_actions(ctx) if a.name == "question.answer")
        assert answer.available is True

    def test_a_stage_without_open_questions_refuses_question_answer(self) -> None:
        ctx = context(stages=[A.StageSnapshot("s1", StageType.MAPPING, StageStatus.READY)])
        answer = next(a for a in A.stage_actions(ctx) if a.name == "question.answer")
        assert answer.available is False
        assert answer.reason_code == A.NO_OPEN_QUESTION

    def test_action_serialisation_carries_reason_only_when_blocked(self) -> None:
        ok = A.Action("stage.run", True, target_id="s1").as_dict()
        blocked = A.Action("stage.run", False, reason_code="X", target_id="s1", details={"a": 1}).as_dict()
        assert "reasonCode" not in ok and ok["targetId"] == "s1"
        assert blocked["reasonCode"] == "X" and blocked["details"] == {"a": 1}


class TestGateAndRunActions:
    def test_gate_not_waiting_blocks_both_decisions(self) -> None:
        ctx = context(
            gates=[A.GateSnapshot("g1", "ARCHITECTURE_GATE", GateStatus.APPROVED)],
        )
        reasons = {a.name for a in A.gate_actions(ctx)}
        assert reasons == {"gate.approve", "gate.reject"}
        assert all(a.available is False for a in A.gate_actions(ctx))

    def test_archived_project_blocks_approval(self) -> None:
        ctx = context(
            archived=True,
            gates=[A.GateSnapshot("g1", "DEPLOY_GATE", GateStatus.WAITING)],
        )
        assert [a.reason_code for a in A.gate_actions(ctx)] == [A.PROJECT_ARCHIVED]

    def test_gate_actions_ask_for_the_precise_gate_permission(self) -> None:
        """A role holds ``gate.approve.DOMAIN_GATE``, never the coarse ``gate.approve``."""
        asked: list[str] = []

        def evaluate(names: object) -> dict[str, SimpleNamespace]:
            asked.extend(str(name) for name in names)  # type: ignore[union-attr]
            return {
                name: SimpleNamespace(
                    allowed=name == "gate.approve.DOMAIN_GATE",
                    reason_code="PERMISSION_DENIED",
                    details={},
                )
                for name in asked
            }

        payload = A.compute_actions(
            context(
                gates=[A.GateSnapshot("g1", "DOMAIN_GATE", GateStatus.WAITING)],
                evaluate=evaluate,
            )
        )
        assert "gate.approve.DOMAIN_GATE" in asked
        assert "gate.approve" not in asked
        allowed = {item["action"] for item in payload["allowed"]}
        assert {"gate.approve", "gate.reject"} <= allowed

    def test_deploy_gate_asks_for_the_environment_qualified_permission(self) -> None:
        asked: list[str] = []

        def evaluate(names: object) -> dict[str, SimpleNamespace]:
            asked.extend(str(name) for name in names)  # type: ignore[union-attr]
            return {name: SimpleNamespace(allowed=True, reason_code="", details={}) for name in asked}

        A.compute_actions(
            context(
                gates=[
                    A.GateSnapshot(
                        "g1", "DEPLOY_GATE", GateStatus.WAITING, environment_key="prod"
                    )
                ],
                environments=[A.EnvironmentFacts("prod", "PRODUCTION")],
                evaluate=evaluate,
            )
        )
        assert "gate.approve.DEPLOY_GATE.PRODUCTION" in asked

    def test_open_warnings_block_approval_only(self) -> None:
        ctx = context(
            gates=[A.GateSnapshot("g1", "PACKAGE_GATE", GateStatus.WAITING, open_warnings=2)],
        )
        actions = A.gate_actions(ctx)
        approve = next(a for a in actions if a.name == "gate.approve")
        reject = next(a for a in actions if a.name == "gate.reject")
        assert approve.available is False and approve.reason_code == A.WARNING_UNRESOLVED
        assert reject.available is True

    def test_deployment_create_requires_finished_design_line(self) -> None:
        """CP-WF-018."""
        ctx = context(design_line_complete=False)
        create = next(a for a in A.run_actions(ctx) if a.name == "deployment.create")
        assert create.available is False and create.reason_code == A.DESIGN_LINE_INCOMPLETE

    def test_deployment_create_blocked_by_open_run(self) -> None:
        """CP-BR-12: one open Run per environment."""
        ctx = context(
            design_line_complete=True,
            runs=[A.RunSnapshot("r1", "dev", "1.0.0", RunStatus.IN_PROGRESS)],
        )
        create = next(a for a in A.run_actions(ctx) if a.name == "deployment.create")
        assert create.reason_code == A.RUN_OPEN_FOR_ENVIRONMENT

    def test_commit_requires_approved_deploy_gate_first(self) -> None:
        ctx = context(
            design_line_complete=True,
            runs=[A.RunSnapshot("r1", "dev", "1.0.0", RunStatus.WAITING_APPROVAL)],
        )
        commit = next(a for a in A.run_actions(ctx) if a.name == "execution.commit")
        assert commit.reason_code == A.GATE_NOT_APPROVED

    def test_finished_run_cannot_be_cancelled(self) -> None:
        ctx = context(
            design_line_complete=True,
            runs=[A.RunSnapshot("r1", "dev", "1.0.0", RunStatus.CANCELLED, deploy_gate_approved=False)],
        )
        actions = {a.name: a for a in A.run_actions(ctx)}
        assert actions["deployment.cancel"].reason_code == A.NO_OPEN_RUN
        assert actions["execution.commit"].available is False

    def test_production_commit_allowed_once_verified_elsewhere(self) -> None:
        ctx = context(
            design_line_complete=True,
            environments=[A.EnvironmentFacts("prod", "PRODUCTION")],
            runs=[A.RunSnapshot("r1", "prod", "1.0.0", RunStatus.WAITING_APPROVAL, deploy_gate_approved=True)],
            verified_version_keys={"1.0.0:NON_PRODUCTION_VERIFIED"},
        )
        commit = next(a for a in A.run_actions(ctx) if a.name == "execution.commit")
        assert commit.available is True

    def test_compute_actions_shape(self) -> None:
        payload = A.compute_actions(context())
        assert set(payload) == {"allowed", "unavailable"}
        assert all(isinstance(a, dict) and "action" in a for a in payload["allowed"] + payload["unavailable"])


class TestInvalidationEdges:
    def test_unknown_affected_stage_names_are_ignored(self) -> None:
        """A provider may name a stage S1 does not model; that must not crash rule 1."""
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MINOR,
                affected_stages=["NOT_A_STAGE"],
                stages=[INV.StageState("s1", StageType.REQUIREMENTS, StageStatus.PASSED)],
            )
        )
        assert plan.invalidate_stage_ids == []

    def test_finished_gates_are_not_re_invalidated(self) -> None:
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MAJOR,
                affected_stages=[StageType.MAPPING],
                stages=[
                    INV.StageState("s1", StageType.MAPPING, StageStatus.PASSED),
                    INV.StageState("s2", StageType.BASELINE_COMPILE, StageStatus.PASSED),
                ],
                gates=[
                    INV.GateState("g1", GateStatus.APPROVED, stage_type=StageType.MAPPING),
                    INV.GateState("g2", GateStatus.INVALIDATED, stage_type=StageType.MAPPING),
                ],
            )
        )
        assert plan.invalidate_gate_ids == []
        assert set(plan.invalidate_stage_ids) == {"s1", "s2"}

    def test_plan_serialisation(self) -> None:
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MAJOR,
                affected_stages=[StageType.DISCOVERY],
                stages=[INV.StageState("s1", StageType.DISCOVERY, StageStatus.PASSED)],
                runs=[INV.RunState("r1", RunStatus.IN_PROGRESS)],
            )
        )
        as_dict = plan.as_dict()
        assert as_dict["cancelRuns"] == ["r1"] and as_dict["reason"] == "BLUEPRINT_SUPERSEDED"

    def test_stages_from_includes_handoff(self) -> None:
        assert INV.stages_from(StageType.MAPPING)[-1] is StageType.HANDOFF


class TestDerivationEdges:
    def test_blocked_wins_over_waiting_user(self) -> None:
        facts = WD.WorkflowFacts(
            stages=[
                WD.StageFact(StageType.REQUIREMENTS, StageStatus.BLOCKED),
                WD.StageFact(StageType.DOMAIN, StageStatus.WAITING_APPROVAL),
            ]
        )
        assert WD.derive_workflow_status(facts) is WD.WorkflowStatus.BLOCKED

    def test_no_stages_means_active(self) -> None:
        assert WD.derive_workflow_status(WD.WorkflowFacts()) is WD.WorkflowStatus.ACTIVE

    def test_run_verifying_but_line_open_is_not_completed(self) -> None:
        facts = WD.WorkflowFacts(
            stages=[WD.StageFact(StageType.REQUIREMENTS, StageStatus.READY)],
            runs=[WD.RunFact(RunStatus.VERIFIED)],
        )
        assert WD.derive_workflow_status(facts) is not WD.WorkflowStatus.COMPLETED

    def test_current_stage_prefers_running_then_waiting_then_ready(self) -> None:
        facts = WD.WorkflowFacts(
            stages=[
                WD.StageFact(StageType.REQUIREMENTS, StageStatus.PASSED),
                WD.StageFact(StageType.DOMAIN, StageStatus.WAITING_INPUT),
                WD.StageFact(StageType.MAPPING, StageStatus.READY),
            ]
        )
        ids = {
            StageType.REQUIREMENTS: "s1",
            StageType.DOMAIN: "s2",
            StageType.MAPPING: "s3",
        }
        assert WD.current_stage_id(facts, ids) == "s2"

    def test_current_stage_none_when_all_settled(self) -> None:
        facts = WD.WorkflowFacts(stages=[WD.StageFact(StageType.REQUIREMENTS, StageStatus.PASSED)])
        assert WD.current_stage_id(facts, {}) is None

    def test_milestone_needs_both_gates_in_mode_b(self) -> None:
        facts = WD.WorkflowFacts(
            delivery_mode="B", architecture_gate_approved=True, design_gate_approved=False
        )
        assert WD.derive_milestone_status(facts) is WD.MilestoneStatus.DRAFT
        both = WD.WorkflowFacts(
            delivery_mode="B", architecture_gate_approved=True, design_gate_approved=True
        )
        assert WD.derive_milestone_status(both) is WD.MilestoneStatus.DESIGN_APPROVED

    def test_milestone_watermarks_in_order(self) -> None:
        base = dict(architecture_gate_approved=True)
        assert (
            WD.derive_milestone_status(WD.WorkflowFacts(package_gate_approved=True, **base))  # type: ignore[arg-type]
            is WD.MilestoneStatus.PACKAGE_APPROVED
        )
        assert (
            WD.derive_milestone_status(WD.WorkflowFacts(dhis2_validated=True, **base))  # type: ignore[arg-type]
            is WD.MilestoneStatus.DHIS2_VALIDATED
        )
        assert (
            WD.derive_milestone_status(
                WD.WorkflowFacts(runs=[WD.RunFact(RunStatus.COMMITTED)], **base)  # type: ignore[arg-type]
            )
            is WD.MilestoneStatus.DEPLOYED
        )


class TestStructureEdges:
    def test_run_line_order_is_fixed(self) -> None:
        """CP-WF-021."""
        plan = ST.run_line_plan(delivery_mode="B", has_fixture_spec=True, environment_class="TEST")
        assert [entry.stage_type.value for entry in plan] == [
            "BIND_COMPILE",
            "STATIC_VALIDATION",
            "DHIS2_VALIDATE",
            "COMMIT",
            "POST_VERIFY",
            "FIXTURE_IMPORT",
        ]
        assert plan[0].status is StageStatus.READY

    def test_unlock_targets_empty_at_end_of_line(self) -> None:
        statuses = {s: StageStatus.PASSED for s in ST.MAIN_LINE}
        assert ST.unlock_targets_for(statuses, StageType.HANDOFF) == []

    def test_stage_index_and_skipped_reasons(self) -> None:
        plan = ST.design_line_plan(delivery_mode="A", has_fixture_spec=True)
        skipped = {entry.stage_type: entry.reason for entry in plan if entry.reason}
        assert skipped[StageType.PAGE_DESIGN] == "DELIVERY_MODE_A"
        assert INV.stage_index(StageType.REQUIREMENTS) == 0
