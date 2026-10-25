"""Authenticated encryption for operator-supplied credentials (CP-SEC-001, FR-SEC-006).

A password hash is one-way, so :mod:`factory_control.identity.passwords` could live entirely on
``hashlib``.  A DHIS2 client token or an LLM API key has to be read back, which is a different
problem: it needs a key that can decrypt, and a key that can decrypt cannot be derived from the
data it protects.  That is why this module exists and why it depends on ``cryptography`` rather
than on a standard library that has no block cipher — an invented construction here would be the
worst possible outcome for a table whose entire purpose is to hold other people's secrets.

The shape: HKDF-SHA256 turns the deployment's master key plus a per-record random salt into a
256-bit AES key, and AES-256-GCM seals the value with that key.  The additional authenticated data
binds the ciphertext to the secret's *name*, so copying one row's ``ciphertext`` over another's is
a decryption failure rather than a silent swap of one environment's token for another's.

Nothing here may log a plaintext, a key, a salt or a nonce.  The encoded string is safe to store
and safe to name in an error; the argument that produced it is not.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = "v1"
_INFO = b"cp.secret_record"
_SALT_BYTES = 16
#: 96 bits is what GCM was designed for and what :class:`AESGCM` expects; a random nonce of this
#: size is safe because a fresh one is generated per encryption and never reused with the same key
#: (the key itself is derived per record from a fresh salt).
_NONCE_BYTES = 12
_KEY_BYTES = 32
_FINGERPRINT_CHARS = 8

#: The one spelling of "which algorithm produced this blob", stated where the algorithm lives.
#: `cp.secrets.algo` stores it per row, so reading an old row after a change here is a refusal to
#: guess rather than a silent reinterpretation of someone's ciphertext.
ALGORITHM = "aes-256-gcm+hkdf-sha256"


class CredentialCorruptError(Exception):
    """The stored blob cannot be decrypted with the key the deployment is running.

    Deliberately not a :class:`factory_foundation.common.errors.FactoryError` subclass with a
    details payload: the callers that turn this into an API answer decide what is safe to say, and
    the message here carries only the reference name.
    """


class CredentialKeyMismatchError(CredentialCorruptError):
    """The row was sealed under a different master key.

    Reported separately because the operator action differs: a corrupt row is a bug, a key
    mismatch means the deployment changed ``FACTORY_CREDENTIAL_MASTER_KEY`` while rows written
    under the old one are still in the table.  Nothing is guessed and nothing is retried against
    another key — one attempt, one answer.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"credential {name!r} was encrypted with a different master key")
        self.name = name


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _decode(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4))


def _derive_key(master_key: str, salt: bytes) -> bytes:
    if not master_key:
        raise ValueError("an empty master key encrypts nothing; it only looks like it does")
    return HKDF(algorithm=SHA256(), length=_KEY_BYTES, salt=salt, info=_INFO).derive(
        master_key.encode("utf-8")
    )


def _aad(name: str) -> bytes:
    return f"cp.secret_record:{name}".encode()


def fingerprint(key: bytes) -> str:
    """The first bytes of ``sha256(derived key)`` — enough to spot a changed master key.

    It is a hash of a derived key, not the key, and not the master key. It exists so an operator can
    answer "do all these rows predate the key I just rotated?" from ``GET /secrets`` alone, without
    the server having to try decrypting anything to find out; it is reported there as
    ``keyFingerprint`` for exactly that reason.
    """
    return hashlib.sha256(key).hexdigest()[:_FINGERPRINT_CHARS]


def encrypt(material: str, *, name: str, master_key: str) -> tuple[str, str]:
    """Seal ``material`` for ``name``; returns ``(encoded, key_fingerprint)``.

    The encoding is self-describing — ``v1$salt$nonce$ciphertext$fingerprint`` — in the same style
    as :func:`factory_control.identity.passwords.hash_password`, so changing an algorithm or a
    parameter later is a new version prefix rather than a schema migration over live secrets.
    """
    if not material:
        raise ValueError("an empty credential is not a value to store")
    if not name:
        raise ValueError("a credential needs the name it is bound to")
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive_key(master_key, salt)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, material.encode("utf-8"), _aad(name))
    return (
        "$".join((_VERSION, _encode(salt), _encode(nonce), _encode(sealed), fingerprint(key))),
        fingerprint(key),
    )


def decrypt(encoded: str, *, name: str, master_key: str) -> str:
    """Open an :func:`encrypt` result, or raise.

    A tampered blob, a wrong name and a wrong master key all arrive here; AES-GCM's authentication
    tag collapses the first two into one answer, which is the point of an AEAD.  The third is
    separated out only because the row carries its key fingerprint, and that check happens before
    any decryption attempt — there is no loop over candidate keys anywhere in this module.
    """
    parts = (encoded or "").split("$")
    if len(parts) != 5 or parts[0] != _VERSION:
        raise CredentialCorruptError(f"credential {name!r} is not in a readable format")
    _, encoded_salt, encoded_nonce, encoded_sealed, expected_fingerprint = parts
    try:
        salt, nonce, sealed = _decode(encoded_salt), _decode(encoded_nonce), _decode(encoded_sealed)
    except Exception as exc:  # noqa: BLE001 - a malformed blob is a failed read, not a stack trace
        raise CredentialCorruptError(f"credential {name!r} is not in a readable format") from exc
    key = _derive_key(master_key, salt)
    if fingerprint(key) != expected_fingerprint:
        raise CredentialKeyMismatchError(name)
    try:
        return AESGCM(key).decrypt(nonce, sealed, _aad(name)).decode("utf-8")
    except InvalidTag as exc:
        # the name is safe to mention; the value never was, and here there is no value to mention
        raise CredentialCorruptError(f"credential {name!r} could not be authenticated") from exc
    except UnicodeDecodeError as exc:
        raise CredentialCorruptError(f"credential {name!r} did not decode as text") from exc


__all__ = [
    "ALGORITHM",
    "CredentialCorruptError",
    "CredentialKeyMismatchError",
    "decrypt",
    "encrypt",
    "fingerprint",
]
