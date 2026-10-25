"""对账（DX-EXE-035、AC-EXE-05、NFR-REL-003）。

一次提交可能“请求已发出、响应没拿到、也没有任务 ID 可问”——这时系统**不知道**环境变成
了什么样子。对账不猜：它读回载荷对象的当前状态，与提交前 Snapshot 以及载荷本身比对，
给出“已生效 / 未生效 / 部分不一致 / 无法判定”四档结论 **加证据**，交由人工确认
（:mod:`factory_dhis2.execution.manual_check`）。

要害在于三方比对的分工：

* **提交前 Snapshot** 回答“什么都没发生吗”——现状与基准一致就是未生效；
* **载荷** 回答“变成我们要的样子了吗”——受控字段与载荷一致才是已生效；
* 两者都不等于 ⇒ 部分不一致：环境被动过，但不是按这次部署动的。

字段的比较口径与部署后验证完全共用（``post_verify`` 的那一组函数），否则“对账说已生效”
与“验证说有差异”会各自按一套规则成立。

只读：本模块不写状态、不释放锁、不重新提交（NFR-REL-003“恢复时不重复执行已完成的不可逆
操作”）；锁保持到人工核实为止（DX-EXE-031）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import external_io, unit_of_work

from factory_dhis2.artifacts import read_json
from factory_dhis2.dto.execution import ReconcileResult
from factory_dhis2.enums import (
    CredentialScope,
    Disposition,
    ExecutionMode,
    ReconcileOutcome,
)
from factory_dhis2.errors import Dhis2Error
from factory_dhis2.execution import steps
from factory_dhis2.execution.context import RunContext, load_context
from factory_dhis2.execution.deletion import deletion_candidates
from factory_dhis2.execution.post_verify import (
    SHARING_KEYS,
    diff_value,
    fields_to_compare,
    omitted_as_default,
    same_scalar,
)
from factory_dhis2.execution.reads import FULL_READ_FIELDS, read_one
from factory_dhis2.payload.package import MetadataPackage
from factory_dhis2.profiles.base import VersionProfile

#: 单个对象的判定词汇（与整体结论同一套字符串，粒度不同）
VERDICT_APPLIED = "APPLIED"
VERDICT_NOT_APPLIED = "NOT_APPLIED"
VERDICT_MISMATCH = "PARTIAL_MISMATCH"
VERDICT_UNKNOWN = "UNKNOWN"


@dataclass
class ObjectFinding:
    """一个对象的三方比对结果。"""

    dhis2_type: str
    uid: str
    business_key: str | None
    disposition: str
    verdict: str
    present_in_baseline: bool | None = None
    present_now: bool | None = None
    baseline_hash: str | None = None
    detail: str | None = None
    fields: list[dict[str, Any]] = field(default_factory=list)

    def as_document(self) -> dict[str, Any]:
        return {
            "dhis2Type": self.dhis2_type,
            "uid": self.uid,
            "businessKey": self.business_key,
            "disposition": self.disposition,
            "verdict": self.verdict,
            "presentInBaseline": self.present_in_baseline,
            "presentNow": self.present_now,
            "baselineContentHash": self.baseline_hash,
            "detail": self.detail,
            "fields": self.fields,
        }


@dataclass
class Reconciliation:
    """对账的全部事实：结论、逐项证据与用到的产物。"""

    outcome: ReconcileOutcome = ReconcileOutcome.UNKNOWN
    findings: list[ObjectFinding] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.findings:
            out[item.verdict] = out.get(item.verdict, 0) + 1
        return out

    def as_evidence(self) -> dict[str, Any]:
        return {
            "verdicts": self.counts,
            "objects": [item.as_document() for item in self.findings],
            "artifacts": self.artifacts,
            "notes": self.notes,
            "comparedAt": datetime.now(UTC).isoformat(),
        }


@external_io
def reconcile(execution_id: str) -> ReconcileResult:
    """公共入口：给出判断与证据，不写任何东西（DX-EXE-035）。"""
    with unit_of_work() as session:
        execution = steps.require(session, execution_id)
        environment_key = execution.environment_key
        project_id = execution.project_id
        payload_artifact_id = execution.payload_artifact_id
        payload_hash = execution.payload_hash
        snapshot_artifact_id = execution.snapshot_artifact_id
        mode = execution.mode
        executor_type = execution.executor_type

    if payload_artifact_id is None:
        return ReconcileResult(
            outcome=ReconcileOutcome.UNKNOWN,
            execution_id=execution_id,
            evidence={
                "notes": ["该执行没有载荷产物，无从对账（DX-EXE-003）"],
                "artifacts": {"executionId": execution_id},
                "verdicts": {},
                "objects": [],
            },
        )

    reconciliation = Reconciliation(
        artifacts={
            "executionId": execution_id,
            "executorType": executor_type,
            "mode": mode,
            "payloadArtifactId": payload_artifact_id,
            "payloadHash": payload_hash,
            "snapshotArtifactId": snapshot_artifact_id,
        }
    )

    baseline: dict[str, dict[str, Any]] = {}
    if snapshot_artifact_id is None:
        # 没有提交前 Snapshot 就只能回答“现在存不存在”，答不了“有没有变化”
        reconciliation.notes.append("该执行没有提交前 Snapshot，缺少比对基准（DX-EXE-022）")
    else:
        baseline = index_baseline(read_json(snapshot_artifact_id), reconciliation.notes)

    context = load_context(
        environment_key,
        payload_artifact_id,
        project_id=project_id,
        scope=CredentialScope.DISCOVERY,
    )
    for subject in reconcile_subjects(context.package, mode):
        reconciliation.findings.append(judge(context, subject, baseline.get(str(subject["uid"]))))

    reconciliation.outcome = overall(reconciliation)
    return ReconcileResult(
        outcome=reconciliation.outcome,
        execution_id=execution_id,
        evidence=reconciliation.as_evidence(),
    )


def index_baseline(document: dict[str, Any], notes: list[str]) -> dict[str, dict[str, Any]]:
    """提交前 Snapshot 的 UID → 条目索引（Snapshot 是基准的唯一来源）。"""
    out: dict[str, dict[str, Any]] = {}
    for entry in document.get("objects") or []:
        if not isinstance(entry, dict):
            continue
        uid = entry.get("uid")
        if isinstance(uid, str):
            out[uid] = entry
    kind = document.get("snapshotKind")
    if isinstance(kind, str):
        notes.append(f"比对基准是 {kind} Snapshot")
    return out


def reconcile_subjects(package: MetadataPackage, mode: str) -> list[dict[str, Any]]:
    """对账对象：载荷里的 CREATE / UPDATE 对象；删除执行则是删除候选集。"""
    if mode == ExecutionMode.DELETE.value:
        return [
            {**row, "disposition": Disposition.DEPRECATED.value} for row in deletion_candidates(package)
        ]

    type_by_uid = package.type_by_uid()
    dispositions = package.dispositions()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in package.objects():
        subject = _subject_of(row, type_by_uid, dispositions, seen)
        if subject is not None:
            out.append(subject)
    # manifest 的 objects 段没给全时退回 uidToBusinessKey（同一处置词汇，不另造规则）
    for row in package.uid_map():
        subject = _subject_of(row, type_by_uid, dispositions, seen)
        if subject is not None:
            out.append(subject)
    return out


def _subject_of(
    row: dict[str, Any],
    type_by_uid: dict[str, str],
    dispositions: dict[str, str],
    seen: set[str],
) -> dict[str, Any] | None:
    business_key = row.get("businessKey")
    disposition = str(row.get("disposition") or dispositions.get(str(business_key)) or "")
    uid = row.get("uid")
    if disposition not in {Disposition.CREATE.value, Disposition.UPDATE.value}:
        return None
    if not isinstance(uid, str) or not uid or uid in seen:
        return None
    dhis2_type = row.get("dhis2Type") or type_by_uid.get(uid)
    if not isinstance(dhis2_type, str) or not dhis2_type:
        return None
    seen.add(uid)
    return {
        "uid": uid,
        "dhis2_type": dhis2_type,
        "business_key": business_key,
        "disposition": disposition,
    }


def judge(
    context: RunContext,
    subject: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> ObjectFinding:
    """单个对象的三方比对：读不到就是 UNKNOWN，绝不把“没看到”说成“没生效”。"""
    package = context.package
    profile = context.target.profile
    uid = str(subject["uid"])
    dhis2_type = str(subject["dhis2_type"])
    disposition = str(subject["disposition"])
    finding = ObjectFinding(
        dhis2_type=dhis2_type,
        uid=uid,
        business_key=subject.get("business_key"),
        disposition=disposition,
        verdict=VERDICT_UNKNOWN,
        present_in_baseline=bool(baseline.get("present")) if baseline else None,
        baseline_hash=(
            str(baseline["contentHash"]) if baseline and baseline.get("contentHash") else None
        ),
    )
    try:
        current = read_one(context.client, dhis2_type, uid, fields=FULL_READ_FIELDS)
    except Dhis2Error as exc:
        # 传输失败是“信息缺失”，不是“未生效”：给 UNKNOWN，让人去确认而不是等自动重发
        finding.verdict = VERDICT_UNKNOWN
        finding.detail = f"读回失败：{exc.message}"
        return finding
    finding.present_now = current is not None

    if disposition == Disposition.DEPRECATED.value:
        return _judge_deletion(finding, current, baseline, profile, dhis2_type)

    if current is None:
        if baseline is None or not baseline.get("present"):
            finding.verdict = VERDICT_NOT_APPLIED
            finding.detail = "对象不存在，与提交前一致"
        else:
            finding.verdict = VERDICT_MISMATCH
            finding.detail = "提交前存在、现在读不到"
        return finding

    payload_object = package.payload_by_uid().get(uid) or {}
    wanted = fields_to_compare(
        disposition,
        dhis2_type,
        payload_object,
        package.controlled_fields(),
        finding.business_key,
        profile,
    )
    if payload_object and wanted:
        mismatches = payload_mismatches(
            payload_object, current, wanted, profile, str(dhis2_type)
        )
        finding.fields = mismatches
        if not mismatches:
            finding.verdict = VERDICT_APPLIED
            finding.detail = "受控字段与载荷一致"
            return finding
        if baseline is not None and same_content(baseline.get("content"), current, profile, dhis2_type):
            finding.verdict = VERDICT_NOT_APPLIED
            finding.detail = "对象存在且与提交前一致：本次部署未生效"
            return finding
        finding.verdict = VERDICT_MISMATCH
        finding.detail = f"{len(mismatches)} 个字段与载荷不一致，且环境已非提交前的样子"
        return finding

    # 载荷没带可比内容（UPDATE 对象在环境绑定包里才合并；对账包也可能只列 UID）：
    # 这时唯一站得住的判断是“存在与否 + 与基准是否相同”
    if baseline is None or not baseline.get("present"):
        finding.verdict = VERDICT_APPLIED
        finding.detail = "对象已存在，提交前不存在"
    elif same_content(baseline.get("content"), current, profile, dhis2_type):
        finding.verdict = VERDICT_NOT_APPLIED
        finding.detail = "对象存在且与提交前一致"
    else:
        finding.verdict = VERDICT_MISMATCH
        finding.detail = "对象存在但内容已非提交前的样子，且载荷未提供可比字段"
    return finding


def _judge_deletion(
    finding: ObjectFinding,
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    profile: VersionProfile,
    dhis2_type: str,
) -> ObjectFinding:
    """删除执行的期望终态是“对象不在”。"""
    if current is None:
        finding.verdict = VERDICT_APPLIED
        finding.detail = "对象已不在环境中"
        return finding
    if baseline is not None and same_content(baseline.get("content"), current, profile, dhis2_type):
        finding.verdict = VERDICT_NOT_APPLIED
        finding.detail = "对象仍在，且与提交前一致"
        return finding
    finding.verdict = VERDICT_MISMATCH
    finding.detail = "对象仍在，但内容已非提交前的样子"
    return finding


def payload_mismatches(
    expected: dict[str, Any],
    actual: dict[str, Any],
    wanted: set[str],
    profile: VersionProfile,
    dhis2_type: str = "",
) -> list[dict[str, Any]]:
    """载荷 vs 现状：只比受控字段，共享与权限交回部署后验证去核对。

    实例省略了某个键不等于该键没生效：2.41 对**取值等于默认值**的字段就是不回读，这条判定与
    部署后验证共用同一个函数，免得一次真实部署得到两个互相矛盾的结论。
    """
    out: list[dict[str, Any]] = []
    volatile = frozenset(profile.volatile_fields)
    for key in sorted(wanted):
        if key not in expected or key in SHARING_KEYS:
            continue
        if key not in actual and omitted_as_default(dhis2_type, key, expected[key], profile):
            continue
        out.extend(diff_value(key, expected[key], actual.get(key), volatile=volatile))
    return out


def same_content(
    baseline_content: Any, current: dict[str, Any], profile: VersionProfile, dhis2_type: str
) -> bool:
    """现状是否仍等于提交前。

    Snapshot 的 ``content`` 可能是薄读取（只带 ``id,code,name,created,lastUpdated``），
    因此比较只在**基准里出现的键**上做，并排除易变与版本禁止比较的字段——键集不同不代表
    内容不同（DX-DSC-007 的同一套排除表）。
    """
    if not isinstance(baseline_content, dict):
        return False
    excluded = set(profile.volatile_fields) | set(profile.forbidden_fields(dhis2_type)) | {"id"}
    for key, value in sorted(baseline_content.items()):
        if key in excluded:
            continue
        if key not in current:
            return False
        if isinstance(value, (dict, list)):
            if comparable_structure(value, excluded) != comparable_structure(current.get(key), excluded):
                return False
        elif not same_scalar(value, current.get(key)):
            return False
    return True


def comparable_structure(value: Any, excluded: set[str]) -> Any:
    """把嵌套结构折算成只含可比较键的规范形态。"""
    if isinstance(value, dict):
        return {
            key: comparable_structure(item, excluded)
            for key, item in sorted(value.items())
            if key not in excluded
        }
    if isinstance(value, list):
        return [comparable_structure(item, excluded) for item in value]
    return value


def overall(reconciliation: Reconciliation) -> ReconcileOutcome:
    """整体结论：全部一致才给确定答案，掺进读不到就整体 UNKNOWN（不猜）。"""
    if not reconciliation.findings:
        return ReconcileOutcome.UNKNOWN
    verdicts = {item.verdict for item in reconciliation.findings}
    if verdicts == {VERDICT_APPLIED}:
        return ReconcileOutcome.APPLIED
    if verdicts == {VERDICT_NOT_APPLIED}:
        return ReconcileOutcome.NOT_APPLIED
    if VERDICT_UNKNOWN in verdicts:
        if verdicts <= {VERDICT_UNKNOWN}:
            return ReconcileOutcome.UNKNOWN
        # 一部分判得出来、一部分读不回来：整体仍是“无法判定”更诚实
        return ReconcileOutcome.UNKNOWN
    return ReconcileOutcome.PARTIAL_MISMATCH


__all__ = [
    "ObjectFinding",
    "Reconciliation",
    "VERDICT_APPLIED",
    "VERDICT_MISMATCH",
    "VERDICT_NOT_APPLIED",
    "VERDICT_UNKNOWN",
    "comparable_structure",
    "index_baseline",
    "judge",
    "overall",
    "payload_mismatches",
    "reconcile",
    "reconcile_subjects",
    "same_content",
]
