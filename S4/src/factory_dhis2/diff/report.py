"""Diff 的行计算与 DIFF_REPORT 产物（DX-DIFF-001、总体 SRS 15.5）。

两类对比（提交前 Snapshot ↔ 提交后状态、同一版本在两个环境的部署状态）用的是**同一套对象
行**：以对象为单位给出 ``kind`` ∈ 新建 / 更新（字段级）/ 不变 / 仅引用（
:class:`factory_dhis2.enums.ObjectChangeKind`），更新再附字段级明细。集中在这里的原因是
“什么算变了”只能有一种答案：如果两条对比各按一套规则判，同一个对象在两份报告里会得出
不同结论，而 Diff 的价值恰恰在于可比。

比较的两个输入都是 **Snapshot 文档**（DX-EXE-022 的形状）：它的 ``objects[]`` 里每项带
``present`` / ``content`` / ``contentHash`` / ``lastUpdated``。字段级明细在 ``content`` 上
算，易变与版本禁止比较的字段一律排除（DX-DSC-007 的同一张排除表）——否则 ``lastUpdated``
会让每个对象都显示为“更新”，报告就失去意义。

DIFF_REPORT 由 S2 与 S4 共同拥有（总体 SRS 15.5、:data:`registries.DX_ARTIFACT_TYPES`），
所以这里登记的产物创建者是 S4，类型合法。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import unit_of_work
from sqlalchemy import select

from factory_dhis2.artifacts import read_json, register_staged, stage_document
from factory_dhis2.enums import Disposition, ObjectChangeKind
from factory_dhis2.execution.post_verify import same_scalar
from factory_dhis2.models.execution import SnapshotIndexRow
from factory_dhis2.profiles.base import VersionProfile

#: 只被引用、不由本次部署创建或更新的对象（DX-DIFF-001 的“仅引用”）
REFERENCE_DISPOSITIONS: frozenset[str] = frozenset(
    {Disposition.REFERENCE.value, Disposition.REUSE.value, Disposition.EXTERNAL.value}
)

#: 字段级明细里的“存在性”字段名：对象在一侧不存在时用它会话最清楚
PRESENCE_FIELD = "__present__"


def entries_of(document: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Snapshot 文档 → UID → 条目（同一 UID 多类型时以 ``类型/UID`` 为键，见 :func:`entry_key`）。"""
    out: dict[str, dict[str, Any]] = {}
    for entry in (document or {}).get("objects") or []:
        if isinstance(entry, dict) and entry.get("uid"):
            out[entry_key(entry)] = entry
    return out


def entry_key(entry: dict[str, Any]) -> str:
    return f"{entry.get('dhis2Type')}/{entry.get('uid')}"


def disposition_map(*documents: dict[str, Any] | None) -> dict[str, str]:
    """从 Snapshot 的 ``payloadObjects`` 里取处置（Disposition 词汇），供“仅引用”判定。"""
    out: dict[str, str] = {}
    for document in documents:
        for row in (document or {}).get("payloadObjects") or []:
            if not isinstance(row, dict):
                continue
            uid = row.get("uid")
            disposition = row.get("disposition")
            if isinstance(uid, str) and isinstance(disposition, str):
                out[str(row.get("dhis2Type")) + "/" + uid] = disposition
    return out


def changed_fields(
    before: Any, after: Any, *, dhis2_type: str, profile: VersionProfile
) -> list[dict[str, Any]]:
    """字段级明细：两侧键的并集里值不同的那些（排除易变与禁止比较的字段）。"""
    excluded = set(profile.volatile_fields) | set(profile.forbidden_fields(dhis2_type))
    out: list[dict[str, Any]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            if key in excluded:
                continue
            out.extend(
                _leaf_diff(
                    key, before.get(key), after.get(key), excluded=excluded, dhis2_type=dhis2_type
                )
            )
        return out
    out.extend(_leaf_diff("", before, after, excluded=excluded, dhis2_type=dhis2_type))
    return out


def _leaf_diff(
    path: str,
    before: Any,
    after: Any,
    *,
    excluded: set[str],
    dhis2_type: str,
) -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            if key in excluded:
                continue
            child = f"{path}.{key}" if path else key
            out.extend(_leaf_diff(child, before.get(key), after.get(key), excluded=excluded, dhis2_type=dhis2_type))
        return out
    if isinstance(before, list) or isinstance(after, list):
        if _normalized(before, excluded) == _normalized(after, excluded):
            return []
        return [{"field": path or dhis2_type, "from": before, "to": after}]
    if before is None and after is None:
        return []
    if (before is None) != (after is None):
        return [{"field": path, "from": before, "to": after}]
    if same_scalar(before, after):
        return []
    return [{"field": path, "from": before, "to": after}]


def _normalized(value: Any, excluded: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalized(item, excluded)
            for key, item in sorted(value.items())
            if key not in excluded
        }
    if isinstance(value, list):
        return sorted(
            (_normalized(item, excluded) for item in value),
            key=lambda item: repr(item),
        )
    return value


def object_row(
    key: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    profile: VersionProfile,
    disposition: str | None = None,
) -> dict[str, Any]:
    """一个对象的对比行（DX-DIFF-001 的展示单位）。

    ``before`` / ``after`` 是两侧的状态条目；``None`` 表示该侧没有这个对象——Snapshot 里
    ``present=False`` 与“清单里根本没这个对象”在展示上是同一件事。
    """
    dhis2_type, _, uid = key.partition("/")
    was_present = bool(before and before.get("present"))
    is_present = bool(after and after.get("present"))
    row: dict[str, Any] = {
        "dhis2Type": dhis2_type,
        "uid": uid,
        "businessKey": _business_key_of(before, after),
        "disposition": disposition,
        "presentInBaseline": was_present,
        "presentInCurrent": is_present,
        "baselineContentHash": (before or {}).get("contentHash"),
        "currentContentHash": (after or {}).get("contentHash"),
        "changedFields": [],
    }
    if not was_present and is_present:
        row["kind"] = ObjectChangeKind.CREATED.value
        return row
    if was_present and not is_present:
        # SRS 3.10 只给四种展示，对象“消失了”仍然是一次更新（字段级明细里用存在性说清楚）
        row["kind"] = ObjectChangeKind.UPDATED.value
        row["changedFields"] = [{"field": PRESENCE_FIELD, "from": True, "to": False}]
        row["note"] = "对象在对比的另一侧已不存在"
        return row
    if not was_present and not is_present:
        row["kind"] = ObjectChangeKind.UNCHANGED.value
        row["note"] = "两侧都没有该对象"
        return row
    differences = changed_fields(
        (before or {}).get("content"),
        (after or {}).get("content"),
        dhis2_type=dhis2_type,
        profile=profile,
    )
    if differences:
        row["kind"] = ObjectChangeKind.UPDATED.value
        row["changedFields"] = differences
        return row
    # 内容没变：被引用的对象就显示为“仅引用”，本次部署没有改变它
    row["kind"] = (
        ObjectChangeKind.REFERENCE_ONLY.value
        if disposition in REFERENCE_DISPOSITIONS
        else ObjectChangeKind.UNCHANGED.value
    )
    return row


def _business_key_of(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str | None:
    for entry in (before, after):
        if entry and isinstance(entry.get("businessKey"), str):
            return str(entry["businessKey"])
    return None


def build_rows(
    baseline: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    *,
    profile: VersionProfile,
    dispositions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """两侧条目的并集逐个比对，行按 (类型, UID) 稳定排序。"""
    known = dispositions or {}
    keys = sorted(set(baseline) | set(current))
    return [
        object_row(
            key,
            baseline.get(key),
            current.get(key),
            profile=profile,
            disposition=known.get(key),
        )
        for key in keys
    ]


def summary_of(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按 ``kind`` 计数 + 字段级差异总数（S1 直接展示的摘要）。"""
    counts = {item.value: 0 for item in ObjectChangeKind}
    fields = 0
    for row in rows:
        kind = str(row.get("kind"))
        if kind in counts:
            counts[kind] += 1
        fields += len(row.get("changedFields") or [])
    return {
        "objects": len(rows),
        "byKind": counts,
        "changedFields": fields,
        "changed": any(row["kind"] != ObjectChangeKind.UNCHANGED.value for row in rows),
    }


def build_document(
    *,
    comparison: str,
    project_id: str | None,
    environment_keys: list[str],
    profile_versions: list[str],
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    artifacts: dict[str, Any],
    blueprint_version: str | None = None,
) -> dict[str, Any]:
    """DIFF_REPORT 文档（两类对比共用一种形状，用 ``comparison`` 区分）。"""
    return {
        "schemaVersion": 1,
        "kind": "DIFF_REPORT",
        "comparison": comparison,
        "projectKey": project_id,
        "environmentKeys": environment_keys,
        "profileVersions": profile_versions,
        "blueprintVersion": blueprint_version,
        "objects": rows,
        "summary": summary,
        "artifacts": artifacts,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def save(
    document: dict[str, Any],
    *,
    project_id: str | None,
    source_artifact_ids: list[str],
) -> str:
    """登记为 DIFF_REPORT 产物（DX-DIFF-001）。

    与其余产物同一形状：**暂存在事务外，登记自开一段短事务**（00 文档 6.2）。Diff 不改变
    任何执行状态，因此这里没有需要与登记同事务写入的行。
    """
    owner = project_id or f"diff-{document.get('comparison')}"
    handle = stage_document(document, project_id=owner, artifact_type="DIFF_REPORT")
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=owner,
            artifact_type="DIFF_REPORT",
            source_artifact_ids=[item for item in source_artifact_ids if item],
            session=session,
        )
    return info.id


def latest_snapshot_artifact(execution_id: str, kinds: Sequence[str]) -> str | None:
    """某次执行最近一份指定用途的 Snapshot（两类对比都从这里取基准）。

    只开一段短事务读索引行；产物内容留给调用方在事务外读——Diff 是外部 I/O，把对象存储的
    读取压在数据库事务里正是 00 文档 6.2 要避免的形状。
    """
    with unit_of_work() as session:
        row = session.execute(
            select(SnapshotIndexRow)
            .where(
                SnapshotIndexRow.execution_id == execution_id,
                SnapshotIndexRow.kind.in_(list(kinds)),
            )
            .order_by(SnapshotIndexRow.created_at_snapshot.desc())
            .limit(1)
        ).scalars().first()
        return row.artifact_id if row is not None else None


def read_snapshot_document(artifact_id: str | None) -> dict[str, Any] | None:
    """读一份 Snapshot 文档；没有引用就是“没有基准”，不猜一份出来。"""
    if artifact_id is None:
        return None
    return read_json(artifact_id)


__all__ = [
    "PRESENCE_FIELD",
    "REFERENCE_DISPOSITIONS",
    "build_document",
    "build_rows",
    "changed_fields",
    "disposition_map",
    "entries_of",
    "entry_key",
    "latest_snapshot_artifact",
    "object_row",
    "read_snapshot_document",
    "save",
    "summary_of",
]
