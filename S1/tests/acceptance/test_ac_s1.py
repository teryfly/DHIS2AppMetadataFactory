"""Acceptance tests over the REST surface, one per SRS section-11 row that the
MVP scope covers (AC-WF-01, AC-GATE-01/02/03, AC-AUD-01, CP-AC-01…09, CP-AC-31,
CP-AC-32, AC-WF-03).
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.acceptance

OPERATOR = {"X-Operator": "ada"}


def headers(**extra: str) -> dict[str, str]:
    return {"X-Operator": "ada", "Content-Type": "application/json", **extra}


def create_project(client, harness, *, delivery_mode: str = "A") -> dict:
    from factory_control.testing.harness import seed_project

    return seed_project(harness, delivery_mode=delivery_mode)


def test_new_project_returns_all_stages_with_mode_a_skips(client, harness) -> None:
    """AC-WF-01."""
    view = create_project(client, harness)
    assert view["currentStageId"]
    statuses = {s["stageType"]: s["status"] for s in view["stages"]}
    assert statuses["REQUIREMENTS"] == "READY"
    assert statuses["PAGE_DESIGN"] == "SKIPPED"
    assert statuses["HANDOFF"] == "SKIPPED"
    assert statuses["DOMAIN"] == "LOCKED"
    got = client.get(f"/api/v1/projects/{view['id']}").json()
    assert got["workflowId"] == view["workflowId"]


def test_state_change_without_operator_is_400(client) -> None:
    """CP-AC-01: 400 OPERATOR_REQUIRED."""
    response = client.post("/api/v1/projects", json={"name": "x", "ownerName": "y", "versionConstraints": "2.41.x", "targetEnvironments": []})
    assert response.status_code == 400
    assert response.json()["errorCode"] == "OPERATOR_REQUIRED"


def test_same_idempotency_key_different_body_is_422(client, harness) -> None:
    """CP-AC-02 / CP-API-003."""
    harness.execution.add_environment("dev", environment_class="TEST")
    key = {"Idempotency-Key": "k-1"}
    first = client.post(
        "/api/v1/projects",
        json={"name": "one", "ownerName": "ada", "versionConstraints": "2.41.x", "targetEnvironments": [
            {"environmentKey": "dev", "isDesignReference": True}]},
        headers=headers(**key),
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/projects",
        json={"name": "two", "ownerName": "ada", "versionConstraints": "2.41.x", "targetEnvironments": [
            {"environmentKey": "dev", "isDesignReference": True}]},
        headers=headers(**key),
    )
    assert second.status_code == 422
    assert second.json()["errorCode"] == "IDEMPOTENCY_CONFLICT"


def test_same_key_same_body_replays(client, harness) -> None:
    key = {"Idempotency-Key": "k-2"}
    body = {
        "name": "replay",
        "ownerName": "ada",
        "targetEnvironments": [{"environmentKey": "dev", "isDesignReference": True}],
    }
    harness.execution.add_environment("dev", environment_class="TEST")
    first = client.post("/api/v1/projects", json=body, headers=headers(**key))
    second = client.post("/api/v1/projects", json=body, headers=headers(**key))
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers.get("x-idempotent-replay") == "true"
    assert second.json()["id"] == first.json()["id"]


def test_agent_stage_completes_then_waits_for_approval(client, harness, seeded) -> None:
    """CP-WF-028: an agent run that COMPLETED ends in WAITING_APPROVAL with a Gate."""
    harness.intelligence.queue_run(
        __import__("factory_control.ports.dto", fromlist=["AgentRunResult"]).AgentRunResult(
            status="COMPLETED", summary="ok", agent_output_artifact_id="art-1"
        )
    )
    stage_id = seeded["currentStageId"]
    started = run_stage(client, harness, stage_id)
    assert started["status"] == "WAITING_APPROVAL", started
    gates = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"]
    assert gates and gates[0]["type"] == "REQUIREMENT_GATE"
    assert gates[0]["status"] == "WAITING"


def test_approval_with_stale_hashes_is_409(client, harness, seeded) -> None:
    """CP-AC-04 / CP-GATE-009."""
    from factory_control.ports.dto import AgentRunResult

    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    stage_id = seeded["currentStageId"]
    run_stage(client, harness, stage_id)
    gate = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"][0]
    stale = [{"refId": r["refId"], "contentHash": "sha256:" + "0" * 64} for r in gate["boundObjects"]]
    response = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={"gateType": gate["type"], "seenBoundHashes": stale, "warningDispositions": []},
        headers=OPERATOR,
    )
    assert response.status_code == 409
    assert response.json()["errorCode"] == "APPROVAL_INVALIDATED"


def test_approve_promotes_version_and_advances_line(client, harness, seeded) -> None:
    """CP-GATE-014: approval, promotion, PASSED and the next stage in one unit of work."""
    from factory_control.ports.dto import AgentRunResult

    harness.blueprint.next_version[seeded["id"]] = ["1.0.0"]
    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    stage_id = seeded["currentStageId"]
    run_stage(client, harness, stage_id)
    gate = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"][0]
    approved = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={"gateType": gate["type"], "seenBoundHashes": gate["boundObjects"], "warningDispositions": []},
        headers=OPERATOR,
    )
    body = approved.json()
    assert approved.status_code == 200, body
    assert body["status"] == "APPROVED"
    assert body["blueprintVersion"] == "1.0.0"
    workflow = client.get(f"/api/v1/workflows/{seeded['workflowId']}").json()
    statuses = {s["stageType"]: s["status"] for s in workflow["stages"]}
    assert statuses["REQUIREMENTS"] == "PASSED"
    assert statuses["DOMAIN"] == "READY"
    project = client.get(f"/api/v1/projects/{seeded['id']}").json()
    assert project["currentBlueprintVersion"] == "1.0.0"


def test_draft_change_invalidates_waiting_gate(client, harness, seeded) -> None:
    """AC-GATE-01."""
    from factory_control.ports.dto import AgentRunResult

    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    stage_id = seeded["currentStageId"]
    run_stage(client, harness, stage_id)
    gate = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"][0]
    patched = client.post(
        f"/api/v1/stages/{stage_id}/patches",
        json={"patch": {"op": "replace", "path": "/project/name", "value": "changed"}},
        headers=OPERATOR,
    )
    assert patched.status_code == 200, patched.text
    after = client.get(f"/api/v1/gates/{gate['id']}").json()
    assert after["status"] == "INVALIDATED"
    assert after["invalidatedReason"] == "DRAFT_HASH_CHANGED"


def test_rejection_returns_stage_to_ready_with_feedback(client, harness, seeded) -> None:
    """AC-GATE-03."""
    from factory_control.ports.dto import AgentRunResult

    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    stage_id = seeded["currentStageId"]
    run_stage(client, harness, stage_id)
    gate = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"][0]
    rejected = client.post(
        f"/api/v1/gates/{gate['id']}/reject", json={"reason": "mapping incomplete"}, headers=OPERATOR
    )
    assert rejected.status_code == 200
    stage = client.get(f"/api/v1/stages/{stage_id}").json()
    assert stage["status"] == "READY"
    assert stage["feedback"]["reason"] == "mapping incomplete"
    notes = client.get(f"/api/v1/notifications?projectId={seeded['id']}").json()["items"]
    assert any(n["type"] == "GATE_REJECTED" for n in notes)


def test_commit_without_approved_deploy_gate_is_409(client, harness, seeded) -> None:
    """AC-GATE-02: 409 GATE_NOT_APPROVED and S4 commit is never called."""
    _advance_design_line(client, harness, seeded)
    created = client.post(
        f"/api/v1/workflows/{seeded['workflowId']}/deployment-runs",
        json={"environmentKey": "dev"},
        headers=OPERATOR,
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run"]["id"]
    response = client.post(
        f"/api/v1/deployment-runs/{run_id}/commit", headers={**OPERATOR, "Idempotency-Key": "ac-s1-commit"}
    )
    assert response.status_code == 409
    assert response.json()["errorCode"] == "GATE_NOT_APPROVED"
    assert not any(call.startswith("commit:") for call in harness.execution.calls)


def test_delete_unarchived_project_is_409(client, harness, seeded) -> None:
    """CP-AC-09."""
    response = client.delete(f"/api/v1/projects/{seeded['id']}", headers=OPERATOR)
    assert response.status_code == 409
    assert response.json()["errorCode"] in {"INVALID_STATE", "PROJECT_ARCHIVED"}


def test_high_open_question_blocks_design_gate(client, harness, seeded) -> None:
    """CP-Q-006 / CP-BR-06."""
    from factory_control.ports.dto import AgentRunResult, QuestionDraft

    harness.intelligence.queue_run(
        AgentRunResult(status="COMPLETED", questions=[
            QuestionDraft(local_id="q1", text="which year?", severity="HIGH", blocking=True)
        ])
    )
    stage_id = seeded["currentStageId"]
    run_stage(client, harness, stage_id)
    gate = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"][0]
    response = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={"gateType": gate["type"], "seenBoundHashes": gate["boundObjects"]},
        headers=OPERATOR,
    )
    assert response.status_code == 409
    body = response.json()
    assert body["errorCode"] == "INVALID_STATE"
    assert body["details"]["cause"] == "HIGH_QUESTION_OPEN"


def test_a_failed_promotion_rolls_the_approval_back(client, harness, seeded) -> None:
    """CP-AC-05: approval, stage and audit undo together when S2 refuses to promote.

    The promotion is the write S2 owes S1 inside the same unit of work, so a refusal has
    to leave the Gate WAITING, the stage exactly where it was and no approval in the
    audit — a half-approved Gate would approve content nobody promoted.
    """
    from factory_control.ports.dto import AgentRunResult

    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    stage_id = seeded["currentStageId"]
    run_stage(client, harness, stage_id)
    gate = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"][0]
    gate_id = str(gate["id"])
    project_id = str(seeded["id"])
    version_before = client.get(f"/api/v1/projects/{project_id}", headers=OPERATOR).json()[
        "currentBlueprintVersion"
    ]

    harness.blueprint.promote_fail = True
    refused = client.post(
        f"/api/v1/gates/{gate_id}/approve",
        json={
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": [
                {"issueId": w.get("issue_id") or w["issueId"], "disposition": "ACCEPTED"}
                for w in gate["warnings"]
            ],
            "confirmations": [{"id": c["id"], "confirmed": True} for c in gate["confirmationItems"]]
            + [{"id": "OPERATIONAL_BACKUP_CONFIRMED", "confirmed": True}],
        },
        headers=OPERATOR,
    )
    assert refused.status_code == 409, refused.text
    # S2's BP_NOT_PROMOTABLE is reported under S1's own code (CP-API-013).
    assert refused.json()["errorCode"] == "INVALID_STATE"

    assert client.get(f"/api/v1/gates/{gate_id}", headers=OPERATOR).json()["status"] == "WAITING"
    assert client.get(f"/api/v1/stages/{stage_id}", headers=OPERATOR).json()["status"] == "WAITING_APPROVAL"
    after = client.get(f"/api/v1/projects/{project_id}", headers=OPERATOR).json()
    assert after["currentBlueprintVersion"] == version_before, "a rolled back approval promotes nothing"
    audit = client.get(f"/api/v1/projects/{project_id}/audit", headers=OPERATOR).json()["items"]
    assert not [row for row in audit if row["eventType"] == "cp.gate.approved"], audit


def test_a_patch_on_an_unrun_stage_opens_its_gate(client, harness, seeded) -> None:
    """CP-AC-18 / S5IF-AC08, the regression of CP-WF-029: UI-WB-005 authors by hand.

    A stage that never ran still accepts a patch and still opens its Gate — what is
    approved is the draft, not an agent run — so the manual-authoring path cannot be
    reachable only after an Agent has already written the content.
    """
    stage_id = seeded["currentStageId"]
    assert client.get(f"/api/v1/stages/{stage_id}", headers=OPERATOR).json()["status"] == "READY"

    patched = client.post(
        f"/api/v1/stages/{stage_id}/patches",
        headers=OPERATOR,
        json={
            "patch": {
                "operations": [
                    {
                        "op": "add",
                        "path": "/requirements/items/-",
                        "value": {
                            "businessKey": "req-by-hand",
                            "statement": "The clinic records a visit outcome.",
                            "source": {"artifactId": "art-hand", "location": "spec \u00a71"},
                        },
                    }
                ],
                "rationale": "authored without an agent run",
            }
        },
    )
    assert patched.status_code == 200, patched.text

    submitted = client.post(f"/api/v1/stages/{stage_id}/submit-for-approval", headers=OPERATOR)
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "WAITING_APPROVAL", body
    assert body["gateId"], body

    gates = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates", headers=OPERATOR).json()["items"]
    opened = [gate for gate in gates if gate["id"] == body["gateId"]]
    assert opened and opened[0]["status"] == "WAITING", gates


def test_allowed_actions_reports_reason_codes(client, harness, seeded) -> None:
    """CP-WF-025 / CP-WF-026."""
    payload = client.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions").json()
    assert "workflowId" in payload
    assert any(a["action"] == "stage.run" and a["targetId"] == seeded["currentStageId"] for a in payload["allowed"])
    assert all("reasonCode" in a for a in payload["unavailable"])


def test_graph_before_baseline_compile_is_a_guided_404(client, harness, seeded) -> None:
    """CP-AC-31: 基线编译前读取依赖图 → 404 NOT_FOUND，details.reason=NOT_GENERATED、requiredStage=BASELINE_COMPILE."""
    response = client.get(f"/api/v1/workflows/{seeded['workflowId']}/object-graph", headers=OPERATOR)
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["errorCode"] == "NOT_FOUND"
    assert body["details"]["reason"] == "NOT_GENERATED"
    assert body["details"]["requiredStage"] == "BASELINE_COMPILE"


def test_the_three_graph_builds_answer_distinctly(client, harness, seeded) -> None:
    """CP-AC-33: 省略参数/`blueprintVersion`/`runId` 分别对应当前版本基线、指定版本基线、该 Run 的环境绑定构建。

    The wire-level proof of "no self-computation" (the field-for-field comparison against
    what the fake served) lives in ``tests/api/test_cp_ui_020_023.py``; this row pins the
    three-way build resolution acceptance end-to-end.
    """
    from factory_control.ports import dto
    from tests.helpers import create_run, drive_run  # type: ignore[import]

    pid = seeded["id"]
    wid = seeded["workflowId"]
    run_id = create_run(client, harness, seeded)
    drive_run(client, harness, run_id, stop_at_deploy_gate=True)
    for build_id in (f"bld-{pid}-baseline", f"bld-{pid}-dev"):
        harness.blueprint.dependency_graphs[build_id] = dto.DependencyGraph(
            build_id=build_id, project_id=pid, blueprint_version="0.1.0", kind="BASELINE"
        )
    current = client.get(f"/api/v1/projects/{pid}", headers=OPERATOR).json()["currentBlueprintVersion"]

    omitted = client.get(f"/api/v1/workflows/{wid}/object-graph", headers=OPERATOR).json()
    by_version = client.get(
        f"/api/v1/workflows/{wid}/object-graph?blueprintVersion={current}", headers=OPERATOR
    ).json()
    by_run = client.get(f"/api/v1/workflows/{wid}/object-graph?runId={run_id}", headers=OPERATOR).json()

    assert omitted["buildId"] == f"bld-{pid}-baseline"
    assert by_version["buildId"] == omitted["buildId"], "the current version's latest baseline compile"
    assert by_run["buildId"] == f"bld-{pid}-dev", "that Run's environment-bound compile"
    for body in (omitted, by_version, by_run):
        for assembled in ("counts", "truncated", "matched"):
            assert assembled not in body, "S1 carries no self-computed graph fields"


def test_adding_a_member_in_mvp_is_refused_with_the_feature_reason(client, harness, seeded) -> None:
    """CP-AC-32: MVP 中添加项目成员 → 409 FEATURE_NOT_ENABLED；allowed-actions 中该动作不可用、同一原因码."""
    refused = client.put(
        f"/api/v1/projects/{seeded['id']}/members", json={"members": []}, headers=OPERATOR
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["errorCode"] == "FEATURE_NOT_ENABLED"
    assert refused.json()["details"] == {"feature": "member.manage", "milestone": "M4"}

    payload = client.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions").json()
    entry = next(a for a in payload["unavailable"] if a["action"] == "member.manage")
    assert entry["reasonCode"] == "FEATURE_NOT_ENABLED"


def test_sse_replays_in_order_and_reset_when_expired(harness, seeded) -> None:
    """CP-AC-03 / CP-EVT-003/004 — the stream replays from the sequence given."""
    import asyncio

    from factory_control.events.sse import stream_workflow_events

    async def collect(after: int, limit: int = 5) -> list[str]:
        generator = stream_workflow_events(
            session_factory=harness.runtime.session_factory,
            bus=harness.bus,
            workflow_id=seeded["workflowId"],
            after_sequence=after,
            heartbeat_seconds=0.05,
        )
        frames: list[str] = []
        async for frame in generator:
            frames.append(frame)
            if len(frames) >= limit:
                break
        return frames

    frames = asyncio.run(collect(0))
    data = [
        json.loads(line.split("data:", 1)[1])
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data:")
    ]
    sequences = [d["sequence"] for d in data]
    assert sequences, frames
    assert sequences == sorted(sequences) and sequences[0] == 1


def test_sse_reset_frame_is_emitted_when_requested_sequence_expired(harness, seeded) -> None:
    """CP-EVT-004: a stale ``after`` yields exactly one ``stream.reset`` frame."""
    import asyncio

    from factory_control.events import event_log
    from factory_control.events.sse import stream_workflow_events
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        for index in range(3):
            event_log.append(
                session,
                workflow_id=seeded["workflowId"],
                event_type="stage.started",
                payload={"i": index},
                retention_days=1,
            )
        oldest = event_log.oldest_retained_sequence(session, seeded["workflowId"])
    assert oldest is not None

    async def first_frame() -> str:
        generator = stream_workflow_events(
            session_factory=harness.runtime.session_factory,
            bus=harness.bus,
            workflow_id=seeded["workflowId"],
            after_sequence=oldest - 2,
            heartbeat_seconds=0.05,
        )
        async for frame in generator:
            return frame
        return ""

    frame = asyncio.run(first_frame())
    assert "stream.reset" in frame


def _advance_design_line(client, harness, seeded) -> None:
    """Drive each design stage to PASSED using the fakes."""
    from factory_control.ports.dto import AgentRunResult

    for stage_type in ("REQUIREMENTS", "DOMAIN", "DISCOVERY", "MAPPING", "PAGE_DESIGN", "BASELINE_COMPILE"):
        workflow = client.get(f"/api/v1/workflows/{seeded['workflowId']}").json()
        stage = next(s for s in workflow["stages"] if s["stageType"] == stage_type)
        if stage["status"] == "SKIPPED":
            continue
        if stage_type in ("REQUIREMENTS", "DOMAIN", "MAPPING"):
            harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
            run_stage(client, harness, stage["id"])
            gate = client.get(
                f"/api/v1/workflows/{seeded['workflowId']}/gates"
            ).json()["items"]
            waiting = [g for g in gate if g["status"] == "WAITING" and g["stageId"] == stage["id"]]
            if waiting:
                client.post(
                    f"/api/v1/gates/{waiting[0]['id']}/approve",
                    json={"gateType": waiting[0]["type"], "seenBoundHashes": waiting[0]["boundObjects"]},
                    headers=OPERATOR,
                )
        else:
            run_stage(client, harness, stage["id"])


def run_stage(client, harness, stage_id: str, *, rerun: bool = False) -> dict:
    """Start a stage over REST, then wait for the in-process engine to finish it.

    The engine accepts the run and executes it on a worker thread, so the caller
    must wait before asserting on stage state (CP-NFR-05).
    """
    endpoint = "rerun" if rerun else "run"
    response = client.post(f"/api/v1/stages/{stage_id}/{endpoint}", headers=OPERATOR)
    assert response.status_code == 202, response.text
    accepted = response.json()
    outcome = harness.runtime.engine.wait(stage_id, timeout=20)
    assert outcome is not None, accepted
    assert "error" not in outcome, outcome
    return {**accepted, **outcome}
