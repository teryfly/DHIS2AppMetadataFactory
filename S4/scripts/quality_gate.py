#!/usr/bin/env python3
"""S4 的质量门（DX-NFR-06）。

覆盖率门槛不是一个“数字指标”，而是“哪些规则真的被测过”的证据：执行管线与环境锁承载了
全部不可逆动作与并发规则，所以它们要求 90%，而整体要求 85%。这个脚本把门槛写死在代码
里，跑 CI 的人改不了“顺手降低门槛”。

用法：``.venv/bin/python scripts/quality_gate.py``（先跑 pytest 生成 coverage.json）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: 目录前缀 → 行覆盖率下限（DX-NFR-06：整体 85%，执行管线与锁 90%）
THRESHOLDS: dict[str, float] = {
    "": 85.0,
    "execution/": 90.0,
    "envlock/": 90.0,
}

ROOT = Path(__file__).resolve().parent.parent
COVERAGE_FILE = ROOT / "coverage.json"


def _summaries() -> dict[str, tuple[int, int]]:
    """文件 → (已覆盖行, 总行数)。

    门槛按**行数加权**算而不是按文件平均：平均会让一个 10 行的 ``__init__`` 与一个 500 行
    的管线文件权重相同，那样得到的数字说明不了“不可逆动作真的被测过”，只是好看。
    """
    data = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
    out: dict[str, tuple[int, int]] = {}
    for path, info in data.get("files", {}).items():
        summary = info.get("summary", {})
        total = int(summary.get("num_statements", 0))
        if not total:
            continue
        normalized = path.replace("src/factory_dhis2/", "factory_dhis2/")
        if not normalized.startswith("factory_dhis2/"):
            continue
        out[normalized] = (int(summary.get("covered_lines", 0)), total)
    return out


def _aggregate(summaries: dict[str, tuple[int, int]], prefix: str) -> tuple[float, int]:
    """某前缀下的加权覆盖率与文件数（``""`` 表示整体）。"""
    wanted = f"factory_dhis2/{prefix}" if prefix else "factory_dhis2/"
    matched = [
        (covered, total)
        for name, (covered, total) in summaries.items()
        if name.startswith(wanted)
    ]
    if not matched:
        return 0.0, 0
    covered = sum(item[0] for item in matched)
    total = sum(item[1] for item in matched)
    return (100.0 * covered / total if total else 0.0), len(matched)


def main() -> int:
    if not COVERAGE_FILE.exists():
        print(
            f"缺少 {COVERAGE_FILE}：先跑 pytest --cov --cov-report=json:coverage.json",
            file=sys.stderr,
        )
        return 2
    summaries = _summaries()
    failures: list[str] = []
    for prefix, floor in THRESHOLDS.items():
        label = prefix or "整体"
        value, count = _aggregate(summaries, prefix)
        if count == 0:
            failures.append(f"{label}：没有匹配到任何文件（目录改名或覆盖率数据缺失）")
            continue
        print(f"{label:<16} {value:5.1f}%  (门槛 {floor:.0f}%, {count} 个文件)")
        if value < floor:
            failures.append(f"{label} 覆盖率 {value:.1f}% 低于门槛 {floor:.0f}%")
    if failures:
        print("\n".join(f"FAIL: {item}" for item in failures), file=sys.stderr)
        return 1
    print("质量门通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
