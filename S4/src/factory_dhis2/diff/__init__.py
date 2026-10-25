"""环境侧 Diff（DX-DIFF-001、总体 SRS 15.5）。

两类对比各一个公共函数，行计算与 DIFF_REPORT 产物集中在 :mod:`diff.report`：

* :func:`snapshot_vs_current.diff_snapshot_vs_current` —— 提交前 Snapshot vs 提交后状态；
* :func:`environments.diff_environments` —— 同一版本在两个环境的部署状态。

两者都是外部 I/O（读产物、必要时读环境），都以对象为单位输出四种
:class:`factory_dhis2.enums.ObjectChangeKind` 之一，更新附字段级明细。
"""

from __future__ import annotations

from factory_dhis2.diff.environments import diff_environments
from factory_dhis2.diff.report import build_rows, object_row, summary_of
from factory_dhis2.diff.snapshot_vs_current import diff_snapshot_vs_current

__all__ = [
    "build_rows",
    "diff_environments",
    "diff_snapshot_vs_current",
    "object_row",
    "summary_of",
]
