"""The assembled process over a real socket (CP-API-001 … CP-API-008, CP-EVT-005).

``httpx``'s ASGI transport buffers streaming responses, so the SSE behaviour that
matters — replay, resumption and the fan-out delay — is checked against a uvicorn
server running in this process.  The server shares the harness engine and fakes,
so a seeded project is visible over HTTP exactly as the tests created it.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from factory_control.testing.harness import ControlHarness, control_harness, seed_project

pytestmark = pytest.mark.api

OPERATOR = {"X-Operator": "ada"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LiveServer:
    def __init__(self, harness: ControlHarness) -> None:
        import uvicorn

        from factory_control.api import attach_event_listener
        from factory_control.rest.app import create_app

        app = attach_event_listener(create_app(harness.runtime), harness.runtime)
        self.port = _free_port()
        self.config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="error", loop="asyncio"
        )
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="factory-api")
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("factory-api did not start")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=20)
        assert not self.thread.is_alive()

    def client(self, **kwargs: Any) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, headers=OPERATOR, timeout=15, **kwargs)


@pytest.fixture
def live() -> Iterator[tuple[_LiveServer, ControlHarness, dict[str, Any]]]:
    with control_harness() as harness:
        seeded = seed_project(harness)
        server = _LiveServer(harness)
        try:
            yield server, harness, seeded
        finally:
            server.stop()


def _frames(lines: Any, count: int, *, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Read ``count`` data frames from an SSE line iterator, skipping comments."""
    deadline = time.monotonic() + timeout
    frames: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in lines:
        if time.monotonic() > deadline:
            raise AssertionError(f"only {len(frames)} frames arrived in {timeout}s")
        if line == "":
            if current.get("data") is not None:
                frames.append(current)
                current = {}
                if len(frames) >= count:
                    return frames
            else:
                current = {}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "id":
            current["id"] = int(value)
        elif field == "event":
            current["event"] = value
        elif field == "data":
            current["data"] = json.loads(value)
    return frames


# --- the surface ------------------------------------------------------------


def test_health_and_openapi_over_http(live: tuple[_LiveServer, ControlHarness, dict]) -> None:
    server, _harness, _seeded = live
    with server.client() as client:
        health = client.get("/healthz").json()
        assert health["status"] == "ok"
        assert health["engine"]["name"] == "inprocess"
        document = client.get("/api/v1/openapi.json").json()
    assert document["openapi"].startswith("3.1")
    assert "/api/v1/workflows/{workflow_id}/events" in document["paths"]
    assert "/api/v1/deployment-runs/{run_id}/commit" in document["paths"]


def test_operator_header_is_mandatory_over_http(live: tuple[_LiveServer, ControlHarness, dict]) -> None:
    server, _harness, seeded = live
    with httpx.Client(base_url=server.base_url, timeout=10) as client:
        # reads are open to any caller (D-16), writes must name their operator
        read = client.get(f"/api/v1/workflows/{seeded['workflowId']}")
        missing = client.post(f"/api/v1/workflows/{seeded['workflowId']}/pause")
    assert read.status_code == 200
    assert missing.status_code == 400
    assert missing.json()["errorCode"] == "OPERATOR_REQUIRED"
    with httpx.Client(base_url=server.base_url, headers=OPERATOR, timeout=10) as client:
        assert client.post(f"/api/v1/workflows/{seeded['workflowId']}/pause").status_code == 200


def test_error_envelope_carries_correlation_id_and_language(
    live: tuple[_LiveServer, ControlHarness, dict]
) -> None:
    server, _harness, _seeded = live
    with server.client() as client:
        english = client.get("/api/v1/workflows/wf-missing", headers={"Accept-Language": "en"})
        chinese = client.get("/api/v1/workflows/wf-missing", headers={"Accept-Language": "zh-CN"})
    assert english.status_code == chinese.status_code == 404
    body = english.json()
    assert body["correlationId"].startswith("corr-")
    assert body["retryable"] is False
    assert english.json()["message"] != chinese.json()["message"]


def test_created_response_and_pagination_over_http(
    live: tuple[_LiveServer, ControlHarness, dict]
) -> None:
    server, _harness, seeded = live
    with server.client() as client:
        listed = client.get("/api/v1/projects", params={"limit": 1, "offset": 0}).json()
        assert listed["total"] >= 1
        assert len(listed["items"]) == 1
        detail = client.get(f"/api/v1/projects/{seeded['id']}")
        actions = client.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions").json()
    assert detail.status_code == 200
    assert detail.json()["id"] == seeded["id"]
    assert actions["allowed"] and actions["unavailable"]
    # Grouped names (``governance.model.register``, ``environment.test_connection``)
    # are the ones S5IF-FLD12 specifies; the invariant that holds for every action is
    # that it is ``group.verb``, not that it has exactly one dot.
    assert all(item["action"].count(".") >= 1 for item in actions["allowed"])


# --- idempotency (CP-API-003) ----------------------------------------------


def test_same_key_and_body_replays_the_first_response(
    live: tuple[_LiveServer, ControlHarness, dict]
) -> None:
    server, _harness, _seeded = live
    payload = {
        "name": "idempotent project",
        "ownerName": "ada",
        "deliveryMode": "A",
        "targets": [{"environmentKey": "dev", "promotionOrder": 0, "isDesignReference": True}],
    }
    with server.client() as client:
        key = {"Idempotency-Key": "live-create-1"}
        first = client.post("/api/v1/projects", json=payload, headers=key)
        replay = client.post("/api/v1/projects", json=payload, headers=key)
        assert first.status_code == replay.status_code == 201
        assert first.json() == replay.json()
        assert replay.headers.get("x-idempotent-replay") == "true"
        conflict = client.post(
            "/api/v1/projects", json={**payload, "name": "other name"}, headers=key
        )
    assert conflict.status_code == 422
    assert conflict.json()["errorCode"] == "IDEMPOTENCY_CONFLICT"


def test_replay_survives_a_second_client_instance(
    live: tuple[_LiveServer, ControlHarness, dict]
) -> None:
    """The record is in the database, not in the process that wrote it."""
    server, _harness, _seeded = live
    payload = {"name": "second client", "ownerName": "ada", "deliveryMode": "A",
               "targets": [{"environmentKey": "dev", "promotionOrder": 0, "isDesignReference": True}]}
    with server.client() as first:
        created = first.post("/api/v1/projects", json=payload, headers={"Idempotency-Key": "live-2"})
    with server.client() as second:
        replayed = second.post("/api/v1/projects", json=payload, headers={"Idempotency-Key": "live-2"})
    assert created.json() == replayed.json()


# --- SSE over the wire (CP-EVT-001 … CP-EVT-005) ----------------------------


def test_sse_replays_the_backlog_over_http(live: tuple[_LiveServer, ControlHarness, dict]) -> None:
    server, _harness, seeded = live
    workflow_id = seeded["workflowId"]
    with server.client() as client:
        client.post(f"/api/v1/workflows/{workflow_id}/pause")
        client.post(f"/api/v1/workflows/{workflow_id}/resume")
        with client.stream("GET", f"/api/v1/workflows/{workflow_id}/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = _frames(response.iter_lines(), 3)
    assert [frame["id"] for frame in frames] == [1, 2, 3]
    assert [frame["event"] for frame in frames] == [
        "workflow.started",
        "workflow.paused",
        "workflow.resumed",
    ]
    assert all(frame["data"]["workflowId"] == workflow_id for frame in frames)


def test_sse_delivers_a_live_event_within_two_seconds(
    live: tuple[_LiveServer, ControlHarness, dict]
) -> None:
    """CP-EVT-005: one process pauses, this socket receives it."""
    server, _harness, seeded = live
    workflow_id = seeded["workflowId"]
    with (
        server.client() as reader,
        server.client() as writer,
        reader.stream("GET", f"/api/v1/workflows/{workflow_id}/events") as stream,
    ):
        lines = stream.iter_lines()
        seen = _frames(lines, 1)  # the created event
        started = time.monotonic()
        writer.post(f"/api/v1/workflows/{workflow_id}/pause")
        live_frames = _frames(lines, 1, timeout=5.0)
        elapsed = time.monotonic() - started
    assert seen[0]["event"] == "workflow.started"
    assert live_frames[0]["event"] == "workflow.paused"
    assert elapsed < 2.0, f"fan-out took {elapsed:.2f}s, budget is 2s"


def test_sse_resumes_after_a_disconnect(live: tuple[_LiveServer, ControlHarness, dict]) -> None:
    server, _harness, seeded = live
    workflow_id = seeded["workflowId"]
    with server.client() as client:
        with client.stream("GET", f"/api/v1/workflows/{workflow_id}/events") as stream:
            lines = stream.iter_lines()
            first = _frames(lines, 1)
        client.post(f"/api/v1/workflows/{workflow_id}/pause")
        client.post(f"/api/v1/workflows/{workflow_id}/resume")
        with client.stream(
            "GET",
            f"/api/v1/workflows/{workflow_id}/events",
            headers={"Last-Event-ID": str(first[-1]["id"])},
        ) as stream:
            resumed = _frames(stream.iter_lines(), 2)
    assert [frame["id"] for frame in resumed] == [first[-1]["id"] + 1, first[-1]["id"] + 2]


def test_sse_sends_heartbeats_while_idle(live: tuple[_LiveServer, ControlHarness, dict]) -> None:
    server, harness, seeded = live
    harness.runtime.settings.sse_heartbeat_seconds = 0.3
    with (
        server.client() as client,
        client.stream("GET", f"/api/v1/workflows/{seeded['workflowId']}/events") as stream,
    ):
        comments: list[str] = []
        for line in stream.iter_lines():
            if line.startswith(":"):
                comments.append(line)
            if len(comments) >= 2:
                break
    assert comments[0] == ": connected"
    assert comments[1] == ": keep-alive"
