"""Process-wide dependency injection points for S2.

S2's services never import a collaborator directly: they ask :func:`get_dhis2`.
The default answer is the **real** S4 adapter, because a deployed S2 must not be able
to mistake an in-memory double for DHIS2 — an unallocated UID pool and a missed
ownership conflict are both silent data corruption.  The stub in ``s4.stub`` is what a
test, or :mod:`factory_blueprint.testing`'s standalone Fake, installs on purpose.
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any

from factory_foundation.api import Clock, FixedClock, SystemClock, get_logger

from factory_blueprint.s4.protocol import ReadonlyDhis2

_logger = get_logger(__name__)

_lock = RLock()
_dhis2: ReadonlyDhis2 | None = None
#: the lazily built default, kept as an instance so a caller can compare identity
_default: ReadonlyDhis2 | None = None


def set_dhis2(implementation: ReadonlyDhis2 | None) -> None:
    """Install the read-only DHIS2 implementation (a stub or a test double).

    Passing ``None`` restores the default, which is the real S4 adapter.
    """
    global _dhis2
    with _lock:
        _dhis2 = implementation


def get_dhis2() -> ReadonlyDhis2:
    """Return the installed implementation, defaulting to the real S4 adapter.

    :class:`~factory_blueprint.s4.adapter.S4Adapter` imports ``factory_dhis2`` only when
    a call is made, so S2 stays importable where S4 is not installed; the call then
    fails with ``INTERNAL_ERROR`` naming the missing package rather than answering from
    invented data.
    """
    global _default
    with _lock:
        if _dhis2 is not None:
            return _dhis2
        if _default is None:
            from factory_blueprint.s4.adapter import S4Adapter

            _default = S4Adapter()
            _logger.info("read-only DHIS2 calls go to the real S4 subsystem")
        return _default


def reset_providers() -> None:
    """Return to the default state; used between tests."""
    global _dhis2, _default
    with _lock:
        _dhis2 = None
        _default = None


def clock_for(build_context: Any | None) -> Clock:
    """Timestamps for bookkeeping rows.

    Deterministic code paths (artifact content) take their time from the build
    context; only metadata columns may use the system clock, and never inside a
    compiler.
    """
    built_at = getattr(build_context, "built_at", None)
    if isinstance(built_at, datetime):
        return FixedClock(built_at.astimezone(UTC) if built_at.tzinfo else built_at.replace(tzinfo=UTC))
    return SystemClock()
