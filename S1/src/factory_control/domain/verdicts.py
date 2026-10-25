"""The refusals an environment gives a COMMIT, built in one place (CP-GATE-012).

The Gate check and the COMMIT path reach the same three verdicts.  S5IF-G07.3 pins what
each one must carry in ``details``, because S5 renders it as the dialog that explains
the refusal: a missing field is not a cosmetic gap, it is a button the user cannot
understand.  ``EnvironmentLockedError`` from a provider arrives through the alias in
:mod:`factory_control.errors`, so the shape below is the one S5 sees either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_control.domain.actions import PRODUCTION_CHECK
from factory_control.errors import (
    EnvironmentLockedError,
    OutsideCommitWindowError,
    PriorVerificationMissingError,
)

#: What ``PRIOR_VERIFICATION_MISSING`` asks for, in the words S5 shows next to the
#: action that produces it.
REQUIRED_EVIDENCE: tuple[str, ...] = (PRODUCTION_CHECK,)


def environment_locked(protection: Any, *, environment_key: str | None = None) -> EnvironmentLockedError:
    key = environment_key or protection.environment_key
    return EnvironmentLockedError(
        f"environment {key} is emergency-locked",
        details={
            "environmentKey": key,
            "lockReason": protection.lock_reason,
            # SRS 8.3: when the lock went on, so S5 can say "locked since …" rather than
            # leave a user wondering whether their own run caused it.
            "lockChangedAt": _iso(getattr(protection, "lock_changed_at", None)),
        },
    )


def outside_commit_window(protection: Any, *, now: datetime, environment_key: str | None = None) -> OutsideCommitWindowError:
    key = environment_key or protection.environment_key
    timezone = _timezone_name(protection.timezone)
    return OutsideCommitWindowError(
        f"environment {key} only accepts commits inside its configured window",
        details={
            "environmentKey": key,
            "windows": list(protection.commit_windows or []),
            "environmentTimezone": timezone,
            "currentTime": _local_time(now, timezone),
        },
    )


def prior_verification_missing(
    *, environment_key: str, blueprint_version: str | None
) -> PriorVerificationMissingError:
    return PriorVerificationMissingError(
        "this Blueprint version has not been verified in a non-production environment",
        details={
            "environmentKey": environment_key,
            "blueprintVersion": blueprint_version,
            "requiredEvidence": list(REQUIRED_EVIDENCE),
        },
    )


def _iso(value: Any) -> str | None:
    """A timestamp in the wire's own form; a provider that says nothing stays null."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _timezone_name(value: Any) -> str:
    """The timezone to name in ``details``; unusable data says ``UTC``.

    The verdict is already a refusal — an unreadable timezone string from the provider
    must not turn it into a 500 on the way out.
    """
    from zoneinfo import ZoneInfo

    name = str(value or "UTC")
    try:
        ZoneInfo(name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError and friends
        return "UTC"
    return name


def _local_time(now: datetime, timezone: str) -> str:
    from zoneinfo import ZoneInfo

    aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo(timezone)).isoformat()


__all__ = [
    "REQUIRED_EVIDENCE",
    "environment_locked",
    "outside_commit_window",
    "prior_verification_missing",
]
