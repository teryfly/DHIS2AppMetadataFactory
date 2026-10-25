"""Coverage for optional / adapter code paths: Vault, cloud, S3, tracing,
object-store factory & provider."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from factory_foundation.common.errors import (
    FactoryError,
    SecretStoreUnsupportedError,
    SecretUnavailableError,
)
from factory_foundation.common.tracing import configure_tracing, trace_span, tracer
from factory_foundation.objectstore import create_object_store
from factory_foundation.objectstore.provider import (
    configure_from_url,
    get_object_store,
    reset_object_store,
)
from factory_foundation.secrets.cloud import CloudSecretBackend
from factory_foundation.secrets.vault import VaultSecretBackend

# --- Vault -----------------------------------------------------------------


class FakeVaultTransport:
    def __init__(self, writable: bool = True) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self._writable = writable

    def read(self, path: str) -> dict[str, Any]:
        # emulate KV v2 envelope
        return {"data": {"data": self.data[path]}}

    def write(self, path: str, value: dict[str, Any]) -> None:
        self.data[path] = value

    def supports_write(self) -> bool:
        return self._writable


def test_vault_read_and_write():
    backend = VaultSecretBackend(FakeVaultTransport())
    ref = backend.store("secret/data/x", "s3cr3t")
    assert ref == "vault://secret/data/x"
    assert backend.resolve("secret/data/x").reveal() == "s3cr3t"
    assert backend.supports_write() is True


def test_vault_read_only_store_raises():
    """FD-SEC-004: a read-only transport reports an unsupported write, not a missing secret."""
    backend = VaultSecretBackend(FakeVaultTransport(writable=False))
    with pytest.raises(SecretStoreUnsupportedError):
        backend.store("a/b", "v")


def test_cloud_read_only_store_raises():
    backend = CloudSecretBackend("proj-1", object())  # transport without put_secret
    with pytest.raises(SecretStoreUnsupportedError):
        backend.store("projects/p/secrets/s", "v")


def test_env_backend_refused_in_production(monkeypatch):
    """FD-SEC-001: the env backend is development-only."""
    from factory_foundation.secrets.env import EnvSecretBackend

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(FactoryError) as caught:
        EnvSecretBackend()
    assert caught.value.error_code == "SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION"
    # and installing development backends is refused the same way
    from factory_foundation.secrets import install_development_backends

    with pytest.raises(FactoryError) as caught:
        install_development_backends()
    assert caught.value.error_code == "SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION"


def test_env_backend_store_reports_unsupported(monkeypatch):
    from factory_foundation.secrets.env import EnvSecretBackend

    monkeypatch.setenv("APP_ENV", "development")
    backend = EnvSecretBackend({"X": "v"})
    assert backend.resolve("X").reveal() == "v"
    with pytest.raises(SecretStoreUnsupportedError):
        backend.store("X", "v")


def test_vault_missing_field_raises():
    transport = FakeVaultTransport()
    transport.data["bad"] = {"other": "x"}
    backend = VaultSecretBackend(transport)
    with pytest.raises(SecretUnavailableError):
        backend.resolve("bad")


def test_vault_transport_error_is_secret_unavailable():
    class Boom:
        def read(self, path):
            raise RuntimeError("network down")

        def write(self, path, value):
            raise RuntimeError("boom")

        def supports_write(self):
            return True

    backend = VaultSecretBackend(Boom())
    with pytest.raises(SecretUnavailableError) as ei:
        backend.resolve("x")
    assert "value" not in str(ei.value).lower() or "ref" in ei.value.details


# --- Cloud -----------------------------------------------------------------


class FakeCloudTransport:
    def __init__(self) -> None:
        self.store_map: dict[str, str] = {}

    def get_secret(self, path: str) -> str:
        return self.store_map[path]

    def put_secret(self, path: str, value: str) -> None:
        self.store_map[path] = value


def test_cloud_read_write():
    backend = CloudSecretBackend("gcp", FakeCloudTransport())
    ref = backend.store("proj/sec", "v1")
    assert ref == "gcp://proj/sec"
    assert backend.resolve("proj/sec").reveal() == "v1"
    assert backend.supports_write() is True


def test_cloud_error_maps():
    backend = CloudSecretBackend("aws", FakeCloudTransport())
    with pytest.raises(SecretUnavailableError):
        backend.resolve("missing")


# --- S3 (fake client, no network) ------------------------------------------


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos : self._pos + n]
            self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        return None


class _FakeClient:
    class _NoSuchKey(Exception):
        pass

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.exceptions = type("E", (), {"NoSuchKey": self._NoSuchKey})

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None):  # noqa: N803
        data = Fileobj.read()
        self.objects[Key] = data

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise self._NoSuchKey("nope")
        data = self.objects[Key]
        return {"Body": _FakeBody(data), "ContentLength": len(data)}

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise self._NoSuchKey("nope")
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)


def test_s3_store_roundtrip():
    from factory_foundation.objectstore.s3 import S3ObjectStore

    client = _FakeClient()
    store = S3ObjectStore("bucket", client=client, prefix="tenant")
    payload = b"streaming data payload"
    res = store.put("a/b", payload, media_type="application/octet-stream")
    assert res.sha256_hex == hashlib.sha256(payload).hexdigest()
    assert store.exists("a/b") is True
    assert store.open_read("a/b").read_all() == payload
    store.delete("a/b")
    assert store.exists("a/b") is False


class _GatewayClient(_FakeClient):
    """A gateway-emulating store: a bare 404, not the ``NoSuchKey`` shape botocore models.

    The adapter has to answer NOT_FOUND for both, or the same missing object reports one way
    on AWS and another way on the S3-compatible server a private deployment runs (FD-OBJ-001).
    """

    def get_object(self, Bucket, Key):  # noqa: N803
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}, "ResponseMetadata": {}},
                "GetObject",
            )
        data = self.objects[Key]
        return {"Body": _FakeBody(data), "ContentLength": len(data)}


def test_s3_a_bare_404_reads_as_not_found():
    from factory_foundation.common.errors import NotFoundError
    from factory_foundation.objectstore.s3 import S3ObjectStore

    store = S3ObjectStore("bucket", client=_GatewayClient())
    with pytest.raises(NotFoundError):
        store.open_read("nope")

    # and a real failure still surfaces as itself rather than being swallowed as missing
    class _Broken(_GatewayClient):
        def get_object(self, Bucket, Key):  # noqa: N803
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "no"}, "ResponseMetadata": {}},
                "GetObject",
            )

    from factory_foundation.objectstore.s3 import S3ObjectStore as _Store

    with pytest.raises(Exception) as caught:
        _Store("bucket", client=_Broken()).open_read("x")
    assert not isinstance(caught.value, NotFoundError)


def test_s3_read_missing_raises():
    from factory_foundation.common.errors import NotFoundError
    from factory_foundation.objectstore.s3 import S3ObjectStore

    store = S3ObjectStore("bucket", client=_FakeClient())
    with pytest.raises(NotFoundError):
        store.open_read("nope")


# --- object store factory / provider ---------------------------------------


def test_create_object_store_file_scheme(tmp_path):
    store = create_object_store(f"file://{tmp_path}/root")
    from factory_foundation.objectstore.local import LocalFileObjectStore

    assert isinstance(store, LocalFileObjectStore)


def test_create_object_store_s3_scheme(monkeypatch):
    # constructing an S3ObjectStore shouldn't hit the network
    import factory_foundation.objectstore.s3 as s3mod

    class _NoopClient:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(s3mod, "_client_factory", lambda **kw: _NoopClient(), raising=False)
    # boto3.client is called inside __init__; patch boto3 to avoid creds checks
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _FakeClient())
    store = create_object_store("s3://mybucket/pre?endpoint=http://minio:9000")
    from factory_foundation.objectstore.s3 import S3ObjectStore

    assert isinstance(store, S3ObjectStore)
    assert store.prefix == "pre"


def test_provider_configure_and_reset():
    reset_object_store()
    store = configure_from_url("memory://")
    assert get_object_store() is store
    reset_object_store()


def test_provider_not_configured_raises_without_url(monkeypatch):
    reset_object_store()
    monkeypatch.setenv("OBJECT_STORE_URL", "")
    from factory_foundation.common.errors import FactoryError

    with pytest.raises(FactoryError):
        get_object_store()


# --- tracing (no OTel installed -> no-op path) -----------------------------


def test_tracer_noop_and_span():
    t = tracer("x")
    with trace_span("op", attr="v") as span:
        assert span is not None
    configure_tracing(None)  # endpoint None -> no-op
    assert t is not None
