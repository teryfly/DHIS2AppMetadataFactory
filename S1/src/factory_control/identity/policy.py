"""The M4 authorization policy (CP-IAM-002, FR-SEC-002/003/004).

Installed in place of :class:`factory_control.iam.AllowAllPolicy` when
``FACTORY_AUTH_MODE`` is not ``off``; every handler already calls ``iam.authorize`` with an
附录 B action name, so nothing outside this file decides who may do what.

Three decisions are worth stating because they are the ones a reader would otherwise have to
guess at:

* **an unresolvable operator is a denial, not an error.**  A name that is not an active account
  has no roles, and "no roles" answers every question with no.  Raising would turn a stale
  token into a 500 and tell an attacker more than a 403 does.
* **project isolation is membership, not a role** (FR-SEC-004).  An organisation-wide
  DHIS2 Implementer may act *in the projects they belong to*; being an Implementer does not
  open every project, and a Platform Administrator is no exception — the roles they hold are
  about the platform (users, environments, governance, settings), and reaching into a project
  still means being a member of it.
* **a dotted action may be granted by a prefix, never by a wildcard of its own choosing.**
  ``gate.approve`` grants ``gate.approve.DEPLOY_GATE.PRODUCTION`` because the SRS's own action
  names are formed by appending the narrower part (CP-GATE-012), so the prefix *is* the
  coarser grant.  What is not allowed is a role table that stores ``*`` and hopes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session as DbSession

from factory_control import iam
from factory_control.foundation import current_session, has_active_uow, unit_of_work
from factory_control.identity import directory, roles

#: Actions that only mean something about *one* project, so they need membership as well as a
#: role.  Anything absent here is a platform action and is decided by the role alone.
PROJECT_SCOPED: frozenset[str] = frozenset(
    {
        iam.PROJECT_VIEW,
        iam.PROJECT_EDIT,
        iam.PROJECT_UPGRADE_MODE,
        iam.PROJECT_ARCHIVE,
        iam.PROJECT_DELETE,
        iam.PROJECT_EXPORT,
        iam.INPUT_UPLOAD,
        iam.INPUT_PARSE,
        iam.INPUT_CONFIRM_MAPPING,
        iam.STAGE_RUN,
        iam.STAGE_CHAT,
        iam.PATCH_SUBMIT,
        iam.QUESTION_ANSWER,
        iam.DEPLOYMENT_CREATE,
        iam.DEPLOYMENT_RUN_DRIFT_CHECK,
        iam.CHANGE_REQUEST_CREATE,
        iam.CHANGE_REQUEST_APPROVE,
        iam.ARTIFACT_READ,
        iam.ARTIFACT_READ_RESTRICTED,
        iam.ARTIFACT_READ_PHI,
        iam.ARTIFACT_PURGE,
        iam.ARTIFACT_EXPLAIN,
        iam.AGENT_CANCEL,
        iam.AUDIT_VIEW,
        iam.AUDIT_VERIFY,
        iam.REGISTRY_BINDING_SET,
        iam.UID_POOL_REPLENISH,
        iam.BUDGET_SET,
        iam.PRODUCTION_EXEMPTION_MANAGE,
        iam.RESTRICTED_GRANT_MANAGE,
    }
)


class SessionProvider(Protocol):
    """How the policy reaches a database session — injectable so tests need no database."""

    def __call__(self) -> Any: ...


@dataclass(frozen=True)
class Decision:
    """Why an action was allowed or refused, so a 403 and ``allowed-actions`` say the same thing."""

    allowed: bool
    reason_code: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"allowed": self.allowed}
        if not self.allowed:
            out["reasonCode"] = self.reason_code
            out["details"] = dict(self.details)
        return out


DENIED_NO_ACCOUNT = "NO_SUCH_ACCOUNT"
DENIED_NO_ROLE = "PERMISSION_DENIED"
DENIED_NOT_MEMBER = "MEMBERSHIP_REQUIRED"
DENIED_NO_GRANT = "GRANT_REQUIRED"


class RolePermissionPolicy:
    """``iam.PermissionPolicy`` over the built-in roles, membership and separate grants."""

    def __init__(self, *, session_provider: SessionProvider | None = None) -> None:
        self._session_provider = session_provider or _default_session

    # --- the hook CP-IAM-001 fixed in place -------------------------------------

    def is_allowed(self, *, operator: str, project_id: str | None, action: str) -> bool:
        return self.explain(operator=operator, project_id=project_id, action=action).allowed

    def explain(
        self, *, operator: str, project_id: str | None, action: str
    ) -> Decision:
        """The same answer as :meth:`is_allowed`, with the reason.

        ``allowed-actions`` needs the reason (CP-WF-026 asks for one per unavailable control and
        S5IF-G10 makes that payload the only source of button state), and asking twice for a
        yes/no would mean reading the database twice per action.
        """
        if not action:
            return Decision(False, DENIED_NO_ROLE, {"action": action})
        with self._session_provider() as session:
            user = directory.by_subject(session, operator)
            if user is None:
                return Decision(False, DENIED_NO_ACCOUNT, {"operator": operator})
            held = directory.roles_of(session, user.id, project_id)
            granted = self._granted_by_roles(held, action)
            if not granted:
                return Decision(False, DENIED_NO_ROLE, {"action": action, "roles": held})
            if action in (iam.ARTIFACT_READ_RESTRICTED, iam.ARTIFACT_READ_PHI):
                classification = (
                    "PHI_PII"
                    if action == iam.ARTIFACT_READ_PHI
                    else "RESTRICTED"
                )
                if project_id is None or not directory.has_restricted_grant(
                    session, project_id, user.id, classification
                ):
                    # FR-SEC-003: the role is not enough, and the grant is per classification
                    return Decision(
                        False, DENIED_NO_GRANT, {"action": action, "classification": classification}
                    )
            # FR-SEC-004: a role, however wide, still needs membership in the project it acts on.
            # A project-scoped grant implies membership, because scoping a role adds the member.
            if action in PROJECT_SCOPED and project_id is not None and not directory.is_member(
                session, project_id, user.id
            ):
                return Decision(False, DENIED_NOT_MEMBER, {"action": action, "projectId": project_id})
            return Decision(True)

    def actions_for(
        self, operator: str, project_id: str | None, candidates: tuple[str, ...]
    ) -> dict[str, Decision]:
        """Batch evaluation for one ``allowed-actions`` payload: one session, one read per person.

        Asking :meth:`explain` per action would open a transaction per control and make the
        workbench's own availability call the most expensive read on the page.
        """
        out: dict[str, Decision] = {}
        with self._session_provider() as session:
            user = directory.by_subject(session, operator)
            if user is None:
                return {
                    name: Decision(False, DENIED_NO_ACCOUNT, {"operator": operator})
                    for name in candidates
                }
            held = directory.roles_of(session, user.id, project_id)
            member = (
                True
                if project_id is None
                else directory.is_member(session, project_id, user.id)
            )
            grants = (
                set()
                if project_id is None or user is None
                else _classifications_granted(session, project_id, user.id)
            )
            for name in candidates:
                if not self._granted_by_roles(held, name):
                    out[name] = Decision(False, DENIED_NO_ROLE, {"action": name, "roles": held})
                elif name in (iam.ARTIFACT_READ_RESTRICTED, iam.ARTIFACT_READ_PHI):
                    wanted = "PHI_PII" if name == iam.ARTIFACT_READ_PHI else "RESTRICTED"
                    out[name] = (
                        Decision(True)
                        if wanted in grants
                        else Decision(False, DENIED_NO_GRANT, {"classification": wanted})
                    )
                elif name in PROJECT_SCOPED and project_id is not None and not member:
                    out[name] = Decision(False, DENIED_NOT_MEMBER, {"action": name})
                else:
                    out[name] = Decision(True)
        return out

    @staticmethod
    def _granted_by_roles(held: list[str], action: str) -> bool:
        """Whether any held role grants ``action``, a prefix of it, or its coarse form."""
        for role in held:
            allowed = roles.actions_for(role)
            if action in allowed or _prefix_granted(allowed, action):
                return True
        return False


def _prefix_granted(allowed: frozenset[str], action: str) -> bool:
    """``gate.approve`` and ``gate.approve.DEPLOY_GATE`` both cover the full name.

    Only the prefixes that 附录 B itself forms by appending are consulted, so an action name
    with an unexpected dot in it cannot accidentally inherit a broader grant.
    """
    parts = action.split(".")
    return any(".".join(parts[:length]) in allowed for length in range(len(parts) - 1, 0, -1))


def _classifications_granted(session: DbSession, project_id: str, user_id: str) -> set[str]:
    out: set[str] = set()
    for classification in ("RESTRICTED", "PHI_PII"):
        if directory.has_restricted_grant(session, project_id, user_id, classification):
            out.add(classification)
    return out


def _default_session() -> Any:
    """Join the caller's transaction if there is one, otherwise open a short read.

    A request handler may call ``authorize`` before, after or inside its own unit of work; a
    policy that always opened a fresh one would deadlock against an open transaction on the same
    connection, and one that never opened one would fail outside a handler.
    """
    if has_active_uow():
        return _JoinedSession(current_session())
    return unit_of_work()


class _JoinedSession:
    """Reuses the live session without committing it on exit."""

    def __init__(self, session: DbSession) -> None:
        self._session = session

    def __enter__(self) -> DbSession:
        return self._session

    def __exit__(self, *_exc: object) -> None:
        return None


__all__ = [
    "DENIED_NO_ACCOUNT",
    "DENIED_NO_GRANT",
    "DENIED_NO_ROLE",
    "DENIED_NOT_MEMBER",
    "Decision",
    "PROJECT_SCOPED",
    "RolePermissionPolicy",
]
