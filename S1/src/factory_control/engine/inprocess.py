"""In-process engine (CP-RT-003).

Runs a stage on a worker thread so the API request that triggered it returns
immediately (CP-NFR-05), while state remains authoritative in ``cp`` — which is
what allows a crash to be recovered by reading the last persisted stage status.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from factory_control.engine.base import EngineStatus
from factory_control.foundation import get_logger

_logger = get_logger(__name__)


class InProcessEngine:
    """Thread-pool executor behind the same interface as the Temporal engine."""

    name = "inprocess"

    def __init__(self, *, runtime: Any, max_workers: int = 4) -> None:
        self._runtime = runtime
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cp-stage")
        self._futures: dict[str, Future[dict[str, Any]]] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start_stage(
        self,
        stage_id: str,
        *,
        operator: str,
        rerun: bool = False,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Claim the stage in this thread, execute it on the pool.

        The claim is synchronous for two reasons: the REST layer answers 202 with the
        step run's id (S5IF-G06), and a stage that is not in a startable state has to
        answer the caller with 409 rather than fail somewhere a caller cannot see.
        """
        from factory_control.services.steps import claim_stage, run_claimed

        claim = claim_stage(self._runtime, stage_id, operator=operator, rerun=rerun)

        def run() -> dict[str, Any]:
            try:
                return run_claimed(self._runtime, claim, operator=operator, language=language)
            except Exception as exc:  # noqa: BLE001 - recorded, then surfaced on query
                _logger.exception("in-process stage %s failed", stage_id)
                return {"stageId": stage_id, "error": type(exc).__name__, "detail": str(exc)[:500]}

        future = self._pool.submit(run)
        with self._lock:
            self._futures[stage_id] = future
        return {**claim, "engine": self.name}

    def start_stage_sync(
        self,
        stage_id: str,
        *,
        operator: str,
        rerun: bool = False,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Same work, but the caller waits — used by tests and the CLI."""
        from factory_control.services.steps import start_stage

        return start_stage(
            self._runtime, stage_id, operator=operator, rerun=rerun, language=language
        )

    def request_cancel(self, workflow_id: str) -> None:
        """Flip the local cancellation flag; long tasks check it at safe points."""
        with self._lock:
            flag = self._cancel_flags.setdefault(workflow_id, threading.Event())
        flag.set()

    def is_cancel_requested(self, workflow_id: str) -> bool:
        with self._lock:
            flag = self._cancel_flags.get(workflow_id)
        return bool(flag and flag.is_set())

    def clear_cancel(self, workflow_id: str) -> None:
        with self._lock:
            flag = self._cancel_flags.pop(workflow_id, None)
        if flag is not None:
            flag.set()

    def wait(self, stage_id: str, *, timeout: float | None = None) -> dict[str, Any] | None:
        with self._lock:
            future = self._futures.get(stage_id)
        if future is None:
            return None
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            return {"stageId": stage_id, "pending": True}

    def status(self) -> EngineStatus:
        with self._lock:
            pending = sum(1 for f in self._futures.values() if not f.done())
        return EngineStatus(
            name=self.name,
            durable=False,
            detail={
                "pendingStages": pending,
                "note": "state is durable in cp; only the thread hand-off is not",
            },
        )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=False)

    def submit(self, work: Callable[[], dict[str, Any]]) -> Future[dict[str, Any]]:
        """Escape hatch used by the CLI to run a notification sweep off-request."""
        return self._pool.submit(work)


__all__ = ["InProcessEngine"]
