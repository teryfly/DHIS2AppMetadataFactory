"""Common tools tests (FD-COM-001..008)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from factory_foundation.api import register_error_codes
from factory_foundation.common.clock import FixedClock, SystemClock, utcnow
from factory_foundation.common.config import load_settings, register_settings_schema
from factory_foundation.common.context import (
    OperationContext,
    bind_operation_context,
    current_context_or_default,
    get_operation_context,
)
from factory_foundation.common.errors import (
    BUILTIN_ERROR_CODES,
    ArtifactError,
    FactoryError,
    RegistryConflictError,
    Subsystem,
    error_catalog,
    raise_for_code,
)
from factory_foundation.common.hashing import (
    canonical_json,
    content_hash,
    file_json,
    is_hash_formatted,
    normalize_hash,
    structure_hash,
)
from factory_foundation.common.ids import new_correlation_id, new_id
from factory_foundation.common.logging import RedactingJsonFormatter
from factory_foundation.common.redaction import contains_credential, redact_structure


def test_canonical_json_sorted_compact_utf8():
    out = canonical_json({"b": 1, "a": [1, 2, 3], "c": "héllo"})
    assert out == b'{"a":[1,2,3],"b":1,"c":"h\xc3\xa9llo"}'
    assert b" " not in out.split(b'"c"')[0]  # compact separators


def test_file_json_indent_and_trailing_newline():
    out = file_json({"a": 1, "b": {"c": 2}})
    assert out.endswith(b"\n")
    assert out.decode().splitlines()[1].startswith('  "a"')


def test_hash_formats():
    h = content_hash(b"hello")
    assert h.startswith("sha256:") and is_hash_formatted(h)
    assert h == "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_structure_hash_uses_canonical_form():
    a = structure_hash({"x": 1, "y": [1, 2]})
    b = structure_hash({"y": [1, 2], "x": 1})  # key order irrelevant
    assert a == b


def test_normalize_hash_accepts_bare_and_prefixed():
    digest = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert normalize_hash(digest) == f"sha256:{digest}"
    assert normalize_hash(f"sha256:{digest}") == f"sha256:{digest}"
    with pytest.raises(ValueError):
        normalize_hash("sha256:xyz")


def test_new_id_prefix_registered():
    assert new_id("art").startswith("art-")
    with pytest.raises(FactoryError):
        new_id("nope")  # unregistered


def test_new_correlation_id():
    assert new_correlation_id().startswith("corr-")


def test_system_and_fixed_clock():
    assert SystemClock().now().tzinfo is not None
    fc = FixedClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert fc.now() == dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    fc.advance(60)
    assert fc.now() == dt.datetime(2026, 1, 1, 0, 1, tzinfo=dt.UTC)
    assert utcnow(fc) == fc.now()


def test_operation_context_binding():
    assert get_operation_context() is None
    with bind_operation_context(operator_name="alice", project_id="p1") as ctx:
        assert isinstance(ctx, OperationContext)
        assert ctx.project_id == "p1"
        cur = current_context_or_default()
        assert cur.operator_name == "alice"
    assert get_operation_context() is None


def test_context_extend_existing():
    with bind_operation_context(operator_name="a"), bind_operation_context(project_id="p") as ctx:
        assert ctx.operator_name == "a"
        assert ctx.project_id == "p"


def test_redaction_masks_sensitive_fields():
    val = {"Authorization": "Bearer abcdef123456", "token": "xyz", "note": "safe"}
    result = redact_structure(val)
    assert result.redacted is True
    assert result.value["Authorization"] == "***"
    assert result.value["token"] == "***"
    assert result.value["note"] == "safe"


def test_redaction_inline_credential():
    assert contains_credential("password=hunter2 secret")
    result = redact_structure("set password=hunter2 for user")
    assert result.redacted and "hunter2" not in str(result.value)


def test_error_catalog_retryability():
    assert error_catalog.is_retryable("LLM_TIMEOUT") is True
    assert error_catalog.is_retryable("SCHEMA_ERROR") is False
    assert error_catalog.is_retryable("TRANSACTION_MISUSE") is False


def test_register_error_code_and_factory_error_fields():
    error_catalog.register("S9_BROKEN", owner_subsystem="S2", retryable=True)
    assert error_catalog.is_retryable("S9_BROKEN")
    with pytest.raises(FactoryError) as ei:
        raise_for_code("ARTIFACT_ERROR", "boom", artifact_id="art-1")
    err = ei.value
    assert err.error_code == "ARTIFACT_ERROR"
    assert isinstance(err, ArtifactError)
    d = err.to_dict()
    assert d["errorCode"] == "ARTIFACT_ERROR" and d["artifactId"] == "art-1" and "message" in d


def test_subsystem_schema_mapping():
    assert Subsystem.S1.schema.value == "cp"
    assert Subsystem.S4.schema.value == "dx"


def test_config_load_from_environ():
    s = load_settings(
        env_file=__import__("pathlib").Path("/nonexistent.env"),
        environ={
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql://x",
            "OBJECT_STORE_URL": "memory://",
            "SECRET_BACKEND": "vault",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otlp:4318",
        },
    )
    assert s.is_production and s.database_url == "postgresql://x"
    assert s.otel_endpoint == "http://otlp:4318"


def test_config_required_declared_key_missing():
    register_settings_schema("MY_URL", owner_subsystem="S1", required=True)
    with pytest.raises(FactoryError):
        load_settings(
            env_file=__import__("pathlib").Path("/nonexistent.env"),
            environ={"APP_ENV": "development"},
        )


def test_logger_redacts_and_includes_context():
    import logging

    with bind_operation_context(operator_name="carol", project_id="p9"):
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hi", None, None)
        out = json.loads(RedactingJsonFormatter().format(rec))
        assert out["correlationId"]
        assert out["operator"] == "carol"
        assert out["projectId"] == "p9"


# --- FD-COM-001a: S0's own built-in error codes -----------------------------

#: The FD-COM-001a checklist (code -> retryable), transcribed from the clause.
#: Written out here instead of imported from the implementation so that a code
#: dropped from the catalog fails these tests rather than passing silently.
FD_COM_001A_CODES: dict[str, bool] = {
    "TRANSACTION_MISUSE": False,
    "CONCURRENCY_ERROR": True,
    "ARTIFACT_ERROR": False,
    "ARTIFACT_NOT_FOUND": False,
    "ARTIFACT_PURGED": False,
    "ARTIFACT_HASH_MISMATCH": False,
    "ARTIFACT_READBACK_MISMATCH": True,
    "ARTIFACT_TOO_LARGE": False,
    "ARTIFACT_CLASSIFICATION_DOWNGRADE": False,
    "ARTIFACT_OWNERSHIP_VIOLATION": False,
    "ARTIFACT_TYPE_UNKNOWN": False,
    "ARTIFACT_TYPE_MISMATCH": False,
    "ARTIFACT_NO_CONTENT": False,
    "ARTIFACT_NOT_PURGEABLE": False,
    "ARTIFACT_VERSION_CONFLICT": False,
    "ARTIFACT_STAGING_NOT_FOUND": False,
    "ARTIFACT_STAGING_MISMATCH": False,
    "INVALID_STATUS_TRANSITION": False,
    "IDEMPOTENCY_CONFLICT": False,
    "SECRET_UNAVAILABLE": False,
    "SECRET_STORE_UNSUPPORTED": False,
    "SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION": False,
    "CANCELLED": False,
    "CHAIN_INTEGRITY_ERROR": False,
    "PACK_INTEGRITY_ERROR": False,
    "OBJECT_NOT_FOUND": False,
    "CONFIGURATION_ERROR": False,
    "MIGRATION_ERROR": False,
    "AUDIT_OUTSIDE_TRANSACTION": False,
    "AUDIT_EVENT_TYPE_UNKNOWN": False,
    "REGISTRY_CONFLICT": False,
}


def test_fd_com_001a_checklist_covers_thirty_one_codes():
    assert len(FD_COM_001A_CODES) == 31
    assert sum(1 for retryable in FD_COM_001A_CODES.values() if retryable) == 2


@pytest.mark.parametrize("code,expected_retryable", sorted(FD_COM_001A_CODES.items()))
def test_fd_com_001a_code_is_registered_and_owned_by_s0(code, expected_retryable):
    meta = error_catalog.get(code)
    assert meta is not None, f"{code} is not registered in the error catalog"
    assert meta.code == code
    assert meta.owner_subsystem == "S0"
    assert meta.retryable is expected_retryable
    assert error_catalog.is_retryable(code) is expected_retryable


def test_fd_com_001a_builtin_table_matches_the_clause():
    assert BUILTIN_ERROR_CODES == FD_COM_001A_CODES


def test_fd_com_001a_retryable_codes_report_themselves_as_retryable():
    for code in ("CONCURRENCY_ERROR", "ARTIFACT_READBACK_MISMATCH"):
        with pytest.raises(FactoryError) as caught:
            raise_for_code(code, "retry me")
        assert caught.value.retryable is True


def test_artifact_type_mismatch_is_raisable_by_any_subsystem():
    """FD-COM-001a: the code exists so other subsystems can raise it directly."""
    with pytest.raises(FactoryError) as caught:
        raise_for_code(
            "ARTIFACT_TYPE_MISMATCH",
            "产物类型不符",
            artifact_id="art-1",
            details={"expectedType": "FRONTEND_PACKAGE", "actualType": "METADATA_PACKAGE"},
        )
    err = caught.value
    assert err.error_code == "ARTIFACT_TYPE_MISMATCH"
    assert err.retryable is False
    assert err.details["expectedType"] == "FRONTEND_PACKAGE"


def test_builtin_code_registered_under_another_owner_is_a_registry_conflict():
    with pytest.raises(FactoryError) as caught:
        error_catalog.register("ARTIFACT_TYPE_MISMATCH", owner_subsystem="S2", retryable=False)
    assert caught.value.error_code == "REGISTRY_CONFLICT"
    assert isinstance(caught.value, RegistryConflictError)
    # the rejected attempt changed nothing
    meta = error_catalog.get("ARTIFACT_TYPE_MISMATCH")
    assert meta is not None and meta.owner_subsystem == "S0"


def test_re_registering_under_the_same_owner_merges():
    error_catalog.register("S0_DEMO_CODE", owner_subsystem="S0", retryable=False)
    error_catalog.register("S0_DEMO_CODE", owner_subsystem="S0", retryable=True)
    assert error_catalog.is_retryable("S0_DEMO_CODE") is True
    with pytest.raises(FactoryError) as caught:
        error_catalog.register("S0_DEMO_CODE", owner_subsystem="S4", retryable=True)
    assert caught.value.error_code == "REGISTRY_CONFLICT"


def test_subsystem_registration_helper_rejects_an_s0_owned_code():
    """A subsystem ``initialize()`` cannot take over one of S0's codes."""
    with pytest.raises(FactoryError) as caught:
        register_error_codes({"IDEMPOTENCY_CONFLICT": ("S1", False)})
    assert caught.value.error_code == "REGISTRY_CONFLICT"
    meta = error_catalog.get("IDEMPOTENCY_CONFLICT")
    assert meta is not None and meta.owner_subsystem == "S0"
