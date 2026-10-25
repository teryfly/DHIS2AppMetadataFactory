"""The read-only slice of S4 that S2 needs (S2 SRS section 8).

S2 never writes to DHIS2 and never calls any other S4 capability.  ``s4.adapter``
satisfies this protocol against the real ``factory_dhis2.api``; ``s4.stub`` implements
it in memory so a test, or S2's own Fake, can answer without an instance.

The existence answers carry *which* object holds the identifier, not just whether one
does.  BP-REG-008 and BP-REG-009 forbid "another object with the same UID/code", and an
UPDATE entry's own target object legitimately holds both — a bare boolean would make
every Brownfield freeze report a false conflict.  S4 publishes the same shape for the
same reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class UidOccupancy:
    """What, if anything, holds a UID in one environment."""

    exists: bool
    dhis2_type: str | None = None
    code: str | None = None
    name: str | None = None

    def __bool__(self) -> bool:
        return self.exists


@dataclass(frozen=True)
class CodeOccupancy:
    """What, if anything, holds a code among objects of one type."""

    exists: bool
    uid: str | None = None
    name: str | None = None

    def __bool__(self) -> bool:
        return self.exists


@runtime_checkable
class ReadonlyDhis2(Protocol):
    """Read-only DHIS2 lookups, always addressed by an explicit environment key."""

    def generate_uids(self, environment_key: str, count: int) -> list[str]:
        """DHIS2's official UID generation endpoint (logical resource ``system.id``)."""
        ...

    def uid_exists(self, environment_key: str, uids: Sequence[str]) -> dict[str, UidOccupancy]:
        """Who holds each UID in that environment."""
        ...

    def code_exists(
        self, environment_key: str, dhis2_type: str, codes: Sequence[str]
    ) -> dict[str, CodeOccupancy]:
        """Who holds each code among objects of that type."""
        ...

    def has_associated_data(self, environment_key: str, dhis2_type: str, uid: str) -> bool:
        """Deletion safety check: does the object still hold data?"""
        ...
