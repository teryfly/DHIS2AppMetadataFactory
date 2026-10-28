"""Failure and recovery paths: what the requirements make mandatory but the
happy-path tests never touch (CP-WF-030, CP-GATE-008/012/013a, CP-WF-008/012,
CP-PRJ-001/002, CP-Q-005).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from factory_control.ports.dto import (
    AgentRunResult,
    Dhis2ValidationOutcome,
    ExecutionOutcome,
    Issue,
    QuestionDraft,
)
from helpers import (
    CONFIRMATIONS,
    OPERATOR,
    approve_waiting_gate,
    create_run,
    gate_items,
    make_client,
    run_stage,
)

pytestmark = pytest.mark.service

BACKUP = [{"id": "OPERATIONAL_BACKUP_CONFIRMED", "confirmed": True}]


def gate_of(client, workflow_id: str, gate_type: str) -> dict:
    gates = client.get(f"/api/v1/workflows/{workflow_id}/gates").json()["items"]
    found = [g for g in gates if g["status"] == "WAITING" and g["type"] == gate_type]
    assert found, gates
    return found[0]


# --- agent outcomes (CP-WF-028/030) ---------------------------------------


def test_agent_failure_marks_stage_failed_and_notifies(harness, seeded) -> None:
    client = make_client(harness)
    harness.intelligence.queue_run(
        AgentRunResult(
            status="FAILED",
            issues=[Issue(issue_id="i9", severity="BLOCKING", source_stage="REQUIREMENTS", description="bad")],
        )
    )
    outcome = run_stage(client, harness, seeded["currentStageId"])
    assert outcome["status"] == "FAILED"
    stage = client.get(f"/api/v1/stages/{seeded['currentStageId']}").json()
    assert stage["failureSourceStage"] == "REQUIREMENTS"  # CP-WF-016
    notes = client.get(f"/api/v1/notifications?projectId={seeded['id']}").json()["items"]
    assert any(n["type"] == "STAGE_FAILED" for n in notes)


def test_policy_denial_and_budget_exhaustion_block_the_stage(harness, seeded) -> None:
    """CP-WF-030: blocked, with what was refused shown; the manual path stays open."""
    client = make_client(harness)
    harness.intelligence.queue_run(
        AgentRunResult(
            status="POLICY_DENIED",
            rejected_artifacts=[{"artifactId": "art-x", "reason": "PHI_PII"}],
            denial_reasons=["no allowed model for PHI_PII"],
        )
    )
    blocked = run_stage(client, harness, seeded["currentStageId"])
    assert blocked["status"] == "BLOCKED"
    stage = client.get(f"/api/v1/stages/{seeded['currentStageId']}").json()
    assert stage["feedback"]["kind"] == "POLICY_DENIED"
    assert stage["feedback"]["denialReasons"] == ["no allowed model for PHI_PII"]

    # A blocked stage can still be worked by hand: patch then submit.
    patched = client.post(
        f"/api/v1/stages/{seeded['currentStageId']}/patches",
        json={"patch": {"op": "add", "path": "/domain/x", "value": 1}},
        headers=OPERATOR,
    )
    assert patched.status_code in (200, 409)

    harness.intelligence.queue_run(
        AgentRunResult(status="BUDGET_EXCEEDED", budget_status={"used": 100, "limit": 50})
    )
    second = run_stage(client, harness, seeded["currentStageId"], rerun=True)
    assert second["status"] == "BLOCKED"
    stage = client.get(f"/api/v1/stages/{seeded['currentStageId']}").json()
    assert stage["feedback"]["kind"] == "BUDGET_EXCEEDED"
    notes = client.get(f"/api/v1/notifications?projectId={seeded['id']}").json()["items"]
    assert any(n["type"] == "BUDGET_EXCEEDED" for n in notes)


def test_waiting_input_then_answer_resumes_the_run(harness, seeded) -> None:
    """CP-Q-005: once the blocking questions are answered the run continues."""
    client = make_client(harness)
    harness.intelligence.queue_run(
        AgentRunResult(
            run_id="agn-wait",
            status="WAITING_INPUT",
            questions=[
                QuestionDraft(
                    local_id="q1",
                    text="which year?",
                    severity="HIGH",
                    suggestions=[{"id": "s1", "text": "2026", "changesUid": False}],
                    blocking=True,
                )
            ],
        )
    )
    outcome = run_stage(client, harness, seeded["currentStageId"])
    assert outcome["status"] == "WAITING_INPUT"
    questions = client.get(f"/api/v1/workflows/{seeded['workflowId']}/questions").json()["items"]
    assert len(questions) == 1 and questions[0]["severity"] == "HIGH"

    bad = client.post(
        f"/api/v1/questions/{questions[0]['id']}/answer",
        json={"suggestionId": "nope"},
        headers=OPERATOR,
    )
    assert bad.status_code == 409  # unknown suggestion id

    harness.intelligence.queue_run(AgentRunResult(run_id="agn-wait", status="COMPLETED"))
    answered = client.post(
        f"/api/v1/questions/{questions[0]['id']}/answer",
        json={"suggestionId": "s1"},
        headers=OPERATOR,
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["resumed"] is True
    assert any(call.startswith("resume_agent_run:") for call in harness.intelligence.calls)


def test_a_boolean_regenerates_does_not_500_the_stage_read(harness, seeded) -> None:
    """S3 emits ``regenerates`` as a boolean; the S1 contract is ``string[]``.

    A persisted ``true`` used to make ``GET /stages/{id}`` raise ``TypeError`` in the
    suggestion view, so the operator saw an internal error and never the real stage state.
    The view must coerce every stored shape to a list instead of crashing.
    """
    client = make_client(harness)
    harness.intelligence.queue_run(
        AgentRunResult(
            run_id="agn-regen",
            status="WAITING_INPUT",
            questions=[
                QuestionDraft(
                    local_id="q1",
                    text="which shape?",
                    severity="HIGH",
                    suggestions=[
                        {"id": "s-bool", "text": "rebuild", "regenerates": True},
                        {"id": "s-list", "text": "named", "regenerates": ["dataElement:age"]},
                        {"id": "s-none", "text": "plain"},
                    ],
                    blocking=True,
                )
            ],
        )
    )
    assert run_stage(client, harness, seeded["currentStageId"])["status"] == "WAITING_INPUT"

    detail = client.get(f"/api/v1/stages/{seeded['currentStageId']}")
    assert detail.status_code == 200, detail.text
    by_id = {s["id"]: s for s in detail.json()["questions"][0]["suggestions"]}
    assert by_id["s-bool"]["regenerates"] == ["*"]
    assert by_id["s-list"]["regenerates"] == ["dataElement:age"]
    assert by_id["s-none"]["regenerates"] == []


def test_a_refused_resume_fails_the_stage_instead_of_leaving_it_running(harness, seeded) -> None:
    """A provider refusal after answers must not strand the stage in RUNNING.

    S2's BP_PATCH_MALFORMED is raised by the external resume call after S1 has already
    transitioned WAITING_INPUT -> RUNNING.  The stage must end FAILED with feedback, and the
    failed attempt must be visible in the run log.
    """
    from factory_control.foundation import FactoryError

    client = make_client(harness)
    harness.intelligence.queue_run(
        AgentRunResult(
            run_id="agn-refused-resume",
            status="WAITING_INPUT",
            questions=[
                QuestionDraft(
                    local_id="q1",
                    text="which option?",
                    severity="HIGH",
                    suggestions=[{"id": "s1", "text": "reuse the object"}],
                    blocking=True,
                )
            ],
        )
    )
    assert run_stage(client, harness, seeded["currentStageId"])["status"] == "WAITING_INPUT"
    question = client.get(f"/api/v1/workflows/{seeded['workflowId']}/questions").json()["items"][0]

    harness.intelligence.resume_error = FactoryError(
        "S2 refused the resumed patch", code="SCHEMA_ERROR",
        details={"providerErrorCode": "BP_PATCH_MALFORMED"},
    )
    answered = client.post(
        "/api/v1/questions/answers",
        json={"answers": [{"questionId": question["id"], "suggestionIds": ["s1"]}]},
        headers=OPERATOR,
    )
    assert answered.status_code >= 400, answered.text

    stage = client.get(f"/api/v1/stages/{seeded['currentStageId']}", headers=OPERATOR).json()
    assert stage["status"] == "FAILED"
    assert stage["feedback"]["kind"] == "EXECUTION_FAILED"
    assert "S2 refused the resumed patch" in stage["feedback"]["reason"]
    failed_attempt = next(run for run in stage["stageRuns"] if run["attempt"] == stage["attempt"])
    assert failed_attempt["result"]["status"] == "FAILED"


def test_a_questionnaire_is_answered_once_and_the_next_round_is_returned(harness, seeded) -> None:
    """CP-Q-005: one submit answers every question, resumes once, and returns the new round."""
    client = make_client(harness)
    harness.intelligence.queue_run(
        AgentRunResult(
            run_id="agn-rounds",
            status="WAITING_INPUT",
            questions=[
                QuestionDraft(
                    local_id="q-single",
                    text="which option set?",
                    severity="HIGH",
                    answer_mode="SINGLE",
                    suggestions=[
                        {"id": "s1", "text": "reuse the existing set", "recommended": True},
                        {"id": "s2", "text": "create a new set"},
                    ],
                    blocking=True,
                ),
                QuestionDraft(
                    local_id="q-multi",
                    text="which facilities share it?",
                    severity="HIGH",
                    answer_mode="MULTIPLE",
                    suggestions=[
                        {"id": "f1", "text": "Kenyatta"},
                        {"id": "f2", "text": "Mbagathi"},
                    ],
                    blocking=True,
                ),
            ],
        )
    )
    assert run_stage(client, harness, seeded["currentStageId"])["status"] == "WAITING_INPUT"
    items = client.get(f"/api/v1/workflows/{seeded['workflowId']}/questions").json()["items"]
    single = next(item for item in items if item["text"].startswith("which option set"))
    multi = next(item for item in items if item["text"].startswith("which facilities"))
    assert single["answerMode"] == "SINGLE" and multi["answerMode"] == "MULTIPLE"
    assert single["suggestions"][0]["recommended"] is True

    # A single-choice question refuses two options; the batch is transactional.
    refused = client.post(
        "/api/v1/questions/answers",
        json={"answers": [{"questionId": single["id"], "suggestionIds": ["s1", "s2"]}]},
        headers=OPERATOR,
    )
    assert refused.status_code == 409, refused.text
    still_open = client.get(f"/api/v1/workflows/{seeded['workflowId']}/questions").json()["items"]
    assert {item["status"] for item in still_open} == {"OPEN"}

    harness.intelligence.queue_run(
        AgentRunResult(
            run_id="agn-rounds",
            status="WAITING_INPUT",
            questions=[
                QuestionDraft(
                    local_id="q-next",
                    text="the follow-up from the new answer",
                    severity="HIGH",
                    suggestions=[{"id": "s9", "text": "accept", "recommended": True}],
                    blocking=True,
                )
            ],
        )
    )
    sent = client.post(
        "/api/v1/questions/answers",
        json={
            "answers": [
                {"questionId": single["id"], "suggestionIds": ["s1"]},
                {"questionId": multi["id"], "suggestionIds": ["f1", "f2"]},
            ]
        },
        headers=OPERATOR,
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["resumed"] is True and body["status"] == "WAITING_INPUT"
    assert [item["text"] for item in body["questions"]] == ["the follow-up from the new answer"]
    assert body["questions"][0]["source"]["localId"] == "q-next"
    assert harness.intelligence.calls.count("resume_agent_run:agn-rounds") == 1

    detail = client.get(f"/api/v1/questions/{multi['id']}").json()
    assert detail["answer"]["suggestionIds"] == ["f1", "f2"]
    assert "Kenyatta" in detail["answer"]["text"] and "Mbagathi" in detail["answer"]["text"]


def test_rerun_supersedes_open_questions_and_invalidates_gates(harness, seeded) -> None:
    """CP-WF-012."""
    client = make_client(harness)
    harness.intelligence.queue_run(
        AgentRunResult(
            status="COMPLETED",
            questions=[QuestionDraft(local_id="q9", text="open one?", severity="LOW", blocking=False)],
        )
    )
    run_stage(client, harness, seeded["currentStageId"])
    before = client.get(f"/api/v1/workflows/{seeded['workflowId']}/questions").json()["items"]
    gate = gate_of(client, seeded["workflowId"], "REQUIREMENT_GATE")

    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    rerun = run_stage(client, harness, seeded["currentStageId"], rerun=True)
    assert rerun["status"] in ("WAITING_APPROVAL", "BLOCKED", "READY")

    after = client.get(f"/api/v1/workflows/{seeded['workflowId']}/questions").json()["items"]
    superseded = [q for q in after if q["id"] == before[0]["id"] and q["status"] == "SUPERSEDED"]
    assert superseded
    gates = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"]
    invalidated = [g for g in gates if g["status"] == "INVALIDATED"]
    assert [g["id"] for g in invalidated] == [gate["id"]]  # CP-WF-012: the waiting Gate dies


# --- Gate rules -------------------------------------------------------------


def test_warning_disposition_rules_on_approval(harness, seeded) -> None:
    """CP-GATE-008 / CP-BR-07: every WARNING marked, and MUST_FIX forces rejection."""
    client = make_client(harness)
    harness.blueprint.freeze_issues = [
        Issue(issue_id="w1", severity="WARNING", description="naming", source_stage="REQUIREMENTS"),
        Issue(issue_id="w2", severity="WARNING", description="dup", source_stage="REQUIREMENTS"),
    ]
    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    run_stage(client, harness, seeded["currentStageId"])
    gate = gate_of(client, seeded["workflowId"], "REQUIREMENT_GATE")
    assert len(gate["warnings"]) == 2

    missing = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={"gateType": gate["type"], "seenBoundHashes": gate["boundObjects"], "warningDispositions": []},
        headers=OPERATOR,
    )
    assert missing.status_code == 409
    assert missing.json()["details"]["cause"] == "WARNING_UNRESOLVED"

    must_fix = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": [
                {"issueId": "w1", "disposition": "ACCEPTED"},
                {"issueId": "w2", "disposition": "MUST_FIX"},
            ],
        },
        headers=OPERATOR,
    )
    assert must_fix.status_code == 409
    assert "MUST_FIX" in must_fix.json()["details"]["problems"][0]

    unknown_warning = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": [
                {"issueId": "w1", "disposition": "ACCEPTED"},
                {"issueId": "w2", "disposition": "ACCEPTED"},
                {"issueId": "ghost", "disposition": "ACCEPTED"},
            ],
        },
        headers=OPERATOR,
    )
    assert unknown_warning.status_code == 409

    ok = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": [
                {"issueId": "w1", "disposition": "ACCEPTED"},
                {"issueId": "w2", "disposition": "ACCEPTED"},
            ],
        },
        headers=OPERATOR,
    )
    assert ok.status_code == 200

    harness.blueprint.freeze_issues = []


def test_rejection_without_reason_is_refused(harness, seeded) -> None:
    """CP-GATE-010 / CP-BR-08."""
    client = make_client(harness)
    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    run_stage(client, harness, seeded["currentStageId"])
    gate = gate_of(client, seeded["workflowId"], "REQUIREMENT_GATE")
    blank = client.post(f"/api/v1/gates/{gate['id']}/reject", json={"reason": "  "}, headers=OPERATOR)
    assert blank.status_code == 409
    assert client.get(f"/api/v1/gates/{gate['id']}/explanation").status_code == 200
    assert client.post("/api/v1/gates/gat-missing/approve", json={}, headers=OPERATOR).status_code == 404


def test_freeze_with_blocking_issues_leaves_no_gate(harness, seeded) -> None:
    """CP-GATE-013a: BLOCKING freeze problems put the stage in BLOCKED, not WAITING."""
    client = make_client(harness)
    harness.blueprint.freeze_issues = [Issue(issue_id="b1", severity="BLOCKING", source_stage="MAPPING")]
    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    outcome = run_stage(client, harness, seeded["currentStageId"])
    assert outcome["status"] == "BLOCKED"
    assert client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"] == []
    harness.blueprint.freeze_issues = []


# --- Run and COMMIT failures ------------------------------------------------


def test_compile_blocking_fails_the_run(harness, seeded) -> None:
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    harness.blueprint.compile_issues = [Issue(issue_id="c1", severity="BLOCKING", source_stage="MAPPING")]
    view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    bind = next(s for s in view["stages"] if s["stageType"] == "BIND_COMPILE")
    outcome = run_stage(client, harness, bind["id"])
    assert outcome["status"] == "FAILED"
    assert client.get(f"/api/v1/deployment-runs/{run_id}").json()["run"]["status"] == "FAILED"
    harness.blueprint.compile_issues = []


def test_static_validation_blocking_fails_the_run(harness, seeded) -> None:
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    bind = next(s for s in view["stages"] if s["stageType"] == "BIND_COMPILE")
    run_stage(client, harness, bind["id"])

    harness.blueprint.validation_issues = [Issue(issue_id="v1", severity="BLOCKING", source_stage="PAGE_DESIGN")]
    view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    static = next(s for s in view["stages"] if s["stageType"] == "STATIC_VALIDATION")
    outcome = run_stage(client, harness, static["id"])
    assert outcome["status"] == "FAILED"
    harness.blueprint.validation_issues = []


def test_dhis2_validate_failure_reports_source_stage(harness, seeded) -> None:
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_until(client, harness, run_id, "DHIS2_VALIDATE")
    harness.execution.validate_results.append(
        Dhis2ValidationOutcome(
            passed=False,
            issues=[Issue(issue_id="d1", severity="BLOCKING", source_stage="MAPPING", description="bad ref")],
            execution_plan_artifact_id=None,
        )
    )
    view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    r3 = next(s for s in view["stages"] if s["stageType"] == "DHIS2_VALIDATE")
    outcome = run_stage(client, harness, r3["id"])
    assert outcome["status"] == "FAILED"
    stage = client.get(f"/api/v1/stages/{r3['id']}").json()
    assert stage["failureSourceStage"] == "MAPPING"  # CP-WF-016


def drive_until(client, harness, run_id: str, stage_type: str) -> None:
    """Advance a run until the named step is READY, approving Gates on the way."""
    for _ in range(8):
        view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
        if any(s["stageType"] == stage_type and s["status"] == "READY" for s in view["stages"]):
            return
        waiting = [g for g in view["gates"] if g["status"] == "WAITING"]
        if waiting:
            approve_waiting_gate(client, harness, view["run"]["workflowId"], stage_id=waiting[0]["stageId"])
            continue
        ready = [s for s in view["stages"] if s["status"] == "READY"]
        assert ready, view
        run_stage(client, harness, ready[0]["id"])


def approve_gate(client, gate: dict) -> Any:
    """Post an approval for one Gate payload, with every WARNING dispositioned."""
    return client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": [
                {"issueId": w.get("issue_id") or w["issueId"], "disposition": "ACCEPTED"} for w in gate["warnings"]
            ],
            "confirmations": [{"id": c["id"], "confirmed": True} for c in gate["confirmationItems"]] + CONFIRMATIONS,
        },
        headers=OPERATOR,
    )


def drive_run_scoped(
    client: Any, harness: Any, run_id: str, *, iterations: int = 14, stop_at_deploy_gate: bool = False
) -> dict[str, Any]:
    """Like :func:`drive_run`, but only touches Gates bound to this Run's stages.

    Two Runs may wait on their own DEPLOY_GATE at the same time, and a workflow
    level Gate list would otherwise approve the other Run's Gate.
    """
    for _ in range(iterations):
        view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
        stage_ids = {s["id"] for s in view["stages"]}
        waiting = [g for g in view["gates"] if g["status"] == "WAITING" and g["stageId"] in stage_ids]
        if waiting:
            gate = waiting[0]
            if stop_at_deploy_gate and gate["type"] == "DEPLOY_GATE":
                return view
            approved = approve_gate(client, gate)
            assert approved.status_code == 200, approved.text
            if gate["type"] == "DEPLOY_GATE":
                accepted = client.post(
                    f"/api/v1/deployment-runs/{run_id}/commit", json={}, headers={**OPERATOR, "Idempotency-Key": f"commit-{run_id}"},
                )
                assert accepted.status_code == 202, accepted.text
            continue
        ready = [s for s in view["stages"] if s["status"] == "READY"]
        if not ready:
            return view
        run_stage(client, harness, ready[0]["id"])
    return client.get(f"/api/v1/deployment-runs/{run_id}").json()


def test_commit_failure_and_manual_check_resolution(harness, seeded) -> None:
    """CP-WF-008 / CP-WF-019: unconfirmable COMMIT lands in MANUAL_CHECK and leaves via a verdict."""
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_until(client, harness, run_id, "COMMIT")

    harness.execution.commit_results.append(
        ExecutionOutcome(execution_id="exe-mc", status="NEEDS_MANUAL_CHECK", applied=None)
    )
    accepted = client.post(f"/api/v1/deployment-runs/{run_id}/commit", json={}, headers={**OPERATOR, "Idempotency-Key": "mc-1"})
    # CP-API-012: the answer names the execution to watch, not what it decided.
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["stepRunId"] == "exe-mc", accepted.json()
    view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    assert view["run"]["status"] == "MANUAL_CHECK"
    commit_stage = next(s for s in view["stages"] if s["stageType"] == "COMMIT")

    bad_outcome = client.post(
        f"/api/v1/stages/{commit_stage['id']}/resolve-manual-check",
        json={"outcome": "MAYBE", "note": "x"},
        headers=OPERATOR,
    )
    assert bad_outcome.status_code == 409

    resolved = client.post(
        f"/api/v1/stages/{commit_stage['id']}/resolve-manual-check",
        json={"outcome": "PASSED", "note": "verified against the instance"},
        headers=OPERATOR,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "PASSED"
    assert any(call.startswith("resolve_manual_check:exe-mc") for call in harness.execution.calls)
    assert harness.execution.executions["exe-mc"].status == "COMPLETED"


def test_commit_failing_path_marks_run_failed(harness, seeded) -> None:
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_until(client, harness, run_id, "COMMIT")
    harness.execution.commit_results.append(
        ExecutionOutcome(
            execution_id="exe-fail",
            status="FAILED",
            issues=[Issue(issue_id="e1", severity="BLOCKING", source_stage="MAPPING")],
        )
    )
    accepted = client.post(
        f"/api/v1/deployment-runs/{run_id}/commit", json={}, headers={**OPERATOR, "Idempotency-Key": "fail-1"}
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["stepRunId"] == "exe-fail", accepted.json()
    assert client.get(f"/api/v1/deployment-runs/{run_id}").json()["run"]["status"] == "FAILED"
    assert client.get("/api/v1/executions/exe-fail").json()["status"] == "FAILED"


def test_drift_detected_at_commit_invalidates_gate(harness, seeded) -> None:
    """CP-GATE-017 / AC-GATE-02: nothing reaches S4 COMMIT."""
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_until(client, harness, run_id, "COMMIT")
    harness.execution.drift["dev"] = __import__("factory_control.ports.dto", fromlist=["DriftResult"]).DriftResult(
        drifted=True, objects=[{"uid": "A1"}]
    )
    before = len([c for c in harness.execution.calls if c.startswith("commit:")])
    response = client.post(
        f"/api/v1/deployment-runs/{run_id}/commit", json={}, headers={**OPERATOR, "Idempotency-Key": "drift-1"}
    )
    assert response.status_code == 409
    assert response.json()["errorCode"] == "DRIFT_DETECTED"
    assert len([c for c in harness.execution.calls if c.startswith("commit:")]) == before


def test_production_deploy_gate_waits_for_prior_verification(harness, seeded) -> None:
    """CP-WF-020 / CP-AC-07: PRODUCTION refuses approval until the version is verified.

    The condition is checked where a human could still be talked past it: at
    DEPLOY_GATE approval as well as at COMMIT (CP-GATE-012).
    """
    client = make_client(harness)
    run_id = create_run(client, harness, seeded, environment_key="prod")
    harness.execution.drift.pop("prod", None)

    view = drive_run_scoped(client, harness, run_id, stop_at_deploy_gate=True)
    gate = next(g for g in view["gates"] if g["type"] == "DEPLOY_GATE" and g["status"] == "WAITING")
    refused = approve_gate(client, gate)
    assert refused.status_code == 409, refused.text
    assert refused.json()["errorCode"] == "PRIOR_VERIFICATION_MISSING"
    assert refused.json()["details"]["requiredEvidence"], refused.json()["details"]
    assert client.get(f"/api/v1/gates/{gate['id']}").json()["status"] == "WAITING"
    assert client.get(f"/api/v1/deployment-runs/{run_id}").json()["run"]["status"] != "COMMITTED"


def test_production_commits_after_non_production_verification(harness, seeded) -> None:
    """CP-WF-020 the other way round: one VERIFIED TEST run unblocks the same version."""
    client = make_client(harness)
    dev_run_id = create_run(client, harness, seeded, environment_key="dev")
    drive_run_scoped(client, harness, dev_run_id)
    assert client.get(f"/api/v1/deployment-runs/{dev_run_id}").json()["run"]["status"] == "VERIFIED"

    harness.execution.drift.pop("prod", None)
    prod_run_id = create_run(client, harness, seeded, environment_key="prod")
    view = drive_run_scoped(client, harness, prod_run_id)
    run = client.get(f"/api/v1/deployment-runs/{prod_run_id}").json()["run"]
    assert run["status"] in {"COMMITTED", "VERIFIED"}, view


def test_commit_window_and_lock_are_enforced_at_request_time(harness, seeded) -> None:
    """CP-GATE-012: the checks are re-read when COMMIT is asked for."""
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_until(client, harness, run_id, "COMMIT")

    harness.execution.protections["dev"] = harness.execution.protections["dev"].model_copy(
        update={
            "within_commit_window": False,
            "commit_windows": [{"days": [1, 2, 3, 4, 5], "start": "01:00", "end": "05:00"}],
        }
    )
    window = client.post(
        f"/api/v1/deployment-runs/{run_id}/commit", json={}, headers={**OPERATOR, "Idempotency-Key": "win-1"}
    )
    assert window.status_code == 409 and window.json()["errorCode"] == "OUTSIDE_COMMIT_WINDOW"
    # SRS 8.3: the window dialog reads the refusal's own details.
    window_details = window.json()["details"]
    assert window_details["environmentKey"] == "dev" and window_details["environmentTimezone"]
    assert window_details["windows"] and window_details["currentTime"]

    harness.execution.protections["dev"] = harness.execution.protections["dev"].model_copy(
        update={"within_commit_window": True, "emergency_locked": True}
    )
    locked = client.post(
        f"/api/v1/deployment-runs/{run_id}/commit", json={}, headers={**OPERATOR, "Idempotency-Key": "lock-1"}
    )
    assert locked.status_code == 409 and locked.json()["errorCode"] == "ENVIRONMENT_LOCKED"
    assert {"environmentKey", "lockReason", "lockChangedAt"} <= set(locked.json()["details"])
    harness.execution.protections["dev"] = harness.execution.protections["dev"].model_copy(
        update={"emergency_locked": False}
    )


def test_cancel_run_while_committing_is_refused(harness, seeded) -> None:
    """CP-WF-011: a COMMIT already under way finishes before the Run can be cancelled."""
    from factory_control import store as S
    from factory_control.domain.enums import StageStatus, StageType
    from factory_control.foundation import unit_of_work

    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_until(client, harness, run_id, "COMMIT")

    with unit_of_work() as session:
        run = S.get_run(session, run_id)
        commit_stage = S.stage_by_type(session, run.workflow_id, StageType.COMMIT, run_id=run.id)
        commit_stage.status = StageStatus.RUNNING.value
        session.flush()

    refused = client.post(f"/api/v1/deployment-runs/{run_id}/cancel", headers=OPERATOR)
    assert refused.status_code == 409
    assert refused.json()["errorCode"] == "INVALID_STATE"
    assert client.get(f"/api/v1/deployment-runs/{run_id}").json()["run"]["status"] != "CANCELLED"

    with unit_of_work() as session:
        run = S.get_run(session, run_id)
        commit_stage = S.stage_by_type(session, run.workflow_id, StageType.COMMIT, run_id=run.id)
        commit_stage.status = StageStatus.READY.value
        session.flush()

    cancelled = client.post(f"/api/v1/deployment-runs/{run_id}/cancel", headers=OPERATOR)
    assert cancelled.status_code == 200, cancelled.text
    view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    assert view["run"]["status"] == "CANCELLED"
    assert not [s for s in view["stages"] if s["status"] in ("RUNNING", "WAITING_INPUT", "WAITING_APPROVAL")]
    assert all(g["status"] != "WAITING" for g in view["gates"])


def test_cancel_run_invalidates_the_work_under_way(harness, seeded) -> None:
    """CP-WF-015 rule 2: a stage awaiting approval and its Gate are invalidated."""
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_run_scoped(client, harness, run_id, stop_at_deploy_gate=True)
    before = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    assert [g for g in before["gates"] if g["status"] == "WAITING"]
    assert [s for s in before["stages"] if s["status"] == "WAITING_APPROVAL"]

    cancelled = client.post(f"/api/v1/deployment-runs/{run_id}/cancel", headers=OPERATOR)
    assert cancelled.status_code == 200, cancelled.text
    after = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    assert after["run"]["status"] == "CANCELLED"
    assert not [s for s in after["stages"] if s["status"] in ("RUNNING", "WAITING_INPUT", "WAITING_APPROVAL")]
    assert not [g for g in after["gates"] if g["status"] == "WAITING"]
    assert [s for s in after["stages"] if s["status"] == "INVALIDATED"]
    assert [g for g in after["gates"] if g["status"] == "INVALIDATED"]


# --- project validation -----------------------------------------------------


def test_a_refused_stage_run_never_stays_running(harness, seeded) -> None:
    """CP-WF-011/016: COMMIT refuses a plain stage run; the stage lands on FAILED.

    A stage left RUNNING would hide the reason, block rerun and make the Run
    uncancellable, so the refusal has to be recorded as a failure.
    """
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_until(client, harness, run_id, "COMMIT")
    view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    commit_stage = next(s for s in view["stages"] if s["stageType"] == "COMMIT")

    accepted = client.post(f"/api/v1/stages/{commit_stage['id']}/run", json={}, headers=OPERATOR)
    assert accepted.status_code == 202, accepted.text
    outcome = harness.runtime.engine.wait(commit_stage["id"], timeout=20)
    assert outcome and "error" in outcome

    after = client.get(f"/api/v1/stages/{commit_stage['id']}").json()
    assert after["status"] == "FAILED"
    actions = client.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions").json()
    retryable = [a for a in actions["allowed"] if a.get("targetId") == commit_stage["id"]]
    assert {a["action"] for a in retryable} == {"stage.rerun"}, retryable
    notes = client.get(f"/api/v1/notifications?projectId={seeded['id']}").json()["items"]
    assert any(n["type"] == "STAGE_FAILED" for n in notes)

    cancelled = client.post(f"/api/v1/deployment-runs/{run_id}/cancel", headers=OPERATOR)
    assert cancelled.status_code == 200, cancelled.text


def test_project_creation_validations(harness) -> None:
    """CP-PRJ-001 / CP-PRJ-002."""
    client = make_client(harness)
    harness.execution.add_environment("dev", environment_class="PRODUCTION")
    harness.execution.add_environment("sit", environment_class="TEST")

    no_targets = client.post(
        "/api/v1/projects", json={"name": "p", "ownerName": "ada", "targetEnvironments": []}, headers=OPERATOR
    )
    assert no_targets.status_code == 422  # missing design reference
    assert "target environment" in no_targets.json()["details"]["fields"][0]["message"].lower()

    two_references = client.post(
        "/api/v1/projects",
        json={
            "name": "p", "ownerName": "ada",
            "targetEnvironments": [
                {"environmentKey": "dev", "isDesignReference": True},
                {"environmentKey": "sit", "isDesignReference": True},
            ],
        },
        headers=OPERATOR,
    )
    assert two_references.status_code == 422

    all_production = client.post(
        "/api/v1/projects",
        json={
            "name": "p", "ownerName": "ada",
            "targetEnvironments": [{"environmentKey": "dev", "isDesignReference": True}],
        },
        headers=OPERATOR,
    )
    assert all_production.status_code == 409  # CP-PRJ-002: needs a non-PRODUCTION target
    # ``classes`` maps each key to its class, so the refusal is that the map holds only
    # PRODUCTION — not that the word "non-PRODUCTION" appears anywhere in it
    assert all_production.json()["details"]["classes"] == {"dev": "PRODUCTION"}

    unknown = client.post(
        "/api/v1/projects",
        json={
            "name": "p", "ownerName": "ada",
            "targetEnvironments": [{"environmentKey": "ghost", "isDesignReference": True}],
        },
        headers=OPERATOR,
    )
    assert unknown.status_code == 409 and "ghost" in unknown.json()["details"]["environmentKey"]

    bad_owner = client.post(
        "/api/v1/projects",
        json={"name": "p", "ownerName": "", "targetEnvironments": [{"environmentKey": "sit"}]},
        headers=OPERATOR,
    )
    assert bad_owner.status_code == 422


def test_project_patch_rejects_non_editable_fields(harness, seeded) -> None:
    """D-15: delivery mode and Blueprint semantics are not editable here."""
    client = make_client(harness)
    rejected = client.patch(
        f"/api/v1/projects/{seeded['id']}", json={"deliveryMode": "B"}, headers=OPERATOR
    )
    assert rejected.status_code == 409
    assert "deliveryMode" in rejected.json()["details"]["fields"]

    edited = client.patch(
        f"/api/v1/projects/{seeded['id']}",
        json={"description": "clinic for two districts", "ownerName": "bob"},
        headers=OPERATOR,
    )
    assert edited.status_code == 200
    body = edited.json()
    assert body["ownerName"] == "bob" and body["description"].startswith("clinic")
    assert client.get("/api/v1/projects/missing").status_code == 404


def test_listing_filters_and_pages(harness, seeded) -> None:
    client = make_client(harness)
    client.post(
        "/api/v1/projects",
        json={
            "name": "second project", "ownerName": "bob",
            "targetEnvironments": [{"environmentKey": "dev", "isDesignReference": True}],
        },
        headers=OPERATOR,
    )
    everything = client.get("/api/v1/projects").json()
    assert everything["total"] == 2
    by_name = client.get("/api/v1/projects", params={"name": "second"}).json()
    assert by_name["total"] == 1
    by_owner = client.get("/api/v1/projects", params={"ownerName": "ada"}).json()
    assert all(i["ownerName"] == "ada" for i in by_owner["items"])
    paged = client.get("/api/v1/projects", params={"limit": 1, "offset": 1}).json()
    assert len(paged["items"]) == 1 and paged["total"] == 2
    assert client.get("/api/v1/projects", params={"includeArchived": False}).status_code == 200


def test_input_parse_feeds_questions_into_the_stage(harness, seeded) -> None:
    """SRS 6.1 step 2."""
    from factory_control.foundation import DataClassification
    from factory_control.services.inputs import parse_input, record_input

    uploaded = record_input(
        harness.runtime,
        seeded["id"],
        filename="brief.md",
        media_type="text/markdown",
        data=b"# requirements\n- monthly summary\n",
        operator="ada",
        data_classification=DataClassification.INTERNAL,
    )
    started = parse_input(harness.runtime, uploaded["artifactId"], operator="ada")
    assert started["runId"]
    from factory_control.foundation import unit_of_work
    from factory_control.models import InputParseRun

    with unit_of_work() as session:
        run = session.get(InputParseRun, started["runId"])
        assert run is not None and run.status == "PARSED"
        assert run.nrr_artifact_id
        first_nrr = run.nrr_artifact_id
    again = parse_input(harness.runtime, uploaded["artifactId"], operator="ada")
    with unit_of_work() as session:
        second = session.get(InputParseRun, again["runId"])
        assert second is not None and second.nrr_artifact_id == first_nrr  # idempotency by input


    from factory_control.foundation import FactoryError

    with pytest.raises(FactoryError):
        parse_input(harness.runtime, "art-not-linked", operator="ada", stage_id="sta-none")


def test_notification_delivery_paths_without_transport(harness, seeded) -> None:
    """CP-NTF-002: unsupported or unconfigured channels are recorded, never raised."""
    from factory_control.services.notifications import (
        NotificationType,
        _redact_target,
        notify,
        queue_channels,
    )

    assert _redact_target("https://u:p@example.org/hook") == "https://***@example.org/hook"
    assert _redact_target("team@example.org") == "team@example.org"

    with harness.runtime.transaction(operator="ada", project_id=seeded["id"]) as ctx:
        note = notify(
            ctx,
            notification_type=NotificationType.EXECUTION_FAILED,
            object_ref={"executionId": None, "workflowId": seeded["workflowId"]},
            message="failed",
            project_id=seeded["id"],
        )
        ctx.session.add(note)
        ctx.session.flush()
        rows = queue_channels(ctx, note)
        assert rows == []  # no channels configured for this project

    from factory_control.services.notifications import deliver_pending

    assert deliver_pending(harness.runtime)["delivered"] == 0


def test_a_commit_s4_already_recorded_is_adopted_not_resubmitted(harness: Any, seeded: dict[str, Any]) -> None:
    """CP-GATE-018 at the service layer, where S1 has no stored response to replay.

    Over REST the second identical request never reaches this code — it is answered from
    S1's stored response (S5IF-G08).  What the key still has to survive is S1 having lost
    its own record: a crash between S4 applying the COMMIT and S1 writing the outcome
    down (AC-EXE-05).
    """
    from factory_control.services import runs as RUNS

    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    drive_until(client, harness, run_id, "COMMIT")

    key = "commit-adopt"
    harness.execution.commit_results.append(
        ExecutionOutcome(execution_id="exe-orph", status="NEEDS_MANUAL_CHECK", applied=None)
    )
    first = RUNS.commit_run(harness.runtime, run_id, operator="ada", idempotency_key=key)
    assert first["needsManualCheck"] is True

    # S4 went on to finish the import after S1 stopped listening.
    harness.execution.by_key[key] = ExecutionOutcome(
        execution_id="exe-orph", status="COMPLETED", applied=True
    )
    calls_before = len([c for c in harness.execution.calls if c.startswith("commit:")])
    second = RUNS.commit_run(harness.runtime, run_id, operator="ada", idempotency_key=key)

    assert second["recovered"] is True, second
    assert second["status"] == "COMMITTED", second
    assert len([c for c in harness.execution.calls if c.startswith("commit:")]) == calls_before


def test_submitting_from_ready_with_a_blocking_freeze_reports_the_issues(harness, seeded) -> None:
    """Appendix A: BLOCKED is reachable from RUNNING only, so a manual submission must claim first.

    The hand-authored path submits straight from READY. Going READY -> BLOCKED raised
    409 INVALID_STATE {"from":"READY","to":"BLOCKED"}, so an operator who did nothing wrong but
    left a draft unpromotable got a state-machine complaint instead of the list of blocking
    issues — the only thing that branch exists to tell them. FakeBlueprint's freeze is promotable
    by default, which is why nothing covered it until the real-S2 seed hit it.
    """
    client = make_client(harness)
    harness.blueprint.freeze_issues = [
        Issue(
            issue_id="bpv-dec-1",
            severity="BLOCKING",
            description="registry entry 'de-weight' has no mapping decision",
            source_stage="MAPPING",
        )
    ]

    response = client.post(
        f"/api/v1/stages/{seeded['currentStageId']}/submit-for-approval",
        headers=OPERATOR,
        json={},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "BLOCKED", body
    assert body["blocking"] == 1, body

    stage = client.get(f"/api/v1/stages/{seeded['currentStageId']}", headers=OPERATOR).json()
    assert stage["status"] == "BLOCKED", stage
    feedback = json.dumps(stage.get("feedback") or {}, ensure_ascii=False)
    assert "no mapping decision" in feedback, stage.get("feedback")

    # Nothing was opened: an unpromotable draft must not put a Gate up for approval.
    assert gate_items(client, seeded["workflowId"]) == []

    # BLOCKED -> READY is legal (BLOCKED -> RUNNING is not), so fixing the content and re-running
    # the stage is not blocked by the state — this asserts the recovery route exists, by rerun.
    harness.blueprint.freeze_issues = []
    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    rerun = client.post(
        f"/api/v1/stages/{seeded['currentStageId']}/rerun", headers=OPERATOR, json={}
    )
    assert rerun.status_code == 202, rerun.text
    outcome = harness.runtime.engine.wait(seeded["currentStageId"], timeout=20)
    assert outcome is not None and "error" not in outcome, outcome


def test_a_freeze_blocked_stage_recovers_by_fixing_the_draft_and_resubmitting(harness, seeded) -> None:
    """BLOCKED by a freeze is a content verdict: the recovery edits the draft, not the dice.

    The old model offered only rerun, but the agent re-applies the same naming policy and
    reproduces the very conflict S2's freeze reported (BPV-ENV-002 style), so the stage
    re-blocked with nothing else clickable. A blocked design stage must now offer
    patch.submit and stage.submit_for_approval, and the resubmit must run the freeze again
    and open the Gate once the draft is clean.
    """
    client = make_client(harness)
    stage_id = seeded["currentStageId"]
    harness.blueprint.freeze_issues = [
        Issue(
            issue_id="bpv-env-1",
            severity="BLOCKING",
            rule_id="BPV-ENV-002",
            description="code GEN_X is already used by a dataStore in environment sbtest",
            source_stage="MAPPING",
        )
    ]
    blocked = client.post(
        f"/api/v1/stages/{stage_id}/submit-for-approval", headers=OPERATOR, json={}
    )
    assert blocked.status_code == 200 and blocked.json()["status"] == "BLOCKED", blocked.text

    # The rejection says what is wrong in human words; the machine code stays in the event.
    stage = client.get(f"/api/v1/stages/{stage_id}", headers=OPERATOR).json()
    reason = (stage.get("feedback") or {}).get("reason") or ""
    assert "blocking issue" in reason.lower() and reason != "FREEZE_BLOCKING", stage["feedback"]

    # The recovery controls are on the toolbar while the stage is BLOCKED.
    actions = client.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions", headers=OPERATOR).json()
    on_stage = {a["action"] for a in actions["allowed"] if a.get("targetId") == stage_id}
    assert {"stage.submit_for_approval", "patch.submit"} <= on_stage, actions["allowed"]

    # The operator fixes the conflict (here: the freeze starts passing) and resubmits —
    # no agent rerun, so the same generated code cannot loop the stage back to BLOCKED.
    harness.blueprint.freeze_issues = []
    resubmitted = client.post(
        f"/api/v1/stages/{stage_id}/submit-for-approval", headers=OPERATOR, json={}
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["status"] == "WAITING_APPROVAL", resubmitted.json()
    assert gate_items(client, seeded["workflowId"]), "the resubmitted draft must open its Gate"
