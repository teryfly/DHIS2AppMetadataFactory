"""Cloud Secret Manager backend (FD-SEC-001).

References look like ``gcp://project/secret`` or ``aws://secret``.  Access is
delegated to an injectable transport (e.g. an ``httpx``-backed client) so the
adapter stays testable and dependency-light.  Failures map to
``SECRET_UNAVAILABLE`` without exposing values (FD-SEC-003).
"""

from __future__ import annotations

from typing import Protocol

from factory_foundation.common.errors import SecretStoreUnsupportedError, SecretUnavailableError
from factory_foundation.secrets.base import Secret


class CloudTransport(Protocol):
    def get_secret(self, path: str) -> str: ...

    def put_secret(self, path: str, value: str) -> None: ...


class CloudSecretBackend:
    def __init__(self, scheme: str, transport: CloudTransport) -> None:
        self.scheme = scheme
        self._transport = transport

    def resolve(self, path: str) -> Secret:
        try:
            value = self._transport.get_secret(path)
        except SecretUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SecretUnavailableError(
                f"cloud secret read failed for reference: {self.scheme}://{path}",
                details={"ref": f"{self.scheme}://{path}"},
            ) from exc
        return Secret(_value=value, ref=f"{self.scheme}://{path}")

    def supports_write(self) -> bool:
        return hasattr(self._transport, "put_secret")

    def store(self, path: str, value: str) -> str:
        if not self.supports_write():
            raise SecretStoreUnsupportedError(
                "cloud transport is read-only", details={"refScheme": self.scheme}
            )
        try:
            self._transport.put_secret(path, value)
        except Exception as exc:  # noqa: BLE001
            raise SecretUnavailableError(
                f"cloud secret write failed for reference: {self.scheme}://{path}",
                details={"ref": f"{self.scheme}://{path}"},
            ) from exc
        return f"{self.scheme}://{path}"
