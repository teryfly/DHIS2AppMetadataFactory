"""Vault secret backend (FD-SEC-001).

References look like ``vault://secret/data/team/dhis2``.  Network access is
delegated to an injectable transport so the backend is unit-testable and can be
wired to the official ``hvac`` client or a plain KV-v2 HTTP client.  Any failure
raises ``SECRET_UNAVAILABLE`` and never surfaces the value (FD-SEC-003).
"""

from __future__ import annotations

from typing import Any, Protocol

from factory_foundation.common.errors import SecretStoreUnsupportedError, SecretUnavailableError
from factory_foundation.secrets.base import Secret


class VaultTransport(Protocol):
    def read(self, path: str) -> dict[str, Any]: ...

    def write(self, path: str, data: dict[str, Any]) -> None: ...

    def supports_write(self) -> bool: ...


class VaultSecretBackend:
    scheme = "vault"

    def __init__(self, transport: VaultTransport, *, value_field: str = "value") -> None:
        self._transport = transport
        self._value_field = value_field

    def resolve(self, path: str) -> Secret:
        try:
            payload = self._transport.read(path)
        except SecretUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak the value
            raise SecretUnavailableError(
                f"vault read failed for reference: vault://{path}",
                details={"ref": f"vault://{path}"},
            ) from exc
        data = payload.get("data", payload)
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            data = data["data"]  # KV v2 envelope
        value = data.get(self._value_field) if isinstance(data, dict) else None
        if not isinstance(value, str):
            raise SecretUnavailableError(
                f"vault secret at vault://{path} missing field {self._value_field!r}",
                details={"ref": f"vault://{path}"},
            )
        return Secret(_value=value, ref=f"vault://{path}")

    def supports_write(self) -> bool:
        return self._transport.supports_write()

    def store(self, path: str, value: str) -> str:
        if not self.supports_write():
            raise SecretStoreUnsupportedError(
                "vault transport is read-only", details={"refScheme": self.scheme}
            )
        try:
            self._transport.write(path, {self._value_field: value})
        except Exception as exc:  # noqa: BLE001
            raise SecretUnavailableError(
                f"vault write failed for reference: vault://{path}",
                details={"ref": f"vault://{path}"},
            ) from exc
        return f"vault://{path}"
