"""Per-instance fan-out (CP-EVT-005).

Every ``factory-api`` process keeps one listener connection; when PostgreSQL
delivers a notification, the listener wakes the local subscribers of that
workflow.  Subscribers are deliberately dumb — they only learn "something new
exists" and then read the authoritative rows from ``cp.event_stream_records``.

Events are therefore never carried in memory between instances, so a dropped
notification cannot lose data: the periodic poll in
:mod:`factory_control.events.listener` re-wakes everyone as a safety net.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

#: A wake-up carries no payload; ``None`` means "poll the database".
_SENTINEL = object()


@dataclass
class Subscription:
    workflow_id: str
    bus: EventBus
    _queue: asyncio.Queue[object] = field(default_factory=asyncio.Queue, repr=False)
    _closed: bool = False

    async def wait(self, timeout: float) -> bool:
        """Wait for a wake-up.  Returns ``True`` if woken, ``False`` on timeout."""
        if self._closed:
            return False
        try:
            await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    def wake(self) -> None:
        # Queue is unbounded by construction; coalescing is safe because the
        # reader re-queries by sequence, not by counting wake-ups.
        if not self._closed:
            self._queue.put_nowait(_SENTINEL)

    def close(self) -> None:
        self._closed = True
        self.bus.unsubscribe(self)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscription]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the serving loop so sync code can wake subscribers safely."""
        self._loop = loop

    def subscribe(self, workflow_id: str) -> Subscription:
        sub = Subscription(workflow_id=workflow_id, bus=self)
        self._subscribers.setdefault(workflow_id, []).append(sub)
        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        bucket = self._subscribers.get(subscription.workflow_id)
        if not bucket:
            return
        if subscription in bucket:
            bucket.remove(subscription)
        if not bucket:
            self._subscribers.pop(subscription.workflow_id, None)

    def notify(self, workflow_id: str) -> None:
        """Wake this instance's subscribers for one workflow."""
        for sub in list(self._subscribers.get(workflow_id, ())):
            if self._loop is not None and self._loop.is_running() and self._loop is not _current_loop():
                self._loop.call_soon_threadsafe(sub.wake)
            else:
                sub.wake()

    def notify_all(self) -> None:
        for workflow_id in list(self._subscribers):
            self.notify(workflow_id)

    def subscriber_count(self) -> int:
        return sum(len(bucket) for bucket in self._subscribers.values())

    def watched_workflows(self) -> int:
        return len(self._subscribers)


def _current_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


__all__ = ["EventBus", "Subscription"]
