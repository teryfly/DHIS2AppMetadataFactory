"""服务端表达式校验（DX-EXE-028/041、D-05、DX-BR-09）。

本地语法与引用校验归 S2；这里只做一件事：**问实例它答得了的那部分**。分类由 manifest 决
定——引用对象都已存在的归 ``DHIS2_VALIDATE``，还有对象不存在的归 ``POST_VERIFY``（D-05）。
实例答不了的类型计入 ``skipped`` 而不是失败：假装能答就是编造结论；而判定失败永远是阻塞
问题，S4 不修改实例（DX-BR-09）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from factory_dhis2.enums import IssueLevel, SourceStage
from factory_dhis2.errors import Dhis2NotFoundError, Dhis2PermissionError
from factory_dhis2.execution import expressions
from factory_dhis2.execution.expressions import (
    DEFERRED,
    SERVER_CHECKABLE,
    ExpressionCheckReport,
    check_expressions,
    deferred,
    server_checkable,
)
from tests.unit.execution import (
    FakeClient,
    FakeResponse,
    build_manifest,
    make_run_context,
    make_target,
)

PROGRAM_UID = "pg0000000001"
RULE_UID = "pr000000001"
DATA_ELEMENT_UID = "de000000001"
INDICATOR_UID = "pi0000000001"
VALIDATION_RULE_UID = "vr0000000001"

CONDITION = "V{weight} > 90"


def _expression(
    business_key: str,
    field: str,
    verdict: str,
    *,
    compiled: str = CONDITION,
    references: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "businessKey": business_key,
        "field": field,
        "validation": verdict,
        "compiled": compiled,
        "original": f"original of {business_key}",
        "references": references or [],
    }


def _package(expressions: list[dict[str, Any]]) -> Any:
    rows = [
        (PROGRAM_UID, "program", "bk.program"),
        (RULE_UID, "programRule", "bk.rule"),
        (DATA_ELEMENT_UID, "dataElement", "bk.de"),
        (INDICATOR_UID, "programIndicator", "bk.indicator"),
        (VALIDATION_RULE_UID, "validationRule", "bk.vr"),
    ]
    return build_manifest(
        objects=[
            {"businessKey": key, "dhis2Type": dhis2_type, "uid": uid, "disposition": "CREATE"}
            for uid, dhis2_type, key in rows
        ],
        uid_map=[
            {"businessKey": key, "uid": uid, "dhis2Type": dhis2_type, "disposition": "CREATE"}
            for uid, dhis2_type, key in rows
        ],
        dispositions={key: "CREATE" for _uid, _t, key in rows},
        controlled_fields={},
        expressions=expressions,
    )


def _context(
    items: list[dict[str, Any]],
    *,
    verdicts: dict[str, Any] | None = None,
    responses: dict[str, Any] | None = None,
    strip_resource: str | None = None,
) -> Any:
    target = make_target()
    if strip_resource is not None:
        profile = target.profile
        target = replace(
            target,
            profile=replace(
                profile,
                resources=tuple(
                    entry
                    for entry in profile.resources
                    if entry.logical_name != strip_resource
                ),
            ),
        )
    package = package_of_manifest(items)
    client = FakeClient(target, expression_verdicts=verdicts or {}, responses=responses or {})
    return make_run_context(package, client=client, target=target)


def package_of_manifest(items: list[dict[str, Any]]) -> Any:
    from tests.unit.execution import package_of

    return package_of(_package(items), _payload())


def _payload() -> dict[str, Any]:
    return {
        "programs": [{"id": PROGRAM_UID, "name": "Antenatal"}],
        "programRules": [{"id": RULE_UID, "name": "rule", "condition": CONDITION}],
        "dataElements": [{"id": DATA_ELEMENT_UID, "name": "Weight"}],
        "programIndicators": [{"id": INDICATOR_UID, "expression": "#{de} * 2"}],
        "validationRules": [{"id": VALIDATION_RULE_UID, "name": "vr"}],
    }


# --- 分类（D-05）------------------------------------------------------------


def test_the_manifest_classification_decides_when_each_expression_is_checked() -> None:
    """D-05：引用对象都已存在的在 VALIDATE 阶段问实例，还不存在的延后到部署后验证。"""
    items = [
        _expression("bk.rule", "condition", SERVER_CHECKABLE),
        _expression("bk.indicator", "expression", DEFERRED),
    ]
    package = package_of_manifest(items)

    assert [item["businessKey"] for item in server_checkable(package)] == ["bk.rule"]
    assert [item["businessKey"] for item in deferred(package)] == ["bk.indicator"]


def test_an_expression_without_a_stated_verdict_is_deferred() -> None:
    """D-05：只标了 ``needsServerCheck`` 而没写归属的表达式按延后处理——不在 VALIDATE 里猜。"""
    package = package_of_manifest(
        [{"businessKey": "bk.rule", "field": "condition", "compiled": CONDITION, "needsServerCheck": True}]
    )

    assert [item["businessKey"] for item in deferred(package)] == ["bk.rule"]
    assert server_checkable(package) == []


def test_an_expression_already_checked_by_the_server_is_not_deferred_again() -> None:
    """D-05：已经归入 ``DHIS2_VALIDATE`` 的表达式不再重复进延后清单（同一问题问两遍）。"""
    package = package_of_manifest([_expression("bk.rule", "condition", SERVER_CHECKABLE)])

    assert deferred(package) == []


# --- 逐条校验 ---------------------------------------------------------------


def test_each_checkable_expression_is_asked_one_by_one() -> None:
    """DX-EXE-028：逐条调用实例的校验接口，并把引用的 program 一起带上。"""
    context = _context(
        [
            _expression("bk.rule", "condition", SERVER_CHECKABLE, references=["bk.program"]),
            _expression("bk.indicator", "expression", SERVER_CHECKABLE, compiled="#{a} + #{b}"),
        ]
    )

    report = check_expressions(
        context.client,
        server_checkable(context.package),
        package=context.package,
        profile=context.target.profile,
        phase="VALIDATE",
    )

    names = [item.logical_name for item in context.client.calls]
    assert names == ["programRules.condition.validate", "programIndicators.expression.validate"]
    assert report.checked == 2 and report.passed == 2
    assert report.ok
    program_id = context.client.calls[0].params["programId"]
    assert program_id == PROGRAM_UID, "programRule 的条件校验要有 program 上下文才答得准"


def test_a_type_without_a_server_endpoint_is_skipped_not_failed() -> None:
    """DX-EXE-028：这一类表达式实例没有校验接口——跳过并说明原因，不假装能答。"""
    context = _context([_expression("bk.de", "formName", SERVER_CHECKABLE, compiled="#{x}")])

    report = check_expressions(
        context.client,
        server_checkable(context.package),
        package=context.package,
        profile=context.target.profile,
        phase="VALIDATE",
    )

    assert report.checked == 0
    assert report.ok, "答不了不等于不通过"
    assert report.skipped == [
        {"businessKey": "bk.de", "field": "formName", "reason": "该版本没有对应的服务端校验接口"}
    ]


def test_an_empty_expression_is_skipped_with_its_own_reason() -> None:
    """DX-EXE-028：空表达式没东西可问，跳过原因要说清是表达式为空而不是没有接口。"""
    item = _expression("bk.rule", "condition", SERVER_CHECKABLE, compiled="")
    item["original"] = ""
    context = _context([item])

    report = check_expressions(
        context.client,
        server_checkable(context.package),
        package=context.package,
        profile=context.target.profile,
        phase="VALIDATE",
    )

    assert report.skipped[0]["reason"] == "表达式为空"
    assert context.client.calls == []


def test_a_registered_but_absent_resource_is_skipped_for_that_version() -> None:
    """DX-EXE-028/CLI-005：映射表里有这条，但该版本的目录里没登记这个接口——跳过而不是打错端点。"""
    context = _context(
        [_expression("bk.rule", "condition", SERVER_CHECKABLE)],
        strip_resource="programRules.condition.validate",
    )

    report = check_expressions(
        context.client,
        server_checkable(context.package),
        package=context.package,
        profile=context.target.profile,
        phase="VALIDATE",
    )

    assert report.skipped == [
        {"businessKey": "bk.rule", "field": "condition", "reason": "programRules.condition.validate 未登记"}
    ]
    assert context.client.calls == []


def test_the_program_context_falls_back_to_the_only_program_in_the_payload() -> None:
    """DX-EXE-028：表达式没写引用时，仍然要给出 program 上下文——载荷里的 program 就是它。"""
    context = _context([_expression("bk.rule", "condition", SERVER_CHECKABLE)])

    check_expressions(
        context.client,
        server_checkable(context.package),
        package=context.package,
        profile=context.target.profile,
        phase="VALIDATE",
    )

    assert context.client.calls[0].params["programId"] == PROGRAM_UID


def test_a_validation_rule_expression_uses_the_validation_rule_endpoint() -> None:
    """DX-EXE-028：同一句话在不同类型上问的是不同接口，映射表是唯一出处。"""
    context = _context(
        [
            _expression("bk.vr", "leftExpression", SERVER_CHECKABLE, compiled="#{left}"),
            _expression("bk.vr", "rightExpression", SERVER_CHECKABLE, compiled="#{right}"),
        ]
    )

    check_expressions(
        context.client,
        server_checkable(context.package),
        package=context.package,
        profile=context.target.profile,
        phase="VALIDATE",
    )

    assert [item.logical_name for item in context.client.calls] == [
        "validationRules.expression.validate",
        "validationRules.expression.validate",
    ]
    assert context.client.calls[0].params["expression"] == "#{left}"


def test_a_rejected_expression_is_a_blocking_issue_that_names_the_instance_verdict() -> None:
    """DX-EXE-028：实例判它不成立就是阻塞问题，并带上实例给的原因（S4 不修正，DX-BR-09）。"""
    context = _context(
        [_expression("bk.rule", "condition", SERVER_CHECKABLE)],
        verdicts={"programRules.condition.validate": {"result": False, "message": "unknown variable V{weight}"}},
    )

    report = check_expressions(
        context.client,
        server_checkable(context.package),
        package=context.package,
        profile=context.target.profile,
        phase="VALIDATE",
    )

    assert not report.ok
    assert report.checked == 1 and report.passed == 0
    issue = report.failed[0]
    assert issue.rule_id == "DX-EXE-028"
    assert issue.level is IssueLevel.BLOCKING
    assert issue.source_stage is SourceStage.MAPPING
    assert issue.object_key == "bk.rule"
    assert "unknown variable" in issue.description
    assert "DX-BR-09" in (issue.suggested_action or "")


def test_a_deferred_expression_failure_cites_the_post_verify_rule() -> None:
    """DX-EXE-041：延后到部署后验证的表达式失败，引用的是那一条规则而不是 VALIDATE。"""
    context = _context(
        [_expression("bk.rule", "condition", DEFERRED)],
        verdicts={"programRules.condition.validate": False},
    )

    report = check_expressions(
        context.client,
        deferred(context.package),
        package=context.package,
        profile=context.target.profile,
        phase="POST_VERIFY",
    )

    assert report.failed[0].rule_id == "DX-EXE-041"
    assert "condition does not parse" in report.failed[0].description


def test_an_unreachable_endpoint_is_reported_as_a_check_that_could_not_run() -> None:
    """DX-EXE-028：问不到答案也要成为阻塞问题——「没校验成」不等于「校验通过」。"""
    context = _context([_expression("bk.rule", "condition", SERVER_CHECKABLE)])

    def refuse(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise Dhis2PermissionError("the account may not read program rules", details={})

    monkey = pytest.MonkeyPatch()
    monkey.setattr(context.client, "call", refuse)
    try:
        report = check_expressions(
            context.client,
            server_checkable(context.package),
            package=context.package,
            profile=context.target.profile,
            phase="VALIDATE",
        )
    finally:
        monkey.undo()

    assert report.checked == 1
    assert not report.ok
    assert "无法完成" in report.failed[0].description


# --- 实例答案的归一 ----------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "ok", "message"),
    [
        (None, True, None),
        (True, True, None),
        (False, False, "condition does not parse"),
        ({"result": True}, True, None),
        ({"result": False, "message": "bad variable"}, False, "bad variable"),
        ({"result": "true"}, True, "true"),
        ({"result": "OK"}, True, "OK"),
        ({"result": "nope"}, False, "nope"),
        ({"valid": False, "description": "not valid"}, False, "not valid"),
        ({"ok": True}, True, None),
        ({"parameters": [{"id": "a"}, {"name": "b"}]}, False, "1 个引用无法解析"),
        ({"parameters": [{"id": "a"}]}, True, None),
        ({"parameters": []}, True, None),
        ("a plain string", False, "非预期的校验响应：str"),
        ({"message": "no verdict field"}, True, "no verdict field"),
    ],
)
def test_the_instance_answers_are_normalised_into_one_verdict(
    payload: Any, ok: bool, message: str | None
) -> None:
    """DX-EXE-028：不同接口回的「通过」形态不一样，归一只能有一个地方做。"""
    context = _context([_expression("bk.rule", "condition", SERVER_CHECKABLE)])
    client = context.client

    def answer(*_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse(200, payload)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(client, "call", answer)
    try:
        report = check_expressions(
            client,
            server_checkable(context.package),
            package=context.package,
            profile=context.target.profile,
            phase="VALIDATE",
        )
    finally:
        monkey.undo()

    verdict_of_only_item = report.results[0]["verdict"]
    assert verdict_of_only_item["ok"] is ok
    assert verdict_of_only_item["message"] == message
    assert report.ok is ok


def test_the_verdict_helper_is_the_single_definition_of_pass() -> None:
    """DX-EXE-028：判定归一是这张表，逐条调用与部署后验证共用同一个定义。"""
    assert expressions._verdict_of(None) == {"ok": True, "message": None}
    assert expressions._verdict_of({"result": "VALID"})["ok"] is True
    assert expressions._verdict_of([{"result": True}])["ok"] is False


# --- 批次结果 ---------------------------------------------------------------


def _issue() -> Any:
    from factory_dhis2.dto.issue import make_issue

    return make_issue(
        "DX-EXE-041",
        IssueLevel.BLOCKING,
        "实例判定表达式不成立",
        object_key="bk.rule",
    )


def test_the_report_document_carries_every_outcome_of_the_batch() -> None:
    """DX-EXE-042：表达式批次的结果要能原样进报告——检查了几条、过了几条、跳过了哪些。"""
    report = ExpressionCheckReport(checked=3, passed=1)
    report.skipped.append({"businessKey": "bk.de", "field": "formName", "reason": "没有接口"})
    report.results.append({"businessKey": "bk.rule", "verdict": {"ok": True}})
    report.failed.append(_issue())

    document = report.as_document(phase="POST_VERIFY")

    assert document["phase"] == "POST_VERIFY"
    assert document["checked"] == 3 and document["passed"] == 1
    assert document["skipped"][0]["businessKey"] == "bk.de"
    assert document["results"][0]["businessKey"] == "bk.rule"
    assert document["failed"][0]["rule_id"] == "DX-EXE-041"
    assert not report.ok


def test_an_empty_batch_is_ok_by_definition() -> None:
    """DX-EXE-028：没有问题清单就是没有失败——空批次不该被当成「校验通过」的证据。"""
    report = ExpressionCheckReport()

    assert report.ok
    assert report.as_document(phase="VALIDATE")["failed"] == []


def test_an_endpoint_the_instance_does_not_have_is_skipped_not_blocked() -> None:
    """404 是「这台实例没有这个端点」，不是「表达式不成立」，也不是「没权限所以没验」。

    官方 2.41 演示实例实测：``programIndicators/expression/validate`` 根本不存在，请求被按
    ``programIndicators/{id}`` 匹配（id="expression"）回 404 ``E1005``。Profile 的资源目录里
    登记了它，所以 :func:`check_expressions` 那个"资源未登记就跳过"的分支挡不住——只有实例
    的答复能说明这个版本到底有没有这个接口。把它算成 BLOCKING 会让每一份带 programRule /
    programIndicator / validationRule 表达式的包都过不了 VALIDATE，而失败原因与表达式无关。

    ``Dhis2PermissionError`` 那条仍必须阻塞：账户读不到就是没校验成，那是安全边界（同一个
    函数上面那条测试钉着）。这里只区分「这台实例不供这个接口」这一种事实。
    """
    context = _context([_expression("bk.rule", "condition", SERVER_CHECKABLE)])

    def not_here(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise Dhis2NotFoundError(
            "not found on dev: programIndicators/expression/validate",
            details={"errorCode": "E1005", "httpStatus": "Not Found"},
        )

    monkey = pytest.MonkeyPatch()
    monkey.setattr(context.client, "call", not_here)
    try:
        report = check_expressions(
            context.client,
            server_checkable(context.package),
            package=context.package,
            profile=context.target.profile,
            phase="VALIDATE",
        )
    finally:
        monkey.undo()

    assert report.ok, "端点不存在不该让 VALIDATE 失败"
    assert report.failed == []
    assert report.checked == 0, report.checked
    assert [item["businessKey"] for item in report.skipped] == ["bk.rule"], report.skipped
    assert "E1005" in report.skipped[0]["reason"], report.skipped
