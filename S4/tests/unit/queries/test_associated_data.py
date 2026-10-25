"""关联数据检查：删除安全（DX-QRY-004、总体 SRS FR-CMP-009）。

判定方式**按类型写在版本 Profile 里**（``profiles/data/2.41/associated_data.json``），代码
不写死：不同版本能问的问题不一样。两个兜底方向都必须偏向“不许删”——类型没登记判定方式、
或者判定所依赖的端点在实例上不可用时，答案都是 ``HAS_DATA``。判不出来就不许删，这是不可
逆动作的不对称性决定的。

本文件同时证明“判定是数据驱动的”：往 Profile 里加一条新判定，函数就按新判定走，一行代码
都不改（DX-CAP-002、DX-NFR-07）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx
import pytest

from factory_dhis2 import api, deps
from factory_dhis2.enums import CredentialScope
from factory_dhis2.profiles import registry
from factory_dhis2.profiles.base import AssociatedDataCheck
from factory_dhis2.testing.instance import FakeDhis2Instance

UID = "nESOm18Mw8x"
PROGRAM = "uOKzIJvTouH"


def answer(path_suffix: str, payload: Any, *, status: int = 200) -> None:
    """给某个逻辑端点装一个确定的答案（假实例伺服不了的路径用它）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(path_suffix):
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json={})

    deps.set_transport(httpx.MockTransport(handler))


# --- 保守方向：判不出来就不许删 --------------------------------------------


def test_unregistered_type_is_conservatively_reported_as_having_data(directory: Any) -> None:
    """DX-QRY-004：类型没有登记判定方式 → 有关联数据，且一次请求都不发。"""
    verdict = api.has_associated_data("dev", "categoryOption", UID)

    assert bool(verdict) is True and verdict.has_data is True
    assert verdict["hasData"] is True
    assert "未登记" in verdict["basis"] and "categoryOption" in verdict["basis"]
    assert verdict["evidence"] == {"rule": "DX-QRY-004"}
    assert directory.total_requests == 0, "判不了时不该拿真实实例去试"


def test_unavailable_endpoint_falls_back_to_has_data(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-004：判定端点不可用时同样返回“有关联数据”，并把依据写清楚。"""
    dev.fail_next("dataValues", 404)
    verdict = api.has_associated_data("dev", "dataElement", UID)

    assert bool(verdict) is True
    assert "端点不可用" in verdict["basis"]
    assert verdict["evidence"]["dataValues"] == {"error": "DHIS2_NOT_FOUND"}
    assert [row["path"] for row in dev.requests] == ["dataValues"], "第一条判定就该给出保守结论"


def test_tracker_endpoint_unavailability_is_also_conservative(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-004：Program 的判定要问 Tracker 接口，问不到依然是“不许删”。"""
    dev.fail_next("tracker/enrollments", 503, times=3)
    verdict = api.has_associated_data("dev", "program", UID)

    assert bool(verdict) is True
    assert verdict["evidence"]["enrollments"] == {"error": "DHIS2_UNREACHABLE"}
    assert "端点不可用" in verdict["basis"]


def test_a_failing_check_without_a_conservative_rule_just_moves_on(
    directory: Any, dev: FakeDhis2Instance, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DX-QRY-004：判定项没写 fallback 时失败只是跳过——方向由 Profile 决定，不由代码猜。"""
    base = registry.profile_for_version("2.41.10")
    custom = replace(
        base,
        associated_data=base.associated_data
        + (
            AssociatedDataCheck(
                dhis2_type="categoryOption",
                checks=(
                    {"label": "optionalProbe", "resource": "identifiableObjects", "params": {}},
                    {
                        "label": "dataElementUse",
                        "resource": "metadata.read",
                        "dhis2Type": "dataElement",
                        "params": {"filter": f"categoryOptions.id:eq:{UID}"},
                    },
                ),
            ),
        ),
    )
    monkeypatch.setattr(registry, "profile_for_version", lambda _version: custom)
    dev.fail_next("identifiableObjects", 404)

    verdict = api.has_associated_data("dev", "categoryOption", UID)

    assert bool(verdict) is False, "两条判定都为空就是没有关联数据"
    assert verdict["basis"] == "所有登记的判定都为空"
    assert verdict["evidence"]["optionalProbe"] == {"error": "DHIS2_NOT_FOUND"}
    assert verdict["evidence"]["dataElementUse"] == {"matched": []}


# --- 判得出来：有数据 / 没数据 ----------------------------------------------


def test_a_referencing_object_makes_the_verdict_true(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-004：Profile 登记的判定命中时，结论是“仍在使用”。"""
    # ``optionSet.id`` 就是判定里那个过滤字段：假实例按字段名匹配，因此这里等价于
    # “实例上确实有一个引用了这个 OptionSet 的 DataElement”。
    dev.put("dataElement", "de1", code="W", name="体重", **{"optionSet.id": UID})
    verdict = api.has_associated_data("dev", "optionSet", UID)

    assert bool(verdict) is True
    assert verdict["basis"] == "dataElementUse 显示该对象仍在使用"
    assert verdict["evidence"]["dataElementUse"] == {"matched": ["de1"]}


def test_all_registered_checks_empty_means_no_associated_data(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-004：登记过的判定全部为空时，才允许删除。"""
    verdict = api.has_associated_data("dev", "optionSet", UID)

    assert bool(verdict) is False and verdict.has_data is False
    assert verdict["basis"] == "所有登记的判定都为空"
    assert verdict["evidence"]["dataElementUse"] == {"matched": []}
    assert verdict["evidence"]["trackedEntityAttributeUse"] == {"matched": []}
    paths = [row["path"] for row in dev.requests]
    assert paths == ["dataElements", "trackedEntityAttributes"], "判定顺序按 Profile 登记顺序走"


def test_data_values_rows_count_as_associated_data(directory: Any) -> None:
    """DX-QRY-004：DataElement 挂了数据值就是有关联数据。"""
    answer("/api/dataValues.json", {"dataValues": [{"dataElement": UID, "value": "1"}]})
    verdict = api.has_associated_data("dev", "dataElement", UID)

    assert bool(verdict) is True
    assert verdict["evidence"]["dataValues"] == {"rows": 1}
    assert verdict["basis"] == "dataValues 显示该对象仍在使用"


def test_empty_data_values_and_nothing_else_means_free(directory: Any) -> None:
    """DX-QRY-004：真的判到空才回答“没有关联数据”，空集合不是“判不出来”。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/api/dataValues.json"):
            return httpx.Response(200, json={"dataValues": []})
        return httpx.Response(200, json={"programStageDataElements": []})

    deps.set_transport(httpx.MockTransport(handler))
    verdict = api.has_associated_data("dev", "dataElement", UID)

    assert bool(verdict) is False
    assert verdict["evidence"] == {"dataValues": {"rows": 0}, "eventOrTrackerUse": {"matched": []}}


def test_enrollments_answered_by_the_tracker_api_are_data(directory: Any) -> None:
    """DX-QRY-004：Tracker 判定的“非空即有数据”按响应里的集合键判断。"""
    answer("/api/tracker/enrollments.json", {"enrollments": [{"enrollment": "e1"}], "pager": {}})
    verdict = api.has_associated_data("dev", "program", UID)

    assert bool(verdict) is True
    assert verdict["basis"] == "enrollments 显示该对象仍在使用"
    assert verdict["evidence"]["enrollments"]["responseKeys"] == ["enrollments", "pager"]


def test_an_empty_tracker_answer_is_not_data(directory: Any) -> None:
    """DX-QRY-004：空集合不算数据；只有判不出来才算判不出来。"""
    answer("/api/tracker/enrollments.json", {"enrollments": [], "pager": {}})
    verdict = api.has_associated_data("dev", "program", UID)

    assert bool(verdict) is False
    assert verdict["basis"] == "所有登记的判定都为空"


def test_a_bare_scalar_payload_is_treated_as_answer_present(directory: Any) -> None:
    """DX-QRY-004：非集合响应按“有没有内容”判定，键名一并留作证据。"""
    answer("/api/tracker/enrollments.json", {"status": "OK", "count": 3})
    verdict = api.has_associated_data("dev", "program", UID)

    assert bool(verdict) is True
    assert verdict["evidence"]["enrollments"]["responseKeys"] == ["count", "status"]


# --- 参数代入与环境绑定 ----------------------------------------------------


def test_the_program_placeholder_is_filled_from_the_call(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-004：ProgramStage 的判定要知道所属 Program，``{program}`` 由调用方给出。"""
    dev.fail_next("tracker/events", 404)
    verdict = api.has_associated_data("dev", "programStage", UID, program=PROGRAM)

    assert bool(verdict) is True
    asked = [row for row in dev.requests if row["path"] == "tracker/events"]
    assert len(asked) == 1
    assert asked[0]["query"]["program"] == PROGRAM
    assert asked[0]["query"]["filter"] == f"programStage:eq:{UID}"
    assert "{uid}" not in str(asked[0]["query"]) and "{program}" not in str(asked[0]["query"]), (
        "占位符没被替换就发出去了"
    )


def test_the_uid_placeholder_is_filled_in_the_nested_filter(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-004：嵌套判定里的 ``{uid}`` 同样要替换成被检查的那个对象。"""
    api.has_associated_data("dev", "optionSet", UID)
    filters = [str(row["query"].get("filter")) for row in dev.requests]
    assert filters == [f"optionSet.id:eq:{UID}", f"optionSet.id:eq:{UID}"]
    assert not any(item.startswith("filter:") for item in filters), (
        "过滤表达式被再套一次 filter: 前缀后，实例把 filter 当成字段名，判定恒不命中"
    )
    for row in dev.requests:
        assert row["query"]["pageSize"] == "1", "分页参数不该被当成过滤条件"


def test_the_verdict_is_per_environment(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-BR-02：关联数据是环境级事实，判定只问被点名的那台实例。"""
    dev.put("dataElement", "de1", code="W", name="体重", **{"optionSet.id": UID})
    assert bool(api.has_associated_data("dev", "optionSet", UID)) is True
    assert bool(api.has_associated_data("uat", "optionSet", UID)) is False
    assert directory.instance("uat").requests and dev.requests


def test_the_verdict_carries_a_truthy_mapping_of_its_own(dev: FakeDhis2Instance) -> None:
    """DX-QRY-004：返回值按真值判断，同时带着 hasData / basis / evidence 三个字段。"""
    verdict = api.has_associated_data("dev", "categoryOption", UID)
    assert sorted(verdict) == ["basis", "evidence", "hasData"]
    assert dict(verdict)["hasData"] is True and bool(dict(verdict)) is True


def test_the_check_uses_only_the_discovery_scope(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-BR-03：删除前的检查是只读动作，用不到 import 范围的身份。"""
    import base64

    headers: list[str] = []

    class Capturing(httpx.BaseTransport):
        def __init__(self, inner: httpx.BaseTransport) -> None:
            self._inner = inner

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            headers.append(request.headers.get("Authorization", ""))
            return self._inner.handle_request(request)

    deps.set_transport(Capturing(directory.transport()))
    api.has_associated_data("dev", "optionSet", UID)

    assert headers
    assert set(headers) == {"Basic " + base64.b64encode(b"svcacct-reader:only-discovery").decode()}
    assert CredentialScope.IMPORT.value not in "".join(headers)
    assert all(row["method"] == "GET" for row in dev.requests)
