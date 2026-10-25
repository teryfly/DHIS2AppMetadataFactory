"""The ``db://`` secret backend (CP-SEC-002).

Registered on the foundation's resolver by :func:`factory_control.bootstrap.bootstrap` after the
database is configured, so ``resolve_secret("db://dhis2-dev-discovery")`` works everywhere a secret
reference is resolved — inside S4's DHIS2 client, inside S3's LLM provider, in the notification
transports — without any of those subsystems knowing that a control-plane table is involved.

Two rules shape this file:

* **Read-only.**  :meth:`SecretResolver.store` infers *the* write backend when a caller omits the
  scheme, and it refuses when there is more than one candidate
  (``factory_foundation/secrets/base.py:112-118``).  ``memory`` already holds that role for the
  one-time demo credentials of FD-SEC-004, so a second writable backend would break every
  unqualified ``store_secret()`` in the repo.  Writes go through
  :mod:`factory_control.services.secrets`, which is where the HTTP rules live.
* **Never hold the value longer than one call frame.**  The returned :class:`Secret` masks itself
  in ``str``/``repr``/``for_json``; the plaintext exists only inside ``reveal()``, which the
  callers use to build an ``Authorization`` header and then drop.
"""

from __future__ import annotations

from factory_control.credentials import store
from factory_control.credentials.crypto import (
    CredentialCorruptError,
    CredentialKeyMismatchError,
    decrypt,
)
from factory_control.foundation import (
    Secret,
    SecretUnavailableError,
    current_session,
    has_active_uow,
    register_secret_backend,
    unit_of_work,
)
from factory_control.models import SecretRecord


class DbSecretBackend:
    """Resolves ``db://<name>`` against ``cp.secrets``."""

    scheme = store.SCHEME

    def __init__(self, master_key: str) -> None:
        # Captured once at bootstrap: a process must not mix keys, and a key that appeared to
        # change mid-run would look like corrupted rows.
        self._master_key = master_key

    def supports_write(self) -> bool:
        return False

    def store(self, path: str, value: str) -> str:
        raise SecretUnavailableError(
            "db:// credentials are written through PUT /api/v1/secrets/{name}, not through the resolver",
            details={"ref": f"db://{path}", "reason": "readOnly"},
        )

    def resolve(self, path: str) -> Secret:
        name = path.strip()
        row = self._read(name)
        if row is None:
            # FD-SEC-003: name the reference, never a value — there is no value to name here.
            raise SecretUnavailableError(
                f"no credential named {name!r} in the control-plane store",
                details={"ref": f"db://{name}", "reason": "missing"},
            )
        try:
            material = decrypt(str(row.ciphertext), name=name, master_key=self._master_key)
        except CredentialKeyMismatchError as exc:
            raise SecretUnavailableError(
                str(exc), details={"ref": f"db://{name}", "reason": "keyMismatch"}
            ) from exc
        except CredentialCorruptError as exc:
            raise SecretUnavailableError(str(exc), details={"ref": f"db://{name}", "reason": "corrupt"}) from exc
        return Secret(_value=material, ref=f"db://{name}")

    def _read(self, name: str) -> SecretRecord | None:
        """One row, on whatever transaction is already open.

        S3 and S4 resolve lazily — usually *inside* a transaction S1 already holds.  Opening a
        second session on the same connection there would serialise behind the outer one, so the
        ambient session is reused when present and a short one is only used for a standalone read.
        """
        if has_active_uow():
            return store.get(current_session(), name)
        with unit_of_work() as session:
            return store.get(session, name)


def install_master_key_backend(settings: object) -> bool:
    """Register :class:`DbSecretBackend` when the settings carry a usable key.

    Returns whether the store is live.  Called from bootstrap and from tests, so the "is it
    configured" question has exactly one answer in the process — a test that decided to register
    the backend on its own terms would be testing a wiring production never builds.
    """
    key = str(getattr(settings, "credential_master_key", "") or "").strip()
    if not bool(getattr(settings, "credential_store_available", lambda: False)()):
        return False
    register_secret_backend(DbSecretBackend(key))
    return True


__all__ = ["DbSecretBackend", "install_master_key_backend"]
