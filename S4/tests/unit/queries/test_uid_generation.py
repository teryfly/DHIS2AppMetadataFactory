"""UID 批量生成（DX-QRY-001、DX-AC-08）。

号只能问实例要：UID 的**唯一性由实例保证**，自己拼随机串会在导入时才暴露成最难查的一类
错误。因此这里要证明的不是“返回了 N 个字符串”，而是四件事：

* 走的是官方 ``system/id`` 资源，且超过单次上限时**分批**（DX-QRY-001）；
* 返回数量正好 N、互不相同、形态合法（DX-AC-08）；
* 实例给少了就继续要，**绝不自己造号**；
* 实例给的答案不合法时直接失败，而不是把畸形号交给 S2。
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from factory_foundation.api import unit_of_work

from factory_dhis2 import api, deps
from factory_dhis2.config import Dhis2Settings, configure
from factory_dhis2.errors import UidFormatInvalidError
from factory_dhis2.models.execution import ExecutionRow
from tests.unit.queries.conftest import valid_uid, valid_uids

UID_SHAPE = re.compile(r"[A-Za-z][A-Za-z0-9]{10}")


def test_generate_uids_batches_the_official_endpoint(directory: Any) -> None:
    """DX-QRY-001：单次上限之内的号一次要完，超出就按上限分批。"""
    configure(Dhis2Settings(uid_batch_limit=4))
    instance = directory.instance("dev")
    instance.generated_uids = valid_uids(12)

    uids = api.generate_uids("dev", 9)

    assert len(uids) == 9 and len(set(uids)) == 9
    limits = [str(row["query"]["limit"]) for row in directory.requests("dev", "system/id")]
    assert limits == ["4", "4", "1"], "分批尺寸必须贴着剩余量收敛，不能固定要一批"
    assert all(row["method"] == "GET" for row in instance.requests), "生成 UID 不得改环境"


def test_generate_uids_respects_the_default_batch_limit_of_one_hundred(directory: Any) -> None:
    """DX-AC-08：500 个 UID 全部合法且互不相同（默认批量上限 100 → 五批）。"""
    instance = directory.instance("dev")
    instance.generated_uids = valid_uids(500)

    uids = api.generate_uids("dev", 500)

    assert len(uids) == 500 and len(set(uids)) == 500
    assert all(UID_SHAPE.fullmatch(item) for item in uids)
    assert [row["query"]["limit"] for row in directory.requests("dev", "system/id")] == ["100"] * 5


@pytest.mark.parametrize("count", [0, -1])
def test_generate_uids_of_a_non_positive_count_asks_nothing(directory: Any, count: int) -> None:
    """DX-QRY-001：零个号就不该有一次网络往返。"""
    assert api.generate_uids("dev", count) == []
    assert directory.total_requests == 0


def test_generate_uids_keeps_asking_when_the_instance_gives_fewer(directory: Any) -> None:
    """DX-QRY-001：实例少给时继续申请补齐，但一个号也不自己造。"""
    issued = {"n": 0}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        issued["n"] += 1
        # 每次只肯给一个，尽管请求要两个
        return httpx.Response(200, json={"codes": [valid_uid(issued["n"])]})

    deps.set_transport(httpx.MockTransport(handler))
    configure(Dhis2Settings(uid_batch_limit=2))
    uids = api.generate_uids("dev", 3)

    assert len(uids) == 3 and len(set(uids)) == 3
    assert all(UID_SHAPE.fullmatch(item) for item in uids)
    assert issued["n"] == 3, seen
    assert all("system/id" in item for item in seen), "补齐只能再问官方接口"


def test_generate_uids_fails_when_the_instance_answers_nothing(directory: Any) -> None:
    """DX-QRY-001：实例一个号都不给时是失败，不是返回空列表让 S2 以为“够用”。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"codes": []})

    deps.set_transport(httpx.MockTransport(handler))
    with pytest.raises(UidFormatInvalidError) as excinfo:
        api.generate_uids("dev", 5)
    assert excinfo.value.details["requested"] == 5
    assert excinfo.value.details["logicalResource"] == "system.id"
    assert excinfo.value.details["environmentKey"] == "dev"


def test_generate_uids_rejects_a_malformed_answer(directory: Any) -> None:
    """DX-QRY-001：畸形号当场拒绝——多年后它以“对象不存在”回来时已经查不清了。"""
    answers = iter([{"codes": ["toolonguidvalue"]}, {"codes": ["1baduidhere"]}, {"codes": []}])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=dict(next(answers, {"codes": []})))

    deps.set_transport(httpx.MockTransport(handler))
    with pytest.raises(UidFormatInvalidError) as excinfo:
        api.generate_uids("dev", 5)
    assert "malformed" in str(excinfo.value)
    assert excinfo.value.details["expected"] == "11 chars, first a letter, alphanumeric"
    assert excinfo.value.details["uid"] in {"toolonguidvalue", "1baduidhere"}


def test_generate_uids_deduplicates_before_counting_them(directory: Any) -> None:
    """DX-QRY-001：重复号不算数——把重复交给 S2 就是两个对象抢一个 UID。"""
    instance = directory.instance("dev")
    pool = [valid_uid(1), valid_uid(2), valid_uid(1), valid_uid(3), valid_uid(2), valid_uid(4)]
    instance.generated_uids = list(pool)
    configure(Dhis2Settings(uid_batch_limit=1))

    uids = api.generate_uids("dev", 4)

    assert uids == [valid_uid(index) for index in (1, 2, 3, 4)]


def test_generate_uids_resolves_the_environment_it_was_given(directory: Any) -> None:
    """DX-BR-02：一次调用绑定一个环境；号是从那台实例要的，不是随便哪台。"""
    dev = directory.instance("dev")
    uat = directory.instance("uat")
    dev.generated_uids = valid_uids(2)
    uat.generated_uids = valid_uids(2)

    assert len(api.generate_uids("uat", 1)) == 1
    assert uat.requests and not dev.requests, "请求跑到了别的环境实例上"


def test_generate_uids_persists_nothing(directory: Any) -> None:
    """DX-QRY-005：只读查询除审计外不落库。"""
    with unit_of_work() as session:
        before = session.query(ExecutionRow).count()
    directory.instance("dev").generated_uids = valid_uids(3)
    api.generate_uids("dev", 3)
    with unit_of_work() as session:
        assert session.query(ExecutionRow).count() == before
    assert directory.instance("dev").imports_accepted == 0
