"""提交前 Snapshot ↔ 提交后状态的对比（DX-DIFF-001 之一、总体 SRS 15.5）。

回答的是运维最想问的那句话：“这次部署到底把环境改成了什么样”。基准是执行自己的提交前
Snapshot（DX-EXE-022），另一侧优先用部署后验证留下的 POST_COMMIT Snapshot——它是同一次
部署读回的状态，两侧同一套薄/厚读取口径，比对才成立（:mod:`execution.post_verify`）。

没有 POST_COMMIT（还没跑过 R5）时才当场读一次环境：那时读的是 **discovery 凭证范围**
（DX-BR-04），Diff 永远不改环境。读回的条目会补 ``contentHash``，与 Snapshot 里的哈希用
同一个定义（:mod:`execution.snapshot`），否则两侧哈希不可比。

输出是逐对象行（新建 / 更新-字段级 / 不变 / 仅引用）+ DIFF_REPORT 产物。
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import external_io, unit_of_work

from factory_dhis2.capability.runtime_profile import apply_environment_capability
from factory_dhis2.diff import report as diff_report
from factory_dhis2.dto.execution import DiffReportOutcome
from factory_dhis2.enums import CredentialScope
from factory_dhis2.execution import steps
from factory_dhis2.execution.context import RunContext, load_context
from factory_dhis2.execution.reads import FULL_READ_FIELDS, read_one
from factory_dhis2.execution.snapshot import KIND_POST_COMMIT, KIND_PRE_COMMIT
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.profiles.base import VersionProfile


@external_io
def diff_snapshot_vs_current(execution_id: str) -> DiffReportOutcome:
    """一次执行的“提交前 vs 提交后”对比（SRS 7.4）。"""
    with unit_of_work() as session:
        execution = steps.require(session, execution_id)
        view: dict[str, Any] = {
            "environmentKey": execution.environment_key,
            "projectId": execution.project_id,
            "payloadArtifactId": execution.payload_artifact_id,
            "payloadHash": execution.payload_hash,
            "snapshotArtifactId": execution.snapshot_artifact_id,
            "dhis2Version": None,
        }

    baseline_artifact = view["snapshotArtifactId"] or diff_report.latest_snapshot_artifact(
        execution_id, [KIND_PRE_COMMIT]
    )
    baseline = diff_report.read_snapshot_document(baseline_artifact)
    if baseline is None:
        # 没有基准就没有对比：这不是“没有差异”，硬说成不变会把一次未记录的部署藏起来
        return DiffReportOutcome(
            diff_report_artifact_id=None,
            objects=[],
            summary={
                "objects": 0,
                "reason": "该执行没有提交前 Snapshot，无法对比（DX-EXE-022）",
                "executionId": execution_id,
            },
        )

    current_artifact = diff_report.latest_snapshot_artifact(execution_id, [KIND_POST_COMMIT])
    current_document = diff_report.read_snapshot_document(current_artifact)
    read_back_live = current_document is None
    context: RunContext | None = None
    if current_document is None and view["payloadArtifactId"]:
        context = load_context(
            view["environmentKey"],
            str(view["payloadArtifactId"]),
            project_id=view["projectId"],
            scope=CredentialScope.DISCOVERY,
        )
        current_document = live_document(context, baseline)
    if current_document is None:
        # 两侧必须真的都拿得到。既没有提交后 Snapshot、又没有载荷可回读时，把空集合当成
        # “现在的环境”会产出一份“所有对象都不见了”的报告——那是把“我没法知道”说成了一次
        # 大规模删除（DX-DIFF-001、DX-EXE-022）。
        return DiffReportOutcome(
            diff_report_artifact_id=None,
            objects=[],
            summary={
                "objects": 0,
                "reason": "该执行既没有提交后 Snapshot，也没有可回读的载荷产物，无法对比"
                "（DX-DIFF-001）",
                "executionId": execution_id,
                "baselineSnapshot": baseline_artifact,
            },
        )

    profile = context.target.profile if context is not None else _profile_for_environment(
        view["environmentKey"]
    )
    rows = diff_report.build_rows(
        diff_report.entries_of(baseline),
        diff_report.entries_of(current_document),
        profile=profile,
        dispositions=diff_report.disposition_map(baseline, current_document),
    )
    summary = diff_report.summary_of(rows)
    document = diff_report.build_document(
        comparison="SNAPSHOT_VS_CURRENT",
        project_id=view["projectId"],
        environment_keys=[view["environmentKey"]],
        profile_versions=[profile.profile_version],
        rows=rows,
        summary=summary,
        artifacts={
            "baselineSnapshot": baseline_artifact,
            "currentSnapshot": current_artifact,
            "currentReadBack": "LIVE" if read_back_live else "SNAPSHOT",
            "payload": view["payloadArtifactId"],
            "payloadHash": view["payloadHash"],
            "executionId": execution_id,
        },
    )
    artifact_id = diff_report.save(
        document,
        project_id=view["projectId"],
        source_artifact_ids=[str(item) for item in (baseline_artifact, current_artifact) if item],
    )
    return DiffReportOutcome(diff_report_artifact_id=artifact_id, objects=rows, summary=summary)


def content_hash_of(row: dict[str, Any], profile: Any) -> str:
    """内容哈希的定义只有一处：与 Snapshot / 漂移检查同一个（DX-DSC-007）。"""
    from factory_dhis2.discovery.canonical import object_hash

    return object_hash(row, profile.volatile_fields)


def live_document(context: RunContext, baseline: dict[str, Any]) -> dict[str, Any]:
    """当场读回环境现状，组装成与 Snapshot 同形的文档（键名一致才比得了）。"""
    profile = context.target.profile
    entries: list[dict[str, Any]] = []
    for key, entry in sorted(diff_report.entries_of(baseline).items()):
        uid = str(entry.get("uid") or key.partition("/")[2])
        dhis2_type = str(entry.get("dhis2Type") or key.partition("/")[0])
        if not uid or not dhis2_type:
            continue
        row = read_one(context.client, dhis2_type, uid, fields=FULL_READ_FIELDS)
        entries.append(
            {
                "dhis2Type": dhis2_type,
                "uid": uid,
                "businessKey": entry.get("businessKey"),
                "present": row is not None,
                "lastUpdated": (row or {}).get("lastUpdated"),
                "contentHash": content_hash_of(row, profile) if row else None,
                "content": row,
            }
        )
    return {
        "kind": "SNAPSHOT",
        "snapshotKind": "LIVE_READ_BACK",
        "environmentKey": context.environment_key,
        "dhis2Version": context.target.dhis2_version,
        "objects": entries,
        "payloadObjects": baseline.get("payloadObjects") or [],
        "objectCount": len(entries),
    }


def _profile_for_environment(environment_key: str) -> VersionProfile:
    """两侧都是已持久化文档时也要比较规则：从该环境的 Capability Profile 取。

    这里**不发任何 DHIS2 请求**——只读 ``dx`` 里的环境行与最近一次探测结果
    （DX-CAP-003“禁止运行时猜测”）。
    """
    from factory_dhis2.environment.loader import build_target

    with unit_of_work() as session:
        target = build_target(session, environment_key, require_probe=False)
    return apply_environment_capability(target).profile


def execution_view(session: Any, execution: ExecutionRow) -> dict[str, Any]:
    """执行行的只读视图（供日志与产物的 artifacts 段引用）。"""
    return {
        "executionId": execution.id,
        "environmentKey": execution.environment_key,
        "payloadArtifactId": execution.payload_artifact_id,
        "snapshotArtifactId": execution.snapshot_artifact_id,
    }


__all__ = [
    "content_hash_of",
    "diff_snapshot_vs_current",
    "execution_view",
    "live_document",
]
