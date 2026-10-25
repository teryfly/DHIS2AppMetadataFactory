"""Pytest fixtures for S1."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from factory_control.testing.harness import ControlHarness, control_harness, seed_project


@pytest.fixture
def harness() -> Iterator[ControlHarness]:
    with control_harness() as h:
        # A parse runs on a pool thread in production; tests run the job inline so the
        # assertions after a POST describe the parse that request claimed.
        h.runtime.parse_submit = lambda job: job()
        yield h


@pytest.fixture
def seeded(harness: ControlHarness) -> dict:
    return seed_project(harness)


@pytest.fixture
def client(harness: ControlHarness) -> Iterator[TestClient]:
    from factory_control.rest.app import create_app

    app = create_app(harness.runtime)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded_env(harness: ControlHarness, seeded: dict) -> dict:
    """A seeded project plus the S4 environments the forwarded endpoints read.

    Lives here because three modules need it: the fake S4 only knows about the
    environments it is told about, and every forwarded read answers nothing otherwise.
    """
    from factory_control.ports.dto import DiscoveryResultInfo

    harness.execution.add_environment("dev", environment_class="TEST")
    harness.execution.add_environment(
        "prod", environment_class="PRODUCTION", secret_refs={"discovery": "vault://prod/d", "import": "vault://prod/i"}
    )
    harness.execution.add_discovery_result(
        "dev",
        DiscoveryResultInfo(
            artifactId="art-disc-1",
            projectId=seeded["id"],
            scope="metadata",
            objectCount=12,
            version="2.41.4",
        ),
    )
    return seeded


@pytest.fixture
def seeded_client(seeded: dict, harness: ControlHarness) -> Iterator[tuple[TestClient, dict]]:
    from factory_control.rest.app import create_app

    app = create_app(harness.runtime)
    with TestClient(app) as test_client:
        yield test_client, seeded
