"""Authenticators: local accounts, an OIDC organisation, and the MVP's name (FR-SEC-001).

``FACTORY_AUTH_MODE`` picks which of the three runs:

``off``
    :class:`NoAuthentication` — the MVP's declared operator name (CP-API-002) is the identity,
    and :class:`factory_control.iam.AllowAllPolicy` still answers every authorisation.  This is
    the mode a deployment that has not adopted M4 keeps, and the reason the existing 385 tests
    and S5's current client are unchanged.
``local``
    :class:`LocalAccounts` against ``cp.users``.
``oidc``
    :class:`OidcRelyingParty` — the authorisation-code flow, then the userinfo endpoint over the
    TLS connection to the issuer to assert who the subject is.
``both``
    local for the bootstrap and break-glass accounts, OIDC for everyone else.

Why userinfo rather than verifying the ID token's signature here: the deployment adds no
cryptography dependency (1.2.0's decision), and a response read over the verified TLS channel
from the issuer that the token came from asserts the same subject.  The cost is that this path
trusts the issuer's transport rather than the issuer's key, so
:func:`factory_control.config.Settings.validate_runtime` refuses a non-HTTPS issuer and the
verification point is deliberately one function (:meth:`OidcRelyingParty._userinfo`) that a
deployment can replace with JWKS verification without touching anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session as DbSession

from factory_control.foundation import FactoryError, new_session, utcnow
from factory_control.identity import directory, passwords
from factory_control.models import User

#: Five wrong passwords in a row lock the account for this many minutes (FR-SEC-005's spirit:
#: the factory is reachable over HTTPS only, and this is what makes a guess rate-limited rather
#: than merely logged).
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


class AuthenticationFailed(FactoryError):
    """Credentials did not authenticate.  The reason is deliberately not returned to the client.

    "no such user" and "wrong password" are different facts, and telling them apart is half of
    what an account-enumeration attack needs; the audit row keeps the detail, the response does
    not (总体 SRS 21.4).
    """

    code = "UNAUTHENTICATED"


class PrincipalUnavailable(FactoryError):
    """The identity provider answered, but Factory has no account to map the answer to."""

    code = "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True)
class Principal:
    """Who the request is, once something vouched for it."""

    user_id: str
    subject: str
    display_name: str
    source: str

    @property
    def operator_name(self) -> str:
        """What audit, approvals and answers record as the operator (附录 A: M4 replaces the
        declared name with the authenticated one, not with an id)."""
        return self.display_name or self.subject


@dataclass(frozen=True)
class PasswordCredential:
    subject: str
    password: str


@dataclass(frozen=True)
class AuthorizationCode:
    code: str
    state: str
    redirect_uri: str
    nonce: str | None = None


Credential = PasswordCredential | AuthorizationCode


class Authenticator(Protocol):
    """One way in.  ``authenticate`` returns a :class:`Principal` or raises."""

    source: str

    def authenticate(self, session: DbSession, credential: Credential) -> Principal: ...


class LocalAccounts:
    """Passwords against ``cp.users`` (FR-SEC-001's 本地账号)."""

    source = "local"

    def authenticate(self, session: DbSession, credential: Credential) -> Principal:
        if not isinstance(credential, PasswordCredential):
            raise AuthenticationFailed("a local account needs a subject and a password")
        user = directory.get_by_subject_any_status(session, credential.subject)
        if user is None:
            # digest a throwaway too, so a missing account and a wrong password cost the same
            passwords.verify_password(credential.password, _DUMMY_DIGEST)
            raise AuthenticationFailed("the credentials did not match")
        if user.status != "ACTIVE":
            raise AuthenticationFailed("this account is not active")
        locked_until = user.locked_until
        if locked_until is not None and locked_until > utcnow():
            raise AuthenticationFailed("this account is temporarily locked")
        if not passwords.verify_password(credential.password, user.password_hash):
            _register_failure(user.id)
            raise AuthenticationFailed("the credentials did not match")
        if passwords.needs_rehash(user.password_hash):
            # the password is known right here and only here: re-digesting on the next login is
            # how a cost parameter is raised without asking anyone for it twice
            user.password_hash = passwords.hash_password(credential.password)
        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = utcnow()
        session.flush()
        return _principal(user, "local")


class NoAuthentication:
    """The MVP path: whoever the request declares it is (CP-API-002, D-16).

    It exists so the middleware has one code path — "ask the authenticator who this is" — instead
    of a mode check in front of every handler, which is exactly the shape CP-IAM-001 warned about.
    """

    source = "declared"

    def authenticate(self, session: DbSession, credential: Credential) -> Principal:
        _ = session
        if not isinstance(credential, PasswordCredential) or not credential.subject.strip():
            raise AuthenticationFailed("no operator name was declared")
        return Principal(
            user_id="",
            subject=credential.subject.strip(),
            display_name=credential.subject.strip(),
            source="declared",
        )


class OidcRelyingParty:
    """The organisation's single sign-on, as an authorisation-code relying party."""

    source = "oidc"

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        scopes: str = "openid profile email",
        http: Any = None,
        auto_provision: bool = False,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._auto_provision = auto_provision
        self._http = http

    def authorization_request(self, state: str, nonce: str, redirect_uri: str) -> dict[str, str]:
        """What S5 needs to redirect the browser with (nothing here stores the state)."""
        document = self._discovery()
        return {
            "authorizationEndpoint": str(document["authorization_endpoint"]),
            "clientId": self._client_id,
            "redirectUri": redirect_uri,
            "responseType": "code",
            "scope": self._scopes,
            "state": state,
            "nonce": nonce,
        }

    def authenticate(self, session: DbSession, credential: Credential) -> Principal:
        if not isinstance(credential, AuthorizationCode):
            raise AuthenticationFailed("an OIDC login needs an authorization code")
        document = self._discovery()
        tokens = self._token(session=session, document=document, credential=credential)
        claims = self._userinfo(document, str(tokens.get("access_token") or ""))
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise PrincipalUnavailable("the identity provider asserted no subject")
        user = directory.get_by_subject_any_status(session, subject)
        if user is None:
            if not self._auto_provision:
                # an unknown subject is not a reason to invent a role for it (FR-SEC-002)
                raise PrincipalUnavailable(
                    "this organisation identity has no Factory account yet",
                    details={"subject": subject},
                )
            user = _provision(session, subject=subject, claims=claims)
        if user.status != "ACTIVE":
            raise AuthenticationFailed("this account is not active")
        user.last_login_at = utcnow()
        session.flush()
        return _principal(user, "oidc")

    # --- the network edge, in one place ---------------------------------------

    def _discovery(self) -> dict[str, Any]:
        payload = self._get(f"{self._issuer}/.well-known/openid-configuration")
        for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
            if key not in payload:
                raise PrincipalUnavailable(
                    f"the issuer's discovery document has no {key}", details={"issuer": self._issuer}
                )
        return payload

    def _token(
        self, *, session: DbSession, document: dict[str, Any], credential: AuthorizationCode
    ) -> dict[str, Any]:
        _ = session
        body = {
            "grant_type": "authorization_code",
            "code": credential.code,
            "redirect_uri": credential.redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        response = self._client().post(document["token_endpoint"], data=body, timeout=15.0)
        if response.status_code >= 400:
            raise PrincipalUnavailable("the identity provider refused the authorization code")
        payload = _json(response)
        if not payload.get("access_token"):
            raise PrincipalUnavailable("the token response carried no access token")
        return payload

    def _userinfo(self, document: dict[str, Any], access_token: str) -> dict[str, Any]:
        """The single point a deployment may replace with JWKS verification of the ID token."""
        if not access_token:
            raise PrincipalUnavailable("no bearer token to assert the subject with")
        response = self._client().get(
            document["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        if response.status_code >= 400:
            raise PrincipalUnavailable("the identity provider refused the userinfo request")
        return _json(response)

    def _get(self, url: str) -> dict[str, Any]:
        response = self._client().get(url, timeout=15.0)
        if response.status_code >= 400:
            raise PrincipalUnavailable("the issuer's discovery document is not reachable")
        return _json(response)

    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client()
        return self._http


def _principal(user: User, source: str) -> Principal:
    return Principal(
        user_id=user.id, subject=user.subject, display_name=user.display_name or user.subject, source=source
    )


def _register_failure(user_id: str) -> None:
    """Count the attempt, in a transaction that is not the one about to be rolled back.

    The ambient unit of work reverts everything it touched when a login raises
    (``factory_foundation/db/uow.py:89``), so a counter written inside it is erased by the very
    rejection it records.  That is not a rate limit: every guess would start again from one, and
    ``MAX_FAILED_ATTEMPTS`` would never be reached no matter how many passwords were tried — which
    is what FR-SEC-005 is for.  The attempt is a fact about the account, so it gets its own session
    and its own commit.
    """
    with new_session() as session:
        user = session.get(User, user_id)
        if user is None:  # deleted between the read above and here; nothing to count against
            return
        user.failed_attempts = int(user.failed_attempts or 0) + 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
            user.failed_attempts = 0
        session.commit()


def _provision(session: DbSession, *, subject: str, claims: dict[str, Any]) -> User:
    """An OIDC-only account, created on first sign-in when the deployment allows it."""
    user = User(
        id=_user_id(),
        subject=subject,
        source="oidc",
        display_name=str(claims.get("name") or claims.get("preferred_username") or subject),
        email=str(claims.get("email") or ""),
        status="ACTIVE",
    )
    session.add(user)
    session.flush()
    return user


def _user_id() -> str:
    from factory_control.foundation import new_id

    return new_id("usr")


def _json(response: Any) -> dict[str, Any]:
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


#: A digest nobody can type, so a missing account costs the same as a wrong password.
_DUMMY_DIGEST = passwords.hash_password("x" * 32)

__all__ = [
    "AuthorizationCode",
    "AuthenticationFailed",
    "Authenticator",
    "Credential",
    "LOCK_MINUTES",
    "LocalAccounts",
    "MAX_FAILED_ATTEMPTS",
    "NoAuthentication",
    "OidcRelyingParty",
    "PasswordCredential",
    "Principal",
    "PrincipalUnavailable",
]
