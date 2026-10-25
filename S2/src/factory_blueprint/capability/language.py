"""The vocabulary a Capability Profile speaks, and the shape of what it says.

Kept apart from the reading so the words a Profile uses are one definition rather
than a string repeated across the compilers (FR-CAP-001: a new Profile is data).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory_blueprint.enums import IssueLevel

#: what this version says about one payload property
SUPPORTED = "SUPPORTED"
DEPRECATED = "DEPRECATED"
REMOVED = "REMOVED"
FORBIDDEN = "FORBIDDEN"

#: S2 addresses every object by the UID it allocated, so these two stay in a payload even
#: where a Profile lists ``id`` among the properties a client may not write: that list
#: means "the server maintains its value", not "leave it out" (BP-CMP-023, FR-REG-001).
ADDRESSING_FIELDS = frozenset({"id", "code"})


@dataclass(frozen=True)
class FieldVerdict:
    name: str
    verdict: str
    detail: str

    @property
    def writable(self) -> bool:
        """Whether the value still reaches the payload; a deprecated field does."""
        return self.verdict in (SUPPORTED, DEPRECATED)


def difference(effect: str, dhis2_type: str | None, name: str, detail: str) -> dict[str, Any]:
    """One Profile-driven difference in the shape the Diff reports (BP-CMP-029).

    ``effect`` names what the Profile did to the compile, ``detail`` says why in the
    reviewer's terms: the pair is what makes a difference attributable to the version
    rather than to the Blueprint (AC-CAP-01).  The keys are the Diff report's own field
    names, because these records only ever travel inside it.
    """
    return {"effect": effect, "dhis2_type": dhis2_type, "name": name, "detail": detail}


def severity_of(rule: dict[str, Any]) -> IssueLevel:
    """The strength the Profile states for one of its rules.

    A severity this code does not know becomes a WARNING, not a BLOCKING: a typo in a
    data file must not refuse a compile, and it must not pass unnoticed either.
    """
    stated = str(rule.get("severity") or "").upper()
    try:
        return IssueLevel(stated)
    except ValueError:
        return IssueLevel.WARNING
