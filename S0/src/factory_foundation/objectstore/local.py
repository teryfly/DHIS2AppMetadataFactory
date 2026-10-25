"""Local filesystem object store — development adapter (FD-OBJ-001).

Writes and reads are chunked so large files stream rather than buffer.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from factory_foundation.common.errors import NotFoundError
from factory_foundation.objectstore.base import (
    CHUNK_SIZE,
    ObjectStore,
    PutResult,
    ReadHandle,
    normalize_source,
)


class LocalFileObjectStore(ObjectStore):
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        # Guard against path traversal in keys.
        safe = os.path.normpath(key)
        if safe.startswith("..") or os.path.isabs(safe):
            raise NotFoundError(f"invalid object key: {key}")
        path = self.root / safe
        resolved = path.resolve()
        if not str(resolved).startswith(str(self.root)):
            raise NotFoundError(f"object key escapes store root: {key}")
        return path

    def put(
        self, key: str, source: object, *, media_type: str = "application/octet-stream"
    ) -> PutResult:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("wb") as fh:
                for chunk in normalize_source(source):
                    digest.update(chunk)
                    size += len(chunk)
                    fh.write(chunk)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return PutResult(
            key=key, location=f"file://{path}", size=size, sha256_hex=digest.hexdigest()
        )

    def open_read(self, key: str) -> ReadHandle:
        path = self._path_for(key)
        if not path.exists():
            raise NotFoundError(f"object not found: {key}", details={"key": key})
        size = path.stat().st_size

        def chunks() -> Iterator[bytes]:
            with path.open("rb") as fh:
                while True:
                    data = fh.read(CHUNK_SIZE)
                    if not data:
                        break
                    yield data

        return ReadHandle(key=key, chunks=chunks(), size=size)

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path_for(key).exists()
