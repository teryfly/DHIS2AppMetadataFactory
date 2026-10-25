"""Identity and permission contracts (CP-IAM-002, FR-SEC-001~005, UI-ADM's 用户与角色).

Every response is typed and camelCase like the rest of the surface (S5IF-G01/G03), and none of
them carries a secret: a password is write-only, a session token is returned exactly once by the
login call that minted it, and a digest never appears anywhere.

The session view answers both authenticated and unauthenticated shapes — ``authMode`` says
which regime is running — because S5 has to render "sign in", "you are signed in as X", and
"this deployment does not use accounts" from one field rather than by inferring it from a 401.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from factory_control.errors import ValidationFailedError
from factory_control.rest.schemas.common import CamelModel, ListEnvelope


class LoginRequest(CamelModel):
    subject: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=512)


class SessionView(CamelModel):
    """Who the caller is, with what they may do next.

    ``csrfToken`` is filled in by the sign-in endpoints only.  ``GET /auth/session`` leaves it out:
    a reloaded page reads the value from the ``factory_csrf`` cookie instead, and having two sources
    for one value is how a client ends up sending a stale one.  Neither channel is readable by a
    cross-site page, which is the whole point of the double-submit check (FR-SEC-005).
    """

    authenticated: bool = False
    user_id: str | None = None
    subject: str | None = None
    display_name: str | None = None
    roles: list[str] = Field(default_factory=list)
    #: ``off`` | ``local`` | ``oidc`` | ``both`` (CP-IAM-001's modes, as configured)
    auth_mode: str = "off"
    expires_at: datetime | None = None
    csrf_token: str | None = None
    must_change_password: bool = False


class OidcRequestView(CamelModel):
    """What S5 redirects the browser to, plus the values it must echo back."""

    authorization_endpoint: str
    client_id: str
    redirect_uri: str
    response_type: str = "code"
    scope: str = "openid profile email"
    state: str = ""
    nonce: str = ""


class OidcExchangeRequest(CamelModel):
    code: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)


class UserView(CamelModel):
    id: str
    subject: str
    source: str = "local"
    display_name: str = ""
    email: str = ""
    status: str = "ACTIVE"
    must_change_password: bool = False
    locked: bool = False
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    #: What the account actually holds, so a revoke names one of these rather than a guess.
    #: ``projectId`` null is the organisation-wide grant (FR-SEC-004).
    roles: list[RoleAssignmentView] = Field(default_factory=list)


class UserList(ListEnvelope):
    items: list[UserView] = Field(default_factory=list)


class UserCreateRequest(CamelModel):
    subject: str = Field(min_length=1, max_length=255)
    display_name: str = ""
    email: str = ""
    #: required for ``source=local``, refused for ``source=oidc`` (FR-SEC-001's two ways in)
    password: str | None = None
    source: str = "local"
    must_change_password: bool = False

    @model_validator(mode="after")
    def _source_shape(self) -> UserCreateRequest:
        if self.source not in ("local", "oidc"):
            raise ValidationFailedError(
                "source is 'local' or 'oidc'",
                details={"fields": [{"path": "source", "message": self.source}]},
            )
        if self.source == "local" and not self.password:
            raise ValidationFailedError(
                "a local account needs a password",
                details={"fields": [{"path": "password", "message": "required for a local account"}]},
            )
        if self.source == "oidc" and self.password:
            raise ValidationFailedError(
                "an OIDC account has no Factory password",
                details={"fields": [{"path": "password", "message": "not allowed for source oidc"}]},
            )
        return self


class UserPatchRequest(CamelModel):
    display_name: str | None = None
    email: str | None = None
    status: str | None = None

    @model_validator(mode="after")
    def _status_known(self) -> UserPatchRequest:
        if self.status is not None and self.status not in ("ACTIVE", "DISABLED"):
            raise ValidationFailedError(
                "status is ACTIVE or DISABLED",
                details={"fields": [{"path": "status", "message": self.status}]},
            )
        return self


class PasswordSetRequest(CamelModel):
    password: str = Field(min_length=8, max_length=512)
    #: an administrator's reset leaves the account on a forced change
    must_change_password: bool = False


class RoleAssignmentRequest(CamelModel):
    role: str = Field(min_length=1)
    #: NULL grants the role organisation-wide; a project id scopes it to that project
    project_id: str | None = None


class RoleAssignmentView(CamelModel):
    """What one assignment did: the role, its scope, and whether it now stands."""

    user_id: str
    role: str
    project_id: str | None = None
    revoked: bool | None = None


class RoleView(CamelModel):
    name: str
    actions: list[str] = Field(default_factory=list)


class RoleList(ListEnvelope):
    items: list[RoleView] = Field(default_factory=list)


class MemberView(CamelModel):
    id: str | None = None
    project_id: str | None = None
    user_id: str
    role: str | None = None
    subject: str = ""


class MemberList(ListEnvelope):
    items: list[MemberView] = Field(default_factory=list)


class MemberPutRequest(CamelModel):
    """The whole membership list, replaced: a PUT that left rows behind could not remove anyone."""

    members: list[MemberView] = Field(default_factory=list)

    @model_validator(mode="after")
    def _users_named(self) -> MemberPutRequest:
        missing = [index for index, item in enumerate(self.members) if not item.user_id]
        if missing:
            raise ValidationFailedError(
                "every member needs userId",
                details={"fields": [{"path": f"members[{index}].userId", "message": "required"} for index in missing]},
            )
        return self


class GrantView(CamelModel):
    id: str | None = None
    project_id: str | None = None
    user_id: str
    classification: str
    reason: str = ""
    granted_by: str = ""
    expires_at: datetime | None = None
    revoked: bool = False
    subject: str = ""


class GrantList(ListEnvelope):
    items: list[GrantView] = Field(default_factory=list)


class GrantCreateRequest(CamelModel):
    user_id: str = Field(min_length=1)
    classification: str = Field(min_length=1)
    reason: str = ""
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _classification_known(self) -> GrantCreateRequest:
        if self.classification not in ("RESTRICTED", "PHI_PII"):
            raise ValidationFailedError(
                "classification is RESTRICTED or PHI_PII",
                details={
                    "fields": [{"path": "classification", "message": self.classification}],
                },
            )
        return self


class ExemptionView(CamelModel):
    id: str
    project_id: str | None = None
    environment_key: str = ""
    waiver: str = "NON_PRODUCTION_VERIFIED"
    reason: str = ""
    granted_by: str = ""
    expires_at: datetime | None = None
    revoked: bool = False


class ExemptionList(ListEnvelope):
    items: list[ExemptionView] = Field(default_factory=list)


class ExemptionCreateRequest(CamelModel):
    reason: str = Field(min_length=1)
    waiver: str = "NON_PRODUCTION_VERIFIED"
    environment_key: str = ""
    expires_at: datetime | None = None


class ClearedView(CamelModel):
    """A mutation that answers with only what it did: a logout, a removal, a revocation.

    Typed per field rather than a free ``details`` map (S5IF-G01): a generated client needs to
    know that a revoked restricted grant names its classification and a logout names its session,
    and neither is a key it has to guess.
    """

    cleared: bool = True
    session_id: str | None = None
    auth_mode: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    classification: str | None = None
    waiver: str | None = None
