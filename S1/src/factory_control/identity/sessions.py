"""Server-side sessions (FR-SEC-005).

A session is a random 256-bit token the client holds and a SHA-256 digest of it in
``cp.sessions``: reading the table cannot mint a session, and stealing the cookie from a browser
is the only way in — which is what ``HttpOnly``, ``SameSite`` and HTTPS are for.

Two clocks run on every session, because they defend different things.  The **idle** timeout
(FR-SEC-005's 30 minutes by default) is what closes an unattended browser; the **absolute**
lifetime is what bounds the damage of a token that leaked while it was valid, and sliding only
the idle clock would let a session live forever on a keyboard that is never left.

``csrf_token_hash`` exists because a session that rides a cookie is sent by the browser to any
form on any origin: the state-changing request must also carry the secret value the page read
from its own session, which a cross-site form cannot do (FR-SEC-005's second clause).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from factory_control.foundation import utcnow
from factory_control.identity import passwords
from factory_control.models import Session as SessionRow


@dataclass(frozen=True)
class Issued:
    """What only the issuer ever holds: the clear token plus its CSRF twin."""

    session_id: str
    token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class Resolved:
    session_id: str
    user_id: str
    csrf_token_hash: str
    auth_source: str
    expires_at: datetime


def issue(
    session: DbSession,
    user_id: str,
    *,
    auth_source: str,
    idle_minutes: int,
    absolute_hours: int,
    now: datetime | None = None,
) -> Issued:
    """Mint a session and return the clear values, which are never stored."""
    moment = now or utcnow()
    token = passwords.new_token()
    csrf = passwords.new_token()
    absolute_deadline = moment + timedelta(hours=absolute_hours)
    record = SessionRow(
        id=_session_id(),
        token_hash=passwords.digest_token(token),
        user_id=user_id,
        csrf_token_hash=passwords.digest_token(csrf),
        auth_source=auth_source,
        last_seen_at=moment,
        # the first idle deadline is the shorter of the two clocks
        expires_at=min(moment + timedelta(minutes=idle_minutes), absolute_deadline),
        absolute_expires_at=absolute_deadline,
    )
    session.add(record)
    session.flush()
    return Issued(
        session_id=record.id, token=token, csrf_token=csrf, expires_at=record.expires_at
    )


def resolve(
    session: DbSession,
    token: str | None,
    *,
    idle_minutes: int,
    now: datetime | None = None,
) -> Resolved | None:
    """Look a token up, sliding the idle clock; ``None`` means "not a live session".

    An expired or revoked row is answered the same way as an unknown token, and the reason is
    not returned: a 401 that said "that token exists but is expired" tells an attacker they
    guessed a real value (FR-SEC-008 asks for the same discretion about secrets in logs).
    """
    if not token:
        return None
    moment = now or utcnow()
    record: SessionRow | None = session.scalar(
        select(SessionRow).where(SessionRow.token_hash == passwords.digest_token(token))
    )
    if record is None or record.revoked_at is not None:
        return None
    if record.expires_at <= moment or record.absolute_expires_at <= moment:
        record.revoked_at = moment
        session.flush()
        return None
    record.last_seen_at = moment
    # only the idle clock slides, and never past the absolute one
    record.expires_at = min(moment + timedelta(minutes=idle_minutes), record.absolute_expires_at)
    session.flush()
    return Resolved(
        session_id=record.id,
        user_id=record.user_id,
        csrf_token_hash=record.csrf_token_hash,
        auth_source=record.auth_source,
        expires_at=record.expires_at,
    )


def revoke(session: DbSession, session_id: str, *, now: datetime | None = None) -> bool:
    """End a session.  Returns whether a live one was found, so a logout can say nothing more."""
    record: SessionRow | None = session.get(SessionRow, session_id)
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = now or utcnow()
    session.flush()
    return True


def revoke_for_user(session: DbSession, user_id: str, *, now: datetime | None = None) -> int:
    """Every live session of one account: what a disabled user or a changed password needs.

    FR-SEC-005's idle timeout alone would leave a stolen cookie working for half an hour after
    an administrator disabled the account, which is the difference between "disabled" and
    "disabled eventually".
    """
    moment = now or utcnow()
    rows = list(
        session.scalars(
            select(SessionRow).where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
        )
    )
    for record in rows:
        record.revoked_at = moment
    session.flush()
    return len(rows)


def csrf_matches(stored_hash: str, presented: str | None) -> bool:
    """Double-submit check for a cookie-authenticated state-changing request."""
    if not presented:
        return False
    import hmac

    return hmac.compare_digest(stored_hash, passwords.digest_token(presented))


def _session_id() -> str:
    from factory_control.foundation import new_id

    return new_id("ses")


__all__ = ["Issued", "Resolved", "csrf_matches", "issue", "resolve", "revoke", "revoke_for_user"]
