"""Remaining SRS section-11 acceptance rows for the M1+M2 scope.

Each test names the acceptance criterion it proves, per the plan's mapping of
SRS §11 to files.
"""

from __future__ import annotations

import pytest

from factory_control.ports.dto import (
    AgentRunResult,
    DriftResult,
    ExecutionOutcome,
)

pytestmark = pytest.mark.acceptance

OPERATOR = {"X-Operator": "ada", "Content-Type": "application/json"}


def _pass_design_line(client, harness, seeded) -> None:
    """Drive the design line to the end so Run behaviour can be observed."""
    from tests.acceptance.test_ac_s1 import run_stage

    while True:
        workflow = client.get(f"/api/v1/workflows/{seeded['workflowId']}").json()
        ready = [s for s in workflow["stages"] if s["status"] == "READY" and s["deploymentRunId"] is None]
        if not ready:
            return workflow
        stage = ready[0]
        if stage["stageType"] in ("REQUIREMENTS", "DOMAIN", "MAPPING", "PAGE_DESIGN"):
            harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
        run_stage(client, harness, stage["id"])
        gates = client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"]
        waiting = [g for g in gates if g["status"] == "WAITING" and g["stageId"] == stage["id"]]
        if waiting:
            client.post(
                f"/api/v1/gates/{waiting[0]['id']}/approve",
                json={"gateType": waiting[0]["type"], "seenBoundHashes": waiting[0]["boundObjects"]},
                headers=OPERATOR,
            )




BACKUP_CONFIRMATION = "OPERATIONAL_BACKUP_CONFIRMED"


def approve_gate(client, harness, gate: dict) -> dict:
    """Approve with every WARNING dispositioned and every confirmation given.

    CP-GATE-008 requires each warning marked, CP-GATE-012 the backup confirmation,
    CP-GATE-013 each non-default import option — the driver must satisfy the same
    rules the UI does.
    """
    dispositions = [
        {"issueId": w.get("issue_id") or w.get("issueId"), "disposition": "ACCEPTED"}
        for w in gate["warnings"]
    ]
    confirmations = [{"id": c["id"], "confirmed": True} for c in gate["confirmationItems"]]
    if gate["type"] == "DEPLOY_GATE" and not any(
        c["id"] == BACKUP_CONFIRMATION for c in confirmations
    ):
        confirmations.append({"id": BACKUP_CONFIRMATION, "confirmed": True})
    return client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": dispositions,
            "confirmations": confirmations,
        },
        headers=OPERATOR,
    )


def drive_run(
    client, harness, run_id: str, *, iterations: int = 12, stop_before_commit: bool = False,
    stop_at_deploy_gate: bool = False,
):
    """Advance a Deployment Run: run READY steps, approve waiting Gates, commit once."""
    from tests.acceptance.test_ac_s1 import run_stage

    for _ in range(iterations):
        view = client.get(f"/api/v1/deployment-runs/{run_id}", headers=OPERATOR).json()
        waiting = [g for g in view["gates"] if g["status"] == "WAITING"]
        if waiting:
            gate = waiting[0]
            if stop_at_deploy_gate and gate["type"] == "DEPLOY_GATE":
                return view
            approved = approve_gate(client, harness, gate)
            assert approved.status_code == 200, approved.text
            if gate["type"] == "DEPLOY_GATE":
                if stop_before_commit:
                    return view
                accepted = client.post(
                    f"/api/v1/deployment-runs/{run_id}/commit",
                    headers={**OPERATOR, "Idempotency-Key": f"drive-{run_id}"},
                )
                assert accepted.status_code == 202, accepted.text
            continue
        ready = [s for s in view["stages"] if s["status"] == "READY"]
        if not ready:
            return view
        run_stage(client, harness, ready[0]["id"])
    return client.get(f"/api/v1/deployment-runs/{run_id}", headers=OPERATOR).json()


def make_run_verified(client, harness, seeded) -> None:
    """Drive a Deployment Run for the dev environment all the way to VERIFIED."""
    run_id = create_run_for(client, harness, seeded)
    drive_run(client, harness, run_id)


def create_run_for(client, harness, seeded, environment_key: str = "dev") -> str:
    _pass_design_line(client, harness, seeded)
    created = client.post(
        f"/api/v1/workflows/{seeded['workflowId']}/deployment-runs",
        json={"environmentKey": environment_key},
        headers=OPERATOR,
    )
    assert created.status_code == 201, created.text
    return created.json()["run"]["id"]

def test_major_version_invalidates_mapping_and_gates(client, harness, seeded) -> None:
    """AC-WF-03 / CP-WF-015 rule 5.

    A Major version from S2 invalidates MAPPING and every later design stage, and
    the Gates that were waiting on them.
    """
    _pass_design_line(client, harness, seeded)
    workflow = client.get(f"/api/v1/workflows/{seeded['workflowId']}").json()
    mapping = next(s for s in workflow["stages"] if s["stageType"] == "MAPPING")

    harness.blueprint.affected_stages[seeded["id"]] = ["MAPPING"]
    harness.blueprint.projects[seeded["id"]]["change_level"] = "Major"
    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    from tests.acceptance.test_ac_s1 import run_stage

    # A new pass over an approved stage is a rerun (CP-WF-010), which must produce
    # fresh artifacts rather than reuse the PASSED stage.
    run_stage(client, harness, mapping["id"], rerun=True)
    gate = [g for g in client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"]
            if g["status"] == "WAITING"][0]
    approved = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        json={"gateType": gate["type"], "seenBoundHashes": gate["boundObjects"]},
        headers=OPERATOR,
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    # S2 spells its change level "Major"; the public wire says UPPER_SNAKE (S5IF-G03/AC18),
    # and S1 is the one that projects it.
    assert body["changeLevel"] == "MAJOR"
    assert body["invalidation"]["invalidateStages"]

    after = client.get(f"/api/v1/workflows/{seeded['workflowId']}").json()
    statuses = {s["stageType"]: s["status"] for s in after["stages"] if s["deploymentRunId"] is None}
    assert statuses["PAGE_DESIGN"] in {"INVALIDATED", "SKIPPED"}
    assert statuses["BASELINE_COMPILE"] == "INVALIDATED"


def test_activity_retry_reuses_idempotency_key(harness, seeded) -> None:
    """AC-WF-02: a retried call continues instead of redoing the run.

    S1 derives one idempotency key per (stage, attempt); re-issuing the same call
    must be answered from S3's stored run rather than a second agent run.
    """
    from factory_control.services.steps import execute_stage, start_stage

    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED", run_id="agn-fixed"))
    start_stage(harness.runtime, seeded["currentStageId"], operator="ada")

    key = f"cp:{seeded['currentStageId']}:replayed"
    first = execute_stage(harness.runtime, seeded["currentStageId"], operator="ada", idempotency_key=key)
    assert harness.intelligence.runs[key].status == "COMPLETED"

    # Replaying the same key is answered from the stored run: no additional run object
    # is created in S3, which is what "continue, do not redo" means here (CP-WF-008).
    run_count_before = len({k for k in harness.intelligence.runs})
    again = execute_stage(harness.runtime, seeded["currentStageId"], operator="ada", idempotency_key=key)
    assert again.get("agentRunId") == harness.intelligence.runs[key].run_id
    assert len({k for k in harness.intelligence.runs}) == run_count_before
    assert first.get("agentRunId") == again.get("agentRunId")


def test_cancel_stops_a_running_agent_at_a_safe_point(client, harness, seeded) -> None:
    """CP-AC-10 / CP-WF-011: cancelling asks S3 to stop and does not advance the stage."""
    from factory_control.foundation import unit_of_work
    from factory_control.models import StageRun
    from factory_control.ports.dto import AgentRunResult, QuestionDraft

    harness.intelligence.queue_run(
        AgentRunResult(status="WAITING_INPUT", questions=[
            QuestionDraft(local_id="q1", text="which module?", severity="MEDIUM", blocking=True)
        ])
    )
    from tests.acceptance.test_ac_s1 import run_stage

    run_stage(client, harness, seeded["currentStageId"])
    stage = client.get(f"/api/v1/stages/{seeded['currentStageId']}").json()
    assert stage["status"] == "WAITING_INPUT"

    with unit_of_work() as session:
        run_ids = [rid for row in session.query(StageRun).all() for rid in (row.agent_run_ids or [])]
    assert run_ids

    cancelled = client.post(f"/api/v1/workflows/{seeded['workflowId']}/cancel", headers=OPERATOR)
    assert cancelled.status_code == 200
    assert run_ids[0] in harness.intelligence.runs
    assert harness.intelligence.runs[run_ids[0]].status == "CANCELLED"
    workflow = client.get(f"/api/v1/workflows/{seeded['workflowId']}").json()
    assert workflow["status"] == "CANCELLED"
    stage_after = client.get(f"/api/v1/stages/{seeded['currentStageId']}").json()
    assert stage_after["status"] != "PASSED"


def test_run_becomes_outdated_when_a_new_version_arrives(client, harness, seeded) -> None:
    """CP-AC-06 / CP-WF-015 rule 3: a finished Run is flagged, never rewritten."""
    make_run_verified(client, harness, seeded)
    workflow_id = seeded["workflowId"]
    runs_before = client.get(f"/api/v1/workflows/{workflow_id}/deployment-runs").json()["items"]
    verified = [r for r in runs_before if r["environmentKey"] == "dev"]
    assert verified and verified[0]["status"] == "VERIFIED"

    # A new version through a rerun of MAPPING with Major change level.
    harness.blueprint.affected_stages[seeded["id"]] = ["MAPPING"]
    harness.blueprint.projects[seeded["id"]]["change_level"] = "Major"
    _approve_new_version(client, harness, seeded, rerun=True)

    runs_after = client.get(f"/api/v1/workflows/{workflow_id}/deployment-runs").json()["items"]
    same = next(r for r in runs_after if r["id"] == verified[0]["id"])
    assert same["outdated"] is True
    assert same["status"] == "VERIFIED"


def _approve_new_version(client, harness, seeded, *, rerun: bool = False) -> None:
    from tests.acceptance.test_ac_s1 import run_stage

    workflow = client.get(f"/api/v1/workflows/{seeded['workflowId']}").json()
    mapping = next(s for s in workflow["stages"] if s["stageType"] == "MAPPING")
    if mapping["status"] not in ("READY", "PASSED", "FAILED", "INVALIDATED"):
        return
    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    run_stage(client, harness, mapping["id"], rerun=rerun)
    gate = [g for g in client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"]
            if g["status"] == "WAITING"]
    if gate:
        client.post(
            f"/api/v1/gates/{gate[0]['id']}/approve",
            json={"gateType": gate[0]["type"], "seenBoundHashes": gate[0]["boundObjects"]},
            headers=OPERATOR,
        )


def test_commit_replayed_under_the_same_key_never_reaches_s4_twice(client, harness, seeded) -> None:
    """AC-EXE-05 / CP-GATE-018, S5IF-G08: one key, one COMMIT.

    The first COMMIT is answered with an unconfirmable outcome.  The key now lives only
    on the header, so the second identical request is answered from S1's stored response
    and S4 is never asked again.  ``commit_run``'s own recovery — asking S4 about the key
    when S1 has no completed record, which is the crash case — is covered at the service
    layer in ``test_failure_paths``.
    """
    run_id = create_run_for(client, harness, seeded)
    drive_run(client, harness, run_id, stop_before_commit=True)

    key = "commit-retry-key"
    harness.execution.commit_results.append(
        ExecutionOutcome(
            execution_id="exe-unconfirmed",
            status="NEEDS_MANUAL_CHECK",
            applied=None,
            report_artifact_id="art-exe-report",
        )
    )
    first = client.post(
        f"/api/v1/deployment-runs/{run_id}/commit",
        headers={**OPERATOR, "Idempotency-Key": key},
    )
    # CP-API-012: the acceptance names the execution; the Run carries the verdict.
    assert first.status_code == 202, first.text
    assert first.json()["stepRunId"] == "exe-unconfirmed", first.json()
    assert client.get(f"/api/v1/deployment-runs/{run_id}", headers=OPERATOR).json()["run"][
        "status"
    ] == "MANUAL_CHECK"

    calls_before = len([c for c in harness.execution.calls if c.startswith("commit:")])
    second = client.post(
        f"/api/v1/deployment-runs/{run_id}/commit",
        headers={**OPERATOR, "Idempotency-Key": key},
    )
    assert second.status_code == 202, second.text
    # S4 was asked about the key, and never asked to commit a second time.
    assert len([c for c in harness.execution.calls if c.startswith("commit:")]) == calls_before


def test_drift_at_approval_invalidates_gate_and_reopens_validate(client, harness, seeded) -> None:
    """CP-AC-08 / CP-GATE-005: S4 drift voids the DEPLOY_GATE and sends R3 back."""
    run_id = create_run_for(client, harness, seeded)
    view = drive_run(client, harness, run_id, stop_at_deploy_gate=True)

    harness.execution.drift["dev"] = DriftResult(drifted=True, objects=[{"uid": "A1", "kind": "CHANGED"}])
    gate = [g for g in client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"]
            if g["status"] == "WAITING" and g["type"] == "DEPLOY_GATE"]
    assert gate, f"a DEPLOY_GATE should be waiting after R3: {view}"
    response = client.post(
        f"/api/v1/gates/{gate[0]['id']}/approve",
        json={"gateType": "DEPLOY_GATE", "seenBoundHashes": gate[0]["boundObjects"]},
        headers=OPERATOR,
    )
    assert response.status_code == 409
    assert response.json()["errorCode"] == "DRIFT_DETECTED"
    after = client.get(f"/api/v1/gates/{gate[0]['id']}").json()
    assert after["status"] == "INVALIDATED"


def test_drift_recheck_records_pushes_and_voids_the_gate(client, harness, seeded) -> None:
    """CP-UI-013 / S5IF-FLD05: the re-check is a step, and its answer is a record.

    Three obligations, one action: the verdict becomes the Run's latest drift conclusion
    (which the console then reads without touching DHIS2), it is pushed as an event, and a
    positive answer voids the approval that rested on the old target (CP-GATE-005).
    """
    from factory_control.events.event_log import read_after
    from factory_control.foundation import unit_of_work

    run_id = create_run_for(client, harness, seeded)
    view = drive_run(client, harness, run_id, stop_at_deploy_gate=True)
    gate = next(g for g in view["gates"] if g["type"] == "DEPLOY_GATE" and g["status"] == "WAITING")

    harness.execution.drift["dev"] = DriftResult(drifted=True, objects=[{"uid": "A1", "kind": "CHANGED"}])
    accepted = client.post(
        f"/api/v1/deployment-runs/{run_id}/drift-check",
        headers={**OPERATOR, "Idempotency-Key": "drift-ui013"},
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["stepRunId"], accepted.json()

    summary = client.get(f"/api/v1/deployment-runs/{run_id}/summary", headers=OPERATOR).json()
    assert summary["drift"]["drifted"] is True, summary.get("drift")

    voided = client.get(f"/api/v1/gates/{gate['id']}", headers=OPERATOR).json()
    assert voided["status"] == "INVALIDATED", voided
    assert voided["invalidatedReason"] == "DRIFT_DETECTED"

    with unit_of_work() as session:
        pushed = [
            event
            for event in read_after(session, seeded["workflowId"], 0)
            if event.event_type == "deployment_run.drift_checked"
        ]
    assert pushed, "the verdict was recorded but never pushed"
    assert pushed[-1].payload["runId"] == run_id and pushed[-1].payload["drifted"] is True


def test_export_then_import_rebuilds_the_project(client, harness, seeded) -> None:
    """AC-DATA-01 / CP-PRJ-008, CP-PRJ-009.

    The export is a PROJECT_EXPORT artifact; importing it creates a new project
    whose Blueprint versions are restored, whose gates are not inherited and whose
    environments must be registered again.
    """
    _pass_design_line(client, harness, seeded)
    exported = client.get(f"/api/v1/projects/{seeded['id']}/export", headers=OPERATOR)
    # CP-API-012: an export is accepted, and the PROJECT_EXPORT artifact it will produce
    # is the record the caller watches — so that id is the answer.
    assert exported.status_code == 202, exported.text
    artifact_id = exported.json()["stepRunId"]
    assert client.get(f"/api/v1/artifacts/{artifact_id}", headers=OPERATOR).json()["type"] == "PROJECT_EXPORT"

    imported = client.post(
        "/api/v1/projects/import", json={"artifactId": artifact_id}, headers=OPERATOR
    )
    assert imported.status_code == 202, imported.text
    new_project = client.get(f"/api/v1/projects/{imported.json()['stepRunId']}", headers=OPERATOR).json()
    # CP-PRJ-009 【新增】: environments and approvals do not cross deployments, and the
    # design line reopens as a draft — all of it readable off the restored project.
    assert new_project["currentBlueprintVersion"]
    assert new_project["targetEnvironments"] == []
    assert {s["stageType"]: s["status"] for s in new_project["stages"]}["REQUIREMENTS"] == "READY"
    gates = client.get(f"/api/v1/workflows/{new_project['workflowId']}/gates").json()["items"]
    assert gates == []


@pytest.mark.m4
def test_executor_cannot_approve_own_production_gate() -> None:
    """AC-GATE-04: the executor may not be one of the approvers.

    Delivered with M4, and asserted at ``tests/api/test_identity_m4.py`` under
    ``FACTORY_AUTH_MODE=local``: the case needs two distinguishable identities, which the MVP's
    declared-name regime cannot supply.  This stub stays so the acceptance inventory still shows
    the case and where it is proved.
    """
    pytest.skip("proved in tests/api/test_identity_m4.py::test_ac_gate_04_the_executor_may_not_approve_their_own_gate")
