"""同一版本在两个环境中的部署状态对比（DX-DIFF-001 之二、总体 SRS 15.5）。

多环境晋级时最贵的问题是“UAT 和 PROD 到底差在哪”。这里的答案来自**两个环境各自最近一次
成功执行的 Snapshot 与部署后读回结果**（SRS 3.10 原话）：优先取 POST_COMMIT——那是部署后
验证从环境读回的状态，比提交前的基准更贴近“现在长什么样”；没有它就退回提交前 Snapshot，
并在每一侧的 ``artifacts.snapshotKind`` 里如实标出用的是哪一种（不假装两侧同一口径）。

方向约定写在每一行里：``presentInBaseline`` = A 侧、``presentInCurrent`` = B 侧，``kind``
描述的是“从 A 到 B 需要发生什么”。SRS 3.10 只给四种展示，所以“只有一侧存在”统一显示为
新建——是 A 独有还是 B 已有，由那两个布尔字段说明，不新造第五种 ``kind``。

没有成功执行过的那个环境就是空的一侧：这本身就是答案（“那边还没部署过”），不编造基准。
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import external_io, unit_of_work
from sqlalchemy import select

from factory_dhis2.artifacts import read_json
from factory_dhis2.capability.runtime_profile import apply_environment_capability
from factory_dhis2.diff import report as diff_report
from factory_dhis2.dto.execution import DiffReportOutcome
from factory_dhis2.enums import ExecutionStatus, ObjectChangeKind
from factory_dhis2.errors import EnvironmentInvalidError
from factory_dhis2.execution.snapshot import KIND_POST_COMMIT, KIND_PRE_COMMIT
from factory_dhis2.models.execution import ExecutionRow, SnapshotIndexRow
from factory_dhis2.profiles.base import VersionProfile

#: 找“最近一次成功执行”时的有界扫描：命中不了就按“没有”处理，不做无界遍历
_SCAN_LIMIT = 20

#: 快照的两种用途，按对比意义排序（先读回、后基准）
_SNAPSHOT_KINDS: tuple[str, ...] = (KIND_POST_COMMIT, KIND_PRE_COMMIT)


@external_io
def diff_environments(
    project_id: str, key_a: str, key_b: str, *, blueprint_version: str | None = None
) -> DiffReportOutcome:
    """两个环境的部署状态对比（SRS 7.4）。"""
    side_a = latest_successful_snapshot(key_a, project_id, blueprint_version)
    side_b = latest_successful_snapshot(key_b, project_id, blueprint_version)

    notes: list[str] = []
    for label, key, side in (("A", key_a, side_a), ("B", key_b, side_b)):
        if side["executionId"] is None:
            scope = f"（Blueprint 版本 {blueprint_version}）" if blueprint_version else ""
            notes.append(f"环境 {key} 没有该项目{scope}的成功执行记录，按空的一侧对比（{label} 侧）")

    profile = _profile_of(side_a if side_a["executionId"] else side_b)
    rows = diff_report.build_rows(
        side_a["entries"],
        side_b["entries"],
        profile=profile,
        dispositions=diff_report.disposition_map(side_a["document"], side_b["document"]),
    )
    for row in rows:
        if row["presentInBaseline"] and not row["presentInCurrent"]:
            # 只在 A 侧存在：从 A 到 B 要做的就是“在 B 里建出来”→ 新建（方向见两个布尔字段）
            row["kind"] = ObjectChangeKind.CREATED.value
            row["note"] = "仅存在于 A 侧，B 侧尚未部署"
    summary = diff_report.summary_of(rows)
    summary["notes"] = notes
    document = diff_report.build_document(
        comparison="ENVIRONMENTS",
        project_id=project_id,
        environment_keys=[key_a, key_b],
        profile_versions=[profile.profile_version],
        rows=rows,
        summary=summary,
        artifacts={
            "environmentA": key_a,
            "environmentB": key_b,
            "A": side_a["artifacts"],
            "B": side_b["artifacts"],
        },
        blueprint_version=blueprint_version,
    )
    sources = [
        str(side["artifacts"].get("snapshot"))
        for side in (side_a, side_b)
        if side["artifacts"].get("snapshot")
    ]
    artifact_id = diff_report.save(document, project_id=project_id, source_artifact_ids=sources)
    return DiffReportOutcome(diff_report_artifact_id=artifact_id, objects=rows, summary=summary)


def latest_successful_snapshot(
    environment_key: str, project_id: str, blueprint_version: str | None = None
) -> dict[str, Any]:
    """该环境（该项目）最近一次成功执行的“部署后读回”Snapshot。

    给了 ``blueprint_version`` 就沿最近的成功执行往前找到计划文档里登记了该版本的那一次——
    “同一版本在两个环境”才是可对比的两个对象，版本不同的两份环境状态没有比较意义
    （DX-DIFF-001）。

    顺序是硬性的：**先在短事务里把候选行与它们的产物 ID 读出来，再到事务外读产物**
    （``read_json`` 是外部 I/O，00 文档 6.2）。
    """
    with unit_of_work() as session:
        candidates = [
            {
                "executionId": row.id,
                "planArtifactId": row.plan_artifact_id,
                "payloadArtifactId": row.payload_artifact_id,
                "fallbackSnapshotArtifactId": row.snapshot_artifact_id,
            }
            for row in session.execute(
                select(ExecutionRow)
                .where(
                    ExecutionRow.environment_key == environment_key,
                    ExecutionRow.project_id == project_id,
                    ExecutionRow.status == ExecutionStatus.COMPLETED.value,
                    ExecutionRow.applied.is_(True),
                )
                .order_by(ExecutionRow.completed_at.desc())
                .limit(_SCAN_LIMIT)
            )
            .scalars()
            .all()
        ]
        snapshots: dict[str, tuple[str | None, str]] = {}
        for kind in _SNAPSHOT_KINDS:
            for execution_id, artifact_id in session.execute(
                select(SnapshotIndexRow.execution_id, SnapshotIndexRow.artifact_id)
                .where(
                    SnapshotIndexRow.execution_id.in_([item["executionId"] for item in candidates]),
                    SnapshotIndexRow.kind == kind,
                )
                .order_by(SnapshotIndexRow.created_at_snapshot.desc())
            ).all():
                # 每种用途只留最近一份，且 POST_COMMIT 优先（外层按 kinds 的顺序覆盖）
                if execution_id is not None and execution_id not in snapshots:
                    snapshots[execution_id] = (artifact_id, kind)

    chosen = _pick_by_blueprint(candidates, blueprint_version)
    if chosen is None:
        return _empty_side(environment_key)
    snapshot_artifact_id, snapshot_kind = snapshots.get(
        str(chosen["executionId"]), (chosen.get("fallbackSnapshotArtifactId"), KIND_PRE_COMMIT)
    )
    document = diff_report.read_snapshot_document(snapshot_artifact_id)
    return {
        "executionId": chosen["executionId"],
        "entries": diff_report.entries_of(document),
        "document": document,
        "artifacts": {
            "executionId": chosen["executionId"],
            "snapshot": snapshot_artifact_id,
            "snapshotKind": snapshot_kind if snapshot_artifact_id else None,
            "payload": chosen["payloadArtifactId"],
            "plan": chosen["planArtifactId"],
        },
        "environmentKey": environment_key,
    }


def _pick_by_blueprint(
    candidates: list[dict[str, Any]], blueprint_version: str | None
) -> dict[str, Any] | None:
    if not candidates:
        return None
    if blueprint_version is None:
        return candidates[0]
    for candidate in candidates:
        plan_artifact_id = candidate.get("planArtifactId")
        document = read_json(str(plan_artifact_id)) if plan_artifact_id else {}
        if str(document.get("blueprintVersion") or "") == blueprint_version:
            return candidate
    return None


def _empty_side(environment_key: str) -> dict[str, Any]:
    return {
        "executionId": None,
        "entries": {},
        "document": None,
        "artifacts": {
            "executionId": None,
            "snapshot": None,
            "snapshotKind": None,
            "environmentKey": environment_key,
        },
        "environmentKey": environment_key,
    }


def _profile_of(side: dict[str, Any]) -> VersionProfile:
    """比较规则取自该环境的 Capability Profile 对应的版本 Profile（DX-CAP-003）。"""
    from factory_dhis2.environment.loader import build_target

    # 只有**真的有一次成功执行**的那一侧才能供出比较规则：空的一侧虽然也有环境键，但拿它的
    # 规则去比两份空状态，就会产出一份“没有差异”的 DIFF_REPORT——那正是把没部署过的环境说成
    # 已经对齐（DX-DIFF-001）。
    environment_key = side.get("environmentKey") if side.get("executionId") else None
    if not environment_key:
        raise EnvironmentInvalidError(
            "no environment to take the comparison profile from: neither environment has a "
            "successful execution for this project (DX-DIFF-001)",
            details={"rule": "DX-DIFF-001"},
        )
    with unit_of_work() as session:
        target = build_target(session, str(environment_key), require_probe=False)
    # 字段级 Diff 排除哪些易变字段，也要按这个环境探测确认过的那份能力（DX-CLI-005）
    return apply_environment_capability(target).profile


__all__ = ["diff_environments", "latest_successful_snapshot"]
