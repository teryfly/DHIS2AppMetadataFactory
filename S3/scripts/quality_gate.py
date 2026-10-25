#!/usr/bin/env python3
"""Run the suite and enforce the coverage floors of AI-NFR-04.

One ``--cov-fail-under`` cannot express the rule: the Policy Engine is a pure decision that
every run passes through and the agent constraint checks are the *only* thing between a model
and S2, so those two carry a higher floor than a CRUD service does.  The floors are stated per
package prefix, applied to line coverage of the shipped package only.

    .venv/bin/python scripts/quality_gate.py                  # runs pytest itself
    .venv/bin/python scripts/quality_gate.py --report-only    # use coverage.json as is
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "coverage.json"
PACKAGE = "factory_intelligence"

#: package prefix (relative to ``factory_intelligence``) -> required line coverage.
#: ``policy`` is AI-NFR-01's pure engine — every denial and every PHI level is a decision that
#: must have been executed at least once; ``agents/checks`` is AI-RT-012, the rung that stands
#: between a model's answer and a real Blueprint.
FLOORS: dict[str, float] = {
    "": 85.0,
    "policy": 90.0,
    "agents/checks": 90.0,
}

#: migrations and shipped test doubles are reviewed as data, not exercised line by line
IGNORE_PREFIXES = ("migrations", "testing")


def package_of(filename: str) -> str | None:
    """The path of one source file inside the package, or ``None`` if it is not in it."""
    marker = f"{PACKAGE}/"
    if marker not in filename:
        return None
    path = filename.split(marker, 1)[-1]
    if path.startswith(IGNORE_PREFIXES):
        return None
    return path


def keys_for(path: str) -> list[str]:
    """Every floor this file counts towards: the whole package plus its own prefix."""
    matched = [key for key in FLOORS if key and (path == key or path.startswith(f"{key}/"))]
    # the deepest declared prefix wins, so ``agents/checks`` is not also counted as ``agents``
    return ["", max(matched, key=len)] if matched else [""]


def measure(report: dict[str, dict[str, float]]) -> tuple[list[str], dict[str, float]]:
    """Aggregate the file summaries into per-package percentages."""
    totals: dict[str, list[int]] = {key: [0, 0] for key in FLOORS}
    for filename, summary in report.items():
        path = package_of(filename)
        if path is None:
            continue
        for key in keys_for(path):
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
    return "whole package" if not key else f"{PACKAGE}.{key}"


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    if "--report-only" not in argv:
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"--cov={PACKAGE}", f"--cov-report=json:{REPORT}"],
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
        print("coverage below the AI-NFR-04 floors:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("coverage floors met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
