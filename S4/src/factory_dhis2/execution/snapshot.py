"""Snapshot（DX-EXE-022/023、FR-SNP-001/002）。

Snapshot 是 **S4 保存的目标环境状态记录**，不是 DHIS2 的事务快照，也不是数据库备份
（总体 SRS 15.4）。它回答两个问题：提交前那个环境长什么样，以及“那一刻的状态”是什么
——VALIDATE 的有效性三要素之一就是它（DX-EXE-029）。

范围按 DX-EXE-022 定义：Managed Scope 内的全部对象 **加上** 本次载荷涉及的全部对象。
两者是并集而不是交集：载荷可能引用范围外的对象，那些对象被改动同样影响本次部署。
分级 CONFIDENTIAL（目标实例的元数据导出，总体 SRS 20.1）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import DataClassification, new_id, structure_hash, unit_of_work

from factory_dhis2 import audit
from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.execution.context import RunContext
from factory_dhis2.execution.reads import FULL_READ_FIELDS, read_many, read_one
from factory_dhis2.models.execution import SnapshotIndexRow

#: Snapshot 的三种用途对应三种来源（DX-DIFF-001 的两类对比都从这里取）
KIND_PRE_COMMIT = "PRE_COMMIT"
KIND_POST_COMMIT = "POST_COMMIT"
KIND_CURRENT = "CURRENT"


def build(
    context: RunContext,
    *,
    kind: str = KIND_PRE_COMMIT,
    cancel: Any | None = None,
) -> dict[str, Any]:
    """读取范围内的当前状态，组装成 Snapshot 文档。"""
    package = context.package
    targets = _targets_of(context)
    by_type: dict[str, list[str]] = {}
    for item in targets:
        dhis2_type = str(item.get("dhis2Type") or "")
        uid = str(item.get("uid") or "")
        if not dhis2_type or not uid:
            continue
        by_type.setdefault(dhis2_type, []).append(uid)

    entries: list[dict[str, Any]] = []
    profile = context.target.profile
    from factory_dhis2.discovery.canonical import object_hash

    for dhis2_type, uids in sorted(by_type.items()):
        if cancel is not None:
            cancel.raise_if_cancelled()
        rows = read_many(context.client, dhis2_type, uids)
        for uid in sorted(uids):
            row = rows.get(uid)
            if row is None:
                # 单个对象再读一次：批量过滤器在某些版本上会漏掉新建的对象
                row = read_one(context.client, dhis2_type, uid, fields=FULL_READ_FIELDS)
            entries.append(
                {
                    "dhis2Type": dhis2_type,
                    "uid": uid,
                    "present": row is not None,
                    "lastUpdated": (row or {}).get("lastUpdated"),
                    "contentHash": object_hash(row, profile.volatile_fields) if row else None,
                    "content": row,
                }
            )

    document = {
        "schemaVersion": 1,
        "kind": "SNAPSHOT",
        "snapshotKind": kind,
        "environmentKey": context.environment_key,
        "environmentClass": context.target.environment_class.value,
        "dhis2Version": context.target.dhis2_version,
        "capabilityProfileArtifactId": context.target.capability_profile_artifact_id,
        "takenAt": datetime.now(UTC).isoformat(),
        "managedScope": package.managed_scope(),
        "payloadObjects": [
            {
                "businessKey": row.get("businessKey"),
                "dhis2Type": row.get("dhis2Type"),
                "uid": row.get("uid"),
                "disposition": row.get("disposition"),
            }
            for row in package.uid_map()
            if isinstance(row.get("uid"), str)
        ],
        "objects": sorted(entries, key=lambda item: (str(item["dhis2Type"]), str(item["uid"]))),
        "objectCount": len(entries),
    }
    return document


def env_state_hash(document: dict[str, Any]) -> str:
    """“那一刻环境状态”的指纹（DX-EXE-029 的第三要素）。

    只用对象 UID + 内容哈希参与：Snapshot 里的大段内容进指纹会让一次无关的字段展开
    改变状态哈希，从而把 VALIDATE 误判为过期。
    """
    return structure_hash(
        {
            "environmentKey": document.get("environmentKey"),
            "state": sorted(
                [
                    [str(item.get("dhis2Type")), str(item.get("uid")), str(item.get("contentHash") or "")]
                    for item in document.get("objects", [])
                ]
            ),
        }
    )


def save(
    context: RunContext,
    document: dict[str, Any],
    *,
    execution_id: str | None = None,
    kind: str = KIND_PRE_COMMIT,
) -> tuple[str, str]:
    """保存为 SNAPSHOT 产物并登记索引（DX-EXE-022）。

    暂存在事务外、登记与索引行与审计在同一短事务里：产物写对象存储是外部 I/O，把它放在
    调用方的事务里会被 S0 直接拒绝（00 文档 6.2）。
    """
    owner = context.project_id or f"env-{context.environment_key}"
    handle = stage_document(document, project_id=owner, artifact_type="SNAPSHOT")
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=owner,
            artifact_type="SNAPSHOT",
            data_classification=DataClassification.CONFIDENTIAL,
            source_artifact_ids=[context.package.artifact_id],
            session=session,
        )
        row = SnapshotIndexRow(
            id=new_id("dxsnp"),
            environment_key=context.environment_key,
            execution_id=execution_id,
            kind=kind,
            scope={
                "managedScopeEntries": len(document.get("managedScope", [])),
                "objects": document.get("objectCount", 0),
            },
            artifact_id=info.id,
            content_hash=info.content_hash,
            dhis2_version=context.target.dhis2_version,
            object_count=int(document.get("objectCount") or 0),
            created_at_snapshot=datetime.now(UTC),
        )
        session.add(row)
        audit.record(
            audit.SNAPSHOT_CREATED,
            object_type="Snapshot",
            object_id=row.id,
            project_id=context.project_id,
            after={
                "environmentKey": context.environment_key,
                "artifactId": info.id,
                "kind": kind,
                "objectCount": row.object_count,
            },
            session=session,
        )
    return info.id, row.id


def _targets_of(context: RunContext) -> list[dict[str, Any]]:
    """Managed Scope ∪ 载荷对象 ∪ 观测基准对象。"""
    package = context.package
    seen: dict[str, dict[str, Any]] = {}
    for row in package.managed_scope():
        uid = row.get("uid")
        if isinstance(uid, str):
            seen[uid] = {
                "uid": uid,
                "dhis2Type": row.get("dhis2Type") or row.get("dhis2_type"),
                "businessKey": row.get("businessKey"),
            }
    for row in package.uid_map():
        uid = row.get("uid")
        if isinstance(uid, str):
            seen.setdefault(uid, {"uid": uid, "dhis2Type": row.get("dhis2Type"), "businessKey": row.get("businessKey")})
    for row in package.observation_baselines():
        uid = row.get("uid")
        if isinstance(uid, str):
            seen.setdefault(uid, {"uid": uid, "businessKey": row.get("businessKey")})
    # 补齐 dhis2Type：Snapshot 必须能寻址每个对象
    types = {str(row.get("uid")): str(row.get("dhis2Type") or "") for row in package.objects()}
    types.update({str(row.get("uid")): str(row.get("dhis2Type") or "") for row in package.uid_map()})
    for uid, item in seen.items():
        if not item.get("dhis2Type"):
            item["dhis2Type"] = types.get(uid, "")
    return sorted((item for item in seen.values() if item.get("dhis2Type")), key=lambda item: (str(item["dhis2Type"]), str(item["uid"])))


__all__ = [
    "KIND_CURRENT",
    "KIND_POST_COMMIT",
    "KIND_PRE_COMMIT",
    "build",
    "env_state_hash",
    "save",
]
