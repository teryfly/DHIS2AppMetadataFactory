"""HANDOFF as a runnable stage (CP-WF-028, 总体 SRS 17.3, D-01).

``execute_stage`` used to answer this stage with ``InvalidStateError("HANDOFF is delivered at
milestone M3")`` while the SRS counted mode-B delivery as current work.  S2's side was
functional all along — nothing in S1 ever called it, and nothing ever created a
``HANDOFF_GATE``, so the delivery-package screens could only ever say "not generated".

What these tests hold open is the part a fake cannot lie about: the precondition.  A package
may not be handed over on the strength of a Run that verified a *different* Blueprint version,
or of a Run that only ever touched PRODUCTION.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.testing.harness import ControlHarness, seed_project
from tests.helpers import (  # type: ignore[import]
    approve_waiting_gate,
    create_run,
    drive_run,
    finish_design_line,
    gate_items,
    make_client,
    run_stage,
)

OPERATOR = {"X-Operator": "ada", "Content-Type": "application/json"}


@pytest.fixture
def mode_b(harness: ControlHarness) -> dict[str, Any]:
    return seed_project(harness, delivery_mode="B")


def _handoff_stage(client: TestClient, workflow_id: str) -> dict[str, Any]:
    stages = client.get(f"/api/v1/workflows/{workflow_id}", headers=OPERATOR).json()["stages"]
    return next(s for s in stages if s["stageType"] == "HANDOFF")


def test_a_verified_non_production_run_unblocks_handoff_and_opens_the_gate(
    harness: ControlHarness, mode_b: dict[str, Any]
) -> None:
    """17.3 / D-01: package generated, Gate created, and the milestone says HANDED_OFF."""
    client = make_client(harness)
    finish_design_line(client, harness, mode_b)
    run_id = create_run(client, harness, mode_b)
    final = drive_run(client, harness, run_id)
    assert final["run"]["status"] == "VERIFIED", final

    stage = _handoff_stage(client, mode_b["workflowId"])
    assert stage["status"] == "READY", "BASELINE_COMPILE must unlock the delivery step"

    outcome = run_stage(client, harness, stage["id"])
    assert "error" not in outcome, outcome
    assert stage_after(client, mode_b) == "WAITING_APPROVAL"

    gates = gate_items(client, mode_b["workflowId"])
    handoff_gate = [g for g in gates if g["type"] == "HANDOFF_GATE"]
    assert handoff_gate, f"no HANDOFF_GATE was created: {[g['type'] for g in gates]}"
    assert handoff_gate[0]["status"] == "WAITING"
    assert handoff_gate[0]["boundObjects"], "an approval must be bound to the bytes it reviewed"

    approve_waiting_gate(client, harness, mode_b["workflowId"], stage_id=handoff_gate[0]["stageId"])
    project = client.get(f"/api/v1/projects/{mode_b['id']}", headers=OPERATOR).json()
    assert project["milestoneStatus"] == "HANDED_OFF", project["milestoneStatus"]
    assert _handoff_stage(client, mode_b["workflowId"])["status"] == "PASSED"


def stage_after(client: TestClient, seeded: dict[str, Any]) -> str:
    return _handoff_stage(client, seeded["workflowId"])["status"]


def test_handoff_refuses_before_anything_was_delivered(
    harness: ControlHarness, mode_b: dict[str, Any]
) -> None:
    """The guard is the requirement: no VERIFIED non-production Run, no handoff.

    Answering 202 and generating a package anyway would put a Frontend Package in the artifact
    store that no environment has ever accepted, and the Gate approval would then be a signature
    on an unproven delivery.  A stage run is accepted and executed by the engine (CP-API-012),
    so the refusal lands on the stage as FAILED with the reason attached — which is what
    ``UI-MAN-*`` "shows the reason" needs.
    """
    client = make_client(harness)
    finish_design_line(client, harness, mode_b)

    stage = _handoff_stage(client, mode_b["workflowId"])
    assert stage["status"] == "READY"
    outcome = run_stage(client, harness, stage["id"])
    assert outcome.get("error") == "InvalidStateError", outcome
    assert "VERIFIED" in str(outcome.get("detail")), outcome

    assert stage_after(client, mode_b) == "FAILED"
    assert [g for g in gate_items(client, mode_b["workflowId"]) if g["type"] == "HANDOFF_GATE"] == []


def test_a_run_that_went_stale_does_not_qualify_as_delivery_evidence(
    harness: ControlHarness, mode_b: dict[str, Any]
) -> None:
    """OUTDATED is the state a VERIFIED Run lands in when the Blueprint moved on.

    Its bytes are not the bytes being handed over, so counting it would let the package be
    approved against evidence of a different version.
    """
    client = make_client(harness)
    finish_design_line(client, harness, mode_b)
    run_id = create_run(client, harness, mode_b)
    assert drive_run(client, harness, run_id)["run"]["status"] == "VERIFIED"

    from factory_control.foundation import unit_of_work
    from factory_control.models import DeploymentRun

    with unit_of_work() as session:
        session.get(DeploymentRun, run_id).outdated = True

    stage = _handoff_stage(client, mode_b["workflowId"])
    outcome = run_stage(client, harness, stage["id"])
    assert outcome.get("error") == "InvalidStateError", outcome
    assert "VERIFIED" in str(outcome.get("detail")), outcome


def test_mode_a_skips_the_delivery_step_and_running_it_is_refused(
    harness: ControlHarness, seeded: dict[str, Any]
) -> None:
    """CP-WF-002 rule 1: mode A has no HANDOFF to run, and the stage says SKIPPED."""
    client = make_client(harness)
    stage = _handoff_stage(client, seeded["workflowId"])
    assert stage["status"] == "SKIPPED", stage
    response = client.post(
        f"/api/v1/stages/{stage['id']}/run", headers={**OPERATOR, "Idempotency-Key": "handoff-mode-a"}
    )
    assert response.status_code in (400, 409), response.text
