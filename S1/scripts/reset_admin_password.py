#!/usr/bin/env python
"""Set a local account's Factory password on this deployment, reading the value from stdin.

    # interactive: the prompt does not echo, and the value has to be typed the same way twice
    S1/.venv/bin/python S1/scripts/reset_admin_password.py

    # non-interactive: the value arrives on a pipe and never in argv
    printf '%s' "$PW" | S1/.venv/bin/python S1/scripts/reset_admin_password.py --subject admin

    # and keep the deployment's own record in step with the library (see --sync-env-file)
    printf '%s' "$PW" | S1/.venv/bin/python S1/scripts/reset_admin_password.py --sync-env-file

Why this exists
---------------
``install.sh`` seeds the bootstrap administrator from ``FACTORY_BOOTSTRAP_ADMIN_PASSWORD`` **once**,
at the moment the account is created — ``bootstrap._ensure_bootstrap_admin`` deliberately refuses to
touch a subject that already exists. From then on the digest in the library is the truth and the
value in the env file is history (`user-guide.md` §6.6 says the same thing to its readers). That is
correct behaviour, but it leaves one deployment shape with no way back in: a single administrator, a
forgotten or replaced password, and a login endpoint that deliberately does not distinguish "locked"
from "wrong password" (CP-IAM-005 ④) — while five wrong guesses freeze the account for a quarter of
an hour. The 用户 page's reset cannot help, because reaching it needs an authenticated administrator.

Hand-written SQL is the wrong tool for that door. It writes the digest column and silently skips the
other four things a reset means. So this script calls the same service the page calls —
``identity.service.set_password`` — which enforces the length rule, **replaces** the digest outright
(an account must not keep answering to an old password), clears ``failed_attempts`` and
``locked_until``, revokes every session already issued to that account (FR-SEC-005's neighbour: a
reset is exactly when a hijacked session must die), and writes the audit event
``cp.user.password_set`` with the revoked count in it.

Why stdin
---------
A password in argv is readable by any process on the machine through ``ps``, lands in shell history,
and gets pasted into tickets. On a terminal the value is read with ``getpass`` and confirmed by
typing it twice — a mistyped reset is how you create the locked-out situation this script exists to
avoid. When stdin is redirected, the bytes are taken as-is: only trailing CR/LF is removed, because a
space is a legal character in a password and stripping it would set something other than what the
caller meant.

This is operator tooling for the deployment's own database, not a product path: like every script
here it is never part of ``factory-api``'s request handling, and it grants nothing — it can only
change a password on an account that already exists with ``source='local'``.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _src in (REPO / "S0" / "src", REPO / "S1" / "src"):
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

DEFAULT_ENV_FILE = REPO / ".deploy" / "env"
PASSWORD_KEY = "FACTORY_BOOTSTRAP_ADMIN_PASSWORD"
SUBJECT_KEY = "FACTORY_BOOTSTRAP_ADMIN_SUBJECT"


def read_new_password(*, confirm: bool = True) -> str:
    """The new password from the terminal (hidden, twice) or from a redirected stdin (once)."""
    if sys.stdin.isatty():
        first = getpass.getpass("new password: ")
        if confirm:
            again = getpass.getpass("type it again: ")
            if first != again:
                raise SystemExit("the two entries differ — nothing was changed")
        return first
    # only the line ending: a password may legitimately end in a space
    return sys.stdin.read().strip("\r\n")


def env_file_values(path: Path) -> dict[str, str]:
    """The deployment's EnvironmentFile as a mapping (``systemd`` syntax, which dotenv reads)."""
    from dotenv import dotenv_values

    return {str(key): (value or "") for key, value in dotenv_values(str(path)).items()}


def load_deployment_environment(env_file: Path) -> list[str]:
    """Put the deployment's own env file into this process, without overwriting exported values.

    A bare shell has none of it — ``DATABASE_URL`` and the credential settings live in
    ``.deploy/env``, which systemd feeds to the service. Names are returned so the caller can say
    where the values came from; values never are.
    """
    if not env_file.is_file():
        return []
    taken: list[str] = []
    for key, value in env_file_values(env_file).items():
        if key and key not in os.environ and value:
            os.environ[key] = value
            taken.append(key)
    return taken


def sync_env_file(path: Path, password: str, *, key: str = PASSWORD_KEY) -> None:
    """Rewrite one line of the deployment env file so the record matches the library again.

    Two refusals, both deliberate: a file that other users can read never gets a credential written
    into it, and the write goes through a 0600 temporary file in the same directory so the value is
    never briefly world-readable the way ``open(path, 'w')`` would leave it.
    """
    if not path.is_file():
        raise SystemExit(f"{path} does not exist — nothing to sync; drop --sync-env-file")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SystemExit(
            f"{path} is readable or writable by others (mode {oct(mode)}); "
            "refusing to write a credential into it — chmod 600 it first"
        )
    text = path.read_text(encoding="utf-8")
    line = f"{key}={password}"
    if re.search(rf"^{re.escape(key)}=", text, re.M):
        # a callable replacement, not the string itself: a password may contain a backslash, and
        # re.sub interprets backslashes in a plain replacement
        updated = re.sub(rf"^{re.escape(key)}=.*$", lambda _match, value=line: value, text, count=1, flags=re.M)
    else:
        updated = text if text.endswith("\n") or text == "" else text + "\n"
        updated += f"{line}\n"
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(updated)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def operator_name(explicit: str) -> str:
    """Who the audit row should name: the flag, then the sudoed login, then the login running it."""
    if explicit:
        return explicit
    sudo = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    if sudo:
        return sudo
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - a missing passwd entry must not block a reset
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", default="", help="the local account to reset (default: the bootstrap administrator)")
    parser.add_argument("--operator", default="", help="name recorded in the audit row (default: the OS user running this)")
    parser.add_argument("--require-change", action="store_true", help="flag the account so it must change the password at first login")
    parser.add_argument(
        "--sync-env-file",
        action="store_true",
        help=f"also rewrite {PASSWORD_KEY} in --env-file, so the deployment record stops being a historical value",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="deployment EnvironmentFile (default: .deploy/env)")
    parser.add_argument("--dry-run", action="store_true", help="resolve the target and say what would happen, writing nothing")
    args = parser.parse_args(argv)

    env_file = Path(args.env_file)
    from_env = load_deployment_environment(env_file)

    from factory_control.config import get_settings
    from factory_control.db import configure_database, resolve_database_url

    settings = get_settings()
    subject = args.subject or settings.bootstrap_admin_subject or os.environ.get(SUBJECT_KEY, "")
    if not subject:
        raise SystemExit(
            "no target account: pass --subject, or set "
            f"{SUBJECT_KEY} in {env_file} ({'read' if from_env else 'not found'})"
        )

    configure_database(resolve_database_url(settings.database_url))

    from factory_control.foundation import unit_of_work
    from factory_control.identity import directory, service

    if args.dry_run:
        # A dry run answers "is this the account I think it is?" — which has to be answerable without
        # typing a secret first, or the exploratory step becomes the risky one.
        with unit_of_work() as session:
            account = directory.get_by_subject_any_status(session, subject)
        if account is None:
            raise SystemExit(f"dry run: no account named {subject!r} in this deployment")
        found = account if isinstance(account, dict) else {"subject": account.subject, "id": account.id}
        print(
            f"dry run: would set a new password for subject={found.get('subject')} "
            f"id={found.get('id')}, revoke that account's sessions and write cp.user.password_set"
        )
        return 0

    password = read_new_password()
    if not password.strip():
        raise SystemExit("an empty password was supplied — nothing was changed")

    with unit_of_work() as session:
        account = directory.get_by_subject_any_status(session, subject)
        if account is None:
            raise SystemExit(
                f"no account named {subject!r} — a reset cannot create one; the bootstrap account "
                "is only made at first start-up when it is missing"
            )
        user_id = account["id"] if isinstance(account, dict) else account.id
        service.set_password(session, operator_name(args.operator), user_id, password, must_change=args.require_change)

    if args.sync_env_file:
        recorded = env_file_values(env_file).get(SUBJECT_KEY, "") if env_file.is_file() else ""
        if recorded and recorded != subject:
            raise SystemExit(
                f"--sync-env-file would rewrite {PASSWORD_KEY}, but the account just reset is "
                f"{subject!r} while that record belongs to {recorded!r} — sync it yourself or leave the flag off"
            )
        sync_env_file(env_file, password)

    print(
        f"password set for subject={subject}; {len(password)} characters, "
        f"must_change={'yes' if args.require_change else 'no'}"
    )
    print(
        "that account's existing sessions are revoked, so every signed-in browser has to sign in again; "
        "the revoked count is in the audit event cp.user.password_set"
    )
    if args.sync_env_file:
        print(f"{PASSWORD_KEY} updated in {env_file}")
    elif env_file.is_file() and env_file_values(env_file).get(PASSWORD_KEY, ""):
        print(
            f"{PASSWORD_KEY} in {env_file} is now a historical value again — start.sh's login self-check "
            "and verify-deployed-pages.mjs read it, so pass --sync-env-file next time or update it by hand"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
