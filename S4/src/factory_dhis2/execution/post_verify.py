"""部署后验证（DX-EXE-040…042、AC-EXE-06、DX-AC-10、总体 SRS 15.2 POST_VERIFY）。

COMMIT 的 ImportReport 只说明“实例接受了这次提交”，不说明“环境真的变成载荷所说的样子”
——引用没落地、共享设置被忽略、字段被版本差异吃掉，都是报告里看不出来的。部署后验证就
是把这件事问清楚：**从环境读回每个 CREATE / UPDATE 对象，逐字段比对**。

三条不可让步的边界：

* 全部读走 **discovery 凭证范围**（DX-BR-04）：验证是观察环境，不是修改环境；
* 失败**只报告不修正**（DX-BR-09、DX-AC-10）：这里没有任何一次写调用，延后校验的表达式
  失败也只进报告；S1 据此把 Run 打回设计阶段，而不是让 S4 就地改实例；
* 比对的是**受控字段**：UPDATE 对象比 manifest 的 ``controlledFields``，CREATE 对象比载荷
  里出现的全部字段（DX-EXE-040）。``lastUpdated`` / ``href`` / ``access`` 这类易变或派生
  字段不参与内容比对（DX-DSC-007 的同一套排除表），但共享设置与关键权限**单独查**——它们
  在哈希里被排除，不等于在部署结论里可以被忽略。

结果一律持久化：POST_VERIFICATION_REPORT 产物 + 一条 POST_COMMIT Snapshot（下一次
Diff 的基准，DX-DIFF-001）+ 审计 ``dx.post_verify.completed``（附录 A）。

事务形态与 COMMIT 相同（00 文档 6.2）：读环境 → 开短事务写状态与审计，网络期间不持有事务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import (
    StagingHandle,
    external_io,
    idempotency,
    structure_hash,
    unit_of_work,
)

from factory_dhis2 import audit
from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.dto.execution import PostVerifyOutcome
from factory_dhis2.dto.issue import Issue, make_issue
from factory_dhis2.enums import (
    CredentialScope,
    Disposition,
    ExecutionMode,
    ExecutionStatus,
    IssueLevel,
    SourceStage,
    StepName,
    StepStatus,
)
from factory_dhis2.execution import snapshot as snapshot_module
from factory_dhis2.execution import steps
from factory_dhis2.execution.context import RunContext, load_context
from factory_dhis2.execution.expressions import ExpressionCheckReport, check_expressions, deferred
from factory_dhis2.execution.reads import FULL_READ_FIELDS, read_one
from factory_dhis2.profiles.base import VersionProfile
from factory_dhis2.registries import IdemScope

#: 只有这两种处置的对象才需要读回比对：REUSE / REFERENCE 不是本次部署改变的（DX-EXE-040）
VERIFIED_DISPOSITIONS: frozenset[str] = frozenset(
    {Disposition.CREATE.value, Disposition.UPDATE.value}
)

#: 共享与权限字段：内容比对里排除（它们进不了内容哈希），但由 check_sharing 单独核对
SHARING_KEYS: tuple[str, ...] = ("sharing", "publicAccess", "externalAccess", "access")

#: 载荷子对象里用于对齐的标识键（DHIS2 的嵌套对象一律带 id，其次是 code / name）
_CHILD_ID_KEYS: tuple[str, ...] = ("id", "code", "name")

#: 差异类型（进报告与问题描述，S1 按它决定回退方向的说明文字）
KIND_MISSING_OBJECT = "MISSING_OBJECT"
KIND_FIELD_MISMATCH = "FIELD_MISMATCH"
KIND_MISSING_FIELD = "MISSING_FIELD"
KIND_REFERENCE_MISSING = "REFERENCE_MISSING"
KIND_SHARING_MISMATCH = "SHARING_MISMATCH"
#: 载荷写了、实例回读时却没有这个键。2.41.10 实测：实例会省略**取值等于登记默认值**的字段，
#  那种情况在 ``omitted_as_default`` 里就已经判为一致，根本走不到这里。走到这里的，是期望值
#  不在 Profile 默认值清单里的字段——它没有生效，只是实例连"它不在"都没有说。
#  把它当说明性差异放行，等于用代码假设实例行为（P-10 禁止）：FR-EXE-010 要的正是逐字段核对
#  受控字段，漏一个字段却报告"全部一致"会让一次部分生效的部署被当成完整部署晋级到生产。
#  若某字段确实总有默认值而被省略，修正的是该版本 Profile 的默认值清单，不是这里的判定。
KIND_FIELD_NOT_ECHOED = "FIELD_NOT_ECHOED"


@dataclass
class ObjectVerification:
    """一个对象的读回比对结果。"""

    dhis2_type: str
    uid: str
    business_key: str | None
    disposition: str
    compared_fields: int = 0
    present: bool = False
    findings: list[dict[str, Any]] = field(default_factory=list)
    content: dict[str, Any] | None = None

    @property
    def matched(self) -> bool:
        return not self.findings

    def as_document(self) -> dict[str, Any]:
        return {
            "dhis2Type": self.dhis2_type,
            "uid": self.uid,
            "businessKey": self.business_key,
            "disposition": self.disposition,
            "present": self.present,
            "comparedFields": self.compared_fields,
            "matched": self.matched,
            "findings": self.findings,
        }


@dataclass
class VerificationResult:
    """一次部署后验证的全部事实（无推断，逐项可追溯）。"""

    objects: list[ObjectVerification] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    expressions: ExpressionCheckReport | None = None

    @property
    def compared_objects(self) -> int:
        return len(self.objects)

    @property
    def diff_list(self) -> list[dict[str, Any]]:
        """DX-EXE-042 的“差异清单”：按类型 + UID 稳定排序。"""
        rows: list[dict[str, Any]] = []
        for item in self.objects:
            for finding in item.findings:
                rows.append(
                    {
                        "dhis2Type": item.dhis2_type,
                        "uid": item.uid,
                        "businessKey": item.business_key,
                        "disposition": item.disposition,
                        **finding,
                    }
                )
        return sorted(rows, key=lambda row: (str(row.get("dhis2Type")), str(row.get("uid")), str(row.get("field"))))

    @property
    def consistent(self) -> bool:
        expressions_ok = self.expressions is None or self.expressions.ok
        return not self.issues and expressions_ok

    def as_document(self, *, environment_key: str, phase: str) -> dict[str, Any]:
        return {
            "environmentKey": environment_key,
            "consistent": self.consistent,
            "comparedObjects": self.compared_objects,
            "objects": [item.as_document() for item in self.objects],
            "diffList": self.diff_list,
            "expressions": (
                self.expressions.as_document(phase=phase) if self.expressions else {"phase": phase, "checked": 0}
            ),
        }


def verify_payload(
    context: RunContext,
    *,
    progress: Any | None = None,
    cancel: Any | None = None,
) -> VerificationResult:
    """读回并比对（纯读：DX-AC-10 的“不修改实例”就在这里成立）。"""
    package = context.package
    profile = context.target.profile
    payload_by_uid = package.payload_by_uid()
    controlled = package.controlled_fields()
    listed, unaddressable = objects_to_verify(package)
    result = VerificationResult(issues=list(unaddressable))

    for row in listed:
        if cancel is not None:
            cancel.raise_if_cancelled()
        uid = str(row["uid"])
        dhis2_type = str(row["dhis2_type"])
        business_key = row["business_key"]
        payload_object = payload_by_uid.get(uid) or {}
        read_back = read_one(context.client, dhis2_type, uid, fields=FULL_READ_FIELDS)
        item = ObjectVerification(
            dhis2_type=dhis2_type,
            uid=uid,
            business_key=business_key,
            disposition=row["disposition"],
        )
        result.objects.append(item)
        if read_back is None:
            # 对象不在环境里：要么没建成，要么被人删了。两种都不是“一致”
            item.findings.append(
                {
                    "kind": KIND_MISSING_OBJECT,
                    "field": None,
                    "expected": "对象存在",
                    "actual": None,
                    "detail": "按 UID 读回为空",
                }
            )
            continue
        item.present = True
        item.content = read_back
        if payload_object:
            wanted = fields_to_compare(
                row["disposition"], dhis2_type, payload_object, controlled, business_key, profile
            )
            findings, compared = compare_object(
                payload_object, read_back, wanted, profile, str(dhis2_type)
            )
            item.compared_fields = compared
            item.findings.extend(findings)
        if progress is not None:
            progress.report(step=StepName.POST_VERIFY.value, message=f"verified {dhis2_type}/{uid}")

    items = deferred(package)
    if items:
        if progress is not None:
            progress.report(
                step=StepName.POST_VERIFY.value, message=f"{len(items)} 条延后表达式待实例校验"
            )
        # DX-EXE-041：延后到此刻的表达式在这里逐条校验，失败只报告（DX-BR-09）
        result.expressions = check_expressions(
            context.client,
            items,
            package=package,
            profile=profile,
            phase="POST_VERIFY",
            cancel=cancel,
        )

    for item in result.objects:
        for finding in item.findings:
            result.issues.append(issue_for(item, finding, artifact_id=None))
    if result.expressions is not None:
        result.issues.extend(result.expressions.failed)
    return result


def objects_to_verify(package: Any) -> tuple[list[dict[str, Any]], list[Issue]]:
    """待验证对象清单 + 无法寻址的 CREATE/UPDATE（DX-EXE-040 要求验证**全部**）。

    第二项不是日志噪音：manifest 里缺 uid / dhis2Type 的受控对象是"这次部署声称创建了它，
    但部署后没有任何东西被读回来核对"。静默跳过会让一份不完整的验证结果看起来是"全部一致"。
    """
    dispositions = package.dispositions()
    out: list[dict[str, Any]] = []
    unaddressable: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    for row in package.objects():
        disposition = str(row.get("disposition") or dispositions.get(str(row.get("businessKey"))) or "")
        if disposition not in VERIFIED_DISPOSITIONS:
            continue
        uid = row.get("uid")
        dhis2_type = row.get("dhis2Type")
        if not isinstance(uid, str) or not isinstance(dhis2_type, str):
            unaddressable.append(
                make_issue(
                    "DX-EXE-040",
                    IssueLevel.BLOCKING,
                    f"处置为 {disposition} 的对象无法按 UID 寻址（manifest objects 缺 "
                    f"{'uid' if not isinstance(uid, str) else 'dhis2Type'}），部署后无从读回核对",
                    object_key=str(row.get("businessKey") or ""),
                    object_uid=uid if isinstance(uid, str) else None,
                    dhis2_type=dhis2_type if isinstance(dhis2_type, str) else None,
                    suggested_action="由 S2 在 manifest objects 里给出 uid 与 dhis2Type",
                )
            )
            continue
        key = (dhis2_type, uid)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "dhis2_type": dhis2_type,
                "uid": uid,
                "business_key": row.get("businessKey"),
                "disposition": disposition,
            }
        )
    # 兜底：manifest objects 没给出时按载荷 + uidToBusinessKey 组装（同一处置词汇）
    if out:
        return sorted(out, key=lambda item: (item["dhis2_type"], item["uid"])), unaddressable
    type_by_uid = package.type_by_uid()
    by_uid = package.payload_by_uid()
    for row in package.uid_map():
        disposition = str(row.get("disposition") or "")
        uid = row.get("uid")
        if disposition not in VERIFIED_DISPOSITIONS or not isinstance(uid, str):
            continue
        dhis2_type = type_by_uid.get(uid) or str(row.get("dhis2Type") or "")
        if not dhis2_type or uid not in by_uid:
            continue
        out.append(
            {
                "dhis2_type": dhis2_type,
                "uid": uid,
                "business_key": row.get("businessKey"),
                "disposition": disposition,
            }
        )
    return sorted(out, key=lambda item: (item["dhis2_type"], item["uid"])), unaddressable


def fields_to_compare(
    disposition: str,
    dhis2_type: str,
    payload_object: dict[str, Any],
    controlled: dict[str, list[str]],
    business_key: str | None,
    profile: VersionProfile,
) -> set[str]:
    """DX-EXE-040 的两条规则：UPDATE 比受控字段，CREATE 比载荷里出现的全部字段。

    Profile 的 ``forbidden`` 与易变字段一律排除——前者的定义就是“读回比对时不比较的字段”，
    后者参与比对只会造成假差异（DX-DSC-007 的同一张排除表）。共享与权限不在这里比，
    它们由 :func:`check_sharing` 用实例的共享视图单独核对。
    """
    excluded = set(profile.forbidden_fields(dhis2_type)) | set(profile.volatile_fields) | {"id"}
    if disposition == Disposition.UPDATE.value:
        # 受控字段清单为空就是“本次部署不声称改变它的任何字段”——不编造比较范围
        return {str(name) for name in controlled.get(str(business_key), []) if str(name) not in excluded}
    return {key for key in payload_object if key not in excluded}


def omitted_as_default(
    dhis2_type: str, key: str, expected: Any, profile: VersionProfile
) -> bool:
    """实例回读没有这个键，而 Profile 登记的默认值正好等于期望值。

    2.41.10 实测：取值等于默认值的字段**不出现在 JSON 里**。部署后验证与对账共用这一条判定，
    否则同一次真实部署会得到两个互相矛盾的结论。
    """
    declared = profile.property_defaults.get(dhis2_type) or {}
    return key in declared and same_scalar(expected, declared[key])


def compare_object(
    expected: dict[str, Any],
    actual: dict[str, Any],
    wanted: set[str],
    profile: VersionProfile,
    dhis2_type: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """逐字段比对 + 引用与共享核对，返回（差异, 比对字段数）。"""
    volatile = frozenset(profile.volatile_fields)
    content_keys = {key for key in wanted if key not in SHARING_KEYS}
    findings: list[dict[str, Any]] = []
    for key in sorted(content_keys):
        if key not in expected:
            continue
        if key not in actual and key not in volatile:
            if omitted_as_default(dhis2_type, key, expected[key], profile):
                # 期望的就是该字段的默认值：实例省略它是正常行为，判定为一致
                continue
            findings.append(
                {
                    "kind": KIND_FIELD_NOT_ECHOED,
                    "field": key,
                    "expected": expected[key],
                    "actual": None,
                    "detail": "实例回读未带该字段（2.41 省略取默认值的字段）",
                }
            )
            continue
        differences = diff_value(key, expected[key], actual.get(key), volatile=volatile)
        for difference in differences:
            difference.setdefault("kind", KIND_FIELD_MISMATCH)
            findings.append(difference)
    findings.extend(
        check_references({key: value for key, value in expected.items() if key in content_keys}, actual, volatile=volatile)
    )
    findings.extend(check_sharing({key: value for key, value in expected.items() if key in wanted}, actual))
    return findings, len([key for key in content_keys if key in expected])


def diff_value(
    path: str,
    expected: Any,
    actual: Any,
    *,
    volatile: frozenset[str],
) -> list[dict[str, Any]]:
    """只比**载荷里出现的东西**：实例回读得更丰富不是差异（DX-EXE-040 的“受控字段”）。"""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [difference(path, expected, actual)]
        out: list[dict[str, Any]] = []
        for key in sorted(expected):
            if key in volatile:
                continue
            child = f"{path}.{key}"
            if key not in actual:
                if is_empty(expected[key]):
                    continue  # 空值在 DHIS2 的回读里本来就不出现
                out.append({**difference(child, expected[key], None), "kind": KIND_MISSING_FIELD})
                continue
            out.extend(diff_value(child, expected[key], actual[key], volatile=volatile))
        return out
    if isinstance(expected, list):
        return diff_list_entries(path, expected, actual, volatile=volatile)
    if same_scalar(expected, actual):
        return []
    return [difference(path, expected, actual)]


def diff_list_entries(
    path: str,
    expected: list[Any],
    actual: Any,
    *,
    volatile: frozenset[str],
) -> list[dict[str, Any]]:
    """列表按子对象标识对齐；纯标量列表按集合比（顺序不是受控事实）。"""
    if not isinstance(actual, list):
        return [difference(path, expected, actual)]
    if all(not isinstance(item, dict) for item in expected):
        if sorted(str(item) for item in expected) != sorted(str(item) for item in actual):
            return [difference(path, expected, actual)]
        return []
    index: dict[str, dict[str, Any]] = {}
    for position, entry in enumerate(actual):
        if isinstance(entry, dict):
            identifier = child_identifier(entry) or f"#{position}"
            index[identifier] = entry
    out: list[dict[str, Any]] = []
    for position, entry in enumerate(expected):
        if not isinstance(entry, dict):
            out.append(difference(f"{path}[{position}]", entry, None))
            continue
        identifier = child_identifier(entry) or f"#{position}"
        match = index.get(identifier)
        if match is None:
            out.append(
                {
                    **difference(f"{path}[{identifier}]", entry, None),
                    "kind": KIND_REFERENCE_MISSING if set(entry) == {"id"} else KIND_FIELD_MISMATCH,
                }
            )
            continue
        for key in sorted(entry):
            if key in volatile:
                continue
            if key not in match and is_empty(entry[key]):
                continue
            out.extend(diff_value(f"{path}[{identifier}].{key}", entry[key], match.get(key), volatile=volatile))
    return out


def check_references(
    expected: dict[str, Any], actual: dict[str, Any], *, volatile: frozenset[str]
) -> list[dict[str, Any]]:
    """引用是否落地（DX-EXE-040 的“检查引用”）。

    载荷里的引用写作 ``{"id": "…"}``；实例只有在引用可解析时才会把该子对象回读出来，
    所以“这一层没有那个 id”就是引用没生效，而不是显示差异。
    """
    out: list[dict[str, Any]] = []
    for key, value in expected.items():
        if key in volatile or key not in actual:
            continue
        if isinstance(value, dict) and set(value) == {"id"}:
            target = actual.get(key)
            if not isinstance(target, dict) or target.get("id") != value["id"]:
                out.append(
                    {
                        "kind": KIND_REFERENCE_MISSING,
                        "field": key,
                        "expected": value["id"],
                        "actual": target.get("id") if isinstance(target, dict) else None,
                        "detail": "引用未指向载荷所指的",
                    }
                )
        elif isinstance(value, list) and all(
            isinstance(item, dict) and set(item) == {"id"} for item in value
        ) and value:
            got = {
                str(item.get("id"))
                for item in actual.get(key, [])
                if isinstance(item, dict)
            }
            missing = [str(item["id"]) for item in value if str(item["id"]) not in got]
            if missing:
                out.append(
                    {
                        "kind": KIND_REFERENCE_MISSING,
                        "field": key,
                        "expected": sorted(str(item["id"]) for item in value),
                        "actual": sorted(got),
                        "detail": f"引用集合缺少 {missing}",
                    }
                )
    return out


def check_sharing(expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    """共享设置与关键权限是否生效（DX-EXE-040）。

    ``publicAccess`` / ``access`` 在内容哈希的排除表里（DX-DSC-007），因此漂移检查与哈希
    都看不见它们；如果不在这里单独核对，一次“共享被实例吃掉”的部署就永远无人发现。
    """
    wanted = sharing_view(expected)
    got = sharing_view(actual)
    if not wanted:
        return []
    out: list[dict[str, Any]] = []
    for key, value in sorted(wanted.items()):
        if key in {"objects", "users", "userGroups", "owner"}:
            expected_ids = member_ids(value)
            actual_ids = member_ids(got.get(key))
            if expected_ids - actual_ids:
                out.append(
                    {
                        "kind": KIND_SHARING_MISMATCH,
                        "field": f"sharing.{key}",
                        "expected": sorted(expected_ids),
                        "actual": sorted(actual_ids),
                        "detail": "共享对象未生效",
                    }
                )
            continue
        if key not in got:
            if is_empty(value):
                continue
            out.append(
                {
                    "kind": KIND_SHARING_MISMATCH,
                    "field": f"sharing.{key}",
                    "expected": value,
                    "actual": None,
                    "detail": "实例未回读该共享/权限设置",
                }
            )
            continue
        if isinstance(value, (str, bool, int, float)) and not same_scalar(value, got[key]):
            out.append(
                {
                    "kind": KIND_SHARING_MISMATCH,
                    "field": f"sharing.{key}",
                    "expected": value,
                    "actual": got[key],
                    "detail": "共享或权限与载荷不一致",
                }
            )
    return out


def sharing_view(document: dict[str, Any]) -> dict[str, Any]:
    """把载荷的 sharing / 顶层 publicAccess 与实例回读的 sharing 视图归一到同一种形状。"""
    out: dict[str, Any] = {}
    sharing = document.get("sharing")
    if isinstance(sharing, dict):
        out.update({str(key): value for key, value in sharing.items()})
    for key in ("publicAccess", "externalAccess"):
        if key in document and key not in out:
            out[key] = document[key]
    access = document.get("access")
    if isinstance(access, dict):
        # 载荷写 access:{read,write}，实例也可能把权限写成字符串
        for key, value in access.items():
            out.setdefault(f"access.{key}", value)
    elif isinstance(access, str):
        out.setdefault("access", access)
    return out


def member_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value}
    if isinstance(value, list):
        out: set[str] = set()
        for item in value:
            if isinstance(item, dict):
                identifier = child_identifier(item)
                if identifier:
                    out.add(identifier)
            elif item is not None:
                out.add(str(item))
        return out
    return set()


def child_identifier(entry: dict[str, Any]) -> str | None:
    for key in _CHILD_ID_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def difference(path: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"kind": KIND_FIELD_MISMATCH, "field": path, "expected": expected, "actual": actual}


def same_scalar(expected: Any, actual: Any) -> bool:
    """标量比较要容得下 JSON 形态差异：``1`` 与 ``"1"``、``true`` 与 ``"true"`` 是同一事实。"""
    if expected is None or actual is None:
        return is_empty(expected) and is_empty(actual)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        try:
            return float(expected) == float(actual)
        except (TypeError, ValueError):
            return str(expected) == str(actual)
    return str(expected) == str(actual)


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def issue_for(item: ObjectVerification, finding: dict[str, Any], *, artifact_id: str | None) -> Issue:
    kind = str(finding.get("kind") or KIND_FIELD_MISMATCH)
    description = (
        f"{item.dhis2_type} {item.uid} 字段 {finding.get('field')} 与载荷不一致："
        f"期望 {finding.get('expected')!r}，环境为 {finding.get('actual')!r}"
    )
    if kind == KIND_MISSING_OBJECT:
        description = f"{item.dhis2_type} {item.uid} 部署后在环境中读不到（DX-EXE-040）"
    return make_issue(
        "DX-EXE-040",
        IssueLevel.BLOCKING,
        description,
        object_key=item.business_key,
        object_uid=item.uid,
        stage=SourceStage.MAPPING,
        artifact_id=artifact_id,
        suggested_action="修正载荷或环境状态后重新部署；S4 不修改实例（DX-BR-09、DX-AC-10）",
    )


@external_io
def post_verify(
    environment_key: str,
    project_id: str,
    package_artifact_id: str,
    execution_id: str | None,
    *,
    operator: str,
    idempotency_key: str,
    progress: Any | None = None,
    cancel: Any | None = None,
) -> PostVerifyOutcome:
    """部署后验证的公共入口（SRS 6.5 第 1 步、AC-EXE-06）。"""
    digest = structure_hash(
        {
            "operation": "post_verify",
            "environmentKey": environment_key,
            "projectId": project_id,
            "packageArtifactId": package_artifact_id,
            "verifiedExecutionId": execution_id,
            "operator": operator,
        }
    )
    begun = idempotency.begin(IdemScope.EXECUTION, idempotency_key, digest)
    if begun.already_completed and begun.first_result:
        # 同键重放：验证是可重复的读，但结论必须与第一次一致（DX-EXE-004）
        return PostVerifyOutcome.model_validate(begun.first_result)

    context = load_context(
        environment_key,
        package_artifact_id,
        project_id=project_id,
        scope=CredentialScope.DISCOVERY,
    )

    with unit_of_work() as session:
        verification = steps.create_execution(
            session,
            environment_key=environment_key,
            project_id=project_id,
            mode=ExecutionMode.POST_VERIFY,
            payload_artifact_id=package_artifact_id,
            payload_hash=context.package.artifact_hash,
            idempotency_key=idempotency_key,
            operator=operator,
        )
        steps.mark_running(session, verification)
        verification_id = verification.id
        if execution_id is not None:
            # 被验证的那次执行只记录“POST_VERIFY 这一步发生过”，其终态不因验证而改写：
            # 提交是否生效是它自己的事实，验证结论是本次执行的事实（DX-EXE-037/046）
            steps.require(session, execution_id)
            steps.begin_step(session, execution_id, StepName.POST_VERIFY)

    result = verify_payload(context, progress=progress, cancel=cancel)
    status = ExecutionStatus.COMPLETED if result.consistent else ExecutionStatus.FAILED

    # POST_COMMIT Snapshot 先落：它是下一次 Diff 的“提交后”那一侧（DX-DIFF-001）
    snapshot_document = post_commit_document(context, cancel=cancel)
    snapshot_artifact_id, _snapshot_row_id = save_snapshot(
        context, snapshot_document, execution_id=verification_id
    )
    document = build_document(
        context=context,
        execution_id=verification_id,
        verified_execution_id=execution_id,
        result=result,
        status=status,
        snapshot_artifact_id=snapshot_artifact_id,
        operator=operator,
    )
    # 暂存在事务外、登记与执行状态和审计在同一短事务里（00 文档 6.2、D-11）
    handle = persist(context, document)
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=_owner_of(context, project_id),
            artifact_type="POST_VERIFICATION_REPORT",
            source_artifact_ids=[context.package.artifact_id],
            session=session,
        )
        report_artifact_id = info.id
        for issue in result.issues:
            issue.artifact_id = report_artifact_id
        row = steps.require(session, verification_id)
        steps.finish_step(
            session,
            verification_id,
            StepName.POST_VERIFY,
            status=StepStatus.COMPLETED if result.consistent else StepStatus.FAILED,
            detail={
                "reportArtifactId": report_artifact_id,
                "snapshotArtifactId": snapshot_artifact_id,
                "comparedObjects": result.compared_objects,
                "differences": len(result.diff_list),
                "verifiedExecutionId": execution_id,
            },
        )
        if execution_id is not None:
            steps.finish_step(
                session,
                execution_id,
                StepName.POST_VERIFY,
                status=StepStatus.COMPLETED if result.consistent else StepStatus.FAILED,
                detail={"reportArtifactId": report_artifact_id, "consistent": result.consistent},
            )
        # applied 与 consistent 同值：部署后验证正是“已生效”的判定证据（AC-EXE-06）
        steps.set_status(
            session,
            row,
            status,
            applied=result.consistent,
            detail=None if result.consistent else "部署后验证发现差异，S4 不自动修正（DX-EXE-042）",
            finished=True,
        )
        audit.record(
            audit.POST_VERIFY_COMPLETED,
            object_type="Execution",
            object_id=verification_id,
            project_id=project_id,
            after={
                "environmentKey": environment_key,
                "verifiedExecutionId": execution_id,
                "payloadHash": context.package.artifact_hash,
                "consistent": result.consistent,
                "status": status.value,
                "comparedObjects": result.compared_objects,
                "differences": len(result.diff_list),
                "expressionsChecked": result.expressions.checked if result.expressions else 0,
                "reportArtifactId": report_artifact_id,
                "snapshotArtifactId": snapshot_artifact_id,
            },
            operator=operator,
            session=session,
        )

    outcome = PostVerifyOutcome(
        report_artifact_id=report_artifact_id,
        consistent=result.consistent,
        issues=result.issues,
        execution_id=verification_id,
        compared_objects=result.compared_objects,
    )
    idempotency.complete(
        IdemScope.EXECUTION, idempotency_key, outcome.model_dump(mode="json")
    )
    return outcome


def build_document(
    *,
    context: RunContext,
    execution_id: str,
    verified_execution_id: str | None,
    result: VerificationResult,
    status: ExecutionStatus,
    snapshot_artifact_id: str | None,
    operator: str | None,
) -> dict[str, Any]:
    """POST_VERIFICATION_REPORT 的文档（DX-EXE-042：结果 + 差异清单，全部是已读回的事实）。"""
    return {
        "schemaVersion": 1,
        "kind": "POST_VERIFICATION_REPORT",
        "executionId": execution_id,
        "verifiedExecutionId": verified_execution_id,
        "environmentKey": context.environment_key,
        "environmentClass": context.target.environment_class.value,
        "projectKey": context.project_id,
        "dhis2Version": context.target.dhis2_version,
        "payload": {
            "artifactId": context.package.artifact_id,
            "hash": context.package.artifact_hash,
            "blueprintVersion": context.package.blueprint_version,
            "objectCount": context.package.object_count,
        },
        "status": status.value,
        **result.as_document(
            environment_key=context.environment_key, phase="POST_VERIFY"
        ),
        "artifacts": {"postCommitSnapshot": snapshot_artifact_id},
        "issues": [issue.model_dump(mode="json") for issue in result.issues],
        "operator": operator,
        "autoRepairPerformed": False,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def persist(context: RunContext, document: dict[str, Any]) -> StagingHandle:
    """把报告暂存到对象存储（**事务外**）；登记由调用方在同一事务里完成。"""
    return stage_document(
        document,
        project_id=_owner_of(context, str(document.get("projectKey") or "")),
        artifact_type="POST_VERIFICATION_REPORT",
    )


def _owner_of(context: RunContext, project_id: str | None) -> str:
    """S0 要求产物归属一个项目；无项目的执行用环境键派生一个稳定归属。"""
    return project_id or context.project_id or f"env-{context.environment_key}"


def save_snapshot(
    context: RunContext,
    document: dict[str, Any],
    *,
    execution_id: str | None,
) -> tuple[str, str]:
    """持久化 POST_COMMIT Snapshot（下一次 Diff 的“提交后状态”，DX-DIFF-001）。"""
    return snapshot_module.save(
        context, document, execution_id=execution_id, kind=snapshot_module.KIND_POST_COMMIT
    )


def post_commit_document(context: RunContext, *, cancel: Any | None = None) -> dict[str, Any]:
    """POST_COMMIT 文档由 :mod:`execution.snapshot` 生成：哈希定义只有一处（DX-DSC-007）。"""
    return snapshot_module.build(context, kind=snapshot_module.KIND_POST_COMMIT, cancel=cancel)


__all__ = [
    "omitted_as_default",
    "KIND_FIELD_MISMATCH",
    "KIND_MISSING_FIELD",
    "KIND_MISSING_OBJECT",
    "KIND_REFERENCE_MISSING",
    "KIND_SHARING_MISMATCH",
    "ObjectVerification",
    "SHARING_KEYS",
    "VERIFIED_DISPOSITIONS",
    "VerificationResult",
    "build_document",
    # 读回比对的核心：对账与 Diff 复用同一套字段口径，比较规则不允许有两处版本
    "check_references",
    "check_sharing",
    "compare_object",
    "diff_value",
    "fields_to_compare",
    "persist",
    "post_commit_document",
    "post_verify",
    "same_scalar",
    "save_snapshot",
    "verify_payload",
]
