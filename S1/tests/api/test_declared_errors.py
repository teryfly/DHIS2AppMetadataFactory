"""Every status the application answers with is declared on its operation (CP-API-010 ②).

Two halves:

* the whole-session check — :data:`factory_control.rest.traffic.log` records what every test
  in the run actually received, and one assertion at session end compares that against the
  published document.  It cannot be a per-test property: a contract is a statement about the
  sum of the traffic, and an operation nobody drives badly stays unchecked.
* the drivers below, which make sure the interesting statuses really do reach the wire — an
  artifact past retention, a model that timed out, a provider that is not installed.  Each
  also asserts its own code is declared, so the failure names the route rather than the run.

A 500 is out of scope on purpose (CP-API-008): ``ServerErrorMiddleware`` answers it outside
every middleware, so it is neither observable here nor a promise the document makes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.rest.traffic import log

pytestmark = pytest.mark.api

OPERATOR = {"X-Operator": "ada"}


def _build_document() -> dict[str, Any]:
    """The published document, built without a database (as ``export_openapi.py`` does)."""
    from factory_control.adapters.registry import default_ports
    from factory_control.config import Settings
    from factory_control.events.bus import EventBus
    from factory_control.rest.app import create_app
    from factory_control.runtime import build_runtime

    settings = Settings(provider_mode="fakes", workflow_engine="inprocess", database_url="sqlite://")
    runtime = build_runtime(settings=settings, bus=EventBus(), **default_ports(settings=settings))
    return create_app(runtime).openapi()


@pytest.fixture(scope="session")
def contract_document() -> dict[str, Any]:
    return _build_document()


@pytest.fixture(scope="session", autouse=True)
def _every_served_status_is_declared() -> Any:
    """After the whole run: nothing may answer a status its operation omits.

    Session teardown is the only point at which all the traffic is known, so the drift report
    surfaces as a session error with one line per undeclared pair.
    """
    yield
    drift = _undeclared(_build_document(), log.observed())
    assert not drift, _drift_report(drift)


def _undeclared(document: dict[str, Any], observed: dict[str, tuple[int, ...]]) -> list[tuple[str, int, list[str]]]:
    """``(endpoint, served status, declared codes)`` for every pair the document omits."""
    rows: list[tuple[str, int, list[str]]] = []
    for endpoint, statuses in sorted(observed.items()):
        method, _, path = endpoint.partition(" ")
        operation = (document.get("paths", {}).get(path) or {}).get(method.lower()) or {}
        declared = sorted(str(code) for code in (operation.get("responses") or {}))
        rows += [
            (endpoint, status, declared)
            for status in statuses
            if status != 500 and str(status) not in declared
        ]
    return rows


def _drift_report(drift: list[tuple[str, int, list[str]]]) -> str:
    header = [
        "S1 answered a status its operation does not declare (CP-API-010 ②, SRS 8.3).",
        "A generated client reads the contract, so an undeclared code is a branch it cannot",
        "build: declare it on the route, or stop serving it from there.",
    ]
    lines = [
        f"  {endpoint} -> {status}   declares: {', '.join(declared) or 'nothing'}"
        for endpoint, status, declared in drift
    ]
    return "\n".join(header + lines)


def _declared(document: dict[str, Any], method: str, path: str) -> set[str]:
    operation = (document.get("paths", {}).get(path) or {}).get(method.lower()) or {}
    return {str(code) for code in (operation.get("responses") or {})}


def test_a_purged_artifact_answers_410_and_the_operation_declares_410(
    client: TestClient, seeded: dict
) -> None:
    """FD-ART-014 / CP-AC-26 at the wire: the id still resolves and the bytes are gone."""
    from factory_control.testing.fakes import register_fake_artifact

    artifact_id = register_fake_artifact(seeded["id"], "METADATA_PACKAGE", "declared-410")
    assert (
        client.post(f"/api/v1/artifacts/{artifact_id}/purge", headers=OPERATOR).status_code == 200
    )
    refused = client.get(f"/api/v1/artifacts/{artifact_id}/content", headers=OPERATOR)
    assert refused.status_code == 410, refused.text
    assert refused.json()["errorCode"] == "ARTIFACT_PURGED"
    assert "410" in _declared(
        _build_document(), "GET", "/api/v1/artifacts/{artifact_id}/content"
    ), "410 is served here and must be declared"


def test_a_model_that_timed_out_answers_504_and_the_operation_declares_504(
    client: TestClient, harness: Any, seeded: dict
) -> None:
    """LLM_TIMEOUT is 504 (errors.ERROR_HTTP_STATUS): the caller may retry the same turn."""
    from factory_control.domain.enums import CHATABLE_STAGE_STATUSES, DIALOGUE_STAGE_TYPES
    from factory_control.foundation import FactoryError

    view = client.get(f"/api/v1/workflows/{seeded['workflowId']}", headers=OPERATOR).json()
    chatable = [
        stage
        for stage in view["stages"]
        if stage["stageType"] in DIALOGUE_STAGE_TYPES and stage["status"] in CHATABLE_STAGE_STATUSES
    ]
    assert chatable, [
        (stage["stageType"], stage["status"]) for stage in view["stages"]
    ]
    intelligence = harness.runtime.intelligence
    original = intelligence.stage_chat

    def _slow(*_a: Any, **_k: Any) -> Any:
        raise FactoryError("the model did not answer in time", code="LLM_TIMEOUT")

    intelligence.stage_chat = _slow  # type: ignore[method-assign]
    try:
        response = client.post(
            f"/api/v1/stages/{chatable[0]['id']}/chat",
            headers={**OPERATOR, "Idempotency-Key": "turn-slow"},
            json={"message": "what changed?"},
        )
    finally:
        intelligence.stage_chat = original  # type: ignore[method-assign]
    assert response.status_code == 504, response.text
    assert response.json()["errorCode"] == "LLM_TIMEOUT"
    assert "504" in _declared(_build_document(), "POST", "/api/v1/stages/{stage_id}/chat")


def test_a_read_of_an_unknown_object_answers_404(client: TestClient) -> None:
    response = client.get("/api/v1/gates/gate-nowhere", headers=OPERATOR)
    assert response.status_code == 404
    assert response.json()["errorCode"] == "NOT_FOUND"


def test_an_uninstalled_provider_answers_503_on_a_read_too(
    client: TestClient, harness: Any, seeded: dict
) -> None:
    """S5IF-UP08 is not only a write-path failure: a read that forwards can be caught by it.

    ``test_s5if_endpoints`` proves the governance read; this proves the Registry read, so the
    declaration is checked per route rather than assumed from the group's list.
    """
    from factory_control.ports.protocols import ProviderUnavailableError

    blueprint = harness.runtime.blueprint
    original = blueprint.get_registry_entries

    def _absent(*_a: Any, **_k: Any) -> Any:
        raise ProviderUnavailableError("factory_blueprint.api exports no function 'get_registry_entries'")

    blueprint.get_registry_entries = _absent  # type: ignore[method-assign]
    try:
        response = client.get(f"/api/v1/projects/{seeded['id']}/registry", headers=OPERATOR)
    finally:
        blueprint.get_registry_entries = original  # type: ignore[method-assign]
    assert response.status_code == 503, response.text
    assert response.json()["errorCode"] == "PROVIDER_UNAVAILABLE"


def test_the_log_is_not_silently_empty(contract_document: dict[str, Any]) -> None:
    """A guard that measured nothing would pass, so measure the codes this module drove."""
    observed = {status for statuses in log.observed().values() for status in statuses}
    assert {404, 410} <= observed, observed
    assert all(
        _declared(contract_document, key.split()[0], key.split(" ", 1)[1]) for key in log.observed()
    ), "every observed endpoint is an operation in the document"
