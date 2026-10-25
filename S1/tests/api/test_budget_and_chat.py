"""Budget write and in-stage dialogue at the S1 boundary (S5IF-GOV18, AI-CHAT-001/002/003).

Both features forward, so what S1 owns and must not get wrong is narrow and specific:

* the **budget** statement is validated by shape here and stored by S3 — S1 never answers a
  budget state it computed itself, and the action asked for is ``governance.budget.set``, not the
  read action (CP-UI-006, FR-SEC-002);
* a **chat turn** reaches S3 with the project's target-environment egress policies and the
  caller's operator name attached (D-07), is answered synchronously, and carries the FR-UI-002
  context header.  S1 keeps no copy of the transcript: the read is S3's (CP-API-007, D-08).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

OPERATOR = {"X-Operator": "ada"}


# --- the budget write ---------------------------------------------------------


def test_budget_write_then_read_answers_the_same_statement(client: TestClient, seeded: dict) -> None:
    written = client.put(
        "/api/v1/governance/budget",
        params={"projectId": seeded["id"]},
        headers=OPERATOR,
        json={"limit": 250.0, "thresholds": [0.5, 0.8], "currency": "usd"},
    )
    assert written.status_code == 200, written.text
    body = written.json()
    assert body["limit"] == 250.0 and body["currency"] == "USD"

    read = client.get("/api/v1/governance/budget", params={"projectId": seeded["id"]}, headers=OPERATOR).json()
    assert read["limit"] == 250.0, "the read side reports what the write stored, not a copy"


def test_a_budget_shape_s1_can_check_is_refused_before_the_call(
    client: TestClient, seeded: dict
) -> None:
    """A threshold outside (0,1) names the field, rather than forwarding a shape S3 rejects."""
    refused = client.put(
        "/api/v1/governance/budget",
        params={"projectId": seeded["id"]},
        headers=OPERATOR,
        json={"limit": 10.0, "thresholds": [1.4]},
    )
    assert refused.status_code == 422, refused.text
    fields = refused.json()["details"]["fields"]
    assert fields and fields[0]["path"].startswith("thresholds"), fields


def test_a_null_limit_is_a_statement_rather_than_an_absence(client: TestClient, seeded: dict) -> None:
    cleared = client.put(
        "/api/v1/governance/budget",
        params={"projectId": seeded["id"]},
        headers=OPERATOR,
        json={"limit": None, "thresholds": []},
    )
    assert cleared.status_code == 200 and cleared.json()["limit"] is None


def test_budget_write_appears_in_allowed_actions(client: TestClient, seeded: dict) -> None:
    """S5IF-G10: a control S5 draws has to be reported by the endpoint, available or not."""
    actions = client.get(
        f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions", headers=OPERATOR
    ).json()
    names = {entry["action"] for entry in actions["allowed"]} | {
        entry["action"] for entry in actions["unavailable"]
    }
    assert "governance.budget.set" in names, sorted(names)[:10]


# --- in-stage dialogue ---------------------------------------------------------


def _design_stage_id(client: TestClient, seeded: dict) -> str:
    stages = client.get(f"/api/v1/workflows/{seeded['workflowId']}/stages", headers=OPERATOR).json()["items"]
    design = [s for s in stages if s["stageType"] == "REQUIREMENTS"]
    assert design, [s["stageType"] for s in stages]
    return str(design[0]["id"])


def test_a_turn_is_answered_with_the_context_header_attached(
    client: TestClient, seeded: dict
) -> None:
    stage_id = _design_stage_id(client, seeded)
    answer = client.post(
        f"/api/v1/stages/{stage_id}/chat",
        headers={**OPERATOR, "Idempotency-Key": "turn-1"},
        json={"message": "which requirement covers the weight record?"},
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["answer"], "an empty answer would render a blank bubble"
    assert body["stageId"] == stage_id
    context = body["context"]
    assert context["projectId"] == seeded["id"] and context["stage"] == "REQUIREMENTS"
    assert "activeDraftId" in context, "FR-UI-002: the panel always says which draft is live"


def test_the_history_and_the_clear_both_reach_the_provider(
    client: TestClient, seeded: dict, harness: Any
) -> None:
    stage_id = _design_stage_id(client, seeded)
    client.post(
        f"/api/v1/stages/{stage_id}/chat",
        headers={**OPERATOR, "Idempotency-Key": "turn-2"},
        json={"message": "and the second?"},
    )
    history = client.get(f"/api/v1/stages/{stage_id}/chat", headers=OPERATOR)
    assert history.status_code == 200
    assert history.json()["turns"] == []  # the fake records no transcript until it is cleared/set

    cleared = client.delete(f"/api/v1/stages/{stage_id}/chat", headers=OPERATOR)
    assert cleared.status_code == 200 and cleared.json()["cleared"] is True
    calls = harness.runtime.intelligence.calls
    assert any(call.startswith("stage_chat") for call in calls), calls[-6:]
    assert any(call.startswith("clear_chat") for call in calls), calls[-6:]


def test_a_stage_without_a_dialogue_is_a_404_naming_the_stages_that_have_one(
    client: TestClient, seeded: dict
) -> None:
    stages = client.get(f"/api/v1/workflows/{seeded['workflowId']}/stages", headers=OPERATOR).json()["items"]
    deployment = [s for s in stages if s["stageType"] not in ("REQUIREMENTS", "DOMAIN", "MAPPING", "PAGE_DESIGN")]
    assert deployment, "the seeded project has a stage outside the design vocabulary"
    refused = client.post(
        f"/api/v1/stages/{deployment[0]['id']}/chat",
        headers=OPERATOR,
        json={"message": "hello?"},
    )
    assert refused.status_code == 404, refused.text
    assert refused.json()["details"]["chatable"] == ["DOMAIN", "MAPPING", "PAGE_DESIGN", "REQUIREMENTS"]
