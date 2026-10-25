"""In-memory object store — the test double (FD-TST-001, FD-OBJ-001)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

from factory_foundation.common.errors import NotFoundError
from factory_foundation.objectstore.base import ObjectStore, PutResult, ReadHandle, normalize_source


class InMemoryObjectStore(ObjectStore):
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(
        self, key: str, source: object, *, media_type: str = "application/octet-stream"
    ) -> PutResult:
        digest = hashlib.sha256()
        parts: list[bytes] = []
        size = 0
        for chunk in normalize_source(source):
            digest.update(chunk)
            parts.append(chunk)
            size += len(chunk)
        self._objects[key] = b"".join(parts)
        return PutResult(
            key=key, location=f"memory://{key}", size=size, sha256_hex=digest.hexdigest()
        )

    def open_read(self, key: str) -> ReadHandle:
        if key not in self._objects:
            raise NotFoundError(f"object not found: {key}", details={"key": key})
        data = self._objects[key]

        def chunks() -> Iterator[bytes]:
            yield data

        return ReadHandle(key=key, chunks=chunks(), size=len(data))

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._objects

    # Test helpers
    def keys(self) -> list[str]:
        return sorted(self._objects)

    def get_bytes(self, key: str) -> bytes:
        return self._objects[key]
