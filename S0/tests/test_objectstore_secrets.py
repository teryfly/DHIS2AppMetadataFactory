"""Object store & secret tests (FD-OBJ, FD-SEC)."""

from __future__ import annotations

import hashlib
import io

import pytest

from factory_foundation.common.errors import FactoryError, SecretUnavailableError
from factory_foundation.objectstore import create_object_store
from factory_foundation.objectstore.base import (
    build_object_path,
    key_from_location,
    normalize_source,
)
from factory_foundation.objectstore.local import LocalFileObjectStore
from factory_foundation.objectstore.memory import InMemoryObjectStore
from factory_foundation.secrets import (
    Secret,
    SecretResolver,
    generate_secret_value,
    parse_ref,
)
from factory_foundation.secrets.env import EnvSecretBackend
from factory_foundation.secrets.memory import MemorySecretBackend


def test_path_generation():
    assert build_object_path("p", "BLUEPRINT", "art-1") == "p/BLUEPRINT/art-1"


def test_memory_store_roundtrip_and_hash():
    store = InMemoryObjectStore()
    data = b"streaming content here"
    res = store.put("k1", data)
    assert res.size == len(data)
    assert res.sha256_hex == hashlib.sha256(data).hexdigest()
    assert store.open_read("k1").read_all() == data
    assert store.open_read("k1").sha256_hex() == res.sha256_hex
    assert store.exists("k1")
    store.delete("k1")
    assert not store.exists("k1")


def test_memory_store_missing_read_raises():
    store = InMemoryObjectStore()
    with pytest.raises(FactoryError):
        store.open_read("nope")


def test_local_store_streaming(tmp_path):
    store = LocalFileObjectStore(tmp_path / "root")
    big = io.BytesIO(b"x" * (2 * 1024 * 1024))  # 2 MiB
    res = store.put("a/b/c", big)
    assert res.size == 2 * 1024 * 1024
    assert (
        store.open_read("a/b/c").sha256_hex()
        == hashlib.sha256(b"x" * (2 * 1024 * 1024)).hexdigest()
    )


def test_local_store_rejects_traversal(tmp_path):
    store = LocalFileObjectStore(tmp_path)
    with pytest.raises(FactoryError):
        store.put("../escape", b"data")


def test_normalize_source_from_iterable():
    chunks = list(normalize_source([b"ab", b"", b"cd"]))
    assert b"".join(chunks) == b"abcd"


def test_create_object_store_schemes():
    assert isinstance(create_object_store("memory://"), InMemoryObjectStore)
    with pytest.raises(FactoryError):
        create_object_store("ftp://x")
    with pytest.raises(FactoryError):
        create_object_store("")


def test_secret_masked_always():
    s = Secret(_value="supersecret", ref="env://X")
    assert "supersecret" not in str(s)
    assert "supersecret" not in repr(s)
    assert s.for_json() == "***"
    assert s.reveal() == "supersecret"


def test_parse_ref():
    assert parse_ref("vault://secret/data/x") == ("vault", "secret/data/x")
    with pytest.raises(SecretUnavailableError):
        parse_ref("no-scheme")


def test_env_backend():
    backend = EnvSecretBackend({"TOKEN": "abc"})
    r = SecretResolver()
    r.register(backend)
    assert r.resolve("env://TOKEN").reveal() == "abc"
    with pytest.raises(SecretUnavailableError):
        r.resolve("env://MISSING")
    assert backend.supports_write() is False


def test_memory_backend_write_read():
    r = SecretResolver()
    r.register(MemorySecretBackend())
    ref = r.store("team/dhis2", "value123")
    assert ref == "memory://team/dhis2"
    assert r.resolve(ref).reveal() == "value123"
    auto = r.store("gen/path")  # value generated
    assert auto.startswith("memory://gen/path")
    assert r.resolve(auto).reveal()


def test_resolver_missing_backend_raises_without_value():
    r = SecretResolver()
    with pytest.raises(SecretUnavailableError) as ei:
        r.resolve("vault://x")
    assert ei.value.error_code == "SECRET_UNAVAILABLE"
    assert ei.value.details.get("ref") == "vault://x"


def test_generate_secret_value_length():
    v = generate_secret_value(24)
    assert len(v) >= 16


# --- FD-OBJ-001: location -> key round-trip on every backend ----------------


def test_file_url_keeps_an_absolute_root(tmp_path):
    """``file:///abs/path`` used to be stripped to a relative root, so the local
    development adapter wrote somewhere other than the configured directory."""
    store = create_object_store(f"file://{tmp_path}/store")
    assert str(store.root) == f"{tmp_path}/store"
    put = store.put("p1/OPENAPI/art-1", b'{"a":1}')
    assert put.location == f"file://{tmp_path}/store/p1/OPENAPI/art-1"
    assert store.exists(key_from_location(put.location, store))
    assert b"".join(store.open_read(key_from_location(put.location, store)).chunks) == b'{"a":1}'


def test_prefixed_s3_store_recovers_the_bare_key():
    """``s3://bucket/prefix/key`` must not be re-prefixed when it is read back."""
    from factory_foundation.objectstore.s3 import S3ObjectStore

    class _NoCallsClient:
        def get_bucket_location(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("no s3 call expected")

    store = S3ObjectStore("bucket", prefix="damf", client=_NoCallsClient())
    assert key_from_location("s3://bucket/damf/p1/MANIFEST/art-9", store) == "p1/MANIFEST/art-9"
    # without a prefix the bucket is simply dropped
    plain = S3ObjectStore("bucket", client=_NoCallsClient())
    assert key_from_location("s3://bucket/p1/MANIFEST/art-9", plain) == "p1/MANIFEST/art-9"


def test_memory_location_still_round_trips():
    store = InMemoryObjectStore()
    put = store.put("p1/T/a", b"x")
    assert key_from_location(put.location, store) == "p1/T/a"


def test_artifact_read_and_purge_work_on_the_local_backend(foundation, tmp_path):
    """The full artifact flow against the development file store (FD-OBJ-001, FD-ART-010):
    it failed as soon as a content_location had to be turned back into a key."""
    from factory_foundation.api import (
        ArtifactCreate,
        DataClassification,
        open_content,
        purge_content,
        register_artifact,
        stage_content,
        unit_of_work,
    )
    from factory_foundation.objectstore.provider import configure_object_store

    configure_object_store(create_object_store(f"file://{tmp_path}/store"))
    staged = stage_content(b'{"hello":1}', project_id="p1", artifact_type="MANIFEST")
    with unit_of_work():
        art = register_artifact(
            staged,
            ArtifactCreate(
                project_id="p1",
                type="MANIFEST",
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S2",
            ),
        )
    chunks, _classification, _media = open_content(art.id)
    assert b"".join(chunks) == b'{"hello":1}'
    purge_content(art.id)
    object_path = tmp_path / "store" / build_object_path("p1", "MANIFEST", art.id)
    assert not object_path.exists()
