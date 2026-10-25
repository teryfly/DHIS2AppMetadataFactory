"""SSE delivery (CP-EVT-003 / 004).

``GET /workflows/{id}/events`` streams frames in the 总体 SRS 19.2.3 shape.  A
client that reconnects sends ``Last-Event-ID`` (or ``?after=``); the stream
replays everything newer from the database, so a reconnect never trusts memory.
If the requested sequence is older than what is still retained, ``stream.reset``
is emitted instead, which tells the client to refresh over REST (CP-EVT-004).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
from sqlalchemy.orm import Session

from factory_control.events.bus import EventBus
from factory_control.events.event_log import Event, oldest_retained_sequence, read_after

_logger = logging.getLogger("factory_control.events.sse")

_RESET_FRAME_EVENT = "stream.reset"


def _open(session_factory: Callable[[], Session]) -> Session:
    return session_factory()


async def _fetch(
    session_factory: Callable[[], Session], workflow_id: str, after: int
) -> tuple[int | None, list[Event]]:
    """Read the oldest retained sequence and the backlog, off the event loop."""

    def _sync() -> tuple[int | None, list[Event]]:
        session = _open(session_factory)
        try:
            return oldest_retained_sequence(session, workflow_id), read_after(
                session, workflow_id, after
            )
        finally:
            session.close()

    return await anyio.to_thread.run_sync(_sync)


def _reset_frame(workflow_id: str, requested: int, oldest: int) -> str:
    import json

    body = {
        "eventType": _RESET_FRAME_EVENT,
        "workflowId": workflow_id,
        "sequence": oldest,
        "payload": {
            "reason": "REQUESTED_SEQUENCE_EXPIRED",
            "requestedAfter": requested,
            "oldestRetained": oldest,
            "action": "FULL_REFRESH_REQUIRED",
        },
    }
    return f"event: {_RESET_FRAME_EVENT}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


async def stream_workflow_events(
    *,
    session_factory: Callable[[], Session],
    bus: EventBus,
    workflow_id: str,
    after_sequence: int = 0,
    heartbeat_seconds: float = 20.0,
) -> AsyncIterator[str]:
    """Yield SSE frames for one workflow until the client disconnects."""
    oldest, backlog = await _fetch(session_factory, workflow_id, after_sequence)

    if backlog and oldest is not None and after_sequence < oldest - 1:
        # The client asked for something that has already been swept away.
        yield _reset_frame(workflow_id, after_sequence, oldest)
        return

    if not backlog and after_sequence > 0 and oldest is None:
        # Everything this workflow ever emitted has been purged, so there is no frame that
        # can continue the client's sequence: the retention rule (FR-IF-005) says a request
        # older than the retention window is answered with stream.reset and a full refresh,
        # and silence is the one answer that leaves S5 showing a stale screen forever -- it
        # waits for sequence+1, which will never arrive.
        yield _reset_frame(workflow_id, after_sequence, after_sequence)
        return

    last = after_sequence
    for event in backlog:
        last = event.sequence
        yield event.to_sse()

    subscription = bus.subscribe(workflow_id)
    try:
        while True:
            woken = await subscription.wait(heartbeat_seconds)
            if not woken:
                yield ": keep-alive\n\n"
                continue
            _, rows = await _fetch(session_factory, workflow_id, last)
            if not rows:
                # A coalesced or spurious wake-up: stay quiet, keep the socket alive.
                yield ": keep-alive\n\n"
                continue
            for event in rows:
                last = event.sequence
                yield event.to_sse()
    finally:
        subscription.close()


def parse_last_event_id(header_value: str | None, query_value: Any) -> int:
    """``Last-Event-ID`` wins over ``?after=``; both must be non-negative ints."""
    for raw in (header_value, query_value):
        if raw is None or raw == "":
            continue
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        return max(value, 0)
    return 0


__all__ = ["parse_last_event_id", "stream_workflow_events"]
