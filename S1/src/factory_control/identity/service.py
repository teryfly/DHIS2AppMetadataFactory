"""Who may be changed, and by whom (CP-IAM-002, 总体 SRS 21.4's 用户与角色变更).

Every mutation here is audited in the caller's transaction, because a permission that cannot be
traced to a person and a moment is a permission nobody can review.  Two rules shape the code:

* **a role name is checked before it is stored.**  The roles are 内置 (FR-SEC-002): an unknown
  name is a typo or an attempt to mint a privilege the SRS does not define, and both are refused
  with the list of what exists rather than stored as a role that grants nothing;
* **disabling a person ends their sessions.**  CP-IAM-001's hook answers from the role rows on
  every request, so revocation is immediate; but a *stolen* bearer token would keep working
  until it expired if ``revoke_for_user`` were not called here, which is the difference between
  "disabled" and "disabled eventually".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from factory_control.errors import NotFoundControlError, ValidationFailedError
from factory_control.foundation import new_id, utcnow
from factory_control.identity import directory, passwords, roles, sessions
from factory_control.models import (
    ProductionExemption,
    Project,
    ProjectMember,
    RestrictedGrant,
    User,
    UserRole,
)

#: The CP-WF-020 precondition an exemption may waive.  Named here because the SRS lists no other.
NON_PRODUCTION_VERIFIED = "NON_PRODUCTION_VERIFIED"
EXEMPTION_WAIVERS = (NON_PRODUCTION_VERIFIED,)
GRANT_CLASSIFICATIONS = ("RESTRICTED", "PHI_PII")


def _require_password_policy(password: str | None) -> None:
    """The one place a stored password is judged, for every path that stores one.

    Both the administrator's create and the user's own change come through here, because a rule
    written only on the request schema would leave the bootstrap account — built by calling
    :func:`create_user` in-process — outside it.  The message names the field and the requirement
    and never repeats what was typed: an error body quoting a password puts it in the client's log,
    the proxy's, and whatever the operator pastes when asking for help (FR-SEC-008).
    """
    supplied = password or ""
    if len(supplied) < passwords.MIN_PASSWORD_CHARS or not supplied.strip():
        raise ValidationFailedError(
            f"a password must be at least {passwords.MIN_PASSWORD_CHARS} characters",
            details={"fields": [{"path": "password", "message": "too short"}]},
        )


def create_user(
    session: DbSession, operator: str,
    *,
    subject: str,
    display_name: str = "",
    email: str = "",
    password: str | None = None,
    source: str = "local",
    must_change_password: bool = False,
) -> dict[str, Any]:
    """A new account.  A local one needs a password; an OIDC one must not carry a digest."""
    name = (subject or "").strip()
    if not name:
        raise ValidationFailedError("a subject is required", details={"fields": [{"path": "subject", "message": "required"}]})
    if source not in ("local", "oidc"):
        raise ValidationFailedError(
            "source is 'local' or 'oidc'", details={"fields": [{"path": "source", "message": source}]}
        )
    if directory.get_by_subject_any_status(session, name) is not None:
        # unique across sources on purpose: an authorization decision must not depend on which
        # of two same-named accounts the lookup happened to read
        raise ValidationFailedError(
            f"{name!r} is already an account in this Factory",
            details={"fields": [{"path": "subject", "message": "already taken"}]},
        )
    if source == "local" and not password:
        raise ValidationFailedError(
            "a local account needs a password",
            details={"fields": [{"path": "password", "message": "required for a local account"}]},
        )
    if password:
        _require_password_policy(password)
    user = User(
        id=new_id("usr"),
        subject=name,
        source=source,
        display_name=(display_name or name).strip(),
        email=(email or "").strip(),
        password_hash=passwords.hash_password(password) if password else None,
        password_algo="scrypt" if password else None,
        status="ACTIVE",
        must_change_password=bool(must_change_password and password),
    )
    session.add(user)
    session.flush()
    _audit(
        session,
        operator,
        "cp.user.created",
        object_id=user.id,
        after={"subject": user.subject, "source": user.source, "status": user.status},
    )
    return user_view(user, session)


def set_password(session: DbSession, operator: str, user_id: str, password: str, *, must_change: bool = False) -> dict[str, Any]:
    """Set a local account's password — the administrator's reset and the user's own change.

    The digest is written and the old one is *replaced*: an account that can log in with two
    historical passwords cannot be revoked by a reset.
    """
    user = _require_user(session, user_id)
    if user.source != "local":
        raise ValidationFailedError(
            "an OIDC account has no Factory password",
            details={"fields": [{"path": "password", "message": f"user source is {user.source}"}]},
        )
    if not password:
        raise ValidationFailedError(
            "a password is required",
            details={"fields": [{"path": "password", "message": "required"}]},
        )
    _require_password_policy(password)
    user.password_hash = passwords.hash_password(password)
    user.password_algo = "scrypt"
    user.must_change_password = bool(must_change)
    user.failed_attempts = 0
    user.locked_until = None
    # FR-SEC-005's neighbour: a reset is exactly when a hijacked session must die
    revoked = sessions.revoke_for_user(session, user.id, now=utcnow())
    session.flush()
    _audit(
        session,
        operator,
        "cp.user.password_set",
        object_id=user.id,
        project_id=None,
        after={"subject": user.subject, "sessionsRevoked": revoked},
    )
    return user_view(user, session)


def update_user(
    session: DbSession, operator: str, user_id: str, *, display_name: str | None = None, email: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    user = _require_user(session, user_id)
    before = {"display_name": user.display_name, "email": user.email, "status": user.status}
    if email is not None:
        user.email = email.strip()
    if display_name is not None:
        user.display_name = display_name.strip() or user.subject
    if status is not None:
        if status not in ("ACTIVE", "DISABLED"):
            raise ValidationFailedError(
                "status is ACTIVE or DISABLED", details={"fields": [{"path": "status", "message": status}]}
            )
        user.status = status
    session.flush()
    revoked = 0
    if status == "DISABLED":
        revoked = sessions.revoke_for_user(session, user.id, now=utcnow())
    event = "cp.user.disabled" if status == "DISABLED" else "cp.user.updated"
    _audit(
        session,
        operator,
        event,
        object_id=user.id,
        before=before,
        after={"display_name": user.display_name, "email": user.email, "status": user.status, "sessionsRevoked": revoked},
    )
    return user_view(user, session)


def assign_role(
    session: DbSession, operator: str, user_id: str, role: str, *, project_id: str | None = None
) -> dict[str, Any]:
    """Grant a built-in role, organisation-wide or inside one project (FR-SEC-002)."""
    user = _require_user(session, user_id)
    _require_role(role)
    existing = next(
        (
            row
            for row in directory.user_role_rows(session, user.id)
            if row.role == role and row.project_id == project_id
        ),
        None,
    )
    if existing is not None:
        return role_view(existing)
    row = UserRole(
        id=new_id("uro"),
        user_id=user.id,
        role=role,
        project_id=project_id,
        granted_by=operator or "system",
        granted_at=utcnow(),
    )
    session.add(row)
    if project_id:
        # `roles_of` reads a project role off the *membership row* (directory.py), never off a
        # user_roles row carrying a project_id — so a project-scoped grant that only inserted the
        # role row looked fine in `GET /users/{id}/roles` and granted nothing.  The role has to be
        # written where the policy reads it (FR-SEC-002, FR-SEC-004).
        _ensure_membership(session, project_id, user.id, role=role, operator=operator)
    session.flush()
    _audit(
        session,
        operator,
        "cp.user.role_changed",
        object_id=user.id,
        project_id=project_id,
        after={"role": role, "scope": project_id or "organization", "decision": "granted"},
    )
    return role_view(row)


def revoke_role(
    session: DbSession, operator: str, user_id: str, role: str, *, project_id: str | None = None
) -> dict[str, Any]:
    user = _require_user(session, user_id)
    for row in directory.user_role_rows(session, user.id):
        if row.role == role and row.project_id == project_id:
            session.delete(row)
    session.flush()
    _audit(
        session,
        operator,
        "cp.user.role_changed",
        object_id=user.id,
        project_id=project_id,
        after={"role": role, "scope": project_id or "organization", "decision": "revoked"},
    )
    return {"userId": user.id, "role": role, "projectId": project_id, "revoked": True}


def add_member(
    session: DbSession, operator: str, project_id: str, user_id: str, *, role: str | None = None
) -> dict[str, Any]:
    """FR-SEC-004: membership is what isolates projects from each other."""
    user = _require_user(session, user_id)
    if role is not None:
        _require_role(role)
    row = _ensure_membership(session, project_id, user.id, role=role, operator=operator)
    session.flush()
    _audit(
        session,
        operator,
        "cp.project.member_added",
        object_id=row.id,
        project_id=project_id,
        after={"subject": user.subject, "role": row.role},
    )
    return member_view(row, subject=user.subject)


def remove_member(session: DbSession, operator: str, project_id: str, user_id: str) -> dict[str, Any]:
    row = session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if row is None:
        raise NotFoundControlError(
            f"user {user_id} is not a member of {project_id}", details={"userId": user_id}
        )
    session.delete(row)
    # the project-scoped roles of a person who is no longer in the project grant nothing, and
    # leaving the rows behind would make a later re-add silently restore them
    for role_row in directory.user_role_rows(session, user_id):
        if role_row.project_id == project_id:
            session.delete(role_row)
    session.flush()
    _audit(session, operator, "cp.project.member_removed", object_id=user_id, project_id=project_id, after={"removed": True})
    return {"projectId": project_id, "userId": user_id, "removed": True}


def grant_restricted(
    session: DbSession, operator: str,
    project_id: str,
    user_id: str,
    classification: str,
    *,
    reason: str,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """FR-SEC-003: a PHI_PII or RESTRICTED read needs its own recorded, reasoned grant."""
    user = _require_user(session, user_id)
    if classification not in GRANT_CLASSIFICATIONS:
        raise ValidationFailedError(
            f"classification is one of {list(GRANT_CLASSIFICATIONS)}",
            details={"fields": [{"path": "classification", "message": classification}]},
        )
    if not (reason or "").strip():
        raise ValidationFailedError(
            "a restricted read grant requires a reason",
            details={"fields": [{"path": "reason", "message": "required"}]},
        )
    _ensure_membership(session, project_id, user.id)
    row = session.scalar(
        select(RestrictedGrant).where(
            RestrictedGrant.project_id == project_id,
            RestrictedGrant.user_id == user.id,
            RestrictedGrant.classification == classification,
        )
    )
    if row is None:
        row = RestrictedGrant(
            id=new_id("rgt"), project_id=project_id, user_id=user.id, classification=classification
        )
        session.add(row)
    row.granted_by = operator or "system"
    row.reason = reason.strip()
    row.expires_at = expires_at
    row.revoked_at = None
    session.flush()
    _audit(
        session,
        operator,
        "cp.artifact.grant_created",
        object_id=row.id,
        project_id=project_id,
        after={"subject": user.subject, "classification": classification, "reason": row.reason},
    )
    return grant_view(row, subject=user.subject)


def revoke_restricted(session: DbSession, operator: str, project_id: str, user_id: str, classification: str) -> dict[str, Any]:
    row = session.scalar(
        select(RestrictedGrant).where(
            RestrictedGrant.project_id == project_id,
            RestrictedGrant.user_id == user_id,
            RestrictedGrant.classification == classification,
        )
    )
    if row is not None:
        row.revoked_at = utcnow()
        session.flush()
    _audit(
        session,
        operator,
        "cp.artifact.grant_revoked",
        object_id=row.id if row is not None else user_id,
        project_id=project_id,
        after={"userId": user_id, "classification": classification},
    )
    return {"projectId": project_id, "userId": user_id, "classification": classification, "revoked": True}


def grant_exemption(
    session: DbSession, operator: str,
    project_id: str,
    *,
    waiver: str = NON_PRODUCTION_VERIFIED,
    environment_key: str = "",
    reason: str,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """CP-WF-020's waiver, granted by an administrator and time-boxed by default.

    The SRS admits the exemption without describing an eternal one: a rule as serious as "verify
    in a non-production environment first" is stepped around for a stated reason and a window, and
    the window is what makes the next reviewer ask again.
    """
    if waiver not in EXEMPTION_WAIVERS:
        raise ValidationFailedError(
            f"waiver is one of {list(EXEMPTION_WAIVERS)}",
            details={"fields": [{"path": "waiver", "message": waiver}]},
        )
    if not (reason or "").strip():
        raise ValidationFailedError(
            "an exemption requires a reason",
            details={"fields": [{"path": "reason", "message": "required"}]},
        )
    row = ProductionExemption(
        id=new_id("pex"),
        project_id=project_id,
        environment_key=environment_key or "",
        waiver=waiver,
        reason=reason.strip(),
        granted_by=operator or "system",
        expires_at=expires_at or _default_exemption_expiry(),
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        operator,
        "cp.production.exemption_granted",
        object_id=row.id,
        project_id=project_id,
        after={"waiver": waiver, "environmentKey": row.environment_key, "reason": row.reason, "expiresAt": row.expires_at},
    )
    return exemption_view(row)


def revoke_exemption(session: DbSession, operator: str, exemption_id: str) -> dict[str, Any]:
    row: ProductionExemption | None = session.get(ProductionExemption, exemption_id)
    if row is None:
        raise NotFoundControlError(
            f"exemption {exemption_id} not found", details={"exemptionId": exemption_id}
        )
    row.revoked_at = utcnow()
    session.flush()
    _audit(
        session,
        operator,
        "cp.production.exemption_revoked",
        object_id=row.id,
        project_id=row.project_id,
        after={"waiver": row.waiver, "revoked": True},
    )
    return {"exemptionId": row.id, "revoked": True}


# --- projections ----------------------------------------------------------------


def user_view(user: User, session: DbSession | None = None) -> dict[str, Any]:
    """What S5 may see about an account.  No digest, no lock counters beyond the fact.

    ``roles`` rides along when a session is given because the Administration page has to be able
    to *revoke* one: `DELETE /users/{id}/roles` names a role and an optional project scope, and a
    console that could not read what an account holds could only guess at what it was deleting.
    """
    view = {
        "id": user.id,
        "subject": user.subject,
        "source": user.source,
        "displayName": user.display_name,
        "email": user.email,
        "status": user.status,
        "mustChangePassword": bool(user.must_change_password),
        "locked": user.locked_until is not None and user.locked_until > utcnow(),
        "lastLoginAt": user.last_login_at,
        "createdAt": user.created_at,
    }
    if session is not None:
        view["roles"] = [role_view(row) for row in directory.user_role_rows(session, user.id)]
    else:
        view["roles"] = []
    return view


def role_view(row: UserRole) -> dict[str, Any]:
    """One held role.  ``revoked`` is deliberately absent here.

    ``revoke_role`` deletes the row rather than flagging it, so every row this returns is a live
    grant; putting ``revoked: false`` on each item would hint that it might not be, which is a thing
    no reader can check.  The field belongs to the response of an assignment (:func:`revoke_role`),
    where it says what that one call did.
    """
    return {"userId": row.user_id, "role": row.role, "projectId": row.project_id}


def member_view(row: ProjectMember, *, subject: str = "") -> dict[str, Any]:
    return {"id": row.id, "projectId": row.project_id, "userId": row.user_id, "role": row.role, "subject": subject}


def grant_view(row: RestrictedGrant, *, subject: str = "") -> dict[str, Any]:
    return {
        "id": row.id,
        "projectId": row.project_id,
        "userId": row.user_id,
        "classification": row.classification,
        "reason": row.reason,
        "grantedBy": row.granted_by,
        "expiresAt": row.expires_at,
        "revoked": row.revoked_at is not None,
        "subject": subject,
    }


def exemption_view(row: ProductionExemption) -> dict[str, Any]:
    return {
        "id": row.id,
        "projectId": row.project_id,
        "environmentKey": row.environment_key,
        "waiver": row.waiver,
        "reason": row.reason,
        "grantedBy": row.granted_by,
        "expiresAt": row.expires_at,
        "revoked": row.revoked_at is not None,
    }


def roles_with_actions() -> list[dict[str, Any]]:
    """The role catalogue as data, so S5 renders the permission matrix without guessing (UI-ADM)."""
    return [
        {"name": name, "actions": sorted(roles.actions_for(name))} for name in roles.BUILT_IN_ROLES
    ]


def _require_user(session: DbSession, user_id: str) -> User:
    user: User | None = session.get(User, user_id)
    if user is None:
        raise NotFoundControlError(f"user {user_id} not found", details={"userId": user_id})
    return user


def _require_role(role: str) -> None:
    if not roles.is_known_role(role):
        raise ValidationFailedError(
            "role is not a built-in Factory role",
            details={"fields": [{"path": "role", "message": role}], "known": list(roles.BUILT_IN_ROLES)},
        )


def _require_project(session: DbSession, project_id: str) -> Project:
    """A project the caller may be pointed at, or the answer the console can show.

    A project-scoped role implies membership (CP-IAM-002), and membership is a row with a foreign key
    at the other end.  Without this check a mistyped project id surfaced as an unmapped
    ``IntegrityError``: the operator got a 500 with a correlation id and nothing to correct, while the
    thing they actually did wrong was name a project that does not exist.  Found by probing the
    account screens' project-id field against the live service.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise NotFoundControlError(
            f"no such project {project_id!r}",
            details={"fields": [{"path": "projectId", "message": "unknown project"}]},
        )
    return project


def _ensure_membership(
    session: DbSession, project_id: str, user_id: str, *, role: str | None = None, operator: str = "system"
) -> ProjectMember:
    _require_project(session, project_id)
    row = session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if row is None:
        row = ProjectMember(
            id=new_id("pmb"),
            project_id=project_id,
            user_id=user_id,
            role=role,
            added_by=operator or "system",
        )
        session.add(row)
        session.flush()
    elif role is not None:
        row.role = role
    return row


def _default_exemption_expiry() -> datetime:
    """One week, because an exemption that never ends is a rule change nobody reviewed."""
    return utcnow() + timedelta(days=7)


def _audit(
    session: DbSession,
    operator: str,
    event_type: str,
    *,
    object_id: str,
    project_id: str | None = None,
    object_type: str = "User",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Ride the caller's transaction (D-11): a permission change and its audit commit together.

    Written straight through S0 rather than by ``services.recording.write_audit``, because that
    helper takes a live :class:`factory_control.runtime.ControlContext` and this module has to
    work during start-up, before a runtime is assembled.
    """
    from factory_control.foundation import AuditEventInput, OperatorType, audit

    audit(
        AuditEventInput(
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            project_id=project_id,
            before=before,
            after=after,
            source_subsystem="S1",
            operator_type=OperatorType.USER,
            operator_name=operator,
        ),
        session=session,
    )


__all__ = [
    "EXEMPTION_WAIVERS",
    "GRANT_CLASSIFICATIONS",
    "NON_PRODUCTION_VERIFIED",
    "add_member",
    "assign_role",
    "create_user",
    "exemption_view",
    "grant_exemption",
    "grant_restricted",
    "grant_view",
    "member_view",
    "remove_member",
    "revoke_exemption",
    "revoke_restricted",
    "revoke_role",
    "role_view",
    "roles_with_actions",
    "set_password",
    "update_user",
    "user_view",
]
