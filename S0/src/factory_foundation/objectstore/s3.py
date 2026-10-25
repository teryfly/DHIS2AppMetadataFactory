"""S3-compatible object store adapter (FD-OBJ-001, overall SRS 19.5 / 24.1).

Uses boto3 and streams content with a chunked reader so large artifacts do not
have to be buffered fully (FD-ART-006).  Selected when ``OBJECT_STORE_URL`` has
an ``s3://`` or ``https://`` scheme.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

from factory_foundation.common.errors import FactoryError, NotFoundError
from factory_foundation.objectstore.base import (
    CHUNK_SIZE,
    ObjectStore,
    PutResult,
    ReadHandle,
    normalize_source,
)


def _client_error_type() -> type[BaseException]:
    """botocore's ClientError, resolved lazily: boto3 is an optional extra (``[s3]``)."""
    global _CLIENT_ERROR
    if _CLIENT_ERROR is None:
        try:
            from botocore.exceptions import ClientError as _Error

            _CLIENT_ERROR = _Error
        except ImportError:  # pragma: no cover - only without botocore installed
            _CLIENT_ERROR = FactoryError
    return _CLIENT_ERROR


#: codes a store uses for "this key is not here", across real S3, gateway emulation and
#: the S3-compatible servers the object-store adapter is meant to work with alike
_CLIENT_ERROR: type[BaseException] | None = None

_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound", "KeyNotFound"})


def _is_not_found(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error") or {}
    code = str(error.get("Code") or response.get("Code") or "")
    if code in _NOT_FOUND_CODES:
        return True
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 404 if isinstance(status, int) else False


class _ReadableSource:
    """Adapts a chunk iterator to the file-like object boto3 upload expects."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buffer = b""

    def read(self, size: int = -1) -> bytes:
        while len(self._buffer) < size or size < 0:
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                break
        if size < 0:
            out, self._buffer = self._buffer, b""
        else:
            out, self._buffer = self._buffer[:size], self._buffer[size:]
        return out


class S3ObjectStore(ObjectStore):
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        prefix: str = "",
        client: Any | None = None,
    ) -> None:
        try:
            import boto3
        except Exception as exc:  # noqa: BLE001
            raise FactoryError(
                "boto3 is required for the S3 object store backend", code="INTERNAL_ERROR"
            ) from exc

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client or boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def _full_key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(
        self, key: str, source: object, *, media_type: str = "application/octet-stream"
    ) -> PutResult:
        # Hash incrementally while streaming to S3; boto3 handles multipart upload.
        chunks = normalize_source(source)
        digest = hashlib.sha256()

        def counting(chunk_iter: Iterator[bytes]) -> Iterator[bytes]:
            for c in chunk_iter:
                digest.update(c)
                yield c

        full_key = self._full_key(key)
        # upload_fileobj streams; we wrap the iterator to also compute the digest.
        self._client.upload_fileobj(
            _ReadableSource(counting(chunks)),
            self.bucket,
            full_key,
            ExtraArgs={"ContentType": media_type},
        )
        size = _uploaded_size(self._client, self.bucket, full_key)
        location = f"s3://{self.bucket}/{full_key}"
        return PutResult(key=key, location=location, size=size, sha256_hex=digest.hexdigest())

    def open_read(self, key: str) -> ReadHandle:
        full_key = self._full_key(key)
        try:
            obj = self._client.get_object(Bucket=self.bucket, Key=full_key)
        except self._client.exceptions.NoSuchKey as exc:
            raise NotFoundError(f"object not found: {key}", details={"key": key}) from exc
        except _client_error_type() as exc:
            # a missing object does not always come back as NoSuchKey: gateway-emulated and
            # plain-S3-compatible stores answer a bare 404, and some sign the error under a
            # different code.  Letting those escape as ClientError means a reader gets an
            # unclassified storage failure for what every other adapter calls NOT_FOUND
            # (FD-OBJ-001 asks the adapters to behave alike).
            if _is_not_found(exc):
                raise NotFoundError(f"object not found: {key}", details={"key": key}) from exc
            raise
        body = obj["Body"]
        size = obj.get("ContentLength")

        def chunks() -> Iterator[bytes]:
            try:
                while True:
                    data = body.read(CHUNK_SIZE)
                    if not data:
                        break
                    yield data
            finally:
                body.close()

        return ReadHandle(key=key, chunks=chunks(), size=size)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=self._full_key(key))

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except Exception:  # noqa: BLE001 - ClientError(404) and friends
            return False


def _uploaded_size(client: Any, bucket: str, key: str) -> int:
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        return int(head.get("ContentLength", 0))
    except Exception:  # noqa: BLE001
        return 0
