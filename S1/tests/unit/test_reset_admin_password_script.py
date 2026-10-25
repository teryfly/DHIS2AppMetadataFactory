"""The parts of ``scripts/reset_admin_password.py`` that can be wrong without anyone noticing.

Why these and not the reset itself: ``identity.service.set_password`` already has coverage in
``tests/api/test_identity_m4.py`` and the lockout rules in ``test_identity_lockout.py``. The script
adds four behaviours of its own, and each one fails silently — it either prints nothing or prints a
plausible success:

* a password may end in a space, so reading a piped value must trim **only** the line ending, or the
  account gets a different secret from the one the operator typed and the next login fails with a
  401 that names nothing (CP-IAM-005 ④: the endpoint deliberately does not distinguish "wrong" from
  "locked");
* the deployment record is rewritten by regex against a caller-supplied value, so a backslash in a
  password would otherwise be reinterpreted as a ``re`` group reference or an escape and write a
  different byte sequence into ``.deploy/env`` than the one the library was given — the two then
  disagree forever, which is the exact drift this script exists to remove;
* the record file must never be relaxed while rewriting it, and a file that strangers can read must
  be refused outright (S0 SRS: a credential lives in a 0600 file or not at all);
* sourcing the deployment env file must not shadow what the caller already exported, otherwise
  ``sudo -E`` or a test harness pointing at another database gets silently re-aimed at the live one.
"""
from __future__ import annotations

import importlib.util
import io
import os
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2].parent  # S1/tests/unit → the repository root
SCRIPT = REPO / "S1" / "scripts" / "reset_admin_password.py"


def load_script():
    spec = importlib.util.spec_from_file_location("reset_admin_password", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_exists_and_is_documented():
    assert SCRIPT.is_file(), f"{SCRIPT} is the documented way back in when there is no second administrator"
    assert load_script().__doc__, "an operator reading --help must be told what a reset does beyond the digest"


def test_piped_password_keeps_every_character_but_the_line_ending(monkeypatch):
    script = load_script()
    # trailing CR and LF are transport; a trailing space is part of the secret
    monkeypatch.setattr(sys, "stdin", io.StringIO("  Pa55word with spaces. \r\n"))
    assert script.read_new_password() == "  Pa55word with spaces. "


def test_piped_password_survives_a_backslash_in_the_env_record(tmp_path):
    script = load_script()
    env = tmp_path / "env"
    env.write_text('FACTORY_AUTH_MODE="local"\nFACTORY_BOOTSTRAP_ADMIN_PASSWORD=old\nDATABASE_URL=x\n', encoding="utf-8")
    os.chmod(env, 0o600)
    secret = "p\\w\\1backslash-and-\\g<0>group-ref"
    script.sync_env_file(env, secret)
    written = env.read_text(encoding="utf-8")
    assert f"FACTORY_BOOTSTRAP_ADMIN_PASSWORD={secret}\n" in written, written
    # the neighbours are what the service reads; losing one is a deployment that cannot start
    assert "FACTORY_AUTH_MODE=" in written and "DATABASE_URL=x" in written


def test_the_record_file_never_loses_its_mode(tmp_path):
    script = load_script()
    env = tmp_path / "env"
    env.write_text("FACTORY_BOOTSTRAP_ADMIN_PASSWORD=old\n", encoding="utf-8")
    os.chmod(env, 0o600)
    script.sync_env_file(env, "Brand-New-Pa55word")
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_a_world_readable_record_file_is_refused(tmp_path):
    script = load_script()
    env = tmp_path / "env"
    env.write_text("FACTORY_BOOTSTRAP_ADMIN_PASSWORD=old\n", encoding="utf-8")
    os.chmod(env, 0o644)
    try:
        script.sync_env_file(env, "Brand-New-Pa55word")
    except SystemExit as exc:
        assert "chmod 600" in str(exc)
    else:
        raise AssertionError("a credential was written into a file other users can read")
    assert "Brand-New" not in env.read_text(encoding="utf-8")


def test_sourcing_the_env_file_does_not_shadow_exported_values(tmp_path, monkeypatch):
    script = load_script()
    env = tmp_path / "env"
    env.write_text("FACTORY_BOOTSTRAP_ADMIN_SUBJECT=admin\nFACTORY_AUTH_MODE=local\n", encoding="utf-8")
    monkeypatch.setenv("FACTORY_AUTH_MODE", "off")
    monkeypatch.delenv("FACTORY_BOOTSTRAP_ADMIN_SUBJECT", raising=False)
    taken = script.load_deployment_environment(env)
    assert os.environ["FACTORY_AUTH_MODE"] == "off", "the caller's own setting must win"
    assert os.environ["FACTORY_BOOTSTRAP_ADMIN_SUBJECT"] == "admin"
    assert taken == ["FACTORY_BOOTSTRAP_ADMIN_SUBJECT"], "only the keys actually taken are reported"


def test_it_stops_before_reading_a_secret_when_the_target_is_unknowable(tmp_path, monkeypatch):
    # The script resolves the account from flags, then the settings, then the deployment record, and
    # refuses if none of them name one. That refusal has to come first: reading a password and then
    # failing would leave a secret in the pipe, in the terminal echo, and in the operator's mind.
    class NoReading(io.StringIO):
        def read(self, *args, **kwargs):  # type: ignore[override]
            raise AssertionError("a password was read before the target account was resolved")

    monkeypatch.delenv("FACTORY_BOOTSTRAP_ADMIN_SUBJECT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "stdin", NoReading(""))
    try:
        load_script().main(["--subject", "", "--env-file", str(tmp_path / "absent-env")])
    except SystemExit as exc:
        assert "no target account" in str(exc)
    else:
        raise AssertionError("the script ran on without a target account")
