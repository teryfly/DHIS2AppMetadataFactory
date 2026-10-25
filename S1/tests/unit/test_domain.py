"""Pure-domain unit tests: state machines, propagation, derivation, actions.

No database, no providers — CP-NFR-08 requires the state machines and Gate rules
to be covered to 90%, and that is only meaningful if these run without fixtures.
"""

from __future__ import annotations

import pytest

from factory_control.config import Settings
from factory_control.domain import actions as A
from factory_control.domain import invalidation as INV
from factory_control.domain import structure as ST
from factory_control.domain import workflow_derive as WD
from factory_control.domain.enums import (
    ChangeLevel,
    GateStatus,
    MilestoneStatus,
    QuestionStatus,
    RunStatus,
    StageStatus,
    StageType,
    WorkflowStatus,
)
from factory_control.domain.machines import (
    assert_cr_transition,
    assert_gate_transition,
    assert_question_transition,
    assert_run_transition,
)
from factory_control.domain.stage_machine import allowed_targets, assert_transition, can_transition
from factory_control.errors import InvalidStateError
from factory_control.i18n import message_for, negotiate_language


class TestStageMachineAppendixA:
    """附录 A is the whole contract for stage transitions."""

    @pytest.mark.parametrize(
        ("current", "targets"),
        [
            (StageStatus.LOCKED, {"READY", "SKIPPED", "INVALIDATED"}),
            (StageStatus.READY, {"RUNNING", "WAITING_APPROVAL", "SKIPPED", "INVALIDATED", "LOCKED"}),
            (
                StageStatus.RUNNING,
                {
                    "WAITING_INPUT",
                    "WAITING_APPROVAL",
                    "PASSED",
                    "FAILED",
                    "BLOCKED",
                    "MANUAL_CHECK",
                    "INVALIDATED",
                },
            ),
            (StageStatus.WAITING_INPUT, {"RUNNING", "READY", "FAILED", "INVALIDATED"}),
            (StageStatus.WAITING_APPROVAL, {"PASSED", "READY", "INVALIDATED"}),
            (StageStatus.PASSED, {"RUNNING", "INVALIDATED"}),
            (StageStatus.FAILED, {"READY", "INVALIDATED"}),
            (StageStatus.BLOCKED, {"READY", "INVALIDATED"}),
            (StageStatus.MANUAL_CHECK, {"PASSED", "FAILED"}),
            (StageStatus.INVALIDATED, {"READY", "LOCKED"}),
            (StageStatus.SKIPPED, {"LOCKED", "READY"}),
        ],
    )
    def test_allowed_set_matches_appendix_a(self, current: StageStatus, targets: set[str]) -> None:
        assert {s.value for s in allowed_targets(current)} == targets

    def test_illegal_transition_raises_409_code(self) -> None:
        with pytest.raises(InvalidStateError) as caught:
            assert_transition(StageStatus.LOCKED, StageStatus.PASSED)
        assert caught.value.error_code == "INVALID_STATE"
        assert "allowed" in caught.value.details

    def test_string_states_are_accepted(self) -> None:
        assert can_transition("READY", "RUNNING") is True
        assert can_transition("PASSED", "READY") is False


class TestOtherMachines:
    def test_gate_only_leaves_waiting(self) -> None:
        assert_gate_transition(GateStatus.WAITING, GateStatus.APPROVED)
        with pytest.raises(InvalidStateError):
            assert_gate_transition(GateStatus.APPROVED, GateStatus.WAITING)
        assert_gate_transition(GateStatus.APPROVED, GateStatus.INVALIDATED)

    def test_answered_question_is_immutable(self) -> None:
        """CP-Q-007: an answer cannot change; a new question must be raised."""
        assert_question_transition(QuestionStatus.OPEN, QuestionStatus.ANSWERED)
        with pytest.raises(InvalidStateError):
            assert_question_transition(QuestionStatus.ANSWERED, QuestionStatus.OPEN)

    def test_run_states(self) -> None:
        assert_run_transition(RunStatus.CREATED, RunStatus.IN_PROGRESS)
        with pytest.raises(InvalidStateError):
            assert_run_transition(RunStatus.VERIFIED, RunStatus.IN_PROGRESS)

    def test_cr_only_cancels_before_approval(self) -> None:
        """CP-CR-002: DRAFT/ANALYZED may be cancelled, APPROVED may not."""
        assert_cr_transition("DRAFT", "CANCELLED")
        assert_cr_transition("ANALYZED", "REJECTED")
        with pytest.raises(InvalidStateError):
            assert_cr_transition("APPROVED", "CANCELLED")


class TestInvalidationPropagation:
    """CP-WF-015 rules 1–5."""

    def _stages(self) -> list[INV.StageState]:
        return [
            INV.StageState("s1", StageType.REQUIREMENTS, StageStatus.PASSED),
            INV.StageState("s2", StageType.DOMAIN, StageStatus.PASSED),
            INV.StageState("s3", StageType.MAPPING, StageStatus.PASSED),
            INV.StageState("s4", StageType.PAGE_DESIGN, StageStatus.PASSED),
        ]

    def test_major_invalidates_mapping_and_everything_after(self) -> None:
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MAJOR,
                affected_stages=[StageType.MAPPING],
                current_stage_id="s4",
                stages=self._stages(),
            )
        )
        assert "s3" in plan.invalidate_stage_ids
        assert "s1" not in plan.invalidate_stage_ids

    def test_current_stage_is_left_alone(self) -> None:
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MINOR,
                affected_stages=[StageType.PAGE_DESIGN],
                current_stage_id="s4",
                stages=self._stages(),
            )
        )
        assert "s4" not in plan.invalidate_stage_ids

    def test_a_promotion_does_not_invalidate_the_stage_that_triggered_it(self) -> None:
        """CP-WF-015 rule 1 with the approving stage named, not just the workflow pointer.

        Approving REQUIREMENT_GATE promotes the Blueprint, and BP-IMP-003 puts REQUIREMENTS among
        the affected stages ("that stage plus every later stage"). ``workflow.current_stage_id``
        has already moved to DOMAIN by then, so reading rule 1 as "anything but the current stage"
        invalidates REQUIREMENTS on its own approval. It cannot then recover: re-running it has
        nothing left to add to an already-promoted section, so it fails, while its draft stays OPEN
        and BP-DRF-001 (one active draft per project) blocks DOMAIN from opening one at all.
        """
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MINOR,
                affected_stages=[StageType.REQUIREMENTS, StageType.DOMAIN],
                current_stage_id="s3",
                protect_stage_ids=["s1"],
                stages=self._stages(),
            )
        )
        assert "s1" not in plan.invalidate_stage_ids, "a stage invalidated itself by passing"
        assert "s2" in plan.invalidate_stage_ids, "the successor still has to lose its basis"

    def test_protecting_a_stage_also_protects_its_passed_handoff(self) -> None:
        """Rule 4 loses a PASSED HANDOFF's basis for the same reason rule 1 does."""
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MINOR,
                affected_stages=[StageType.HANDOFF],
                protect_stage_ids=["s5"],
                stages=[INV.StageState("s5", StageType.HANDOFF, StageStatus.PASSED)],
            )
        )
        assert plan.invalidate_stage_ids == []

    def test_open_run_cancelled_but_committing_run_marked_outdated(self) -> None:
        """Rules 2 and 3: history is never rewritten."""
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MINOR,
                affected_stages=[StageType.MAPPING],
                stages=self._stages(),
                runs=[
                    INV.RunState("r-open", RunStatus.IN_PROGRESS),
                    INV.RunState("r-commit", RunStatus.IN_PROGRESS, in_commit=True),
                    INV.RunState("r-done", RunStatus.VERIFIED),
                ],
            )
        )
        assert plan.cancel_run_ids == ["r-open"]
        assert set(plan.mark_outdated_run_ids) == {"r-commit", "r-done"}

    def test_waiting_gates_of_affected_stages_are_invalidated(self) -> None:
        plan = INV.plan(
            INV.InvalidationInput(
                change_level=ChangeLevel.MAJOR,
                affected_stages=[StageType.MAPPING],
                stages=self._stages(),
                gates=[
                    INV.GateState("g-map", GateStatus.WAITING, stage_type=StageType.MAPPING),
                    INV.GateState("g-req", GateStatus.WAITING, stage_type=StageType.REQUIREMENTS),
                ],
            )
        )
        assert plan.invalidate_gate_ids == ["g-map"]


class TestDerivation:
    def make(self, **changes: object) -> WD.WorkflowFacts:
        base = dict(
            stages=[
                WD.StageFact(StageType.REQUIREMENTS, StageStatus.PASSED),
                WD.StageFact(StageType.DOMAIN, StageStatus.PASSED),
                WD.StageFact(StageType.MAPPING, StageStatus.WAITING_APPROVAL),
            ],
            runs=[],
        )
        base.update(changes)
        return WD.WorkflowFacts(**base)  # type: ignore[arg-type]

    def test_priority_cancelled_beats_paused(self) -> None:
        """CP-WF-023 order: CANCELLED → PAUSED → COMPLETED → FAILED → BLOCKED → WAITING_USER."""
        assert WD.derive_workflow_status(self.make(cancelled=True, paused=True)) is WorkflowStatus.CANCELLED

    def test_waiting_user_when_a_gate_is_open(self) -> None:
        assert WD.derive_workflow_status(self.make()) is WorkflowStatus.WAITING_USER

    def test_manual_check_counts_as_failed(self) -> None:
        derived = self.make(
            stages=[WD.StageFact(StageType.REQUIREMENTS, StageStatus.MANUAL_CHECK)]
        )
        assert WD.derive_workflow_status(derived) is WorkflowStatus.FAILED

    def test_completed_needs_a_verified_run_and_finished_line(self) -> None:
        derived = WD.WorkflowFacts(
            stages=[WD.StageFact(StageType.REQUIREMENTS, StageStatus.PASSED)],
            runs=[WD.RunFact(RunStatus.VERIFIED)],
        )
        assert WD.derive_workflow_status(derived) is WorkflowStatus.COMPLETED

    def test_mode_b_also_needs_handoff_passed(self) -> None:
        derived = WD.WorkflowFacts(
            delivery_mode="B",
            stages=[
                WD.StageFact(StageType.REQUIREMENTS, StageStatus.PASSED),
                WD.StageFact(StageType.HANDOFF, StageStatus.PASSED),
            ],
            runs=[WD.RunFact(RunStatus.VERIFIED)],
        )
        assert WD.derive_workflow_status(derived) is WorkflowStatus.COMPLETED

    def test_mode_b_without_handoff_is_not_completed(self) -> None:
        derived = WD.WorkflowFacts(
            delivery_mode="B",
            stages=[WD.StageFact(StageType.REQUIREMENTS, StageStatus.PASSED)],
            runs=[WD.RunFact(RunStatus.VERIFIED)],
        )
        assert WD.derive_workflow_status(derived) is not WorkflowStatus.COMPLETED

    def test_milestone_watermarks(self) -> None:
        """CP-WF-024."""
        assert (
            WD.derive_milestone_status(self.make(architecture_gate_approved=True))
            is MilestoneStatus.DESIGN_APPROVED
        )
        assert WD.derive_milestone_status(self.make()) is MilestoneStatus.DRAFT
        with_run = WD.WorkflowFacts(
            architecture_gate_approved=True,
            stages=[WD.StageFact(StageType.REQUIREMENTS, StageStatus.PASSED)],
            runs=[WD.RunFact(RunStatus.VERIFIED)],
        )
        assert WD.derive_milestone_status(with_run) is MilestoneStatus.VERIFIED


class TestStructureAndSkips:
    def test_mode_a_skips_page_design_and_handoff(self) -> None:
        """AC-WF-01."""
        plan = ST.design_line_plan(delivery_mode="A", has_fixture_spec=True)
        by_type = {entry.stage_type: entry for entry in plan}
        assert by_type[StageType.PAGE_DESIGN].status is StageStatus.SKIPPED
        assert by_type[StageType.HANDOFF].status is StageStatus.SKIPPED
        assert by_type[StageType.REQUIREMENTS].status is StageStatus.READY

    def test_mode_b_keeps_page_design(self) -> None:
        plan = ST.design_line_plan(delivery_mode="B", has_fixture_spec=True)
        by_type = {entry.stage_type: entry for entry in plan}
        assert by_type[StageType.PAGE_DESIGN].status is StageStatus.LOCKED

    def test_fixture_stage_skips(self) -> None:
        """CP-WF-002 rules 2 and 3."""
        no_spec = ST.run_line_plan(delivery_mode="A", has_fixture_spec=False, environment_class="TEST")
        prod = ST.run_line_plan(delivery_mode="A", has_fixture_spec=True, environment_class="PRODUCTION")
        assert _status(no_spec, StageType.FIXTURE_IMPORT) is StageStatus.SKIPPED
        assert _status(prod, StageType.FIXTURE_IMPORT) is StageStatus.SKIPPED
        normal = ST.run_line_plan(delivery_mode="A", has_fixture_spec=True, environment_class="UAT")
        assert _status(normal, StageType.FIXTURE_IMPORT) is StageStatus.LOCKED

    def test_next_design_stage_skips_over_skipped(self) -> None:
        statuses = {
            StageType.REQUIREMENTS: StageStatus.PASSED,
            StageType.DOMAIN: StageStatus.PASSED,
            StageType.DISCOVERY: StageStatus.PASSED,
            StageType.MAPPING: StageStatus.PASSED,
            StageType.PAGE_DESIGN: StageStatus.SKIPPED,
            StageType.BASELINE_COMPILE: StageStatus.LOCKED,
        }
        assert ST.next_design_stage(statuses, StageType.MAPPING) is StageType.BASELINE_COMPILE


class TestAllowedActions:
    def context(self, **changes: object) -> A.ActionContext:
        base = dict(
            stages=[A.StageSnapshot("s1", StageType.REQUIREMENTS, StageStatus.READY)],
            gates=[],
            runs=[],
            environments=[A.EnvironmentFacts("dev", "TEST")],
        )
        base.update(changes)
        return A.ActionContext(**base)  # type: ignore[arg-type]

    def test_running_elsewhere_blocks_new_run(self) -> None:
        """CP-WF-006 surfaced as a reason code, not a silent button-off."""
        ctx = self.context(
            stages=[
                A.StageSnapshot("s1", StageType.REQUIREMENTS, StageStatus.RUNNING),
                A.StageSnapshot("s2", StageType.DOMAIN, StageStatus.READY),
            ]
        )
        blocked = [a for a in A.stage_actions(ctx) if a.name == "stage.run" and a.target_id == "s2"]
        assert blocked[0].available is False
        assert blocked[0].reason_code == A.ALREADY_RUNNING_ELSEWHERE

    def test_production_commit_needs_prior_verification(self) -> None:
        """CP-AC-07 as an explanation, per CP-WF-026."""
        ctx = A.ActionContext(
            design_line_complete=True,
            environments=[A.EnvironmentFacts("prod", "PRODUCTION")],
            runs=[A.RunSnapshot("r1", "prod", "1.0.0", RunStatus.WAITING_APPROVAL, deploy_gate_approved=True)],
        )
        commit = [a for a in A.run_actions(ctx) if a.name == "execution.commit"]
        assert commit[0].available is False
        assert commit[0].reason_code == A.PRIOR_VERIFICATION_MISSING

    def test_locked_environment_and_window(self) -> None:
        """CP-WF-026: an unavailable action always carries one reason code."""
        def commit_reason(**protection: bool) -> str | None:
            ctx = A.ActionContext(
                design_line_complete=True,
                environments=[A.EnvironmentFacts("dev", "TEST", **protection)],
                runs=[
                    A.RunSnapshot("r1", "dev", "1.0.0", RunStatus.WAITING_APPROVAL, deploy_gate_approved=True)
                ],
            )
            action = next(a for a in A.run_actions(ctx) if a.name == "execution.commit")
            return action.reason_code if not action.available else None

        assert commit_reason(emergency_locked=True) == A.ENVIRONMENT_LOCKED
        assert commit_reason(within_commit_window=False) == A.OUTSIDE_COMMIT_WINDOW

    def test_archived_project_blocks_writes(self) -> None:
        ctx = self.context(archived=True)
        names = {a.name for a in A.project_actions(ctx) if not a.available}
        assert "project.edit" in names
        assert "project.delete" not in names

    def test_a_disabled_environment_keeps_the_controls_that_can_re_enable_it(self) -> None:
        """S4 refuses an operation *about the instance*, not every operation *about the row*.

        ``build_target`` raises for a DISABLED environment before it looks at
        ``allow_unprobed``, so the connection test and the capability probe are refused as well
        as any commit — offering them would put a button on the screen whose answer is a
        refusal. Editing the profile and locking or unlocking are S4's own exceptions, and
        withdrawing them would strand an environment nobody can switch back on.
        """
        ctx = self.context(environments=[A.EnvironmentFacts("dev", "TEST", enabled=False)])
        by_name = {a.name: a for a in A.environment_actions(ctx)}
        for name in ("environment.disable", "environment.test_connection", "environment.probe_capabilities"):
            assert by_name[name].available is False, name
            assert by_name[name].reason_code == A.ENVIRONMENT_DISABLED, name
        for name in ("environment.edit", "environment.emergency_lock"):
            assert by_name[name].available is True, name

    def test_one_rule_draws_the_environment_control_in_both_views(self) -> None:
        """The workflow list and the Administration pages used to differ on ``details.locked``."""
        facts = [A.EnvironmentFacts("dev", "TEST", emergency_locked=True, enabled=False)]
        workflow = {a.name: a for a in A.environment_actions(self.context(environments=facts))}
        platform = {
            a.name: a for a in A.platform_actions(facts) if a.target_id == "dev"
        }
        assert workflow["environment.emergency_lock"].details == {"locked": True}
        assert platform["environment.emergency_lock"].details == workflow["environment.emergency_lock"].details
        assert platform["environment.test_connection"].available is False
        assert platform["environment.test_connection"].reason_code == A.ENVIRONMENT_DISABLED
        # `environment.create` belongs to the platform once, never per environment
        assert "environment.create" not in platform

    def test_high_question_open_offers_answer_action(self) -> None:
        ctx = self.context(
            stages=[A.StageSnapshot("s1", StageType.MAPPING, StageStatus.WAITING_APPROVAL, True)]
        )
        assert any(a.name == "question.answer" and a.available for a in A.stage_actions(ctx))

    def test_compute_actions_splits_allowed_and_unavailable(self) -> None:
        payload = A.compute_actions(self.context())
        assert set(payload) == {"allowed", "unavailable"}
        assert all(item["available"] for item in payload["allowed"])


class TestErrorMappingAndI18n:
    def test_status_mapping(self) -> None:
        from factory_control.errors import http_status_for

        assert http_status_for("OPERATOR_REQUIRED") == 400
        assert http_status_for("INVALID_STATE") == 409
        assert http_status_for("IDEMPOTENCY_CONFLICT") == 422
        assert http_status_for("NOT_FOUND") == 404
        assert http_status_for("SOMETHING_UNKNOWN") == 500

    def test_language_negotiation(self) -> None:
        assert negotiate_language("zh-CN,zh;q=0.9") == "zh-CN"
        assert negotiate_language("en-US") == "en"
        assert negotiate_language(None) == "en"

    def test_localised_messages_differ(self) -> None:
        """CP-NFR-09."""
        assert message_for("GATE_NOT_APPROVED", "zh-CN") != message_for("GATE_NOT_APPROVED", "en")
        assert "X-Operator" in message_for("OPERATOR_REQUIRED", "en")

    def test_settings_validation_reports_problems(self) -> None:
        bad = Settings(database_url="", migrations_mode="sideways", workflow_engine="nope")
        problems = bad.validate_runtime()
        assert any("MIGRATIONS_MODE" in p for p in problems)
        assert any("DATABASE_URL" in p for p in problems)


def _status(plan: list[ST.StagePlan], stage_type: StageType) -> StageStatus:
    return next(entry.status for entry in plan if entry.stage_type is stage_type)


def test_a_run_reaches_verified_only_after_a_commit() -> None:
    """7.4 / CP-WF-021: VERIFIED is the state a Run reaches past COMMITTED, not instead of it.

    The transition table used to allow IN_PROGRESS → VERIFIED. Nothing takes that edge today
    -- the marker is only set from COMMITTED -- but leaving it open means a later refactor can
    declare a Deployment Run verified without any COMMIT having happened, and 27.2 lists the
    COMMIT as a precondition of the project being complete.
    """
    from factory_control.domain.machines import RUN_TRANSITIONS

    reaching_verified = {
        current for current, targets in RUN_TRANSITIONS.items() if RunStatus.VERIFIED in targets
    }
    assert reaching_verified == {RunStatus.COMMITTED, RunStatus.MANUAL_CHECK}, (
        "MANUAL_CHECK stays allowed: an unconfirmed COMMIT that a human has verified as "
        "applied still has its POST_VERIFY step to pass"
    )
    assert RunStatus.COMMITTED in RUN_TRANSITIONS[RunStatus.IN_PROGRESS]
    assert RunStatus.COMMITTED in RUN_TRANSITIONS[RunStatus.WAITING_APPROVAL]
    # terminal states stay terminal (OUTDATED is a marker, not a state -- CP-WF-019)
    for terminal in (RunStatus.VERIFIED, RunStatus.FAILED, RunStatus.CANCELLED):
        assert RUN_TRANSITIONS[terminal] == frozenset(), terminal


class TestChangeRequestAvailability:
    """FR-UI-009: a change request's buttons come from CP-CR-002's own transition table."""

    @staticmethod
    def _offered(status: str, archived: bool = False) -> set[str]:
        from factory_control.domain.actions import ActionContext, ChangeRequestSnapshot

        ctx = ActionContext(
            archived=archived,
            change_requests=[ChangeRequestSnapshot("CR-1", status)],
        )
        from factory_control.domain.actions import change_request_actions

        return {a.name for a in change_request_actions(ctx) if a.available}

    def test_draft_offers_only_analysis_and_cancel(self) -> None:
        assert self._offered("DRAFT") == {"change_request.impact", "change_request.cancel"}

    def test_analyzed_offers_the_decision_and_no_start(self) -> None:
        offered = self._offered("ANALYZED")
        assert {"change_request.approve", "change_request.reject"} <= offered
        assert "change_request.start" not in offered

    def test_approved_offers_start_and_nothing_else(self) -> None:
        assert self._offered("APPROVED") == {"change_request.start"}

    def test_a_finished_request_offers_nothing(self) -> None:
        for status in ("COMPLETED", "REJECTED", "CANCELLED"):
            assert self._offered(status) == set(), status

    def test_the_refusal_names_the_state_instead_of_leaving_it_a_mystery(self) -> None:
        from factory_control.domain.actions import (
            ActionContext,
            ChangeRequestSnapshot,
            change_request_actions,
        )

        actions = change_request_actions(
            ActionContext(change_requests=[ChangeRequestSnapshot("CR-1", "DRAFT")])
        )
        refused = [a for a in actions if not a.available and a.name == "change_request.approve"][0]
        assert refused.reason_code == "STATE_NOT_ALLOWED"
        assert refused.details == {"status": "DRAFT", "wouldBecome": "APPROVED"}

    def test_an_archived_project_refuses_every_change_request_step(self) -> None:
        from factory_control.domain.actions import (
            ActionContext,
            ChangeRequestSnapshot,
            change_request_actions,
        )

        actions = change_request_actions(
            ActionContext(archived=True, change_requests=[ChangeRequestSnapshot("CR-1", "ANALYZED")])
        )
        assert actions and all(not a.available for a in actions)
        assert all(a.reason_code == "PROJECT_ARCHIVED" for a in actions)

    def test_each_request_carries_its_own_target(self) -> None:
        from factory_control.domain.actions import (
            ActionContext,
            ChangeRequestSnapshot,
            change_request_actions,
        )

        actions = change_request_actions(
            ActionContext(
                change_requests=[
                    ChangeRequestSnapshot("CR-1", "DRAFT"),
                    ChangeRequestSnapshot("CR-2", "APPROVED"),
                ]
            )
        )
        starts = [a for a in actions if a.name == "change_request.start" and a.available]
        assert [a.target_id for a in starts] == ["CR-2"], (
            "two requests in one project must not share one button's answer"
        )
