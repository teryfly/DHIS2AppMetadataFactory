"""PostgreSQL LISTEN side of the fan-out (CP-EVT-005).

One long-lived connection per process.  ``conn.notifies(timeout=...)`` ends the
iteration after a quiet interval, which doubles as the safety-net poll: a lost
notification delays delivery by at most ``poll_seconds`` instead of losing it,
because the rows are always read back from ``cp.event_stream_records``.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

from factory_control.config import SSE_CHANNEL_PATTERN
from factory_control.events.bus import EventBus

_logger = logging.getLogger("factory_control.events.listener")


def async_dsn_from_sqlalchemy_url(url: str) -> str:
    """``postgresql+psycopg://`` is not a libpq scheme; strip the driver.

    Rebuilt by hand rather than with ``urlunsplit``: that helper drops the ``//``
    whenever the authority is empty, and the development and test DSNs are exactly
    ``postgresql+psycopg:///dbname?host=/var/run/postgresql``.  The result was
    ``postgresql:/dbname?host=...``, which libpq reads as a keyword/value string and
    rejects with ``invalid connection option`` — so an empty host must stay written as
    an empty authority.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.split("+")[0]
    if scheme == "postgres":
        scheme = "postgresql"
    tail = f"?{parts.query}" if parts.query else ""
    if parts.fragment:
        tail += f"#{parts.fragment}"
    return f"{scheme}://{parts.netloc}{parts.path}{tail}"


def _quoted(channel: str) -> str:
    """Channel names are fixed by configuration, but quote defensively anyway.

    The pattern is the one :meth:`factory_control.config.Settings.validate_runtime` already
    checks at start-up, so the two cannot drift into accepting different names.
    """
    if not SSE_CHANNEL_PATTERN.match(channel):
        raise ValueError(f"unsafe NOTIFY channel name: {channel!r}")
    return f'"{channel}"'


class NotificationListener:
    def __init__(
        self,
        *,
        dsn: str,
        channel: str,
        bus: EventBus,
        poll_seconds: float = 15.0,
        reconnect_seconds: float = 1.0,
        max_reconnect_seconds: float = 30.0,
    ) -> None:
        # Validated on construction, not inside ``run()``: the API process starts ``run()`` with
        # ``loop.create_task`` and nobody awaits that task, so a bad channel name used to raise a
        # ValueError the event loop swallowed -- the LISTEN connection simply never existed, and
        # pushes went out only on the quiet-interval poll while the log stayed clean.
        self._quoted = _quoted(channel)
        self._dsn = dsn
        self._channel = channel
        self._bus = bus
        self._poll_seconds = poll_seconds
        self._reconnect_seconds = reconnect_seconds
        self._max_reconnect_seconds = max_reconnect_seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Serve notifications until ``stop`` is set.  Never raises on I/O failure.

        The wait is taken in short slices so a long ``poll_seconds`` does not turn
        into an equal-length shutdown: ``stop`` is noticed within a slice.
        """
        import psycopg

        quoted = self._quoted
        backoff = self._reconnect_seconds
        slice_seconds = min(self._poll_seconds, 1.0)
        while not stop.is_set():
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._dsn, autocommit=True, application_name="factory-sse"
                ) as conn:
                    await conn.execute(f"LISTEN {quoted}")
                    _logger.info("listening on channel %s", self._channel)
                    backoff = self._reconnect_seconds
                    quiet = 0.0
                    while not stop.is_set():
                        got_one = False
                        async for notification in conn.notifies(timeout=slice_seconds):
                            got_one = True
                            self._dispatch(notification.payload)
                            if stop.is_set():
                                break
                        if stop.is_set():
                            break
                        quiet = 0.0 if got_one else quiet + slice_seconds
                        if quiet >= self._poll_seconds:
                            # quiet interval elapsed: re-wake everybody as a safety net
                            self._bus.notify_all()
                            quiet = 0.0
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                _logger.warning("notification listener failed: %s; retrying in %.1fs", exc, backoff)
                await asyncio.sleep(min(backoff, self._max_reconnect_seconds))
                backoff = min(backoff * 2, self._max_reconnect_seconds)

    def _dispatch(self, payload: str | None) -> None:
        if not payload or ":" not in payload:
            self._bus.notify_all()
            return
        workflow_id = payload.split(":", 1)[0]
        self._bus.notify(workflow_id)


__all__ = ["NotificationListener", "async_dsn_from_sqlalchemy_url"]
