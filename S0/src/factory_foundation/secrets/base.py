"""Secret reference resolution (FD-SEC).

* :class:`Secret` wraps a sensitive value whose string form is *always* masked
  (FD-SEC-002).  Use :meth:`Secret.reveal` to read it; never embed it in logs,
  exceptions or audit records.
* A backend implements ``resolve`` (read) and optionally ``store`` (write, M3).
* :class:`SecretResolver` routes a reference such as ``vault://path`` to the
  backend registered for its scheme.
"""

from __future__ import annotations

import secrets as _secrets
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from factory_foundation.common.errors import (
    FactoryError,
    SecretStoreUnsupportedError,
    SecretUnavailableError,
)

_MASK = "***"


@dataclass(frozen=True)
class Secret:
    """A resolved secret value with a permanently masked string form."""

    _value: str
    ref: str = field(repr=False)

    def reveal(self) -> str:
        """Return the actual value.  Callers must not log or persist this."""
        return self._value

    def __str__(self) -> str:  # FD-SEC-002
        return f"Secret({self.ref}={_MASK})"

    def __repr__(self) -> str:
        return self.__str__()

    def for_json(self) -> str:
        return _MASK


@runtime_checkable
class SecretBackend(Protocol):
    scheme: str

    def resolve(self, path: str) -> Secret: ...

    def supports_write(self) -> bool: ...

    def store(self, path: str, value: str) -> str: ...


def generate_secret_value(length: int = 32) -> str:
    """Cryptographically strong one-time value (FD-SEC-004 / FR-COV-008)."""
    return _secrets.token_urlsafe(length)


def parse_ref(ref: str) -> tuple[str, str]:
    """Split ``scheme://path`` into ``(scheme, path)``."""
    if "://" not in ref:
        raise SecretUnavailableError(
            f"malformed secret reference (missing scheme): {ref!r}",
            details={"ref": ref},
        )
    scheme, _, path = ref.partition("://")
    scheme = scheme.lower()
    if not scheme or not path:
        raise SecretUnavailableError(f"malformed secret reference: {ref!r}", details={"ref": ref})
    return scheme, path


class SecretResolver:
    def __init__(self) -> None:
        self._backends: dict[str, SecretBackend] = {}

    def register(self, backend: SecretBackend) -> None:
        self._backends[backend.scheme.lower()] = backend

    def resolve(self, ref: str) -> Secret:
        scheme, path = parse_ref(ref)
        backend = self._backends.get(scheme)
        if backend is None:
            # FD-SEC-003: error names the ref but never any value.
            raise SecretUnavailableError(
                f"no secret backend registered for scheme {scheme!r}",
                details={"ref": ref},
            )
        try:
            return backend.resolve(path)
        except FactoryError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak the failure detail's value
            raise SecretUnavailableError(
                f"failed to resolve secret reference: {ref}",
                details={"ref": ref},
            ) from exc

    def store(self, path: str, value: str | None = None, *, scheme: str | None = None) -> str:
        """Write a secret and return its reference (FD-SEC-004, M3).

        ``scheme`` selects the write backend (default: the only writable one).
        ``value`` is generated when omitted.
        """
        actual = value if value is not None else generate_secret_value()
        target_scheme = scheme
        if target_scheme is None:
            writable = [s for s, b in self._backends.items() if b.supports_write()]
            if len(writable) != 1:
                raise SecretStoreUnsupportedError(
                    "cannot infer a write backend; specify scheme",
                    details={"candidates": writable},
                )
            target_scheme = writable[0]
        backend = self._backends.get(target_scheme.lower())
        if backend is None or not backend.supports_write():
            raise SecretStoreUnsupportedError(
                f"secret backend {target_scheme!r} does not support writes",
                details={"ref_scheme": target_scheme},
            )
        return backend.store(path, actual)


resolver = SecretResolver()
