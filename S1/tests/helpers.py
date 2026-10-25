"""Shared test helpers: a client over the fakes and design-line driving."""

from __future__ import annotations

from typing import Any

from factory_control.ports.dto import AgentRunResult

OPERATOR = {"X-Operator": "ada", "Content-Type": "application/json"}
CONFIRMATIONS = [{"id": "OPERATIONAL_BACKUP_CONFIRMED", "confirmed": True}]


def commit_headers(key: str) -> dict[str, str]:
    """S5IF-G08: the header is the only place a COMMIT's idempotency key comes from."""
    return {**OPERATOR, "Idempotency-Key": key}


def upload_input(client: Any, project_id: str, name: str) -> str:
    """CP-API-009 two-step upload, through the public endpoint."""
    # No Content-Type header: multipart needs the boundary the transport generates.
    response = client.post(
        f"/api/v1/projects/{project_id}/inputs",
        headers={"X-Operator": "ada"},
        files={"file": (f"{name}.csv", b"a,b\n1,2\n", "text/csv")},
        data={"dataClassification": "INTERNAL"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["artifactId"])


def make_client(harness: Any) -> Any:
    from fastapi.testclient import TestClient

    from factory_control.rest.app import create_app

    return TestClient(create_app(harness.runtime))


def run_stage(client: Any, harness: Any, stage_id: str, *, rerun: bool = False) -> dict[str, Any]:
    """Start a stage over REST and wait for the in-process engine to finish it.

    The engine accepts and executes asynchronously (CP-NFR-05), so a caller that
    asserts on stage state must wait for the outcome first.
    """
    endpoint = "rerun" if rerun else "run"
    response = client.post(f"/api/v1/stages/{stage_id}/{endpoint}", headers=OPERATOR)
    assert response.status_code == 202, response.text
    outcome = harness.runtime.engine.wait(stage_id, timeout=20)
    assert outcome is not None, response.text
    return {**response.json(), **outcome}


def gate_items(client: Any, workflow_id: str) -> list[dict[str, Any]]:
    return client.get(f"/api/v1/workflows/{workflow_id}/gates").json()["items"]


def approve_waiting_gate(client: Any, harness: Any, workflow_id: str, *, stage_id: str | None = None) -> dict:
    """Approve the first WAITING Gate, dispositioning every WARNING (CP-GATE-008)."""
    waiting = [g for g in gate_items(client, workflow_id) if g["status"] == "WAITING"]
    if stage_id:
        waiting = [g for g in waiting if g["stageId"] == stage_id]
    assert waiting, f"no waiting gate for stage {stage_id}: {gate_items(client, workflow_id)}"
    gate = waiting[0]
    dispositions = [
        {"issueId": w.get("issue_id") or w.get("issueId"), "disposition": "ACCEPTED"}
        for w in gate["warnings"]
    ]
    confirmations = [{"id": c["id"], "confirmed": True} for c in gate["confirmationItems"]] + CONFIRMATIONS
    response = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": dispositions,
            "confirmations": confirmations,
        },
        headers=OPERATOR,
    )
    assert response.status_code == 200, response.text
    return response.json()


def finish_design_line(client: Any, harness: Any, seeded: dict[str, Any], *, iterations: int = 16) -> dict[str, Any]:
    """Drive every design stage to PASSED/SKIPPED through the public API."""
    workflow_id = seeded["workflowId"]
    agent_stages = ("REQUIREMENTS", "DOMAIN", "MAPPING", "PAGE_DESIGN")
    for _ in range(iterations):
        workflow = client.get(f"/api/v1/workflows/{workflow_id}").json()
        # HANDOFF is a design-line row but is not part of the design line this helper closes:
        # it unlocks after BASELINE_COMPILE and needs a VERIFIED Deployment Run to run at all.
        design = [
            s for s in workflow["stages"] if s["deploymentRunId"] is None and s["stageType"] != "HANDOFF"
        ]
        if any(g["status"] == "WAITING" for g in gate_items(client, workflow_id)):
            approve_waiting_gate(client, harness, workflow_id)
            continue
        ready = [s for s in design if s["status"] in ("READY", "INVALIDATED")]
        if not ready:
            return client.get(f"/api/v1/workflows/{workflow_id}").json()
        stage = ready[0]
        if stage["stageType"] in agent_stages:
            harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
        # Appendix A: a superseded stage re-enters RUNNING only through the rerun edge, which is
        # how the work a change request invalidated (CP-CR-004) gets redone.
        outcome = run_stage(client, harness, stage["id"], rerun=stage["status"] == "INVALIDATED")
        if "error" in outcome:
            raise AssertionError(f"stage {stage['stageType']} failed: {outcome}")
    raise AssertionError("design line did not close within the iteration budget")


def drive_run(client: Any, harness: Any, run_id: str, *, iterations: int = 14, stop_at_deploy_gate: bool = False) -> dict[str, Any]:
    """Advance a Deployment Run: run READY steps, approve Gates, COMMIT once."""
    workflow_id = client.get(f"/api/v1/deployment-runs/{run_id}").json()["run"]["workflowId"]
    for _ in range(iterations):
        view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
        waiting = [g for g in view["gates"] if g["status"] == "WAITING"]
        if waiting:
            gate = waiting[0]
            if stop_at_deploy_gate and gate["type"] == "DEPLOY_GATE":
                return view
            approve_waiting_gate(client, harness, workflow_id, stage_id=gate["stageId"])
            if gate["type"] == "DEPLOY_GATE":
                accepted = client.post(
                    f"/api/v1/deployment-runs/{run_id}/commit", json={}, headers=commit_headers(f"advance-{run_id}")
                )
                # CP-API-012: a COMMIT is accepted, not answered; the Run says how it went.
                assert accepted.status_code == 202, accepted.text
            continue
        ready = [s for s in view["stages"] if s["status"] == "READY"]
        if not ready:
            return view
        outcome = run_stage(client, harness, ready[0]["id"])
        if "error" in outcome:
            raise AssertionError(f"run stage failed: {outcome}")
    return client.get(f"/api/v1/deployment-runs/{run_id}").json()


def create_run(client: Any, harness: Any, seeded: dict[str, Any], environment_key: str = "dev") -> str:
    finish_design_line(client, harness, seeded)
    created = client.post(
        f"/api/v1/workflows/{seeded['workflowId']}/deployment-runs",
        json={"environmentKey": environment_key},
        headers=OPERATOR,
    )
    assert created.status_code == 201, created.text
    return str(created.json()["run"]["id"])
