"""Extra coverage for edge branches across the foundation."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from factory_foundation.api import (
    ErrorMeta,
    NullProgressReporter,
    Subsystem,
    error_catalog,
    is_registered_event_type,
    list_audit_event_types,
    raise_for_code,
    register_audit_event_types,
)
from factory_foundation.artifacts.diff import _json_diff, _line_diff
from factory_foundation.common.clock import SystemClock
from factory_foundation.common.config import load_settings
from factory_foundation.common.errors import FactoryError, InternalError
from factory_foundation.common.errors import Subsystem as SB
from factory_foundation.common.hashing import structure_hash
from factory_foundation.common.ids import id_prefix_registry, new_id
from factory_foundation.objectstore.base import ReadHandle, normalize_source
from factory_foundation.testing import FoundationHarness


def test_ids_registry_operations():
    id_prefix_registry.register("cust", owner_subsystem="S2")
    assert id_prefix_registry.owner_of("cust") == "S2"
    assert "cust" in id_prefix_registry.all()
    # same owner again is fine
    id_prefix_registry.register("cust", owner_subsystem="S2")
    # different owner for an existing prefix is rejected (FD-COM-001a conflict rule)
    with pytest.raises(FactoryError) as caught:
        id_prefix_registry.register("cust", owner_subsystem="S4")
    assert caught.value.error_code == "REGISTRY_CONFLICT"
    # invalid prefix characters rejected
    with pytest.raises(FactoryError):
        id_prefix_registry.register("BAD-PREFIX", owner_subsystem="S2")
    assert new_id("CUST").startswith("cust-")


def test_error_catalog_coerce_and_register_many():
    assert SB.coerce("s1") is Subsystem.S1
    assert SB.coerce(Subsystem.S3) is Subsystem.S3
    with pytest.raises(ValueError):
        SB.coerce("S9")
    error_catalog.register_many([ErrorMeta("X_ONE", "S2", True), ErrorMeta("X_TWO", "S2", False)])
    assert error_catalog.is_retryable("X_ONE")
    assert not error_catalog.is_retryable("X_TWO")


def test_raise_for_code_unknown_raises_internal():
    with pytest.raises(InternalError):
        raise_for_code("TOTALLY_UNKNOWN_CODE", "nope")


def test_hashing_default_serialisers():
    # datetime and pydantic models are handled deterministically
    ts = datetime(2026, 5, 1, tzinfo=UTC)
    h1 = structure_hash({"when": ts})
    assert h1 == structure_hash({"when": "2026-05-01T00:00:00+00:00"})

    class M(BaseModel):
        a: int
        b: str

    assert structure_hash({"m": M(a=1, b="x")}) == structure_hash({"m": {"a": 1, "b": "x"}})


def test_hashing_rejects_nonserialisable():
    with pytest.raises(TypeError):
        structure_hash({"bad": object()})


def test_config_env_file_and_development():
    import os
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
        fh.write("APP_ENV=development\nDATABASE_URL=postgresql://file\n")
        path = Path(fh.name)
    try:
        s = load_settings(env_file=path, environ={})
        assert s.is_development
        assert s.database_url == "postgresql://file"
    finally:
        os.unlink(path)


def test_normalize_source_from_file_object():
    chunks = list(normalize_source(io.BytesIO(b"hello world")))
    assert b"".join(chunks) == b"hello world"


def test_read_handle_iter_and_hash():
    h = ReadHandle(key="k", chunks=iter([b"abc", b"def"]), size=6)
    assert h.read_all() == b"abcdef"


def test_diff_json_nested_paths():
    result = _json_diff(
        "a", "b", b'{"x": {"y": 1}, "list": [1, 2]}', b'{"x": {"y": 2}, "list": [1, 3, 4]}'
    )
    paths = {row["path"] for row in result.lines}
    assert "/x/y" in paths
    assert any(p.startswith("/list") for p in paths)
    assert result.changed


def test_diff_line_no_change():
    result = _line_diff("a", "b", b"same\nlines", b"same\nlines")
    assert result.changed is False


def test_audit_registry_validation_errors(db_harness):
    # invalid name form
    with pytest.raises(FactoryError):
        register_audit_event_types(["NoDotsHere"], owner_subsystem="S1")
    # list without owner
    with pytest.raises(FactoryError):
        register_audit_event_types(["x.y.z"])
    register_audit_event_types(["s0.sample.action"], owner_subsystem="S0")
    assert is_registered_event_type("s0.sample.action")
    assert ("s0.sample.action", "S0") in list_audit_event_types()


@pytest.fixture
def db_harness():
    h = FoundationHarness(
        url=__import__("os").environ.get(
            "FACTORY_TEST_DATABASE_URL",
            "postgresql+psycopg://factory:factory@localhost:5432/factory_test",
        )
    )
    h.setup()
    try:
        yield h
    finally:
        h.teardown()


def test_null_progress_reporter_consumes():
    rep = NullProgressReporter()
    rep.report(step="x")  # no error
    assert SystemClock().now().tzinfo is not None
