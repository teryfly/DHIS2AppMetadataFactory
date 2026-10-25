"""Engine and session factory (FD-DB-001).

A process-wide provider built from ``DATABASE_URL``.  Subsystems obtain the
session factory from here; the unit of work (``db.uow``) opens sessions from it.
Tests call :func:`configure_engine` to point the foundation at a real or
in-memory database.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from factory_foundation.common.errors import FactoryError


@dataclass
class Database:
    url: str
    engine: Engine
    session_factory: sessionmaker[Session]


_lock = RLock()
_current: Database | None = None


def configure_engine(
    url: str,
    *,
    echo: bool = False,
    pool_pre_ping: bool = True,
    future: bool = True,
    **engine_kwargs: object,
) -> Database:
    """Create (and install) the engine/session factory for ``url``."""
    global _current
    with _lock:
        engine = create_engine(
            url,
            echo=echo,
            pool_pre_ping=pool_pre_ping,
            future=future,
            **engine_kwargs,
        )
        factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False, autoflush=False, future=future
        )
        _current = Database(url=url, engine=engine, session_factory=factory)
        return _current


def get_database() -> Database:
    if _current is None:
        # Lazily configure from settings if present.
        from factory_foundation.common.config import load_settings

        settings = load_settings()
        if not settings.database_url:
            raise FactoryError(
                "database not configured: set DATABASE_URL or call configure_engine()",
                code="INTERNAL_ERROR",
            )
        return configure_engine(settings.database_url)
    return _current


def get_engine() -> Engine:
    return get_database().engine


def new_session() -> Session:
    return get_database().session_factory()


def reset_engine() -> None:
    global _current
    with _lock:
        if _current is not None:
            _current.engine.dispose()
        _current = None
