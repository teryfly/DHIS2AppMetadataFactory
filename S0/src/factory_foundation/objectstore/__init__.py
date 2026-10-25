"""Object store construction from configuration (FD-OBJ-001).

``OBJECT_STORE_URL`` selects the backend:

* ``memory://``                              → :class:`InMemoryObjectStore`
* ``file://<root>`` or ``local://<root>``     → :class:`LocalFileObjectStore`
* ``s3://<bucket>[/<prefix>]``               → :class:`S3ObjectStore`

For S3, the endpoint is taken from the optional ``?endpoint=`` query parameter
(needed for MinIO and other S3-compatible services).
"""

from __future__ import annotations

from urllib.parse import urlparse

from factory_foundation.common.errors import FactoryError
from factory_foundation.objectstore.base import ObjectStore
from factory_foundation.objectstore.local import LocalFileObjectStore
from factory_foundation.objectstore.memory import InMemoryObjectStore


def create_object_store(url: str) -> ObjectStore:
    if not url:
        raise FactoryError("OBJECT_STORE_URL is empty", code="INTERNAL_ERROR")
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme in ("memory",):
        return InMemoryObjectStore()
    if scheme in ("file", "local"):
        # ``file:///var/data`` has an empty netloc and an absolute path: the path is the
        # root as written.  ``file://relative`` (netloc set, empty-ish path) keeps the old
        # netloc+path spelling for a root relative to the working directory.
        root = parsed.path if parsed.netloc in ("", "localhost") else f"{parsed.netloc}{parsed.path}"
        if not root:
            raise FactoryError(f"object store url has no path: {url}", code="INTERNAL_ERROR")
        return LocalFileObjectStore(root)
    if scheme in ("s3", "s3a", "https", "http"):
        from factory_foundation.objectstore.s3 import S3ObjectStore

        if scheme in ("http", "https"):
            # full endpoint URL, bucket as first path segment
            bucket = parsed.path.lstrip("/").split("/")[0]
            prefix = "/".join(parsed.path.lstrip("/").split("/")[1:])
            endpoint = f"{parsed.scheme}://{parsed.netloc}"
            return S3ObjectStore(bucket, endpoint_url=endpoint, prefix=prefix)
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")
        return S3ObjectStore(bucket, endpoint_url=_query_endpoint(parsed.query), prefix=prefix)
    raise FactoryError(f"unsupported OBJECT_STORE_URL scheme: {scheme}", code="INTERNAL_ERROR")


def _query_endpoint(query: str) -> str | None:
    from urllib.parse import parse_qs

    qs = parse_qs(query)
    ep = qs.get("endpoint")
    return ep[0] if ep else None


__all__ = [
    "ObjectStore",
    "create_object_store",
    "InMemoryObjectStore",
    "LocalFileObjectStore",
]
