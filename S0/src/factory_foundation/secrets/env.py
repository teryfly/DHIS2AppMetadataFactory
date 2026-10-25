"""Environment-variable secret backend — development only (FD-SEC-001).

References look like ``env://VAR_NAME`` and read from ``os.environ``.  This
backend is write-incompatible and must not be used in production: constructing
it under ``APP_ENV=production`` raises, and ``store`` reports the backend as
unable to write rather than as a missing secret (FD-SEC-004).
"""

from __future__ import annotations

import os

from factory_foundation.common.config import load_settings
from factory_foundation.common.errors import (
    SecretEnvBackendForbiddenInProductionError,
    SecretStoreUnsupportedError,
    SecretUnavailableError,
)
from factory_foundation.secrets.base import Secret


class EnvSecretBackend:
    scheme = "env"

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        if load_settings().is_production:
            raise SecretEnvBackendForbiddenInProductionError(
                "the env secret backend is development-only (FD-SEC-001)"
            )
        self._environ = environ if environ is not None else os.environ

    def resolve(self, path: str) -> Secret:
        value = self._environ.get(path)
        if value is None:
            raise SecretUnavailableError(
                f"environment secret not found: {path}", details={"ref": f"env://{path}"}
            )
        return Secret(_value=value, ref=f"env://{path}")

    def supports_write(self) -> bool:
        return False

    def store(self, path: str, value: str) -> str:
        raise SecretStoreUnsupportedError(
            "env secret backend is read-only", details={"refScheme": self.scheme}
        )
