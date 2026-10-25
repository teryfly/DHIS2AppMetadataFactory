"""Password digests for local accounts (FR-SEC-001, FR-SEC-006).

``hashlib.scrypt`` is in the standard library, and this path adds no crypto dependency for it (a
decision recorded in ``docs/CHANGELOG.md`` for 1.2.0): the memory-hard work factor is what makes an
offline guess against a stolen ``cp.users`` row expensive, and scrypt gives it without a third
party.  The qualification matters as of 1.7.0 — the *credential store* does depend on
``cryptography``, because a DHIS2 token, unlike a password, has to be readable again.  A digest is
one-way by design and stays on the standard library; a secret is not and cannot.

The encoded form carries its own parameters, so raising them later verifies old hashes rather
than failing them — :func:`needs_rehash` is what a login path calls to decide whether to re-digest
the password it just confirmed.

Nothing here may log a password, a salt or a digest: FR-SEC-008 makes the same rule for
credentials as for DHIS2 tokens, and a digest in an audit row is a copy of the password list.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_SCHEME = "scrypt"
#: 16 MiB of work per guess, which is OWASP's floor for an interactive login and the largest
#: figure that fits OpenSSL's default scrypt memory ceiling on a small container — going above
#: it makes a login fail with a memory error rather than a slow hash, which is a worse
#: trade than one doubling of cost.  Stated once, so the accepted cost is visible.
_DEFAULT_N = 2**14
_DEFAULT_R = 8
_DEFAULT_P = 1
#: passed explicitly because the library default (32 MiB) is measured against ``n*r*p`` plus
#: overhead and an upgrade of the parameters above would otherwise surface as a runtime error
_MAXMEM_BYTES = 64 * 1024 * 1024
_SALT_BYTES = 16
_HASH_BYTES = 32
#: The shortest password this Factory will store (FR-SEC-001).  A policy floor belongs next to the
#: cost parameters because the two work together: scrypt makes an offline guess expensive, and a
#: six-character dictionary word makes it unnecessary.  It lives here rather than only in a request
#: schema because the paths that create accounts do not all come through HTTP — the bootstrap
#: administrator is built by calling :func:`hash_password` directly, and a rule stated on the wire
#: is not a rule for the process.
MIN_PASSWORD_CHARS = 8


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding)


def hash_password(password: str, *, n: int = _DEFAULT_N, r: int = _DEFAULT_R, p: int = _DEFAULT_P) -> str:
    """A salted scrypt digest, self-describing: ``scheme$N$r$p$salt$hash``."""
    if not password:
        raise ValueError("an empty password is not a credential to store")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_HASH_BYTES, maxmem=_MAXMEM_BYTES
    )
    return "$".join((_SCHEME, str(n), str(r), str(p), _encode(salt), _encode(digest)))


def verify_password(password: str, encoded: str | None) -> bool:
    """Constant-time comparison against the stored digest; never raises on a bad stored value.

    A malformed or absent hash answers ``False`` rather than an exception: a login path that
    throws on its own data would tell an attacker which accounts are shaped oddly.
    """
    parts = (encoded or "").split("$")
    if len(parts) != 6 or parts[0] != _SCHEME or not password:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = _decode(parts[4]), _decode(parts[5])
    except (ValueError, TypeError):
        return False
    if n < 2 or (n & (n - 1)) or r < 1 or p < 1:
        return False
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=_MAXMEM_BYTES
        )
    except (ValueError, MemoryError):
        # a parameter set this process cannot afford is still a failed login, not a 500
        return False
    return hmac.compare_digest(digest, expected)


def needs_rehash(encoded: str | None) -> bool:
    """Whether a confirmed password should be re-digested at the current cost parameters.

    Checked only after a successful verification, so raising ``_DEFAULT_N`` costs a deployment
    nothing at boot and quietly retires the weaker digests as people sign in.
    """
    parts = (encoded or "").split("$")
    if len(parts) != 6 or parts[0] != _SCHEME:
        return True
    return [int(parts[1]), int(parts[2]), int(parts[3])] != [_DEFAULT_N, _DEFAULT_R, _DEFAULT_P]


def new_token(bytes_length: int = 32) -> str:
    """A URL-safe opaque token: a session id or a CSRF token, never derived from user data."""
    return secrets.token_urlsafe(bytes_length)


def digest_token(token: str) -> str:
    """What a session or CSRF value is stored as — the client's secret is never persisted.

    SHA-256 rather than scrypt: these are 256-bit random values, so an offline guess against
    the column is not the attack; the attack is reading the table and replaying a live token,
    which hashing defeats just as well and without making every request pay for scrypt.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = ["MIN_PASSWORD_CHARS", "digest_token", "hash_password", "needs_rehash", "new_token", "verify_password"]
