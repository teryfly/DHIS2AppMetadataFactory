"""Process-wide object store provider (FD-OBJ-002, D-12).

Only S0 constructs and holds the store.  Subsystems never touch object storage
directly; they go through the artifact service, which reads from here.
"""

from __future__ import annotations

from threading import RLock

from factory_foundation.common.errors import FactoryError
from factory_foundation.objectstore.base import ObjectStore

_lock = RLock()
_current: ObjectStore | None = None


def configure_object_store(store: ObjectStore) -> None:
    global _current
    with _lock:
        _current = store


def configure_from_url(url: str) -> ObjectStore:
    from factory_foundation.objectstore import create_object_store

    store = create_object_store(url)
    configure_object_store(store)
    return store


def get_object_store() -> ObjectStore:
    if _current is None:
        from factory_foundation.common.config import load_settings

        settings = load_settings()
        if not settings.object_store_url:
            raise FactoryError(
                "object store not configured: set OBJECT_STORE_URL or call configure_object_store()",
                code="INTERNAL_ERROR",
            )
        return configure_from_url(settings.object_store_url)
    return _current


def reset_object_store() -> None:
    global _current
    with _lock:
        _current = None
