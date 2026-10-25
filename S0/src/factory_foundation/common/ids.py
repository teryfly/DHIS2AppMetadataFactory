"""ID generation (FD-COM-002).

IDs are type-prefixed ULIDs, e.g. ``art-01H...``.  Prefixes are registered per
subsystem at start-up (FD-ASM-01) so that cross-subsystem callers cannot mint
IDs in another subsystem's namespace by accident.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

from ulid import ULID

from factory_foundation.common.errors import InternalError, RegistryConflictError

# Default S0 prefixes.  Subsystems register their own via register_id_prefixes.
_BUILTIN_PREFIXES: dict[str, str] = {
    "art": "S0",  # artifact
    "aud": "S0",  # audit event
    "stg": "S0",  # artifact staging handle
    "idem": "S0",  # idempotency record
    "corr": "S0",  # correlation id
}


class IdPrefixRegistry:
    def __init__(self) -> None:
        self._mutex = threading.RLock()
        self._prefixes: dict[str, str] = dict(_BUILTIN_PREFIXES)

    def register(self, prefix: str, *, owner_subsystem: str) -> None:
        norm = prefix.strip().lower()
        if not norm or not norm.isascii() or not norm.isalnum():
            raise InternalError(f"invalid id prefix: {prefix!r}")
        with self._mutex:
            existing = self._prefixes.get(norm)
            if existing is not None and existing != owner_subsystem:
                # Same conflict rule as the error-code catalog (FD-COM-001a): a prefix
                # cannot acquire a second owner, and REGISTRY_CONFLICT is the code for it.
                raise RegistryConflictError(
                    f"id prefix {norm!r} already owned by {existing}, "
                    f"cannot reassign to {owner_subsystem}",
                    details={
                        "code": norm,
                        "existingOwner": existing,
                        "requestedOwner": owner_subsystem,
                    },
                )
            self._prefixes[norm] = owner_subsystem

    def register_many(self, pairs: Iterable[tuple[str, str]]) -> None:
        for prefix, owner in pairs:
            self.register(prefix, owner_subsystem=owner)

    def owner_of(self, prefix: str) -> str | None:
        return self._prefixes.get(prefix.strip().lower())

    def all(self) -> dict[str, str]:
        with self._mutex:
            return dict(self._prefixes)


id_prefix_registry = IdPrefixRegistry()


def new_id(prefix: str) -> str:
    """Return a new type-prefixed ULID string.

    The prefix must be registered (FD-ASM-01); unknown prefixes raise so callers
    cannot silently invent namespaces.
    """
    norm = prefix.strip().lower()
    if id_prefix_registry.owner_of(norm) is None:
        raise InternalError(f"unregistered id prefix: {prefix!r}")
    return f"{norm}-{ULID()}"


def new_correlation_id() -> str:
    # Correlation ids may be generated anywhere in a chain, even before the
    # registry is fully loaded, so this bypasses the prefix check.
    return f"corr-{ULID()}"
