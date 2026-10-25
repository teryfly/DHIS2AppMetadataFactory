"""S1 public entry points (SRS 8.2, CP-RT-001 … CP-RT-005).

S1 is the topmost orchestrator: it exposes no library API to S0–S4 (the dependency
rules forbid them from calling S1).  What it does export is the runtime surface —
the ASGI application, the Temporal worker entry point, its migration config and its
``initialize()`` — plus the ``ProgressReporter`` implementation.

``factory-api`` and ``factory-worker`` are the two processes built from this module.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
from contextlib import suppress
from typing import Any

from factory_control.bootstrap import initialize as initialize_s1
from factory_control.config import Settings, get_settings
from factory_control.db import CP_MIGRATION_CONFIG
from factory_control.foundation import (
    CancellationToken,
    ProgressReporter,
    configure_logging,
    configure_object_store,
    get_object_store,
)
from factory_control.runtime import Runtime, build_runtime

#: Exported for the assembly runner, per 00 doc 6.10.
MIGRATION_CONFIG = CP_MIGRATION_CONFIG

__all__ = [
    "MIGRATION_CONFIG",
    "api_main",
    "attach_event_listener",
    "build_asgi_app",
    "create_app",
    "initialize",
    "prepare_process",
    "settings_for_cli",
    "worker_main",
]


def initialize(*, session: Any = None) -> dict[str, int]:
    """Register S1's artifact types, audit event types, error codes and ID prefixes."""
    return initialize_s1(session=session)


def prepare_process(settings: Settings | None = None) -> Runtime:
    """Run the full start-up order and install the process wiring."""
    from factory_control import api_service
    from factory_control.bootstrap import bootstrap
    from factory_control.db import resolve_database_url

    cfg = settings or get_settings()
    problems = cfg.validate_runtime()
    if problems:
        raise RuntimeError("invalid configuration: " + "; ".join(problems))

    configure_logging()
    url = resolve_database_url(cfg.database_url)
    report = bootstrap(
        database_url=url,
        migrations_mode=cfg.migrations_mode,
        object_store_url=cfg.object_store_url,
    )

    runtime = build_runtime(settings=cfg)
    if cfg.workflow_engine == "temporal":
        engine: Any = _build_temporal_engine(cfg, runtime=runtime)
    else:
        from factory_control.engine.inprocess import InProcessEngine

        engine = InProcessEngine(runtime=runtime)
    runtime.engine = engine
    api_service.configure(runtime=runtime, engine=engine, startup_report=report, settings=cfg)
    return runtime


def _build_temporal_engine(settings: Settings, *, runtime: Runtime | None = None) -> Any:
    from factory_control.engine.temporal import TemporalEngine

    return TemporalEngine(settings=settings, runtime=runtime)


def build_asgi_app(settings: Settings | None = None) -> Any:
    """Create the FastAPI application, starting the LISTEN task on startup."""
    from factory_control.rest.app import create_app

    runtime = prepare_process(settings)
    app = create_app(runtime)
    attach_event_listener(app, runtime)
    return app


def attach_event_listener(app: Any, runtime: Runtime) -> Any:
    """Register the LISTEN task on an application's own lifecycle (CP-EVT-005)."""

    # Starlette types this decorator loosely, so the ignore is the only way to keep
    # the lifecycle hook; add_event_handler no longer exists on this version.
    @app.on_event("startup")  # type: ignore[untyped-decorator]
    async def _start_listener() -> None:
        # A pool thread cannot survive the process that owned it: close what the previous
        # process claimed before this one starts answering, so no input reads PARSING forever.
        import asyncio

        from factory_control.services.parse_runs import reconcile_interrupted

        await asyncio.to_thread(reconcile_interrupted, runtime)
        await _attach_listener(app, runtime)

    @app.on_event("shutdown")  # type: ignore[untyped-decorator]
    async def _stop_listener() -> None:
        stop = getattr(app.state, "listener_stop", None)
        if stop is not None:
            stop.set()
        from factory_control.services.parse_runs import shutdown_parse_pool

        shutdown_parse_pool()

    return app


async def _attach_listener(app: Any, runtime: Runtime) -> None:
    """One LISTEN connection per API instance (CP-EVT-005 fan-out)."""
    from factory_control.db import resolve_database_url
    from factory_control.events.listener import NotificationListener, async_dsn_from_sqlalchemy_url

    loop = asyncio.get_running_loop()
    runtime.bus.attach_loop(loop)
    stop = asyncio.Event()
    app.state.listener_stop = stop
    listener = NotificationListener(
        dsn=async_dsn_from_sqlalchemy_url(resolve_database_url(runtime.settings.database_url)),
        channel=runtime.settings.sse_channel,
        bus=runtime.bus,
        poll_seconds=runtime.settings.sse_poll_seconds,
    )
    app.state.listener_task = loop.create_task(listener.run(stop))


def create_app(runtime: Runtime | None = None) -> Any:
    """ASGI factory for a runtime that is already prepared (used by tests)."""
    from factory_control.rest.app import create_app as _create

    if runtime is None:
        return build_asgi_app()
    return _create(runtime)


def settings_for_cli(*, port: int | None, base: Settings | None = None) -> Settings:
    """Apply ``--port`` to a **copy** of the settings (CP-RT-001).

    :func:`factory_control.config.get_settings` is an ``lru_cache``d process singleton, and this
    entry point used to write the flag straight onto it.  The value then outlived the command: every
    later reader of ``get_settings().api_port`` — a second runtime, a notification transport, the
    next test in the process — saw the port some caller had happened to pass on the command line.
    A copy says the same thing to the one process that asked for it.
    """
    source = base if base is not None else get_settings()
    if not port:
        return source
    return source.model_copy(update={"api_port": int(port)})


def api_main(argv: list[str] | None = None) -> int:
    """``factory-api`` entry point: REST + SSE."""
    parser = _parser("factory-api")
    args = parser.parse_args(argv)
    settings = settings_for_cli(port=args.port)
    import uvicorn

    uvicorn.run(
        build_asgi_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        loop="asyncio",
    )
    return 0


def worker_main(argv: list[str] | None = None) -> int:
    """``factory-worker`` entry point: Temporal worker, or the in-process loop."""
    parser = _parser("factory-worker")
    parser.add_argument("--once", action="store_true", help="run every scheduled job once and exit")
    args = parser.parse_args(argv)
    runtime = prepare_process()

    from factory_control import scheduled

    if args.once:
        print(scheduled.run_all_once(runtime))
        return 0

    if runtime.settings.workflow_engine == "temporal":
        return _run_temporal_worker(runtime)
    return _run_inprocess_worker(runtime)


def _run_temporal_worker(runtime: Runtime) -> int:
    from factory_control.engine.temporal import build_worker

    async def main() -> None:
        from temporalio.client import Client

        settings = runtime.settings
        client = await Client.connect(settings.temporal_target, namespace=settings.temporal_namespace)
        await scheduled_ensure(client, settings)
        worker = build_worker(client, settings=settings)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        async with worker:
            await stop.wait()

    async def scheduled_ensure(client: Any, settings: Any) -> None:
        from factory_control.scheduled import ensure_temporal_schedules

        await ensure_temporal_schedules(client, settings=settings)

    asyncio.run(main())
    return 0


def _run_inprocess_worker(runtime: Runtime) -> int:
    from factory_control.scheduled import run_inprocess_loop

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):  # pragma: no cover - non-POSIX
                loop.add_signal_handler(sig, stop.set)
        await run_inprocess_loop(runtime, stop=stop)

    asyncio.run(main())
    return 0


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--port", type=int, default=0)
    return parser


