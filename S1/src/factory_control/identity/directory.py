"""Reading who is who (FR-SEC-002/003/004).

Read-only lookups, kept apart from the mutations in :mod:`factory_control.identity.service`
because the authorization policy calls these on *every* request and must not acquire a write
transaction, and because "does this grant still count" is one question with one answer — the
policy, the ``allowed-actions`` payload and the audit reader all ask it.

A subject is looked up across sources on purpose: the operator string a request carries is the
authenticated identity's sign-in name, and two accounts in different sources with the same name
would make an authorization decision depend on which row was read first.
:func:`factory_control.identity.service.create_user` therefore refuses a name that is already
taken in the other source, and says so, rather than leaving it to be discovered by a 403.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from factory_control.foundation import utcnow
from factory_control.models import (
    ProductionExemption,
    ProjectMember,
    RestrictedGrant,
    User,
    UserRole,
)


def by_subject(session: DbSession, subject: str) -> User | None:
    """The active account a sign-in name resolves to, or ``None``.

    A disabled account is answered as absent: an administrator's ``disable`` must revoke the
    person's authority at once, not merely block the next login (CP-IAM-002).
    """
    name = (subject or "").strip()
    if not name:
        return None
    return session.scalar(
        select(User).where(User.subject == name, User.status == "ACTIVE").limit(1)
    )


def get_by_subject_any_status(session: DbSession, subject: str) -> User | None:
    """The account behind a name whether active or not — for login and for user management."""
    name = (subject or "").strip()
    if not name:
        return None
    return session.scalar(select(User).where(User.subject == name).limit(1))


def roles_of(
    session: DbSession, user_id: str, project_id: str | None = None, *, now: datetime | None = None
) -> list[str]:
    """The roles held organisation-wide, plus those scoped to this project.

    ``project_id`` NULL on a role row is the organisation-wide grant; a member row's role is the
    same role held *inside* one project.  Both are unioned because a Platform Administrator is a
    Platform Administrator in every project they can see, while a project-scoped architect is
    not an architect elsewhere (FR-SEC-004).
    """
    _ = now
    rows = session.scalars(
        select(UserRole).where(UserRole.user_id == user_id, UserRole.project_id.is_(None))
    )
    out = [str(row.role) for row in rows]
    if project_id:
        member = session.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
            )
        )
        if member is not None and member.role:
            out.append(str(member.role))
    return sorted(set(out))


def is_member(session: DbSession, project_id: str, user_id: str) -> bool:
    return (
        session.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
            )
        )
        is not None
    )


def holds_role(session: DbSession, user_id: str, role: str) -> bool:
    """Whether the person holds ``role`` anywhere — the check that gates a role's own grants."""
    return (
        session.scalar(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role == role).limit(1)
        )
        is not None
    )


def has_restricted_grant(
    session: DbSession, project_id: str, user_id: str, classification: str, *, now: datetime | None = None
) -> bool:
    """FR-SEC-003: an expired or revoked grant is no grant at all."""
    moment = now or utcnow()
    row = session.scalar(
        select(RestrictedGrant).where(
            RestrictedGrant.project_id == project_id,
            RestrictedGrant.user_id == user_id,
            RestrictedGrant.classification == classification,
        )
    )
    if row is None or row.revoked_at is not None:
        return False
    return row.expires_at is None or row.expires_at > moment


def active_exemption(
    session: DbSession,
    project_id: str,
    waiver: str,
    environment_key: str = "",
    *,
    now: datetime | None = None,
) -> ProductionExemption | None:
    """CP-WF-020's waiver, if one is standing for this project and precondition.

    An exemption that covers the whole project is stored with an empty ``environment_key``; a
    named one is narrower, and the narrower row is what a Run's protection view reports.
    """
    moment = now or utcnow()
    rows = session.scalars(
        select(ProductionExemption).where(
            ProductionExemption.project_id == project_id,
            ProductionExemption.waiver == waiver,
        )
    )
    for row in rows:
        if row.revoked_at is not None:
            continue
        if row.expires_at is not None and row.expires_at <= moment:
            continue
        if not row.environment_key or row.environment_key == environment_key:
            return row
    return None


def list_users(session: DbSession, *, limit: int = 100, offset: int = 0) -> list[User]:
    return list(
        session.scalars(select(User).order_by(User.subject).offset(offset).limit(limit))
    )


def list_members(session: DbSession, project_id: str) -> list[ProjectMember]:
    return list(
        session.scalars(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
    )


def user_role_rows(session: DbSession, user_id: str) -> list[UserRole]:
    return list(session.scalars(select(UserRole).where(UserRole.user_id == user_id)))


def grant_rows(session: DbSession, project_id: str) -> list[RestrictedGrant]:
    return list(
        session.scalars(
            select(RestrictedGrant).where(RestrictedGrant.project_id == project_id)
        )
    )


def exemption_rows(session: DbSession, project_id: str) -> list[ProductionExemption]:
    return list(
        session.scalars(
            select(ProductionExemption).where(ProductionExemption.project_id == project_id)
        )
    )


__all__ = [
    "active_exemption",
    "by_subject",
    "exemption_rows",
    "get_by_subject_any_status",
    "grant_rows",
    "holds_role",
    "has_restricted_grant",
    "is_member",
    "list_members",
    "list_users",
    "roles_of",
    "user_role_rows",
]
