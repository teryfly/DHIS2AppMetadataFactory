"""SRS 8.3: the per-code ``details`` shape, proved at the wire.

Each row is one error S1 actually raises, requested in the way that fails.  ``details``
is what UI-REQ-002 renders — which header to add, which field is wrong, which state the
object is in and which ones would have allowed the action — so a missing key is a dialog
a user cannot understand, not a cosmetic gap.  The three environment verdicts and the
purged artifact are proved where their scenarios already exist (``test_failure_paths``,
``test_s5if_acceptance``).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

OPERATOR = {"X-Operator": "ada"}


def _body(response: Any) -> dict[str, Any]:
    assert response.status_code >= 400, response.text
    return dict(response.json())


def test_operator_required_names_the_header(client: TestClient, seeded: dict) -> None:
    """CP-AC-01 with the SRS 8.3 shape: say which header, do not make it a riddle."""
    body = _body(client.post(f"/api/v1/projects/{seeded['id']}/archive", json={}))
    assert body["errorCode"] == "OPERATOR_REQUIRED"
    assert body["details"] == {"header": "X-Operator"}


def test_commit_without_a_key_names_the_header(client: TestClient) -> None:
    """S5IF-AC17: the key is the header's job alone, and the refusal says so.

    The header is checked before the Run is read, because a COMMIT without idempotency
    must never reach S4 whatever it was addressed to.
    """
    body = _body(client.post("/api/v1/deployment-runs/run-nowhere/commit", json={}, headers=OPERATOR))
    assert body["errorCode"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert body["details"]["header"] == "Idempotency-Key"


def test_invalid_state_names_the_state_the_target_and_the_states_that_would_work(
    client: TestClient, seeded: dict
) -> None:
    """CP-AC-09: 409 INVALID_STATE, and S5 can say what to do next."""
    body = _body(client.delete(f"/api/v1/projects/{seeded['id']}", headers=OPERATOR))
    assert body["errorCode"] == "INVALID_STATE"
    details = body["details"]
    assert details["currentState"] == "ACTIVE"
    assert details["requiredStates"] == ["ARCHIVED"]
    assert details["targetId"] == seeded["id"]


def test_a_stage_refusal_carries_the_states_it_would_accept(
    client: TestClient, harness: Any, seeded: dict
) -> None:
    """The same shape on the stage machine, where the list of states is the point."""
    from tests.helpers import run_stage  # type: ignore[import]

    view = client.get(f"/api/v1/workflows/{seeded['workflowId']}", headers=OPERATOR).json()
    handoff = next(s for s in view["stages"] if s["stageType"] == "HANDOFF")
    body = _body(client.post(f"/api/v1/stages/{handoff['id']}/patches", json={"patch": {"operations": []}}, headers=OPERATOR))
    assert body["errorCode"] == "INVALID_STATE"
    assert body["details"]["targetId"] == handoff["id"]
    assert set(body["details"]["requiredStates"]) == {"READY", "RUNNING", "WAITING_APPROVAL"}
    assert body["details"]["currentState"] == "SKIPPED"
    del run_stage


def test_validation_error_lists_the_fields(client: TestClient) -> None:
    """S1's own check, in the shape the document declares."""
    body = _body(
        client.post(
            "/api/v1/projects",
            json={"name": "", "ownerName": "ada", "targetEnvironments": []},
            headers=OPERATOR,
        )
    )
    assert body["errorCode"] == "VALIDATION_ERROR"
    fields = body["details"]["fields"]
    assert {"path": "name", "message": "name is required"} in fields
    assert all(set(entry) == {"path", "message"} for entry in fields), fields


def test_a_request_body_the_model_rejected_also_answers_with_the_envelope(
    client: TestClient,
) -> None:
    """CP-API-008: FastAPI's own 422 body is not the S1 contract.

    The document already attaches ``components/responses/Error`` to every 422, so a
    request the Pydantic model refused has to answer in the same shape — a generated
    client would otherwise fail to parse its own most common mistake.
    """
    response = client.post("/api/v1/projects", json={"name": "p"}, headers=OPERATOR)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["errorCode"] == "VALIDATION_ERROR"
    assert body["correlationId"]
    paths = {entry["path"] for entry in body["details"]["fields"]}
    assert "ownerName" in paths, paths
    assert response.headers["x-correlation-id"] == body["correlationId"]


def test_an_upload_too_big_says_the_limit_and_what_passes(
    client: TestClient, seeded_env: dict, harness: Any
) -> None:
    """S5IF-AC02 family: UPLOAD_REJECTED carries the shape S5 shows in the picker."""
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        from factory_control.settings import load_settings_snapshot

        policy = load_settings_snapshot(session).upload_policy()

    big = b"x" * (policy.max_bytes + 1)
    response = client.post(
        f"/api/v1/projects/{seeded_env['id']}/inputs",
        headers=OPERATOR,
        files={"file": ("big.csv", big, "text/csv")},
        data={"dataClassification": "INTERNAL"},
    )
    body = _body(response)
    assert body["errorCode"] == "UPLOAD_REJECTED"
    details = body["details"]
    assert details["reason"] == "SIZE"
    assert details["maxBytes"] == policy.max_bytes
    assert policy.allowed_types[0] in details["allowedTypes"]
    del harness


def test_a_wrong_media_type_is_refused_as_a_type_problem(
    client: TestClient, seeded_env: dict
) -> None:
    """Only after a real limit is configured — the factory default allows anything."""
    configured = client.put(
        "/api/v1/settings", headers=OPERATOR, json={"allowedUploadTypes": ["text/csv"]}
    )
    assert configured.status_code == 200, configured.text
    body = _body(
        client.post(
            f"/api/v1/projects/{seeded_env['id']}/inputs",
            headers=OPERATOR,
            files={"file": ("notes.docx", b"not a csv", "application/msword")},
            data={"dataClassification": "INTERNAL"},
        )
    )
    assert body["details"]["reason"] == "TYPE"
    assert body["details"]["violation"] == "MEDIA_TYPE_NOT_ALLOWED"
    assert body["details"]["mediaType"] == "application/msword"
    assert body["details"]["allowedTypes"] == ["text/csv"]


def test_an_idempotency_conflict_names_the_scope(
    client: TestClient, seeded_env: dict
) -> None:
    """CP-AC-02 with the SRS 8.3 shape: the conflict says which endpoint holds the key."""
    key = {**OPERATOR, "Idempotency-Key": "conflict-scope"}
    first = client.post(f"/api/v1/projects/{seeded_env['id']}/inputs", headers=key,
                        files={"file": ("a.csv", b"a,b\n1,2\n", "text/csv")},
                            data={"dataClassification": "INTERNAL"},
                        )
    assert first.status_code == 201, first.text
    second = client.post(f"/api/v1/projects/{seeded_env['id']}/inputs", headers=key,
                         files={"file": ("b.csv", b"c,d\n3,4\n", "text/csv")},
                             data={"dataClassification": "INTERNAL"},
                         )
    body = _body(second)
    assert body["errorCode"] == "IDEMPOTENCY_CONFLICT"
    assert "inputs" in body["details"]["scope"]


def test_an_invalidated_approval_lists_the_objects_that_moved(
    client: TestClient, harness: Any, seeded: dict
) -> None:
    """CP-AC-24 / S5IF-AC14: refType, refId and both hashes — the row S5 renders."""
    from tests.helpers import create_run, drive_run  # type: ignore[import]

    run_id = create_run(client, harness, seeded)
    view = drive_run(client, harness, run_id, stop_at_deploy_gate=True)
    gate = next(g for g in view["gates"] if g["status"] == "WAITING")
    bound = dict(gate["boundObjects"][0])
    stale = {**bound, "contentHash": "sha256:" + "0" * 64}

    body = _body(
        client.post(
            f"/api/v1/gates/{gate['id']}/approve",
            headers=OPERATOR,
            json={"gateType": gate["type"], "seenBoundHashes": [stale]},
        )
    )
    assert body["errorCode"] == "APPROVAL_INVALIDATED"
    details = body["details"]
    assert details["gateId"] == gate["id"]
    row = next(item for item in details["mismatches"] if item["refId"] == bound["refId"])
    assert set(row) == {"refType", "refId", "expectedHash", "actualHash", "problem"}
    assert row["expectedHash"] == bound["contentHash"]
    assert row["actualHash"] == stale["contentHash"]
