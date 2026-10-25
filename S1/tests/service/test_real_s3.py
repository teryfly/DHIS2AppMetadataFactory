"""S1 running against the *real* S3 (``factory_intelligence``), for the dialogue read.

The rest of the suite exercises S1's ``FakeIntelligence``, whose ``get_chat`` answers the shape
S1's port declares.  S3 answers a ``Transcript`` dataclass instead, so opening a stage's
conversation was an HTTP 500 on every deployment with ``FACTORY_PROVIDER_MODE=real`` while the
whole fake suite stayed green — the drift ``test_registry_chat_transcript_shape.py`` now pins at
the adapter, and this pins through the REST layer.

S2 is real because the harness requires it (``harness.py`` refuses ``providers="real"`` without
an importable ``factory_blueprint``); S4 stays faked, since no environment is asked about here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.testing.harness import (
    ControlHarness,
    control_harness,
    has_real_provider,
    is_real_adapter,
    seed_project,
)

pytestmark = pytest.mark.skipif(not has_real_provider("S3"), reason="factory_intelligence is not installed")

OPERATOR = {"X-Operator": "ada"}


@pytest.fixture
def real_s3() -> Iterator[ControlHarness]:
    with control_harness(providers="real", real=("S2", "S3")) as harness:
        assert is_real_adapter(harness.intelligence), "S3 fell back to the fake"
        yield harness


@pytest.fixture
def real_client(real_s3: ControlHarness) -> Iterator[TestClient]:
    from factory_control.rest.app import create_app

    with TestClient(create_app(real_s3.runtime)) as client:
        yield client


@pytest.fixture
def real_seeded(real_s3: ControlHarness) -> dict[str, Any]:
    return seed_project(real_s3)


def _design_stage(client: TestClient, seeded: dict[str, Any]) -> dict[str, Any]:
    view = client.get(f"/api/v1/workflows/{seeded['workflowId']}", headers=OPERATOR).json()
    ready = [stage for stage in view["stages"] if stage["status"] == "READY"]
    assert ready, view["stages"]
    return ready[0]


def test_reading_a_stage_transcript_answers_the_view_the_port_promises(
    real_s3: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """AI-CHAT-003 / FR-UI-002: the empty history of a stage nobody has talked to yet."""
    stage = _design_stage(real_client, real_seeded)

    response = real_client.get(f"/api/v1/stages/{stage['id']}/chat", headers=OPERATOR)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["turns"] == [], body
    assert isinstance(body["turns"], list)
    assert body["context"]["stage"] == stage["stageType"]
    assert body["context"]["projectId"] == real_seeded["id"]
