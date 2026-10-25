"""服务端表达式校验（DX-EXE-028/041、D-05）。

本地语法与引用校验由 S2 静态层负责；这里只做一件事：**让实例自己回答它答得了的那部分**。
manifest 把每条表达式分类为 ``DHIS2_VALIDATE``（引用对象已全部存在）或 ``POST_VERIFY``
（还有对象不存在），D-05 决定前者在 VALIDATE 阶段逐条调用实例的校验接口，后者在部署后
验证时再校验，且**失败只报告不修正**（DX-BR-09）。

哪条表达式归哪个接口，取决于对象类型 + 字段名，这张映射表放在这里而不是散在各处：
新增一类表达式就是加一行数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.dto.issue import Issue, make_issue
from factory_dhis2.enums import IssueLevel, SourceStage
from factory_dhis2.errors import Dhis2Error, Dhis2NotFoundError
from factory_dhis2.payload.package import MetadataPackage
from factory_dhis2.profiles.base import VersionProfile

#: manifest 里的两种归类（S2 的 classify_expression 返回值）
SERVER_CHECKABLE = "DHIS2_VALIDATE"
DEFERRED = "POST_VERIFY"

#: ``(dhis2Type, field)`` → 逻辑资源名与需要的参数
VALIDATE_RESOURCES: dict[tuple[str, str], tuple[str, str]] = {
    ("programRule", "condition"): ("programRules.condition.validate", "expression"),
    ("programRule", "expression"): ("programRules.condition.validate", "expression"),
    ("programIndicator", "expression"): ("programIndicators.expression.validate", "expression"),
    ("validationRule", "expression"): ("validationRules.expression.validate", "expression"),
    ("validationRule", "leftExpression"): ("validationRules.expression.validate", "expression"),
    ("validationRule", "rightExpression"): ("validationRules.expression.validate", "expression"),
}

#: 实例返回里表示“通过”的字段形态
_TRUE_KEYS = ("result", "valid", "ok")


def _not_found_code(error: Dhis2NotFoundError) -> str:
    """实例自己给的错误码（``E1005``），拿不到才退回 Factory 的 ``DHIS2_NOT_FOUND``。

    报"这台实例没有这个端点"时，运维要看的是实例说了什么，而不是 S4 把自己的错误类名复读一遍。
    """
    details = error.details if isinstance(error.details, dict) else {}
    return str(details.get("errorCode") or details.get("httpStatus") or error.code)


@dataclass
class ExpressionCheckReport:
    """一次表达式校验批次的结果。"""

    checked: int = 0
    passed: int = 0
    failed: list[Issue] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def as_document(self, *, phase: str) -> dict[str, Any]:
        return {
            "phase": phase,
            "checked": self.checked,
            "passed": self.passed,
            "failed": [issue.model_dump(mode="json") for issue in self.failed],
            "skipped": self.skipped,
            "results": self.results,
        }


def server_checkable(package: MetadataPackage) -> list[dict[str, Any]]:
    return _of_classification(package, SERVER_CHECKABLE)


def deferred(package: MetadataPackage) -> list[dict[str, Any]]:
    return _of_classification(package, DEFERRED)


def _of_classification(
    package: MetadataPackage, verdict: str
) -> list[dict[str, Any]]:
    """分类字段既可能在 ``validation`` 也可能已经折算成 ``needsServerCheck``。"""
    out: list[dict[str, Any]] = []
    for item in package.expression_validation():
        stated = str(item.get("validation") or "")
        if stated == verdict or verdict == DEFERRED and bool(item.get("needsServerCheck")) and stated != SERVER_CHECKABLE:
            out.append(item)
    return out


def check_expressions(
    client: Dhis2Client,
    items: list[dict[str, Any]],
    *,
    package: MetadataPackage,
    profile: VersionProfile,
    phase: str,
    cancel: Any | None = None,
) -> ExpressionCheckReport:
    """逐条调用实例的表达式校验接口（DX-EXE-028 明确是“逐条”）。

    没有对应校验接口的类型计入 ``skipped`` 而不是失败：实例答不了就是答不了，S4 不去
    假装能答。
    """
    report = ExpressionCheckReport()
    type_by_key = {
        str(row.get("businessKey")): str(row.get("dhis2Type") or "")
        for row in package.objects()
    }
    uid_by_key = {
        str(row.get("businessKey")): row.get("uid")
        for row in package.uid_map()
    }
    for item in items:
        if cancel is not None:
            cancel.raise_if_cancelled()
        business_key = str(item.get("businessKey") or "")
        dhis2_type = type_by_key.get(business_key, "")
        field_name = str(item.get("field") or "")
        mapping = VALIDATE_RESOURCES.get((dhis2_type, field_name))
        expression = str(item.get("compiled") or item.get("original") or "")
        if mapping is None or not expression:
            report.skipped.append(
                {
                    "businessKey": business_key,
                    "field": field_name,
                    "reason": "该版本没有对应的服务端校验接口" if mapping is None else "表达式为空",
                }
            )
            continue
        resource, param_name = mapping
        if profile.resource(resource) is None:
            report.skipped.append(
                {"businessKey": business_key, "field": field_name, "reason": f"{resource} 未登记"}
            )
            continue
        params = {param_name: expression}
        program_uid = _program_uid(item, package, uid_by_key)
        if program_uid:
            params["programId"] = str(program_uid)
        report.checked += 1
        try:
            payload = client.call(resource, params={k: str(v) for k, v in params.items()}).payload
        except Dhis2NotFoundError as exc:
            # ``DHIS2_NOT_FOUND`` means "this object **or endpoint** is not on the instance".
            # For an expression check that is the same fact the two branches above already
            # treat as ``skipped`` — the instance cannot answer — just discovered one request
            # later.  Measured on the official 2.41 demo: ``programIndicators/expression/
            # validate`` is not a route there at all, the request is matched as
            # ``programIndicators/{id}`` with id="expression" and comes back 404 ``E1005``.
            # Raising that as a BLOCKING issue failed VALIDATE for every package carrying a
            # programRule / programIndicator / validationRule expression, for a reason that
            # has nothing to do with any of them.
            report.skipped.append(
                {
                    "businessKey": business_key,
                    "field": field_name,
                    "reason": f"{resource} 在这台实例上不存在（{_not_found_code(exc)}）",
                }
            )
            report.checked -= 1
            continue
        except Dhis2Error as exc:
            report.failed.append(
                make_issue(
                    f"DX-EXE-{'028' if phase == 'VALIDATE' else '041'}",
                    IssueLevel.BLOCKING,
                    f"表达式服务端校验无法完成：{exc.message}",
                    object_key=business_key,
                    stage=SourceStage.MAPPING,
                )
            )
            continue
        verdict = _verdict_of(payload)
        report.results.append(
            {
                "businessKey": business_key,
                "field": field_name,
                "expression": expression,
                "resource": resource,
                "verdict": verdict,
                "raw": payload if isinstance(payload, dict) else None,
            }
        )
        if verdict.get("ok"):
            report.passed += 1
        else:
            report.failed.append(
                make_issue(
                    f"DX-EXE-{'028' if phase == 'VALIDATE' else '041'}",
                    IssueLevel.BLOCKING,
                    f"实例判定表达式不成立：{verdict.get('message') or '未给出原因'}",
                    object_key=business_key,
                    stage=SourceStage.MAPPING,
                    suggested_action="修正表达式后重新编译；S4 不修改实例（DX-BR-09）",
                )
            )
    return report


def _verdict_of(payload: Any) -> dict[str, Any]:
    """把不同接口的“通过”形态归一。"""
    if payload is None:
        return {"ok": True, "message": None}
    if isinstance(payload, bool):
        return {"ok": payload, "message": None if payload else "condition does not parse"}
    if not isinstance(payload, dict):
        return {"ok": False, "message": f"非预期的校验响应：{type(payload).__name__}"}
    for key in _TRUE_KEYS:
        if key in payload:
            value = payload[key]
            if isinstance(value, bool):
                return {"ok": value, "message": payload.get("message") or payload.get("description")}
            if isinstance(value, str):
                return {"ok": value.lower() in {"true", "ok", "valid"}, "message": value}
    # 实例用参数清单表达“引用不到的变量”：非空即视为不成立
    params = payload.get("parameters")
    if isinstance(params, list) and params:
        unresolved = [item for item in params if isinstance(item, dict) and not item.get("id")]
        if unresolved:
            return {"ok": False, "message": f"{len(unresolved)} 个引用无法解析"}
    return {"ok": True, "message": payload.get("message")}


def _program_uid(
    item: dict[str, Any], package: MetadataPackage, uid_by_key: dict[str, Any]
) -> str | None:
    """表达式校验接口需要 program 上下文；引用里能找到的第一个 Program 就是它。"""
    for reference in item.get("references") or []:
        uid = uid_by_key.get(str(reference))
        if uid:
            return str(uid)
    for row in package.objects():
        if str(row.get("dhis2Type")) == "program" and row.get("uid"):
            return str(row["uid"])
    return None


__all__ = [
    "DEFERRED",
    "SERVER_CHECKABLE",
    "ExpressionCheckReport",
    "check_expressions",
    "deferred",
    "server_checkable",
]
