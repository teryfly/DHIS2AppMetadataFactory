"""The lock-out, the counters behind it, and what an account may be named and turned into (FR-SEC-001, FR-SEC-005).

Concretely, the ten cases here are: five failures lock and the right password is then refused; the
locked answer is indistinguishable from a wrong one; the lock is readable where the consoles draw it;
a success clears the counters; an administrator's reset unlocks *and* ends the open sessions; a short
password is refused at the create rather than only at the reset; a login name cannot be taken twice;
retiring an account over the wire stops both the next sign-in and the session already open; and a
role pointed at a project that is not there is answered as a missing project rather than a 500.

Nothing in the suite referenced ``MAX_FAILED_ATTEMPTS``, ``LOCK_MINUTES`` or ``locked_until`` before
this file: five wrong passwords and a fifteen-minute freeze is a rule the operator experiences but
no test defended, and the rule that decides whether an account can be created at all was stated on
one request schema and not in the service that also builds the bootstrap administrator.

Two properties are worth more than the arithmetic:

* **a locked account must not look like a locked account.**  ``authn.py`` already refuses to tell
  "no such user" from "wrong password" because that distinction is half of an enumeration attack
  (总体 SRS 21.4).  A response that said "temporarily locked" would hand the same oracle back — so
  the test compares bodies byte for byte rather than only checking the status code.
* **the lock is what the consoles draw**, so the read that feeds them (``GET /users`` → ``locked``)
  has to flip with it.  That is where an operator finds out, precisely because the login answer
  cannot say.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.foundation import unit_of_work
from factory_control.identity import authn, passwords, service
from factory_control.identity.roles import PLATFORM_ADMINISTRATOR
from factory_control.models import User

pytestmark = pytest.mark.api

PASSWORD = "a-correct-horse-battery"
WRONG = "not-the-password-at-all"
OPERATOR = {"X-Operator": "provisioner"}


def _create(subject: str, *, role: str | None = None, password: str = PASSWORD) -> dict[str, Any]:
    with unit_of_work() as session:
        created = service.create_user(session, "provisioner", subject=subject, password=password)
        if role is not None:
            service.assign_role(session, "provisioner", created["id"], role)
        return created


def _row(user_id: str) -> User:
    with unit_of_work() as session:
        return session.get(User, user_id)


def _fail(secure: TestClient, subject: str) -> Any:
    return secure.post("/api/v1/auth/login", json={"subject": subject, "password": WRONG})


def _sign_in(secure: TestClient, subject: str, password: str = PASSWORD) -> Any:
    return secure.post("/api/v1/auth/login", json={"subject": subject, "password": password})


def test_five_wrong_passwords_lock_the_account_and_the_right_one_is_then_refused(
    secure: TestClient, enforced: Any
) -> None:
    """``MAX_FAILED_ATTEMPTS`` in, ``LOCK_MINUTES`` out — and the correct password does not help."""
    account = _create("lock-a@example.org")
    for _ in range(authn.MAX_FAILED_ATTEMPTS):
        assert _fail(secure, "lock-a@example.org").status_code == 401

    row = _row(account["id"])
    assert row.locked_until is not None, "the fifth failure must set the freeze"
    assert row.failed_attempts == 0, "locking resets the counter; the freeze is the state now"

    refused = _sign_in(secure, "lock-a@example.org")
    assert refused.status_code == 401, "a locked account cannot sign in with the right password"


def test_a_locked_account_is_indistinguishable_from_a_wrong_password(
    secure: TestClient, enforced: Any
) -> None:
    """The body, not just the status: an enumeration oracle is a defect with a nice UX."""
    _create("lock-b@example.org")
    first = _fail(secure, "lock-b@example.org")
    for _ in range(authn.MAX_FAILED_ATTEMPTS - 1):
        _fail(secure, "lock-b@example.org")

    locked = _sign_in(secure, "lock-b@example.org")
    assert locked.status_code == first.status_code
    # every field but the per-request correlation id, which differs by construction
    assert {k: v for k, v in locked.json().items() if k != "correlationId"} == {
        k: v for k, v in first.json().items() if k != "correlationId"
    }, "the locked answer must be identical to a wrong password"
    body = locked.text.lower()
    for giveaway in ("lock", "temporar", "disable", "attempt", "minute"):
        assert giveaway not in body, f"the response leaks the account state through {giveaway!r}"


def test_the_lock_is_visible_in_the_list_the_consoles_draw_from(
    secure: TestClient, enforced: Any
) -> None:
    """``GET /users`` is the only honest place to say "this person is locked out".

    The administrator signs in for this one, because in a local-mode deployment the list is gated on
    ``user.manage`` — the same gate the screens' own controls are gated by.
    """
    _create("admin-c@example.org", role=PLATFORM_ADMINISTRATOR)
    victim = _create("victim-c@example.org")
    for _ in range(authn.MAX_FAILED_ATTEMPTS):
        _fail(secure, "victim-c@example.org")

    signed_in = _sign_in(secure, "admin-c@example.org")
    assert signed_in.status_code == 200, signed_in.text
    csrf = signed_in.json()["csrfToken"]
    listed = secure.get("/api/v1/users", headers={"X-CSRF-Token": csrf}).json()

    by_subject = {row["subject"]: row for row in listed["items"]}
    assert by_subject["victim-c@example.org"]["locked"] is True
    assert by_subject["admin-c@example.org"]["locked"] is False

    # and the way out is the reset, which the same list then reports as closed
    with unit_of_work() as session:
        service.set_password(session, "admin-c@example.org", victim["id"], "the-reset-password-02")
    after = secure.get("/api/v1/users", headers={"X-CSRF-Token": csrf}).json()
    assert {row["subject"]: row for row in after["items"]}["victim-c@example.org"]["locked"] is False


def test_a_successful_login_clears_the_counters_and_stamps_the_visit(
    secure: TestClient, enforced: Any
) -> None:
    account = _create("clean-d@example.org")
    _fail(secure, "clean-d@example.org")
    _fail(secure, "clean-d@example.org")
    assert _row(account["id"]).failed_attempts == 2

    assert _sign_in(secure, "clean-d@example.org").status_code == 200
    row = _row(account["id"])
    assert row.failed_attempts == 0 and row.locked_until is None
    assert row.last_login_at is not None, "the audit of a sign-in starts from this column"


def test_an_administrators_reset_unlocks_the_account_and_kills_its_live_sessions(
    secure: TestClient, enforced: Any, seeded: dict
) -> None:
    """FR-SEC-005's neighbour: a reset must end the sessions the old password opened.

    Otherwise "reset" and "reset, eventually" are the same answer to a stolen cookie, and the
    difference is the half-hour an idle timeout would otherwise let it keep working.
    """
    account = _create("reset-e@example.org")
    assert _sign_in(secure, "reset-e@example.org").status_code == 200
    cookie = secure.cookies.get(enforced.cookie_name)
    assert cookie

    with unit_of_work() as session:
        service.set_password(session, "admin-e@example.org", account["id"], "the-new-password-02")

    # ``GET /auth/session`` answers 200 for a stranger on purpose (the console has to be able to ask
    # who is signed in before anyone is), so the dead token shows up as ``authenticated: false``…
    who = secure.get("/api/v1/auth/session", headers={"Cookie": f"{enforced.cookie_name}={cookie}"}).json()
    assert who["authenticated"] is False, "the session that existed before the reset must be gone"
    # …and as a refusal on anything that actually needs the identity
    refused = secure.get(
        f"/api/v1/projects/{seeded['id']}", headers={"Cookie": f"{enforced.cookie_name}={cookie}"}
    )
    assert refused.status_code == 401, refused.text

    again = _sign_in(secure, "reset-e@example.org", password="the-new-password-02")
    assert again.status_code == 200, "and the new password signs in, so the account is not locked either"


def test_a_short_password_is_refused_when_the_account_is_created_and_quotes_nothing(
    secure: TestClient, enforced: Any
) -> None:
    """The floor has to be in the service, not only in a schema.

    A request-shaped rule leaves the in-process paths (bootstrap, provisioning) unpoliced, and a
    rejected password that comes back in the 422 body has been written into a client log, a proxy
    log, and a support message.
    """
    _create("admin-f@example.org", role=PLATFORM_ADMINISTRATOR)
    signed_in = _sign_in(secure, "admin-f@example.org")
    csrf = signed_in.json()["csrfToken"]

    sentinel = "Sh0rt!"
    refused = secure.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf, **OPERATOR},
        json={"subject": "tooshort@example.org", "password": sentinel},
    )
    assert refused.status_code == 422, refused.text
    detail = refused.json()["details"]["fields"]
    assert [entry["path"] for entry in detail] == ["password"], detail
    assert sentinel not in refused.text, "the rejection must name the field, never repeat the value"

    listed = secure.get("/api/v1/users", headers={"X-CSRF-Token": csrf}).json()
    assert "tooshort@example.org" not in {row["subject"] for row in listed["items"]}, "no row was written"


def test_an_older_digest_still_signs_in_and_is_rewritten_at_the_current_cost(
    secure: TestClient, enforced: Any
) -> None:
    """Raising the work factor retires weak digests as people sign in, never at boot.

    Without this, ``_DEFAULT_N`` going up would be a migration over live accounts — or an outage for
    anyone whose digest was written under the old parameters.
    """
    account = _create("rehash-g@example.org")
    old = passwords.hash_password(PASSWORD, n=2**12, r=8, p=1)
    with unit_of_work() as session:
        row = session.get(User, account["id"])
        row.password_hash = old
        session.flush()
    assert passwords.needs_rehash(_row(account["id"]).password_hash)

    assert _sign_in(secure, "rehash-g@example.org").status_code == 200
    updated = _row(account["id"]).password_hash
    assert not passwords.needs_rehash(updated), "the login should have re-digested at the current cost"
    assert updated != old
    assert passwords.verify_password(PASSWORD, updated), "and the same password still works"


def test_disabling_an_account_over_the_wire_stops_the_next_sign_in_and_the_open_session(
    secure: TestClient, enforced: Any, harness: Any
) -> None:
    """``PATCH /users/{id} {status: DISABLED}`` is the only way to retire an account (there is no delete).

    So "this person is gone" is a status change, and it has to be felt twice: the next sign-in
    refused, **and** the cookie already in a browser stopped.  A disable that only blocked future
    sign-ins would leave the session that exists right now running for the rest of its idle window —
    which is precisely the window a departing operator would care about.  The two halves are checked
    from two clients on purpose: one holds the victim's cookie, the other the administrator's, and a
    single jar cannot tell S1 which identity a request is speaking as.
    """
    from factory_control.rest.app import create_app

    account = _create("gone-h@example.org")
    with TestClient(create_app(harness.runtime), base_url="https://testserver") as victim:
        assert _sign_in(victim, "gone-h@example.org").status_code == 200
        stale = victim.cookies.get(enforced.cookie_name)
        assert stale, "the victim needs a session to lose"

    _create("admin-h@example.org", role=PLATFORM_ADMINISTRATOR)
    signed_in = _sign_in(secure, "admin-h@example.org")
    assert signed_in.status_code == 200, signed_in.text
    csrf = signed_in.json()["csrfToken"]

    disabled = secure.patch(
        f"/api/v1/users/{account['id']}",
        headers={"X-CSRF-Token": csrf, **OPERATOR},
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200, disabled.text

    assert disabled.status_code == 200, disabled.text

    with TestClient(create_app(harness.runtime), base_url="https://testserver") as stranger:
        # A clean jar, not the administrator's: a login POST that carries somebody else's still-valid
        # session cookie is a cookie-authenticated write without a CSRF value, and S1 answers that 403
        # by design. "This account can no longer sign in" has to be measured from a stranger.
        refused = _sign_in(stranger, "gone-h@example.org")
        assert refused.status_code == 401, "a disabled account must not be able to sign in again"

        # ``/auth/session`` answers 200 for a stranger on purpose, so the dead session shows up as the
        # flag rather than the status code — the same reading the consoles get "signed out" from.
        who = stranger.get("/api/v1/auth/session", headers={"Cookie": f"{enforced.cookie_name}={stale}"}).json()
    assert who["authenticated"] is False, "the session opened before the disable has to stop working with it"


def test_two_accounts_cannot_share_a_login_name(secure: TestClient, enforced: Any) -> None:
    """``subject`` is the identity the audit trail names, so a second account cannot take it.

    The check spans sources and statuses on purpose: a local and an OIDC row with the same name would
    make an authorization decision depend on which row the lookup happened to read, and re-creating a
    retired account would hand its old grants to whoever asked last.  A 422 that names the field is
    what the consoles show, so the naming of the field is the part worth pinning.
    """
    _create("dup-i@example.org", role=PLATFORM_ADMINISTRATOR)
    signed_in = _sign_in(secure, "dup-i@example.org")
    assert signed_in.status_code == 200, signed_in.text
    csrf = signed_in.json()["csrfToken"]

    second = secure.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf, **OPERATOR},
        json={"subject": "dup-i@example.org", "displayName": "Namesake", "password": PASSWORD},
    )
    assert second.status_code == 422, second.text
    assert [entry["path"] for entry in second.json()["details"]["fields"]] == ["subject"], second.text

    listed = secure.get("/api/v1/users", headers={"X-CSRF-Token": csrf}).json()
    rows = [row for row in listed["items"] if row["subject"] == "dup-i@example.org"]
    assert len(rows) == 1, "the rejected create wrote nothing"


def test_a_role_pointed_at_a_project_that_is_not_there_is_answered_as_a_missing_project(
    secure: TestClient, enforced: Any, seeded: dict
) -> None:
    """A scoped grant implies membership, and membership has a foreign key behind it.

    Probing the account screens' new project-id field found this: an unknown id used to reach an
    unmapped ``IntegrityError`` and answer 500 — a correlation id and nothing to correct, when the
    mistake was simply a name that does not exist.  A 404 that names the field is the same fact told
    usefully, and the role must not be half-written on the way out.
    """
    account = _create("ghost-j@example.org", role=PLATFORM_ADMINISTRATOR)
    signed_in = _sign_in(secure, "ghost-j@example.org")
    assert signed_in.status_code == 200, signed_in.text
    csrf = signed_in.json()["csrfToken"]

    refused = secure.put(
        f"/api/v1/users/{account['id']}/roles",
        headers={"X-CSRF-Token": csrf, **OPERATOR},
        json={"role": "SOLUTION_ARCHITECT", "projectId": "PRJ-does-not-exist"},
    )
    assert refused.status_code == 404, refused.text
    assert [entry["path"] for entry in refused.json()["details"]["fields"]] == ["projectId"], refused.text

    listed = secure.get("/api/v1/users", headers={"X-CSRF-Token": csrf}).json()
    row = next(u for u in listed["items"] if u["id"] == account["id"])
    assert [r["role"] for r in row["roles"]] == ["PLATFORM_ADMINISTRATOR"], "the failed grant wrote nothing"

    # and the same call with a project that does exist still works — the guard is about the name,
    # not about project-scoped grants as such
    ok = secure.put(
        f"/api/v1/users/{account['id']}/roles",
        headers={"X-CSRF-Token": csrf, **OPERATOR},
        json={"role": "SOLUTION_ARCHITECT", "projectId": seeded["id"]},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["projectId"] == seeded["id"]
