"""The SSE generator and the LISTEN side of the fan-out (CP-EVT-001 … CP-EVT-007).

The wire-level behaviour is covered by ``test_live_http.py``; this module drives
:func:`stream_workflow_events` and :class:`NotificationListener` directly so the
frames that no client can force — a reset, a heartbeat, a coalesced wake-up — can
be produced on purpose.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from factory_control.events.listener import NotificationListener, async_dsn_from_sqlalchemy_url
from factory_control.events.sse import _reset_frame, parse_last_event_id, stream_workflow_events
from factory_control.services.workflow import pause_workflow, resume_workflow
from factory_control.testing.harness import ControlHarness

pytestmark = pytest.mark.api

FRAME_TIMEOUT = 5.0


async def _emit(harness: ControlHarness, workflow_id: str, count: int) -> None:
    """Append a known number of events through the service layer."""
    for index in range(count):
        service = pause_workflow if index % 2 == 0 else resume_workflow
        await asyncio.to_thread(service, harness.runtime, workflow_id, operator="ada")


def _stream(harness: ControlHarness, workflow_id: str, *, after: int = 0, heartbeat: float = 0.2) -> Any:
    return stream_workflow_events(
        session_factory=harness.runtime.session_factory,
        bus=harness.runtime.bus,
        workflow_id=workflow_id,
        after_sequence=after,
        heartbeat_seconds=heartbeat,
    )


async def _next_frame(stream: Any) -> str:
    return await asyncio.wait_for(stream.__anext__(), timeout=FRAME_TIMEOUT)


def _data_of(frame: str) -> dict[str, Any]:
    line = next(part for part in frame.splitlines() if part.startswith("data: "))
    return json.loads(line.removeprefix("data: "))


def test_stream_emits_the_backlog_in_order_then_heartbeats(
    harness: ControlHarness, seeded: dict[str, Any]
) -> None:
    workflow_id = str(seeded["workflowId"])

    async def scenario() -> list[str]:
        await _emit(harness, workflow_id, 2)
        stream = _stream(harness, workflow_id)
        try:
            return [await _next_frame(stream) for _ in range(3)]
        finally:
            await stream.aclose()

    frames = asyncio.run(scenario())
    sequences = [int(frame.splitlines()[0].removeprefix("id: ")) for frame in frames]
    assert sequences == [1, 2, 3]  # one per state change, in order (CP-EVT-001)
    bodies = [_data_of(frame) for frame in frames]
    assert [body["eventType"] for body in bodies] == [
        "workflow.started",
        "workflow.paused",
        "workflow.resumed",
    ]
    assert all(body["workflowId"] == workflow_id for body in bodies)
    assert all(body["occurredAt"].endswith("Z") for body in bodies)


def test_stream_resumes_from_a_cursor_without_repeating(
    harness: ControlHarness, seeded: dict[str, Any]
) -> None:
    workflow_id = str(seeded["workflowId"])

    async def scenario() -> tuple[int, list[int]]:
        await _emit(harness, workflow_id, 3)
        first = _data_of(await _next_frame(_stream(harness, workflow_id)))
        cursor = int(first["sequence"])
        resumed = []
        stream = _stream(harness, workflow_id, after=cursor)
        for _ in range(2):
            resumed.append(int(_data_of(await _next_frame(stream))["sequence"]))
        await stream.aclose()
        return cursor, resumed

    cursor, resumed = asyncio.run(scenario())
    assert cursor == 1
    assert resumed == [2, 3]


def test_stream_reports_a_reset_when_the_cursor_is_gone(
    harness: ControlHarness, seeded: dict[str, Any]
) -> None:
    """CP-EVT-004: swept-away events cannot be replayed, so the client must refresh."""
    from factory_control.events.event_log import oldest_retained_sequence, purge_expired

    workflow_id = str(seeded["workflowId"])
    asyncio.run(_emit(harness, workflow_id, 3))
    with harness.runtime.transaction(operator="system") as ctx:
        ctx.session.execute(
            text(
                "UPDATE cp.event_stream_records SET expires_at = :expires "
                "WHERE workflow_id = :wf AND sequence <= 2"
            ),
            {"expires": ctx.clock.now() - timedelta(hours=1), "wf": workflow_id},
        )
    with harness.runtime.transaction(operator="system") as ctx:
        assert purge_expired(ctx.session, now=ctx.clock.now()) >= 2
    with harness.runtime.transaction(operator="system") as ctx:
        oldest = oldest_retained_sequence(ctx.session, workflow_id)

    async def scenario() -> str:
        stream = _stream(harness, workflow_id, after=0)
        try:
            return await _next_frame(stream)
        finally:
            await stream.aclose()

    frame = asyncio.run(scenario())
    assert "event: stream.reset" in frame
    payload = _data_of(frame)["payload"]
    assert payload["reason"] == "REQUESTED_SEQUENCE_EXPIRED"
    assert payload["requestedAfter"] == 0
    assert payload["oldestRetained"] == oldest
    assert payload["action"] == "FULL_REFRESH_REQUIRED"


def test_a_workflow_with_no_events_yields_a_quiet_socket(harness: ControlHarness) -> None:
    """Nothing retained is not an error, and not a reset either (CP-EVT-004)."""

    async def scenario() -> str:
        stream = _stream(harness, "wf-no-events-yet", heartbeat=0.1)
        try:
            return await _next_frame(stream)
        finally:
            await stream.aclose()

    assert asyncio.run(scenario()).startswith(":")


def test_reset_frame_text_is_self_contained() -> None:
    frame = _reset_frame("wf-1", requested=3, oldest=9)
    assert frame.startswith("event: stream.reset\n")
    body = json.loads(frame.splitlines()[1].removeprefix("data: "))
    assert body["sequence"] == 9
    assert body["payload"]["requestedAfter"] == 3


# --- LISTEN / NOTIFY --------------------------------------------------------


def test_notify_wakes_only_the_subscribed_workflow(harness: ControlHarness) -> None:
    """A notification for another workflow must not wake this subscriber."""

    async def scenario() -> tuple[bool, bool]:
        loop = asyncio.get_running_loop()
        bus = harness.runtime.bus
        bus.attach_loop(loop)
        listener = NotificationListener(
            dsn=async_dsn_from_sqlalchemy_url(harness.url),
            channel="factory_cp_events",
            bus=bus,
            poll_seconds=30.0,
        )
        stop = asyncio.Event()
        task = loop.create_task(listener.run(stop))
        await asyncio.sleep(0.3)
        watched = bus.subscribe("wf-watched")
        ignored = bus.subscribe("wf-other")
        try:
            listener._dispatch("wf-watched:7")  # noqa: SLF001 - the dispatch is the unit here
            woke = await watched.wait(1.0)
            other = await ignored.wait(0.3)
            return woke, other
        finally:
            watched.close()
            ignored.close()
            stop.set()
            await asyncio.wait_for(task, 5.0)

    woke, other = asyncio.run(scenario())
    assert woke and not other


def test_malformed_payload_wakes_every_subscriber(harness: ControlHarness) -> None:
    """A truncated notification degrades to a poll for all, never to a lost event."""
    from factory_control.events.bus import EventBus

    async def scenario() -> bool:
        loop = asyncio.get_running_loop()
        bus = EventBus()
        bus.attach_loop(loop)
        listener = NotificationListener(dsn="unused", channel="factory_cp_events", bus=bus)
        subscription = bus.subscribe("wf-any")
        try:
            listener._dispatch(None)  # noqa: SLF001
            return await subscription.wait(1.0)
        finally:
            subscription.close()

    assert asyncio.run(scenario())


def test_quiet_interval_falls_back_to_waking_everyone(harness: ControlHarness) -> None:
    """A lost NOTIFY delays delivery by one poll instead of losing the event."""

    async def scenario() -> bool:
        loop = asyncio.get_running_loop()
        bus = harness.runtime.bus
        bus.attach_loop(loop)
        listener = NotificationListener(
            dsn=async_dsn_from_sqlalchemy_url(harness.url),
            channel="factory_sse_probe_unused",  # nobody notifies this channel
            bus=bus,
            poll_seconds=0.2,
        )
        stop = asyncio.Event()
        task = loop.create_task(listener.run(stop))
        subscription = bus.subscribe("wf-quiet")
        try:
            return await subscription.wait(5.0)
        finally:
            subscription.close()
            stop.set()
            await asyncio.wait_for(task, 5.0)

    assert asyncio.run(scenario())


def test_listener_reconnects_after_a_connection_failure(harness: ControlHarness) -> None:
    """The loop survives a lost connection (CP-EVT-005) and keeps polling."""

    async def scenario() -> bool:
        loop = asyncio.get_running_loop()
        bus = harness.runtime.bus
        bus.attach_loop(loop)
        listener = NotificationListener(
            dsn=async_dsn_from_sqlalchemy_url(harness.url).replace(
                "postgres", "not-a-scheme", 1
            ),  # cannot connect at all
            channel="factory_cp_events",
            bus=bus,
            poll_seconds=0.1,
            reconnect_seconds=0.05,
            max_reconnect_seconds=0.05,
        )
        stop = asyncio.Event()
        task = loop.create_task(listener.run(stop))
        await asyncio.sleep(0.4)
        healthy = not task.done()  # still retrying, never raised
        # point it at the real database and the fan-out resumes
        listener._dsn = async_dsn_from_sqlalchemy_url(harness.url)  # noqa: SLF001
        await asyncio.sleep(0.4)
        subscription = bus.subscribe("wf-reconnect")
        try:
            woke = await subscription.wait(2.0)  # safety-net poll or a real NOTIFY
        finally:
            subscription.close()
            stop.set()
            await asyncio.wait_for(task, 5.0)
        return healthy and woke

    assert asyncio.run(scenario())


# --- cursor parsing and channel safety --------------------------------------


def test_last_event_id_beats_the_query_parameter() -> None:
    assert parse_last_event_id("7", "3") == 7
    assert parse_last_event_id(None, "3") == 3
    assert parse_last_event_id("", "3") == 3
    assert parse_last_event_id(None, None) == 0
    assert parse_last_event_id("abc", "3") == 3
    assert parse_last_event_id("-5", None) == 0
    assert parse_last_event_id(" 12 ", None) == 12


def test_channel_name_is_validated_before_it_reaches_the_sql() -> None:
    from factory_control.events.listener import _quoted

    assert _quoted("factory_cp_events") == '"factory_cp_events"'
    for bad in ('a"b', "a b", "drop table", ""):
        with pytest.raises(ValueError):
            _quoted(bad)


def test_async_dsn_strips_the_sqlalchemy_driver() -> None:
    assert async_dsn_from_sqlalchemy_url(
        "postgresql+psycopg://user@/db?host=/var/run/postgresql"
    ) == "postgresql://user@/db?host=/var/run/postgresql"
    assert async_dsn_from_sqlalchemy_url("postgres://user@localhost/db").startswith("postgresql://")


def test_every_declared_event_type_is_actually_emitted() -> None:
    """CP-EVT-001 / 19.2.3: declaring an event type is not the same as sending one.

    Seven types were registered in the catalogue and never appended anywhere, so a client
    that subscribed for ``execution.completed`` waited forever on a run that had finished.
    Scanning the source is the only check that catches the whole class at once: an event
    with no producer is invisible to every test that does not happen to exercise that path.
    """
    from pathlib import Path

    from factory_control.domain.enums import EVENT_TYPES

    source_root = Path(__file__).resolve().parents[2] / "src" / "factory_control"
    # the two modules that *declare* the catalogue are not evidence that anything sends it
    declaration = {"enums.py", "event_log.py"}
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in source_root.rglob("*.py")
        if path.name not in declaration
    )
    # every name the code appends arrives as a literal, whether it is spelled
    # ``event_type="stage.passed"`` or passed through a helper that takes the type
    emitted = {event for event in EVENT_TYPES if f'"{event}"' in text}
    # the two progress events are assembled from the reporter's kind
    # (``f"{self.kind}.progress"`` in progress.py), so they never appear as a literal; the
    # kinds that exist are the proof, and they are checked by the progress tests
    emitted |= {"agent.progress", "execution.progress"}

    silent = sorted(event for event in EVENT_TYPES if event not in emitted)
    assert silent == [], (
        f"these event types are declared but never appended: {silent}"
    )


def test_stream_resets_when_every_event_of_the_workflow_has_been_purged(
    harness: ControlHarness, seeded: dict[str, Any]
) -> None:
    """FR-IF-005: a cursor into a fully swept stream still has to be answered.

    The first reset rule only fired while at least one event survived.  Once the last row
    aged out, a reconnecting client was handed nothing at all -- and S5 waits on
    ``sequence + 1``, which never comes, so its screen quietly keeps a state the backend
    has long since moved past.
    """
    from sqlalchemy import text

    from factory_control.events.event_log import purge_expired

    workflow_id = str(seeded["workflowId"])
    asyncio.run(_emit(harness, workflow_id, 3))
    with harness.runtime.transaction(operator="system") as ctx:
        ctx.session.execute(
            text("UPDATE cp.event_stream_records SET expires_at = :expires WHERE workflow_id = :wf"),
            {"expires": ctx.clock.now() - timedelta(hours=1), "wf": workflow_id},
        )
    with harness.runtime.transaction(operator="system") as ctx:
        assert purge_expired(ctx.session, now=ctx.clock.now()) >= 3

    async def scenario() -> str:
        stream = _stream(harness, workflow_id, after=2)
        try:
            return await _next_frame(stream)
        finally:
            await stream.aclose()

    frame = asyncio.run(scenario())
    assert "event: stream.reset" in frame, frame
    assert "FULL_REFRESH_REQUIRED" in frame, frame
