"""Identity and permissions (CP-IAM-002, 总体 SRS 21.1).

Split by question rather than by table:

:mod:`.passwords`
    how a local credential is stored;
:mod:`.directory`
    who a name is, what roles they hold, whether a grant still counts;
:mod:`.roles`
    what each built-in role may do, keyed on 附录 B's action names;
:mod:`.policy`
    the object that replaces :class:`factory_control.iam.AllowAllPolicy` when ``FACTORY_AUTH_MODE``
    is not ``off`` — the one thing the handler paths already call;
:mod:`.sessions`
    the two clocks FR-SEC-005 asks for, and the CSRF secret that makes a cookie safe to send;
:mod:`.authn`
    the three ways in (declared name, local password, organisation OIDC);
:mod:`.service`
    the audited mutations behind ``/auth``, ``/users`` and a project's members and grants.
"""

from __future__ import annotations

from factory_control.identity.authn import (
    AuthenticationFailed,
    AuthorizationCode,
    LocalAccounts,
    NoAuthentication,
    OidcRelyingParty,
    PasswordCredential,
    Principal,
    PrincipalUnavailable,
)
from factory_control.identity.policy import Decision, RolePermissionPolicy
from factory_control.identity.roles import BUILT_IN_ROLES

__all__ = [
    "BUILT_IN_ROLES",
    "AuthorizationCode",
    "AuthenticationFailed",
    "Decision",
    "LocalAccounts",
    "NoAuthentication",
    "OidcRelyingParty",
    "PasswordCredential",
    "Principal",
    "PrincipalUnavailable",
    "RolePermissionPolicy",
]
