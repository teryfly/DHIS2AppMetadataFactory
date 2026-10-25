"""漂移检查（DX-EXE-020/021、AC-EXE-03、AC-EXE-07）。

漂移的定义很窄也很关键：**REUSE / REFERENCE / UPDATE 对象在观测基准之后被他人修改**。
自己刚提交的不算漂移，所以比对的是 S2 从 Registry 带来的观测基准，而不是上一次执行。

比对两件事：``lastUpdated`` 与内容哈希。只看 ``lastUpdated`` 会在无关字段变化时误报
（DX-DSC-007 的动机），只看内容哈希则会漏掉“内容没变但被人改过又改回来”这种确实发生过
的状态迁移。哈希在排除易变字段之后计算，因此仅 ``lastUpdated`` 变化不报漂移（AC-EXE-07）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import external_io, structure_hash, unit_of_work

from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.config import get_settings
from factory_dhis2.dto.execution import DriftResult
from factory_dhis2.enums import DRIFT_WATCHED_DISPOSITIONS
from factory_dhis2.execution.context import RunContext, load_context
from factory_dhis2.execution.reads import read_many
from factory_dhis2.payload.package import MetadataPackage

#: 观测基准缺失时的处理方向：宁可漏报一次比较，也不凭空造一个基准
NO_BASELINE = "NO_BASELINE"


def drift_watch(package: MetadataPackage) -> list[dict[str, Any]]:
    """需要漂移检查的对象清单（DX-EXE-020 的 REUSE / REFERENCE / UPDATE）。

    清单是**对象集合**，不是两段来源的拼接。一个被引用对象既可能是 Registry 条目（于是
    出现在 ``uidToBusinessKey``），又是范围外引用（于是出现在
    ``changeSummary.referencesOutsideManagedScope``），它只该被监测一次；第二段不能去掉，
    因为隐式引用（环境默认 CategoryCombo 之类）不是 Registry 条目，只有第二段有它。留着
    重复行不改变 ``drifted`` 判定，但同一个对象的两次判定会写进执行报告、写进执行计划，
    也写进 Snapshot 的状态指纹（DX-EXE-029），报告上还会显示成"这个对象测了两遍"。
    """
    baselines = {str(row.get("businessKey")): row for row in package.observation_baselines()}
    watched: dict[tuple[str, str], dict[str, Any]] = {}
    for row in package.uid_map():
        disposition = str(row.get("disposition") or "")
        if disposition not in DRIFT_WATCHED_DISPOSITIONS:
            continue
        business_key = str(row.get("businessKey"))
        baseline = baselines.get(business_key, {})
        uid = row.get("uid")
        if not isinstance(uid, str) or not uid:
            continue
        watched[(uid, business_key)] = {
            "businessKey": business_key,
            "dhis2Type": str(row.get("dhis2Type") or ""),
            "uid": uid,
            "disposition": disposition,
            "observedLastUpdated": baseline.get("observedLastUpdated")
            or row.get("observedLastUpdated"),
            "observedHash": baseline.get("observedHash") or row.get("observedHash"),
        }
    for row in package.change_summary().get("referencesOutsideManagedScope") or []:
        if not isinstance(row, dict) or not isinstance(row.get("uid"), str):
            continue
        uid = str(row["uid"])
        business_key = str(row.get("businessKey"))
        already = watched.get((uid, business_key))
        if already is not None and already.get("observedHash"):
            # 同一个对象已经带着可比对的基准进清单了，不再测第二遍；反过来（清单里那份
            # 没有基准而引用行有）则以能真正比对的那份为准
            continue
        watched[(uid, business_key)] = {
            "businessKey": business_key,
            # 比对要按类型寻址，缺类型的行读不回来也不能假装读回来
            "dhis2Type": str(row.get("dhis2Type") or ""),
            "uid": uid,
            "disposition": str(row.get("disposition") or "REFERENCE"),
            "observedLastUpdated": row.get("observedLastUpdated"),
            "observedHash": row.get("observedHash"),
        }
    return sorted(watched.values(), key=lambda item: (item["uid"], item["businessKey"]))


def check(
    context: RunContext,
    watch: list[dict[str, Any]] | None = None,
    *,
    cancel: Any | None = None,
) -> DriftResult:
    """比对观测基准与当前状态（只读，不写任何东西）。"""
    items = watch if watch is not None else drift_watch(context.package)
    if not items:
        return DriftResult(
            drifted=False,
            environment_key=context.environment_key,
            checked_at=datetime.now(UTC).isoformat(),
        )

    by_type: dict[str, list[str]] = {}
    for item in items:
        by_type.setdefault(str(item["dhis2Type"]), []).append(str(item["uid"]))

    settings = get_settings()
    current: dict[str, dict[str, Any]] = {}
    for dhis2_type, uids in sorted(by_type.items()):
        if cancel is not None:
            cancel.raise_if_cancelled()
        for chunk_start in range(0, len(uids), settings.drift_batch_size):
            chunk = uids[chunk_start : chunk_start + settings.drift_batch_size]
            current.update(
                read_many(context.client, dhis2_type, chunk, batch_size=len(chunk))
            )

    differences: list[dict[str, Any]] = []
    for item in items:
        uid = str(item["uid"])
        observed_hash = item.get("observedHash")
        row = current.get(uid)
        if row is None:
            differences.append(
                {
                    "uid": uid,
                    "dhis2Type": item.get("dhis2Type"),
                    "businessKey": item.get("businessKey"),
                    "disposition": item.get("disposition"),
                    "kind": "MISSING",
                    "observedLastUpdated": item.get("observedLastUpdated"),
                    "detail": "对象已不存在于目标环境",
                }
            )
            continue
        if not observed_hash:
            # 没有基准就无法判定：报告出来，但不把它算成漂移（不凭空造结论）
            differences.append(
                {
                    "uid": uid,
                    "dhis2Type": item.get("dhis2Type"),
                    "businessKey": item.get("businessKey"),
                    "disposition": item.get("disposition"),
                    "kind": NO_BASELINE,
                    "detail": "Registry 没有记录观测哈希，无法比较",
                }
            )
            continue
        from factory_dhis2.discovery.canonical import object_hash

        live_hash = object_hash(row, context.target.profile.volatile_fields)
        observed_updated = item.get("observedLastUpdated")
        live_updated = row.get("lastUpdated")
        if live_hash != observed_hash:
            differences.append(
                {
                    "uid": uid,
                    "dhis2Type": item.get("dhis2Type"),
                    "businessKey": item.get("businessKey"),
                    "disposition": item.get("disposition"),
                    "kind": "CONTENT_CHANGED",
                    "observedHash": observed_hash,
                    "currentHash": live_hash,
                    "observedLastUpdated": observed_updated,
                    "currentLastUpdated": live_updated,
                }
            )
        elif observed_updated and live_updated and str(observed_updated) != str(live_updated):
            differences.append(
                {
                    "uid": uid,
                    "dhis2Type": item.get("dhis2Type"),
                    "businessKey": item.get("businessKey"),
                    "disposition": item.get("disposition"),
                    "kind": "UPDATED_ONLY",
                    "observedLastUpdated": observed_updated,
                    "currentLastUpdated": live_updated,
                }
            )
    drifted = any(item["kind"] not in {NO_BASELINE, "UPDATED_ONLY"} for item in differences)
    return DriftResult(
        drifted=drifted,
        objects=differences,
        environment_key=context.environment_key,
        checked_at=datetime.now(UTC).isoformat(),
    )


def state_hash_of_observation(differences: list[dict[str, Any]], watch: list[dict[str, Any]]) -> str:
    """把“这一刻看到的基准集合”折算成一个状态指纹（DX-EXE-029 的第三要素）。"""
    return structure_hash(
        {
            "watch": sorted(
                [
                    [str(item.get("uid")), str(item.get("observedHash") or "")]
                    for item in watch
                ]
            ),
            "drifted": sorted(str(item.get("uid")) for item in differences),
        }
    )


@external_io
def check_drift(
    environment_key: str,
    package_artifact_id: str,
    *,
    project_id: str | None = None,
    cancel: Any | None = None,
) -> DriftResult:
    """公共函数：漂移检查独立可查（DX-EXE-021，S1 在审批与提交时各调一次）。"""
    context = load_context(environment_key, package_artifact_id, project_id=project_id)
    return check(context, cancel=cancel)


def save_drift_report(
    *, project_id: str | None, environment_key: str, result: DriftResult, watch_count: int
) -> str:
    """漂移结果作为执行报告的一部分保存（不单独设产物类型，附录 A 没有这个事件）。"""
    document = {
        "schemaVersion": 1,
        "kind": "DRIFT_CHECK",
        "environmentKey": environment_key,
        "drifted": result.drifted,
        "watchedObjects": watch_count,
        "objects": result.objects,
        "checkedAt": result.checked_at,
    }
    owner = project_id or f"env-{environment_key}"
    handle = stage_document(document, project_id=owner, artifact_type="EXECUTION_REPORT")
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=owner,
            artifact_type="EXECUTION_REPORT",
            session=session,
        )
    return info.id


__all__ = [
    "NO_BASELINE",
    "check",
    "check_drift",
    "drift_watch",
    "save_drift_report",
    "state_hash_of_observation",
]
