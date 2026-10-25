"""In-memory secret backend — test double and development writable store
(FD-TST-001, FD-SEC-004).

Supports both reads (``memory://path``) and writes so the M3 store path is
exercisable without a live Vault/Cloud instance.
"""

from __future__ import annotations

from factory_foundation.common.errors import SecretUnavailableError
from factory_foundation.secrets.base import Secret


class MemorySecretBackend:
    scheme = "memory"

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._values: dict[str, str] = dict(initial or {})

    def resolve(self, path: str) -> Secret:
        if path not in self._values:
            raise SecretUnavailableError(
                f"memory secret not found: {path}", details={"ref": f"memory://{path}"}
            )
        return Secret(_value=self._values[path], ref=f"memory://{path}")

    def supports_write(self) -> bool:
        return True

    def store(self, path: str, value: str) -> str:
        self._values[path] = value
        return f"memory://{path}"

    # introspection for tests
    def raw(self, path: str) -> str:
        return self._values[path]
