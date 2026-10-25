"""The daily and pre-release regression plan (FR-TST-003, FR-TST-005, DX-TST-005).

Two different runs, deliberately different in scope:

* **daily** is read-only and unattended.  It answers three questions and nothing else: is each
  instance reachable, does it still report the version the catalogue pins, and has its
  metadata catalogue moved underneath us.  Writing to four public demo instances every day,
  unattended, is a denial of service against everybody else's demo data -- and 25.4 gives the
  daily pass no mandate to write.
* **pre-release** is the full Golden Project chain, VALIDATE and COMMIT included, behind the
  explicit write opt-in.  A release is gated on a passing run of this within seven days.

The patch-version reminder is here because nothing else notices: DHIS2 ships 2.41.11, the
catalogue still says 2.41.10, the regression passes against the older instance, and the drift
is invisible until somebody deploys to the newer one.  FR-TST-001 asks for the configuration
to be updated and the run repeated, which only happens if something reports the difference.

Run as a module from the S4 root (a plain path invocation puts ``tests/contract`` on
``sys.path`` instead of the repository root, and the governance import then fails)::

    .venv/bin/python -m tests.contract.schedule --daily
    .venv/bin/python -m tests.contract.schedule --pre-release --version 2.41
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from tests.contract import live_run

#: the daily read-only pass, across every instance in the catalogue
DAILY_PLAN: dict[str, Any] = {
    "writes": False,
    "serialisePerInstance": True,
    "instances": [
        {"version": item["version"], "profile": item["profile"], "baseUrl": item["baseUrl"]}
        for item in live_run.catalogue()["instances"]
    ],
    "checks": ["reachable", "reportedVersion", "catalogueDrift"],
}

#: the pre-release full chain; a release may not go out without one of these within the window
PRE_RELEASE_PLAN: dict[str, Any] = {
    "writes": True,
    "requiresOptIn": "FACTORY_DX_LIVE_ALLOW_COMMITS=1",
    "maxAgeOfPassingRunDays": int(live_run.catalogue()["schedule"]["maxAgeOfPassingRunDays"]),
    "retries": int(live_run.catalogue()["schedule"]["retries"]),
    "backoffSeconds": [float(x) for x in live_run.catalogue()["schedule"]["backoffSeconds"]],
    "outcomes": ["passed", "failed", "indeterminate"],
    "$comment": (
        "FR-TST-003: an unreachable, resetting or 5xx-ing instance is 'indeterminate' and is "
        "retried with a backoff -- it is never recorded as a failure, and a release is not "
        "blocked by it either.  A merge-blocking failure would make the suite ignorable."
    ),
}


def patch_version_reminders(reported: dict[str, str]) -> list[str]:
    """Lines telling the operator that an instance moved past the pinned patch version.

    ``reported`` maps an instance base URL to the version it answered with.
    """
    reminders: list[str] = []
    for base_url, version in sorted(reported.items()):
        pinned = live_run.newest_patch_known(version)
        if pinned and pinned != version:
            reminders.append(
                f"{base_url}: instance reports {version}, catalogue pins {pinned} -- update "
                "live_instances.json and re-run the regression (FR-TST-001)"
            )
    return reminders


def release_gate(results: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    """Is a release allowed?  Every supported version needs a *passing* run in the window.

    Indeterminate does not block a release -- the vendor's instance is not our defect -- but
    it does not satisfy the gate either, so a run that never became a verdict has to be
    re-run rather than waved through (FR-TST-003).
    """
    window_days = PRE_RELEASE_PLAN["maxAgeOfPassingRunDays"]
    blockers: list[str] = []
    for item in DAILY_PLAN["instances"]:
        version = str(item["version"])
        entry = results.get(version) or {}
        outcome = str(entry.get("outcome", "missing"))
        age = float(entry.get("ageDays", 1e9))
        if outcome != "passed":
            blockers.append(f"{version}: last run was {outcome}")
        elif age > window_days:
            blockers.append(f"{version}: passing run is {age:.1f} days old, over the {window_days}-day window")
    return (not blockers, blockers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--daily", action="store_true", help="read-only pass over every instance")
    mode.add_argument("--pre-release", action="store_true", help="full chain; needs the write opt-in")
    parser.add_argument("--version", default=None, help="limit to one DHIS2 version")
    parser.add_argument("--results", default=None, help="JSON of last outcomes, for --gate")
    parser.add_argument("--gate", action="store_true", help="answer the release question and exit")
    args = parser.parse_args(argv)

    if args.gate:
        if not args.results:
            parser.error("--gate needs --results")
        with open(args.results, encoding="utf-8") as handle:
            ok, blockers = release_gate(json.load(handle))
        for line in blockers:
            print(f"BLOCKED {line}")
        print("RELEASE_OK" if ok else "RELEASE_BLOCKED")
        return 0 if ok else 1

    plan = PRE_RELEASE_PLAN if args.pre_release else DAILY_PLAN
    instances = plan["instances"]
    if args.version:
        instances = [item for item in instances if str(item["version"]).startswith(args.version)]
        if not instances:
            print(f"--version {args.version} matches no catalogue entry", file=sys.stderr)
            return 2
    if plan["writes"]:
        import os

        if os.environ.get("FACTORY_DX_LIVE_ALLOW_COMMITS", "").strip().lower() not in {"1", "true", "yes"}:
            print(
                "the pre-release plan writes to shared public instances and needs "
                f"{plan['requiresOptIn']}",
                file=sys.stderr,
            )
            return 2

    print(
        json.dumps(
            {
                "mode": "pre-release" if args.pre_release else "daily",
                "writes": plan["writes"],
                "serialisedPerInstance": True,
                "runTag": live_run.RUN_TAG,
                "codePrefix": live_run.CODE_PREFIX,
                "instances": [item["baseUrl"] for item in instances],
                "invocation": (
                    "pytest -q -m contract tests/contract "
                    f"{'--live-commit ' if plan['writes'] else ''}"
                    "# one InstanceLock per baseUrl, held for the whole run"
                ),
            },
            indent=2,
        )
    )
    print(
        "This prints the plan; the run itself is the contract suite, which needs a reachable "
        "instance and a migrated dx schema. See tests/contract/conftest.py.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
