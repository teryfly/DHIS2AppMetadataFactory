"""Process-level wiring singleton.

``factory-api`` and ``factory-worker`` build one :class:`Runtime` plus one engine
at start-up and install it here; the Temporal activities import it lazily, so no
service ever reaches for a module-level global of its own.
"""

from __future__ import annotations

from typing import Any

from factory_control.config import Settings, get_settings
from factory_control.engine.base import WorkflowEngine, build_engine
from factory_control.runtime import Runtime

_runtime: Runtime | None = None
_engine: WorkflowEngine | None = None
_startup_report: Any = None
_settings: Settings | None = None


def configure(
    *, runtime: Runtime, engine: WorkflowEngine | None = None, startup_report: Any = None, settings: Settings | None = None
) -> None:
    global _runtime, _engine, _startup_report, _settings
    _runtime = runtime
    _engine = engine or runtime.engine
    _startup_report = startup_report
    _settings = settings or get_settings()
    if runtime.engine is None and _engine is not None:
        runtime.engine = _engine


def get_runtime() -> Runtime:
    if _runtime is None:
        raise RuntimeError("S1 runtime is not configured; call factory_control.api_service.configure() first")
    return _runtime


def get_engine() -> WorkflowEngine:
    if _engine is None:
        _engine_ref = get_runtime().engine
        if _engine_ref is None:
            raise RuntimeError("no workflow engine configured")
        return _engine_ref
    return _engine


def get_settings_obj() -> Settings:
    return _settings or get_settings()


def get_startup_report() -> Any:
    return _startup_report


def reset() -> None:
    """Test hook: drop all process wiring."""
    global _runtime, _engine, _startup_report, _settings
    _runtime = None
    _engine = None
    _startup_report = None
    _settings = None


def start_runtime_for(settings: Settings, *, engine_kind: str | None = None, ports: dict[str, Any] | None = None) -> Runtime:
    """Convenience used by the CLI, tests and scripts."""
    from factory_control.runtime import build_runtime

    runtime = build_runtime(
        settings=settings,
        **(ports or {}),
    )
    engine = build_engine(engine_kind or settings.workflow_engine, runtime=runtime, settings=settings)
    runtime.engine = engine
    configure(runtime=runtime, engine=engine, settings=settings)
    return runtime


__all__ = [
    "configure",
    "get_engine",
    "get_runtime",
    "get_settings_obj",
    "get_startup_report",
    "reset",
    "start_runtime_for",
]
