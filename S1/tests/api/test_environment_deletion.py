"""Deleting metadata out of a target environment (DX-EXE-038, FR-TST-002③).

``POST /api/v1/environments/{environmentKey}/delete-objects`` is the only public door onto S4's
deletion executor, and it exists because a run against the shared official demo instance has to
remove what it created when it finishes (总体 SRS 25.4).  These tests drive the door against S1's
own S4 double; what the double cannot answer — that a ``FORBID`` deletion policy, an emergency
lock or an object with associated data stops the deletion — is asserted against the real S4 in
``tests/service/test_real_s4_deletion.py``, because those are S4's rules over S4's rows.

What is checked here is the part S1 owns: that the request is *shaped* the way an irreversible
call must be (an operator, a caller-supplied idempotency key, nothing S1 invents), that the
arguments land on the port in S4's own order, and that S1 refuses on its own account only what
it can answer better than the provider can.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control import iam
from factory_control.testing.fakes import register_fake_artifact

pytestmark = pytest.mark.api

OPERATOR = {"X-Operator": "ada", "Content-Type": "application/json"}
PATH = "/api/v1/environments/dev/delete-objects"


def _body(project_id: str, artifact_id: str, expected_hash: str = "sha256:" + "a" * 64) -> dict[str, Any]:
    return {
        "projectId": project_id,
        "deletionArtifactId": artifact_id,
        "expectedHash": expected_hash,
    }


def _key(key: str = "cleanup-1") -> dict[str, str]:
    return {**OPERATOR, "Idempotency-Key": key}


def test_a_deletion_is_forwarded_and_the_execution_is_reported(
    client: TestClient, harness: Any, seeded_env: dict[str, Any]
) -> None:
    """S4's ``ExecutionOutcome`` on the wire, in camelCase, with the ids that make it followable."""
    artifact_id = register_fake_artifact(seeded_env["id"], "METADATA_PACKAGE", "delete-dev")

    response = client.post(PATH, headers=_key(), json=_body(seeded_env["id"], artifact_id))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["environmentKey"] == "dev"
    assert body["deletionArtifactId"] == artifact_id
    assert body["executionId"], body
    assert body["status"] == "COMPLETED"
    assert body["applied"] is True
    assert body["payloadHash"] == "sha256:" + "a" * 64
    assert body["reportArtifactId"], "an execution that reports no artifact leaves nothing to audit"
    assert body["issues"] == []
    # S5IF-AC02: one spelling on the whole body
    assert not [name for name in body if "_" in name], body

    # the port is called with S4's own positional order, and the key is the caller's
    execution = harness.execution
    assert [call for call in execution.calls if call.startswith("delete_objects:")] == [
        f"delete_objects:dev:{artifact_id}"
    ], execution.calls
    stored = execution.get_execution_by_key("delete:cleanup-1")
    assert stored is not None and stored.execution_id == body["executionId"], (
        "S4 is keyed on the caller's Idempotency-Key, prefixed so it cannot collide with a COMMIT"
    )


def test_a_second_call_on_the_same_key_is_replayed_and_deletes_nothing_again(
    client: TestClient, harness: Any, seeded_env: dict[str, Any]
) -> None:
    """NFR-REL-003 at the wire: a retry of a deletion must not be a second deletion.

    Two layers do this on purpose.  The middleware replays the stored first response, so the
    provider is not even reached; and S4's own executor keys the execution on the same value, so
    a caller that bypassed the HTTP layer still gets the first verdict.  The double mirrors the
    second, which is why the assertion is about the call log rather than about an instance.
    """
    artifact_id = register_fake_artifact(seeded_env["id"], "METADATA_PACKAGE", "delete-twice")
    headers = _key("same-key-twice")

    first = client.post(PATH, headers=headers, json=_body(seeded_env["id"], artifact_id))
    assert first.status_code == 200, first.text
    second = client.post(PATH, headers=headers, json=_body(seeded_env["id"], artifact_id))

    assert second.status_code == 200, second.text
    assert second.headers.get("X-Idempotent-Replay") == "true", dict(second.headers)
    assert second.json() == first.json()
    assert len([c for c in harness.execution.calls if c.startswith("delete_objects:")]) == 1


def test_a_deletion_without_an_idempotency_key_is_refused(client: TestClient, seeded_env: dict) -> None:
    """S5IF-G08: the header is the only source, and an irreversible call takes no chances.

    ``Idempotency-Key`` is optional everywhere else because a replay there costs a duplicate row;
    here it would cost a second delete that no instance can undo.
    """
    from factory_control.testing.fakes import register_fake_artifact as register

    artifact_id = register(seeded_env["id"], "METADATA_PACKAGE", "delete-no-key")
    response = client.post(PATH, headers=OPERATOR, json=_body(seeded_env["id"], artifact_id))
    assert response.status_code == 400, response.text
    assert response.json()["errorCode"] == "IDEMPOTENCY_KEY_REQUIRED", response.json()
    assert response.json()["details"]["header"] == "Idempotency-Key"


def test_a_deletion_without_an_operator_is_refused(client: TestClient, seeded_env: dict) -> None:
    """CP-API-002: the audit of a destruction has to name who asked for it."""
    response = client.post(
        PATH,
        headers={"Idempotency-Key": "no-operator", "Content-Type": "application/json"},
        json=_body(seeded_env["id"], "art-whatever"),
    )
    assert response.status_code == 400, response.text
    assert response.json()["errorCode"] == "OPERATOR_REQUIRED"


def test_the_idempotency_key_is_not_read_from_the_body(client: TestClient, seeded_env: dict) -> None:
    """S5IF-G08 【裁决】: a body key is a second source that can disagree with the header.

    ``extra="forbid"`` answers 422 rather than dropping the field in silence, because silence here
    teaches the caller that its deletion is replay-protected when it is not.
    """
    body = _body(seeded_env["id"], "art-whatever") | {"idempotencyKey": "in-the-body"}
    response = client.post(PATH, headers=_key(), json=body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "patched",
    [
        {"projectId": ""},
        {"projectId": "   "},
        {"deletionArtifactId": ""},
        {"expectedHash": ""},
    ],
)
def test_a_deletion_must_name_project_payload_and_hash(
    client: TestClient, seeded_env: dict, patched: dict[str, Any]
) -> None:
    """DX-EXE-038: the three arguments are what makes "which objects, approved against what" answerable."""
    body = _body(seeded_env["id"], "art-whatever") | patched
    response = client.post(PATH, headers=_key(), json=body)
    assert response.status_code == 422, response.text
    fields = response.json()["details"].get("fields") or []
    assert fields, response.json()
    assert {field["path"] for field in fields} <= set(patched), fields


def test_a_caller_without_the_environment_right_is_refused(
    client: TestClient, harness: Any, seeded_env: dict[str, Any]
) -> None:
    """CP-IAM-001: the hook runs before the provider does, so nothing reaches DHIS2 either."""
    artifact_id = register_fake_artifact(seeded_env["id"], "METADATA_PACKAGE", "delete-denied")
    original = iam.get_permission_policy()

    class _NoEnvironmentManagement:
        def is_allowed(self, *, operator: str, project_id: str | None, action: str) -> bool:
            return action != iam.ENVIRONMENT_MANAGE

    iam.set_permission_policy(_NoEnvironmentManagement())
    try:
        response = client.post(PATH, headers=_key(), json=_body(seeded_env["id"], artifact_id))
    finally:
        iam.set_permission_policy(original)

    assert response.status_code == 403, response.text
    assert response.json()["errorCode"] == "PERMISSION_DENIED", response.json()
    assert response.json()["details"]["action"] == iam.ENVIRONMENT_MANAGE
    assert not [c for c in harness.execution.calls if c.startswith("delete_objects:")], (
        "a refused caller must not have deleted anything on S1's authority anyway"
    )


def test_a_payload_of_another_project_is_refused_before_the_provider_is_called(
    client: TestClient, harness: Any, seeded_env: dict[str, Any]
) -> None:
    """S4 answers a project mismatch with a bare ``ValueError``; CP-API-008 wants a field.

    The payload is what gets deleted, so whose it is cannot be left to a 500 that names nothing —
    and the same id asked for twice would look like a defect in S4 rather than a wrong request.
    """
    other = register_fake_artifact("prj-somebody-else", "METADATA_PACKAGE", "delete-foreign")

    response = client.post(PATH, headers=_key(), json=_body(seeded_env["id"], other))
    assert response.status_code == 422, response.text
    assert response.json()["errorCode"] == "VALIDATION_ERROR", response.json()
    assert response.json()["details"]["artifactId"] == other
    assert not [c for c in harness.execution.calls if c.startswith("delete_objects:")]


def test_an_unknown_payload_is_a_404_and_not_a_crash(client: TestClient, seeded_env: dict) -> None:
    response = client.post(PATH, headers=_key(), json=_body(seeded_env["id"], "art-does-not-exist"))
    assert response.status_code == 404, response.text
    assert response.json()["errorCode"] == "NOT_FOUND"
    assert response.json()["details"]["artifactId"] == "art-does-not-exist"


def test_a_disabled_environment_answers_the_way_s4_would(
    client: TestClient, harness: Any, seeded_env: dict[str, Any]
) -> None:
    """S4's loader refuses a DISABLED environment before it looks at anything else.

    Saying it here is what keeps the answer the same on a ``fakes`` deployment: the console would
    otherwise green-light the button and then meet a 409 the first time the target is real
    (``_require_in_service``, and the same reasoning as test-connection and probe-capabilities).
    """
    artifact_id = register_fake_artifact(seeded_env["id"], "METADATA_PACKAGE", "delete-offline")
    harness.execution.disable_environment("dev", "for maintenance")

    response = client.post(PATH, headers=_key(), json=_body(seeded_env["id"], artifact_id))
    assert response.status_code == 409, response.text
    assert response.json()["errorCode"] == "INVALID_STATE", response.json()
    assert response.json()["details"]["reasonCode"] == "ENVIRONMENT_DISABLED"
    assert not [c for c in harness.execution.calls if c.startswith("delete_objects:")]


def test_the_environment_is_addressed_in_the_path_and_never_defaulted(
    client: TestClient, harness: Any, seeded_env: dict[str, Any]
) -> None:
    """DX-BR-02 / FR-ENV-003: one operation, one explicitly named environment.

    ``prod`` is a second real row in the double; a deletion that quietly picked one of them would
    be indistinguishable from a deletion that asked for it, which is the wrong asymmetry for an
    irreversible call.
    """
    artifact_id = register_fake_artifact(seeded_env["id"], "METADATA_PACKAGE", "delete-target")

    response = client.post(
        "/api/v1/environments/prod/delete-objects",
        headers=_key("targets-prod"),
        json=_body(seeded_env["id"], artifact_id),
    )
    assert response.status_code == 200, response.text
    assert response.json()["environmentKey"] == "prod", response.json()
    assert harness.execution.calls.count("delete_objects:prod:" + artifact_id) == 1
    assert not [c for c in harness.execution.calls if c.startswith("delete_objects:dev:")]
