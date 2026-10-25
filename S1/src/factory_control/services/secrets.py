"""The credential store as an operator uses it (S5IF-SEC01…04, CP-SEC-001…003).

One rule explains the shape of this module: **the value is a parameter, never a variable that
outlives the call.**  It is encrypted and written, and nothing downstream sees it — no return
value, no audit row, no log line, no exception message.  Every dict returned from here is safe to
serialise, which is why they are built from named columns rather than from model objects.

Deleting is deliberately awkward.  A credential is pointed at by environment credentials and by
provider/model key references; dropping one those still name would turn a working DHIS2 call into
a ``SECRET_UNAVAILABLE`` failure at the least convenient moment, so the holder has to be moved off
it first (CP-SEC-003, and the same two-step logic CP-PRJ-007 uses for project deletion).
"""

from __future__ import annotations

import re
from typing import Any

from factory_control import errors as E
from factory_control.credentials import crypto, store
from factory_control.errors import SecretInUseError, SecretStoreDisabledError
from factory_control.foundation import utcnow
from factory_control.runtime import Runtime
from factory_control.services.recording import write_audit

#: What :func:`factory_control.credentials.crypto.encrypt` actually runs; recorded per row so an
#: algorithm change is a new value rather than a silent reinterpretation of old ciphertext. Stated
#: once, in the module that performs the algorithm.
ALGO = crypto.ALGORITHM

#: ``db://`` references are typed, listed and quoted back, so a name is a slug and nothing else.
#: A username is deliberately not a legal name: ``GET /secrets`` lists names to whoever holds the
#: manage right, and ``dhis2-import-zhang.wei`` would leak whose credential that is.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


class InvalidCredentialName(ValueError):
    """A name the reference grammar cannot carry.  The route turns it into a 422."""


def _master_key(runtime: Runtime) -> str:
    """The deployment's master key, or the refusal that says there isn't a usable one."""
    if not runtime.settings.credential_store_available():
        raise SecretStoreDisabledError("the credential store is not configured on this deployment")
    return str(runtime.settings.credential_master_key).strip()


def validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not NAME_PATTERN.match(cleaned):
        raise InvalidCredentialName(
            "a credential name must be 2-64 characters of lower-case letters, digits, '.' '_' or '-'"
        )
    return cleaned


def _view(row: Any) -> dict[str, Any]:
    """What an operator is told about one credential: name, reference, timestamps.

    There is no field here a ciphertext, a length or a prefix could land in (S5IF-G09) — and none
    derived from the sealing key either.  The row's ``key_fingerprint`` stays out deliberately: each
    encryption draws a fresh salt, so the fingerprint identifies *one blob*, not one master key, and
    two credentials written minutes apart under the same key never match.  It answers no question an
    operator can ask, while being a check value an offline guess could be tested against; whether a
    reference still resolves is ``/setup``'s ``credentials.unresolvable`` job, which decrypts.
    """
    return {
        "name": row.name,
        "ref": store.reference(row.name),
        "algo": row.algo,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
        "rotatedAt": row.rotated_at,
        "createdBy": row.created_by,
    }


def _holders(runtime: Runtime) -> dict[str, list[str]]:
    """``{reference: [who names it]}``, read through the same ports the console reads.

    One pass for both the delete guard and the setup summary, because both are answering the same
    question — "what breaks if this reference stops resolving?" — and a second implementation of
    that question is how the two answers start to disagree.

    No operator is passed on deliberately: this answers "is anything pointing at this", which is a
    property of the deployment, not of the caller. Filtering the scan by the reader would make a
    credential look deletable to exactly the person who could delete it.
    """
    from factory_control.services import governance as GOV

    holders: dict[str, list[str]] = {}

    def add(ref: Any, who: str) -> None:
        text = str(ref or "")
        if text:
            holders.setdefault(text, []).append(who)

    for info in runtime.execution.list_environments():
        for credential in getattr(info, "credentials", []) or []:
            add(credential.secret_ref, f"environment:{info.environment_key}:{credential.scope}")
        for scope, ref in (getattr(info, "secret_refs", {}) or {}).items():
            add(ref, f"environment:{info.environment_key}:{scope}")

    for row in GOV.providers(runtime)["items"]:
        add(row.get("apiKeySecretRef"), f"provider:{row.get('name')}")
    for row in GOV.models(runtime)["items"]:
        add(row.get("apiKeySecretRef"), f"model:{row.get('provider')}/{row.get('name')}")
    return holders


def write(runtime: Runtime, *, name: str, material: str, operator: str) -> dict[str, Any]:
    """Seal ``material`` under ``name``, creating or rotating it (S5IF-SEC01).

    ``material`` is encrypted inside this function and dropped when it returns; the row that comes
    back out of :func:`store.put` carries ciphertext only.  An empty value is refused with a
    message that names the *field*, because a validation error that quotes the input would put the
    credential into an audit row and into the client's error log.
    """
    clean = validate_name(name)
    if not (material or "").strip():
        raise E.ValidationFailedError(
            "material must not be empty", details={"field": "material", "reason": "empty"}
        )
    key = _master_key(runtime)
    ciphertext, fingerprint = crypto.encrypt(material, name=clean, master_key=key)
    with runtime.transaction(operator=operator) as ctx:
        row = store.put(
            ctx.session,
            name=clean,
            ciphertext=ciphertext,
            algo=ALGO,
            key_fingerprint=fingerprint,
            operator=operator,
        )
        write_audit(
            ctx,
            event_type="cp.secret.written",
            object_type="Secret",
            object_id=clean,
            # the reference and the clock, never the value (FR-SEC-008); the timestamp is a string
            # because an audit payload is serialised as-is into JSONB, where a datetime is not a value
            after={
                "name": clean,
                "ref": store.reference(clean),
                "rotatedAt": row.rotated_at.isoformat() if row.rotated_at else None,
            },
        )
        return _view(row)


def listing(runtime: Runtime, *, operator: str) -> dict[str, Any]:
    """Every credential's name and timestamps, oldest first (S5IF-SEC02)."""
    _master_key(runtime)
    with runtime.read_only(operator=operator) as ctx:
        rows = store.list_records(ctx.session)
    items = [_view(row) for row in rows]
    return {"items": items, "total": len(items), "generatedAt": utcnow()}


def remove(runtime: Runtime, *, name: str, operator: str) -> dict[str, Any]:
    """Drop one credential once nothing points at it (S5IF-SEC03).

    The guard is best-effort in one specific way worth naming: the holder scan runs across S4 and
    S3 through their ports, each on its own session, so it cannot join the transaction that deletes
    the row. A provider pointed at this name in the milliseconds between the scan and the delete
    would be missed. Narrowing that window is not possible without a cross-subsystem lock; what is
    possible is checking last, so the answer is stale by as little as it can be, and telling the
    operator which holders were found when the answer is no.
    """
    clean = validate_name(name)
    _master_key(runtime)
    reference = store.reference(clean)
    with runtime.transaction(operator=operator) as ctx:
        holders = sorted({who for who in _holders(runtime).get(reference, [])})
        if holders:
            raise SecretInUseError(f"credential {clean!r} is still referenced", referenced_by=holders)
        if not store.delete(ctx.session, clean):
            raise E.NotFoundControlError(f"no credential named {clean!r}", details={"name": clean})
        write_audit(
            ctx,
            event_type="cp.secret.deleted",
            object_type="Secret",
            object_id=clean,
            before={"name": clean, "ref": reference},
        )
    return {"name": clean, "deleted": True}


def summary(runtime: Runtime, *, operator: str) -> dict[str, Any]:
    """The ``credentials`` section of ``GET /setup`` — facts about the store, not verdicts.

    ``unresolvable`` is the line that saves an operator an afternoon: a reference an environment
    still names but that no longer decrypts — deleted row, restored backup, changed master key —
    reads as a working configuration everywhere else and only fails when a stage reaches out to
    DHIS2.  Reporting it costs one decrypt per referenced name, on a read the console makes once
    per page.
    """
    available = runtime.settings.credential_store_available()
    # Two different facts, and a deployment can be in either state independently: a key is present
    # at all, and a key is long enough to be worth anything. Reporting a 10-character key as
    # "not configured" would send the operator to the wrong line of the env file — the setting is
    # there, it is just too short, which is what `validate_runtime()`'s own sentence says.
    configured = bool(str(runtime.settings.credential_master_key).strip())
    body: dict[str, Any] = {
        "storeAvailable": available,
        "masterKeyConfigured": configured,
        "scheme": store.SCHEME,
        "named": 0,
        "unresolvable": [],
    }
    if not available:
        return body

    with runtime.read_only(operator=operator) as ctx:
        rows = {row.name: row for row in store.list_records(ctx.session)}
    body["named"] = len(rows)

    key = str(runtime.settings.credential_master_key).strip()
    holders = _holders(runtime)
    for ref, who in sorted(holders.items()):
        name = store.name_of(ref)
        if not name:
            continue  # an env:// or vault:// reference is not this store's business
        row = rows.get(name)
        if row is None:
            body["unresolvable"].append({"ref": ref, "holders": sorted(set(who)), "reason": "missing"})
            continue
        try:
            crypto.decrypt(str(row.ciphertext), name=name, master_key=key)
        except crypto.CredentialKeyMismatchError:
            body["unresolvable"].append({"ref": ref, "holders": sorted(set(who)), "reason": "keyMismatch"})
        except crypto.CredentialCorruptError:
            body["unresolvable"].append({"ref": ref, "holders": sorted(set(who)), "reason": "corrupt"})
    return body


__all__ = [
    "ALGO",
    "NAME_PATTERN",
    "InvalidCredentialName",
    "listing",
    "remove",
    "summary",
    "validate_name",
    "write",
]
