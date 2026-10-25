"""``GET /api/v1/setup`` — the cold-start facts the consoles draw their checklist from (S5IF-SET01).

The point of this endpoint is that a fresh install could previously not be diagnosed from the UI at
all, so the assertions are about **what a brand-new deployment says**, not about happy-path counts.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control import iam
from factory_control.rest.schemas import setup as SS
from factory_control.services import setup as SETUP
from factory_control.testing.harness import ControlHarness

OPERATOR = {"X-Operator": "ada"}


def _snake_free(node: Any, where: str = "body") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if "_" in key and not key.startswith("_") and not key.isupper():
                found.append(f"{where}.{key}")
            found += _snake_free(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found += _snake_free(item, f"{where}[{index}]")
    return found


def test_a_fresh_install_answers_zeroes_rather_than_errors(harness: ControlHarness, client: TestClient) -> None:
    """Nothing registered yet is a *fact to report*, not a 404 and not a 500."""
    response = client.get("/api/v1/setup", headers=OPERATOR)
    assert response.status_code == 200, response.text
    body = response.json()
    SS.SetupView.model_validate(body)
    assert not _snake_free(body), _snake_free(body)

    assert body["environments"] == {
        "total": 0,
        "inService": 0,
        "nonProductionInService": 0,
        "neverTested": [],
        "items": [],
    }
    assert body["projects"]["total"] == 0
    assert body["configuration"]["problems"] == []
    assert set(body["providers"]) == {"S2", "S3", "S4"}


def test_an_environment_registered_but_never_tested_is_called_out(
    harness: ControlHarness, client: TestClient
) -> None:
    """The difference between "registered" and "usable": the validation stage refuses the untested."""
    harness.execution.add_environment("dev", environment_class="TEST")
    harness.execution.add_environment("stage", environment_class="TEST")
    harness.execution.test_connection("dev", ["discovery", "import"], idempotency_key="setup-test-dev")

    body = client.get("/api/v1/setup", headers=OPERATOR).json()
    environments = body["environments"]
    assert environments["total"] == 2
    assert environments["inService"] == 2
    assert environments["neverTested"] == ["stage"]
    tested = next(item for item in environments["items"] if item["environmentKey"] == "dev")
    assert tested["lastTestedAt"] is not None


def test_a_disabled_environment_is_not_counted_as_available(harness: ControlHarness, client: TestClient) -> None:
    harness.execution.add_environment("dev", environment_class="TEST")
    harness.execution.add_environment("live", environment_class="PRODUCTION")
    harness.execution.disable_environment("dev")

    body = client.get("/api/v1/setup", headers=OPERATOR).json()
    environments = body["environments"]
    assert environments["total"] == 2
    assert environments["inService"] == 1
    # ``dev`` is out of service, so the one non-production environment left is… none: a project
    # could not be created against this configuration, and the console has to be able to say so.
    assert environments["nonProductionInService"] == 0
    assert environments["neverTested"] == ["live"]
    disabled = next(item for item in environments["items"] if item["environmentKey"] == "dev")
    assert disabled["status"] == "DISABLED"
    assert disabled["lastTestedAt"] is None


def test_a_caller_without_governance_rights_gets_no_intelligence_counts(
    harness: ControlHarness, client: TestClient
) -> None:
    """``null``, not zero: a zero would read as "nothing registered" to someone who simply may not look."""
    original = iam.get_permission_policy()

    class _NoGovernance:
        def is_allowed(self, *, operator: str, project_id: str | None, action: str) -> bool:
            return action != iam.GOVERNANCE_MANAGE

    iam.set_permission_policy(_NoGovernance())
    try:
        body = client.get("/api/v1/setup", headers=OPERATOR).json()
    finally:
        iam.set_permission_policy(original)
    assert body["intelligence"] is None
    del harness


def test_a_caller_without_the_credential_right_gets_no_credential_counts(
    harness: ControlHarness, client: TestClient
) -> None:
    """Same rule as the section above, applied to `credentials` (S5IF-SET01 as amended).

    A caller who may not manage secrets must not learn how many are stored either, and must not get
    `named: 0` — that would read as "nobody has entered a credential yet" to someone whose only
    crime is looking at the checklist without the right to press the button.
    """
    original = iam.get_permission_policy()

    class _NoSecrets:
        def is_allowed(self, *, operator: str, project_id: str | None, action: str) -> bool:
            return action != iam.SECRET_MANAGE

    iam.set_permission_policy(_NoSecrets())
    try:
        body = client.get("/api/v1/setup", headers=OPERATOR).json()
    finally:
        iam.set_permission_policy(original)
    assert body["credentials"] is None
    del harness


def test_a_misconfiguration_is_reported_as_a_sentence_about_the_variable(
    harness: ControlHarness,
) -> None:
    """``validate_runtime`` used to be read only to fail fast; now the console can show it.

    Assembled through the service rather than over HTTP on purpose: flipping ``auth_mode`` to
    ``local`` on a running app makes the middleware refuse the very request that would report it,
    which is correct behaviour and would test nothing here.
    """
    harness.runtime.settings.auth_mode = "local"
    harness.runtime.settings.https_only = False
    problems = SETUP.readiness(harness.runtime, operator="ada")["configuration"]["problems"]
    assert any("FACTORY_HTTPS_ONLY" in line for line in problems), problems
    # sentences name variables, never values
    assert not any("=" in line for line in problems), problems


@pytest.mark.parametrize("path", ["/api/v1/setup"])
def test_the_setup_endpoint_needs_the_platform_listing_read_right(client: TestClient, path: str) -> None:
    """An answer discloses no more than ``GET /environments`` + ``GET /projects`` already do."""
    original = iam.get_permission_policy()

    class _Nothing:
        def is_allowed(self, *, operator: str, project_id: str | None, action: str) -> bool:
            return False

    iam.set_permission_policy(_Nothing())
    try:
        response = client.get(path, headers=OPERATOR)
    finally:
        iam.set_permission_policy(original)
    assert response.status_code == 403
    assert response.json()["errorCode"] == "PERMISSION_DENIED"


def test_creating_the_first_project_is_an_action_the_console_can_ask_about(
    harness: ControlHarness, client: TestClient
) -> None:
    """``POST /projects`` guards itself with ``project.create``, so the list must answer for it.

    Before this the name appeared in the guard and in a comment and nowhere in the vocabulary the
    console is allowed to read, which left a "New project" button that could only be tried.
    """
    del harness
    body = client.get("/api/v1/allowed-actions", headers=OPERATOR).json()
    allowed = {row["action"] for row in body["allowed"]}
    assert "project.create" in allowed


def test_a_role_without_project_create_is_answered_the_way_the_guard_would_refuse_it() -> None:
    """The answer a control is drawn from and the answer its request meets cannot diverge.

    ``allowed_actions_for_platform`` only consults the policy while identity is enforced, so this
    goes straight to the domain function with an evaluator instead of pretending over an MVP app.
    """
    from types import SimpleNamespace

    from factory_control.domain import actions as A

    def _evaluate(names: tuple[str, ...]) -> dict[str, SimpleNamespace]:
        return {
            name: SimpleNamespace(
                allowed=name != iam.PROJECT_CREATE,
                reason_code=A.PERMISSION_DENIED,
                details={},
            )
            for name in names
        }

    rows = {a.name: a for a in A.platform_actions([], evaluate=_evaluate)}
    assert rows["project.create"].available is False
    assert rows["project.create"].reason_code == A.PERMISSION_DENIED
    assert rows["environment.create"].available is True
