"""Secret adapter package (FD-SEC).

The module-level :data:`resolver` holds the registered backends.  Development
starts with the ``env`` and ``memory`` backends available; production registers
``vault``/``aws``/``gcp`` with their transports at start-up.
"""

from __future__ import annotations

from factory_foundation.common.config import load_settings
from factory_foundation.common.errors import (
    SecretEnvBackendForbiddenInProductionError,
    SecretStoreUnsupportedError,
)
from factory_foundation.secrets.base import (
    Secret,
    SecretBackend,
    SecretResolver,
    generate_secret_value,
    parse_ref,
    resolver,
)
from factory_foundation.secrets.cloud import CloudSecretBackend, CloudTransport
from factory_foundation.secrets.env import EnvSecretBackend
from factory_foundation.secrets.memory import MemorySecretBackend
from factory_foundation.secrets.vault import VaultSecretBackend, VaultTransport


def install_development_backends(res: SecretResolver | None = None) -> SecretResolver:
    """Register the ``env`` and ``memory`` backends (idempotent, development only).

    Refused outright under ``APP_ENV=production``: the env backend is development-only
    (FD-SEC-001), and it must not stay installed from an earlier development phase.
    """
    target = res or resolver
    if load_settings().is_production:
        raise SecretEnvBackendForbiddenInProductionError(
            "development secret backends are forbidden in production (FD-SEC-001)"
        )
    if "env" not in target._backends:  # noqa: SLF001 - intentional wiring helper
        target.register(EnvSecretBackend())
    if "memory" not in target._backends:  # noqa: SLF001
        target.register(MemorySecretBackend())
    return target


def register_secret_backend(backend: SecretBackend) -> SecretResolver:
    """Install one more backend on the process resolver and return it.

    Subsystems that keep their own secret storage (a control plane encrypting operator-supplied
    credentials, say) need a way to teach the resolver about them without reaching into
    :attr:`SecretResolver._backends`.  The backend decides whether it can take writes:
    ``SecretResolver.store`` infers *the* write backend when a caller omits the scheme, so a
    second writable backend would make every unqualified ``store_secret()`` fail
    (`base.py:112-118`).  A read-only backend cannot disturb that inference.
    """
    resolver.register(backend)
    return resolver


def resolve_secret(ref: str) -> Secret:
    return resolver.resolve(ref)


def store_secret(path: str, value: str | None = None, *, scheme: str | None = None) -> str:
    """Write a secret (generating a one-time value when ``value`` is omitted)
    and return its reference (FD-SEC-004)."""
    return resolver.store(path, value, scheme=scheme)


__all__ = [
    "Secret",
    "SecretBackend",
    "SecretResolver",
    "SecretStoreUnsupportedError",
    "generate_secret_value",
    "parse_ref",
    "register_secret_backend",
    "resolve_secret",
    "store_secret",
    "install_development_backends",
    "EnvSecretBackend",
    "MemorySecretBackend",
    "VaultSecretBackend",
    "VaultTransport",
    "CloudSecretBackend",
    "CloudTransport",
    "resolver",
]
