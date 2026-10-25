"""Reading and writing the credential table (CP-SEC-001).

Kept apart from :mod:`factory_control.services.secrets` for the same reason ``identity/directory.py``
is: the resolver that runs on *every* DHIS2 call needs one lookup and nothing else, and it must not
acquire a write transaction or import the HTTP-facing rules that happen to live next door.

The value passes through :func:`put` as a plain ``str`` and is dropped on return — no row object
here carries it, and :class:`factory_control.models.SecretRecord` has no column it could be
assigned to.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from factory_control.foundation import utcnow
from factory_control.models import SecretRecord

SCHEME = "db"


def reference(name: str) -> str:
    """The ``db://`` reference an environment or provider row stores instead of the value."""
    return f"{SCHEME}://{name}"


def name_of(ref: str) -> str:
    """The name inside a ``db://`` reference, or ``""`` when it is some other scheme."""
    prefix = f"{SCHEME}://"
    return ref[len(prefix) :] if ref.startswith(prefix) else ""


def _new_id() -> str:
    from factory_control.foundation import new_id

    return new_id("sec")


def _by_name(session: DbSession, name: str) -> SecretRecord | None:
    return session.scalar(select(SecretRecord).where(SecretRecord.name == name).limit(1))


def put(
    session: DbSession,
    *,
    name: str,
    ciphertext: str,
    algo: str,
    key_fingerprint: str,
    operator: str,
) -> SecretRecord:
    """Insert or replace one credential.

    A single ``INSERT ... ON CONFLICT DO UPDATE`` rather than read-then-write: two operators
    saving the same name at the same moment would otherwise let the older request finish last,
    and "last write wins" is the one answer that would silently replace a working token with a
    stale one.
    """
    now = utcnow()
    statement = (
        insert(SecretRecord)
        .values(
            id=_new_id(),
            name=name,
            ciphertext=ciphertext,
            algo=algo,
            key_fingerprint=key_fingerprint,
            created_by=operator,
            created_at=now,
            updated_at=now,
            rotated_at=None,
        )
        .on_conflict_do_update(
            index_elements=[SecretRecord.name],
            set_={
                "ciphertext": ciphertext,
                "algo": algo,
                "key_fingerprint": key_fingerprint,
                # only the update branch sets it, so a first-time write keeps ``rotated_at`` NULL
                "rotated_at": now,
                "updated_at": now,
            },
        )
    )
    session.execute(statement)
    session.flush()
    row = _by_name(session, name)
    if row is None:  # pragma: no cover - the upsert above just wrote this name
        raise RuntimeError(f"credential {name!r} did not read back after being written")
    return row


def get(session: DbSession, name: str) -> SecretRecord | None:
    return _by_name(session, name)


def list_records(session: DbSession) -> list[SecretRecord]:
    """Every credential, oldest first — names and timestamps only.

    The caller renders this into an API answer, and the schema it uses
    (``rest/schemas/secrets.py``) has no field a ciphertext could land in.
    """
    return list(session.scalars(select(SecretRecord).order_by(SecretRecord.created_at, SecretRecord.id)))


def delete(session: DbSession, name: str) -> bool:
    """Drop one credential; ``False`` when no such name existed."""
    row = _by_name(session, name)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


__all__ = ["SCHEME", "delete", "get", "list_records", "name_of", "put", "reference"]
