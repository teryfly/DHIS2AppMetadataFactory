"""MAJOR.MINOR.PATCH allocation (BP-VER-004, FR-BP-005).

The version number is a pure function of the change level; nothing else can set
it.  Initialization produces ``0.1.0``.
"""

from __future__ import annotations

from dataclasses import dataclass

from factory_foundation.api import FactoryError

from factory_blueprint.enums import ChangeLevel

INITIAL_MAJOR = 0
INITIAL_MINOR = 1
INITIAL_PATCH = 0


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def initial(cls) -> Version:
        return cls(INITIAL_MAJOR, INITIAL_MINOR, INITIAL_PATCH)

    @classmethod
    def parse(cls, text: str) -> Version:
        parts = text.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise FactoryError(
                f"not a Blueprint version: {text!r}", code="SCHEMA_ERROR", details={"version": text}
            )
        return cls(*(int(part) for part in parts))


def next_version(current: Version, change_level: ChangeLevel | str) -> Version:
    """Increment the component the change level owns and reset the lower ones."""
    level = ChangeLevel(change_level)
    if level is ChangeLevel.MAJOR:
        return Version(current.major + 1, 0, 0)
    if level is ChangeLevel.MINOR:
        return Version(current.major, current.minor + 1, 0)
    return Version(current.major, current.minor, current.patch + 1)


def bump(current: str, change_level: ChangeLevel | str) -> str:
    return str(next_version(Version.parse(current), change_level))
