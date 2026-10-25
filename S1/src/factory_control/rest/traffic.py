"""Which status each operation actually answers with.

CP-API-010 ② asks the published document to describe the failures a caller has to handle.
A ``responses=`` list nobody can check is how that promise rots: a service starts raising
a code the route never declared, the middleware turns it into 410, and a generated client
that only reads the contract has no branch for it.

So the running application records its own behaviour — every response of 400 or above,
keyed by the same ``"METHOD /route/template"`` scope the idempotency guard uses
(CP-API-003, :func:`factory_control.rest.failure.scope_of`) — and
``tests/api/test_declared_errors.py`` asserts each observed pair is declared in that
operation's ``responses``.

The log is bounded by the number of operations times the number of statuses, so it costs
a few hundred small strings and no request path reads the database for it.  A 500 is
deliberately absent: ``ServerErrorMiddleware`` converts an unhandled exception *outside*
every user middleware, so 500 is never a response this layer can see — and CP-API-008
already says an S1 crash carries no contract, only the envelope.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from factory_control.rest.failure import scope_of

#: Below this a response is an answer, not a failure, and is not of interest here.
FAILURE_FLOOR = 400


class TrafficLog:
    """Observed failure statuses per operation endpoint key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._observed: dict[str, set[int]] = {}

    def record(self, endpoint: str, status: int) -> None:
        with self._lock:
            self._observed.setdefault(endpoint, set()).add(status)

    def observed(self) -> dict[str, tuple[int, ...]]:
        """Every failure status seen since the last :meth:`reset`, sorted."""
        with self._lock:
            return {key: tuple(sorted(statuses)) for key, statuses in self._observed.items()}

    def reset(self) -> None:
        with self._lock:
            self._observed.clear()


#: One log per process: the API tests assert over a whole session's traffic.
log = TrafficLog()


class TrafficLogMiddleware(BaseHTTPMiddleware):
    """Note the status of every failure that reaches a route.

    Installed outermost so a refusal raised by a handler *or* by the idempotency and
    session middleware below is recorded.  A request that never matched a route has no
    operation to attribute its status to — the 400 ``OPERATOR_REQUIRED`` refused before
    routing belongs to every state-changing operation at once, which is why 400 is one of
    the universal codes rather than something this log has to prove.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        status = int(response.status_code)
        if status >= FAILURE_FLOOR and _route_template(request) is not None:
            log.record(scope_of(request), status)
        return response


def _route_template(request: Request) -> str | None:
    """The path template of the matched route, or None when routing never happened."""
    route: Any = request.scope.get("route")
    template = getattr(route, "path", None)
    return str(template) if template else None


__all__ = ["FAILURE_FLOOR", "TrafficLog", "TrafficLogMiddleware", "log"]
