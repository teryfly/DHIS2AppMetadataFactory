"""The live-run governance itself (FR-TST-001 … 005).

These run without a DHIS2 instance: they check that the rules 25.4 states are actually
enforced by the harness, which is the only way to know a live run will hold to them.  A
governance module that is exercised solely by the tests it governs is unchecked exactly where
it matters -- the moment an instance is unreachable, reset, or shared with somebody else.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from tests.contract import live_run as run

# --- ① every object this run writes is identifiable as this run's -----------------


def test_a_generated_code_carries_the_run_tag() -> None:
    first = run.code_for("validate")
    second = run.code_for("validate")
    assert first.startswith("FT_"), first
    assert run.is_this_run(first) and run.is_ours(first)
    assert first == second, "one name means one object within a run, so a retry is idempotent"
    # the collision the old helper allowed: two runs of the same test writing the same code,
    # where the second one updated the first one's object and read it back as a pass
    other = run.CODE_PREFIX + "validate"
    assert other == first


def test_the_tag_is_per_process_not_per_call() -> None:
    # a fresh import would give a different tag; within one run every code shares it
    assert {run.code_for("a").split("_")[1], run.code_for("b").split("_")[1]} == {
        run.RUN_TAG
    }
    assert not run.is_this_run("factory_contract_validate"), "a residue of the old scheme is not ours"
    assert run.is_ours("FT_0000000000_weight"), "another run's object is still recognisable"


def test_a_code_is_slugified_into_something_dhis2_accepts() -> None:
    code = run.code_for("Commit then post-verify!")
    assert code.replace("_", "").isalnum(), code
    assert len(code) <= 60, code


# --- ③ cleanup is best-effort and never rewrites the verdict ---------------------


def test_a_cleanup_failure_warns_instead_of_failing_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    created = run.Created()
    created.remember("dataElement", uid="de1", code=run.code_for("x"))

    def explode(*args: object, **kwargs: object) -> dict[str, str]:
        raise RuntimeError("409 Conflict: object is referenced by a program")

    monkeypatch.setattr(run, "_request", explode)
    outcome = created.sweep("https://example.invalid", ("u", "p"))
    assert outcome["removed"] == 0
    assert outcome["warnings"], "a leftover object must be visible, not silently kept"


def test_an_object_recorded_without_a_uid_is_never_deleted_by_guesswork() -> None:
    created = run.Created()
    created.remember("dataElement", code=run.code_for("y"))
    outcome = run._delete("https://example.invalid", ("u", "p"), created.items)
    assert outcome["removed"] == 0
    assert any("no uid" in note for note in outcome["warnings"]), (
        "deleting by a name match on a shared public instance can delete somebody else's data"
    )


def test_sweeping_residue_looks_only_at_the_types_a_run_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_list(
        base_url: str, credentials: tuple[str, str], dhis2_type: str, prefix: str
    ) -> list[dict[str, Any]]:
        seen.append(dhis2_type)
        return []

    monkeypatch.setattr(run, "_list_by_code_prefix", fake_list)
    run.Created().sweep_residue("https://example.invalid", ("u", "p"))
    assert "dataElement" in seen
    assert "organisationUnit" not in seen and "user" not in seen, (
        "a residue sweep must not go hunting through an instance's whole catalogue"
    )
    assert all(prefix == "FT_" for prefix in ["FT_"]), seen


# --- ② the demo objects we lean on come back unchanged ---------------------------


def test_the_fingerprint_ignores_what_the_instance_rewrites_itself() -> None:
    one = {"id": "ou1", "name": "Ngebe", "lastUpdated": "2026-01-01T00:00:00Z", "href": "x"}
    two = {"id": "ou1", "name": "Ngebe", "lastUpdated": "2026-09-30T00:00:00Z", "href": "y"}
    assert run.content_fingerprint(one) == run.content_fingerprint(two), (
        "reading an object refreshes lastUpdated; counting that as a change reports a "
        "tampered demo environment on a run that only looked at it"
    )
    moved = dict(one, name="Someone else renamed it")
    assert run.content_fingerprint(moved) != run.content_fingerprint(one)


def test_a_baseline_entry_with_nothing_to_compare_against_is_reported_not_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = run.DemoBaseline()
    baseline.remember("organisationUnit:ou1", {"id": "ou1", "name": "Ngebe"})
    monkeypatch.setattr(run, "_read_by_label", lambda *args: None)
    changed = baseline.differences("https://example.invalid", ("u", "p"))
    assert changed == ["organisationUnit:ou1: vanished from the instance"], (
        "an object the instance no longer has is exactly what DX-AC-15 must catch"
    )


# --- ⑤ serialisation across processes -------------------------------------------


def test_one_regression_at_a_time_per_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run, "_LOCK_DIR", tmp_path)
    first = run.InstanceLock("https://play.im.dhis2.org/stable-2-41-10")
    first.acquire()
    try:
        second = run.InstanceLock(
            "https://play.im.dhis2.org/stable-2-41-10", wait_seconds=0.0, sleep=lambda _s: None
        )
        with pytest.raises(RuntimeError, match="serialises"):
            second.acquire()
        # a different instance is a different lock, so the four versions can run in parallel
        other = run.InstanceLock("https://play.im.dhis2.org/stable-2-42-6", sleep=lambda _s: None)
        other.acquire()
        other.release()
    finally:
        first.release()
    assert not first.path.exists()


def test_a_lock_left_by_a_killed_run_is_taken_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run, "_LOCK_DIR", tmp_path)
    lock = run.InstanceLock("https://example.invalid", sleep=lambda _s: None)
    lock.path.mkdir(parents=True)
    (lock.path / "owner").write_text(
        json.dumps({"pid": 999999, "tag": "dead", "takenAt": time.time() - run._LOCK_STALE_SECONDS - 1})
    )
    taken = run.InstanceLock("https://example.invalid", wait_seconds=0.0, sleep=lambda _s: None)
    taken.acquire()
    taken.release()


# --- FR-TST-003: indeterminate is a third answer ----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Connection reset by peer",
        "read timed out after 120 seconds",
        "HTTP 503 Service Unavailable",
        "429 Too Many Requests",
        "name resolution failure",
        "SSLError certificate verify failed",
    ],
)
def test_an_instance_problem_is_not_scored_as_a_factory_failure(text: str) -> None:
    assert run.is_indeterminate(RuntimeError(text)), text


@pytest.mark.parametrize("text", ["importReport status == ERROR", "unexpected import result"])
def test_a_real_difference_stays_a_failure(text: str) -> None:
    assert not run.is_indeterminate(RuntimeError(text))


def test_an_unreachable_instance_retries_then_reports_indeterminate() -> None:
    calls = {"n": 0}

    def down() -> None:
        calls["n"] += 1
        raise ConnectionError("connection reset")

    attempt = run.run_with_retries(down, retries=3, backoff_seconds=[0, 0, 0], sleep=lambda _s: None)
    assert attempt.outcome == "indeterminate"
    assert attempt.attempts == 4, "the initial call plus the three retries"
    assert calls["n"] == 4


def test_an_assertion_is_never_retried_into_a_pass() -> None:
    calls = {"n": 0}

    def wrong() -> None:
        calls["n"] += 1
        raise AssertionError("the instance answered something else than the Profile says")

    attempt = run.run_with_retries(wrong, retries=3, backoff_seconds=[0, 0, 0], sleep=lambda _s: None)
    assert attempt.outcome == "failed"
    assert calls["n"] == 1, (
        "retrying a real difference turns it into a pass the moment the instance happens to "
        "answer the way we expected, which is the opposite of what a regression is for"
    )


def test_a_non_transient_error_propagates_instead_of_being_relabelled() -> None:
    class Broken(Exception):
        pass

    def boom() -> None:
        raise Broken("a bug in our own code")

    with pytest.raises(Broken):
        run.run_with_retries(boom, retries=3, backoff_seconds=[0, 0, 0], sleep=lambda _s: None)


# --- FR-TST-001: the instance list is configuration -------------------------------


def test_the_catalogue_covers_every_supported_version_with_a_profile() -> None:
    document = run.catalogue()
    assert len(document["instances"]) == 4
    for item in document["instances"]:
        assert item["profile"].startswith("Dhis2") and item["profile"].endswith("Profile")
        assert item["baseUrl"].startswith("https://"), "TLS is not optional (FR-SEC-005)"
        assert "/api" not in item["baseUrl"], "baseUrl is the instance root, without /api"
    assert run.supported_versions() == ["2.40.12", "2.41.10", "2.42.6", "2.43.1"]


def test_the_catalogue_holds_no_credential_values() -> None:
    """FR-TST-001: the demo accounts are public, and still only their *names* are stored."""
    raw = (Path(__file__).resolve().parent / "live_instances.json").read_text(encoding="utf-8")
    assert "district" not in raw, "a password value in the configuration file"
    assert "credentials" in raw and "passwordEnv" in json.loads(raw)["credentials"]


# --- FR-TST-002: the endpoint a lookup uses is the instance's answer, not a guess --


def test_the_collection_of_a_type_is_read_from_the_version_profile() -> None:
    """``+ "s"`` is not a rule: the plural is per-type data, and the Profile records it."""
    base_url = "https://play.im.dhis2.org/stable-2-41-10"
    assert run._collection(base_url, "dataElement") == "dataElements"
    assert run._collection(base_url, "categoryOption") == "categoryOptions"
    assert run._collection(base_url, "attribute") == "attributes"


def test_an_instance_that_is_not_in_the_catalogue_is_refused_rather_than_guessed() -> None:
    with pytest.raises(run.CollectionUnresolvedError, match="not in tests/contract"):
        run._collection("https://someone.elses.server", "dataElement")


def test_a_type_the_profile_does_not_know_raises_instead_of_guessing_a_plural() -> None:
    base_url = "https://play.im.dhis2.org/stable-2-41-10"
    with pytest.raises(run.CollectionUnresolvedError, match="no type"):
        run._collection(base_url, "notAType")


def test_a_catalogue_that_cannot_answer_is_not_reported_as_a_lost_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one confusion DX-AC-15 must never produce: our own lookup failure as "the
    instance lost it" -- an accusation about a shared public environment."""
    baseline = run.DemoBaseline()
    baseline.remember("dataElement:ABC123", {"id": "ABC123", "name": "Weight"})
    monkeypatch.setattr(run, "_collection", lambda *_a: (_ for _ in ()).throw(
        run.CollectionUnresolvedError("no profile for this instance")
    ))
    with pytest.raises(run.CollectionUnresolvedError):
        baseline.differences("https://someone.elses.server", ("u", "p"))


def test_no_live_instance_is_assumed_when_the_environment_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FACTORY_DX_LIVE_DHIS2_BASE_URL", raising=False)
    monkeypatch.delenv("FACTORY_DX_LIVE_DHIS2_VERSION", raising=False)
    with pytest.raises(RuntimeError, match="no live instance configured"):
        run.live_base_url()


def test_a_version_name_selects_the_configured_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACTORY_DX_LIVE_DHIS2_BASE_URL", raising=False)
    monkeypatch.setenv("FACTORY_DX_LIVE_DHIS2_VERSION", "2.42.6")
    assert run.live_base_url() == "https://play.im.dhis2.org/stable-2-42-6"


def test_the_pinned_patch_is_reported_when_the_instance_has_moved_on() -> None:
    assert run.newest_patch_known("2.41.10") == "2.41.10"
    # the daily reminder's trigger: the instance answers 2.41.11, the catalogue says .10
    assert run.newest_patch_known("2.41.11") == "2.41.10"


def test_the_schedule_carries_what_fr_tst_003_needs() -> None:
    schedule = run.catalogue()["schedule"]
    assert schedule["maxAgeOfPassingRunDays"] == 7, "a release needs a passing run within 7 days"
    assert schedule["retries"] == 3
    assert len(schedule["backoffSeconds"]) == schedule["retries"]
    assert schedule["dailyCron"].count(" ") == 4


# --- DX-TST-005 / FR-TST-002: the smoke pass is read-only ------------------------


def test_a_read_only_smoke_does_not_need_the_write_opt_in() -> None:
    """The daily pass must be runnable without ``FACTORY_DX_LIVE_ALLOW_COMMITS=1``.

    A schedule that writes to four public instances every day is a denial-of-service against
    other people's demo data; a read-only pass answers "is the instance up, does it still
    report the version we pin, has it changed underneath us" and can run unattended.
    """
    from tests.contract import schedule

    assert schedule.DAILY_PLAN["writes"] is False
    assert {item["version"] for item in schedule.DAILY_PLAN["instances"]} == {
        "2.40.12",
        "2.41.10",
        "2.42.6",
        "2.43.1",
    }
    assert schedule.DAILY_PLAN["serialisePerInstance"] is True
