"""Optimistic concurrency control (FD-DB-005).

Aggregates carry a monotonically increasing ``version``.  Updating one requires
passing the version the caller last observed; a mismatch means someone else
committed in between and raises ``CONCURRENCY_ERROR``.  This is the implementation
basis for overall SRS NFR-CON-001 (e.g. only one Patch may be approved against a
given baseline).
"""

from __future__ import annotations

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, declared_attr

from factory_foundation.common.errors import ConcurrencyError


class VersionedAggregate:
    """Mixin adding an integer ``version`` column to an ORM model.

    Subsystems inherit from this together with their schema base.  Call
    :func:`bump_version` on update rather than mutating ``version`` directly.
    """

    @declared_attr.directive
    def version(cls) -> Mapped[int]:  # noqa: N805
        from sqlalchemy.orm import mapped_column

        return mapped_column(Integer, nullable=False, default=1)


def check_version(row: object, expected_version: int) -> None:
    """Raise if ``row.version`` does not equal ``expected_version``."""
    actual = getattr(row, "version", None)
    if actual is None:
        raise ConcurrencyError(
            f"aggregate {type(row).__name__} has no version column",
        )
    if actual != expected_version:
        raise ConcurrencyError(
            f"version conflict on {type(row).__name__}: expected {expected_version}, "
            f"found {actual}",
            details={"expected": expected_version, "actual": actual},
        )


def bump_version(row: object, expected_version: int) -> int:
    """Validate ``expected_version`` then increment and return the new version."""
    check_version(row, expected_version)
    new_version = expected_version + 1
    setattr(row, "version", new_version)  # noqa: B010 - row is typed object; attr is set dynamically
    return new_version
