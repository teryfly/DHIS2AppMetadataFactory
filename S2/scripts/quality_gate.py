#!/usr/bin/env python3
"""Run the suite and enforce the coverage floors of BP-NFR-05.

A single ``--cov-fail-under`` cannot express the rule: the compiler and validator
packages carry the acceptance criteria and the reproducibility promise, so they need a
higher floor than a CRUD service does.  This script reads one JSON coverage report and
applies the per-package floors, then exits non-zero with the list of shortfalls.

    .venv/bin/python scripts/quality_gate.py            # runs pytest itself
    .venv/bin/python scripts/quality_gate.py --report-only   # use coverage.json as is
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "coverage.json"

#: package prefix (relative to ``factory_blueprint``) -> required line coverage
FLOORS: dict[str, float] = {
    "": 85.0,
    "compilers": 90.0,
    "validators": 90.0,
    # NFR-MNT-004 names the Compiler and Validator; this package is what they consult to
    # be version-correct (FR-CAP-001), so an uncovered branch here means a Profile that
    # would be guessed at.
    "capability": 90.0,
}

#: data files are reviewed as data, not exercised line by line
IGNORE_PREFIXES = ("migrations", "testing")


def measure(report: dict[str, dict[str, float]]) -> tuple[list[str], dict[str, float]]:
    """Aggregate the file summaries into per-package percentages."""
    totals: dict[str, list[int]] = {key: [0, 0] for key in FLOORS}
    for filename, summary in report.items():
        path = filename.split("factory_blueprint/", 1)[-1]
        if path.startswith(IGNORE_PREFIXES):
            continue
        package = path.split("/", 1)[0] if "/" in path else ""
        keys = ["", package] if package in FLOORS else [""]
        for key in keys:
            totals[key][0] += int(summary["covered_lines"])
            totals[key][1] += int(summary["num_statements"])
    problems: list[str] = []
    measured: dict[str, float] = {}
    for key, floor in FLOORS.items():
        covered, statements = totals[key]
        if not statements:
            problems.append(f"{label(key)}: no statements measured")
            continue
        percent = 100.0 * covered / statements
        measured[key] = percent
        if percent < floor:
            problems.append(f"{label(key)}: {percent:.1f}% < {floor:.0f}%")
    return problems, measured


def label(key: str) -> str:
    return "whole package" if not key else f"factory_blueprint.{key}"


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    if "--report-only" not in argv:
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--cov=factory_blueprint", f"--cov-report=json:{REPORT}"],
            cwd=root,
            check=False,
        )
    if not REPORT.exists():
        print(f"no coverage report at {REPORT}", file=sys.stderr)
        return 2
    report = json.loads(REPORT.read_text(encoding="utf-8"))["files"]
    summaries = {name: entry["summary"] for name, entry in report.items()}
    problems, measured = measure(summaries)
    for key, percent in sorted(measured.items()):
        print(f"{label(key)}: {percent:.1f}% (floor {FLOORS[key]:.0f}%)")
    if problems:
        print("coverage below the BP-NFR-05 floors:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("coverage floors met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
