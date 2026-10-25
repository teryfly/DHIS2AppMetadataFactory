"""UID / code 存在性检查（DX-QRY-002/003、DX-QRY-005、总体 SRS FR-REG-007/008）。

这里最贵的一件事是**不要把“查不到”当成“不存在”**：2.41 的 ``identifiableObjects`` 接口
实测可以对确实存在的 UID 返回空集合（在线契约测试记下的事实），照着它的答案分号就会撞号。
所以 S4 在该接口整体答“无”时必须退回目录扫描，并把用了哪条依据写进 ``basis``。

另一半要证明的是“只读”：四个查询除审计外不落库、不发写请求、而且只用 discovery 凭证
范围（DX-QRY-005、DX-BR-03）。用 import 范围的身份去做一次只读查询，看起来结果一样，
实际是把服务账户的权限边界抹掉了。
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from factory_foundation.api import unit_of_work

from factory_dhis2 import api, deps
from factory_dhis2.enums import CredentialScope
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.profiles import registry
from factory_dhis2.testing.instance import FakeDhis2Instance
from tests.unit.queries.conftest import InstanceDirectory, valid_uids

EXISTED = "FTRrcoaog83"
ABSENT = "Qw9zZ3kBwLk"
DISCOVERY_HEADER = "Basic " + base64.b64encode(b"svcacct-reader:only-discovery").decode()


def _sweep_requests(directory: InstanceDirectory, key: str = "dev") -> list[dict[str, Any]]:
    return [
        row
        for row in directory.requests(key)
        if "id:in:[" in str(row["query"].get("filter", ""))
    ]


# --- DX-QRY-002：UID 存在性 ------------------------------------------------


def test_uid_exists_does_not_trust_identifiable_objects(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-002：接口对存在的 UID 返回空集时，退回目录扫描，结论仍是“已占用”。"""
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")

    answer = api.uid_exists("dev", [EXISTED])[EXISTED]

    assert bool(answer) is True, "把已占用的号当成可用，S2 就会把它再分配出去"
    assert answer["basis"] == "catalogue-sweep"
    assert answer["dhis2Type"] == "dataElement"
    assert answer["code"] == "WEIGHT"
    assert answer["name"] == "体重"
    asked = [row["path"] for row in directory.requests("dev")]
    assert "identifiableObjects" in asked, "先问官方接口，扫描只是兜底"
    assert _sweep_requests(directory), "扫描必须真的发生"


def test_uid_exists_records_the_basis_it_used(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-002：两条路径的结论都要写出依据，调用方看得见结论是怎么来的。"""
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    answers = api.uid_exists("dev", [EXISTED, ABSENT])

    assert dict(answers)[EXISTED] and not dict(answers)[ABSENT]
    assert answers[EXISTED]["basis"] == "catalogue-sweep"
    # 接口对"没占用"不再逐号发问：完整扫过目录后按 catalogue-sweep-none 给出结论
    assert answers[ABSENT]["basis"] == "catalogue-sweep-none"
    assert answers[ABSENT]["exists"] is False
    assert answers[ABSENT]["dhis2Type"] is None
    # 非空 dict 但假值：这正是 UidOccupancy 必须是 dict 子类而不是普通 dict 的理由
    assert len(answers[ABSENT]) == 5 and not answers[ABSENT]


def test_uid_exists_asks_once_per_batch_not_once_per_uid(
    directory: Any, dev: FakeDhis2Instance
) -> None:
    """DX-QRY-002 / DX-NFR-02：一批 UID 一次请求，不是一个 UID 一次请求。

    Registry 一次可能核几百个号；逐个问会把"核存在性"变成部署前的第一个瓶颈。
    """
    uids = [EXISTED, *valid_uids(120)]
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    answers = api.uid_exists("dev", uids)

    rows = [row for row in directory.requests("dev") if row["path"] == "identifiableObjects"]
    from factory_dhis2.config import get_settings

    batch = get_settings().presence_batch_size
    assert len(rows) == -(-len(uids) // batch), [row["query"]["criterion"] for row in rows]
    assert rows[0]["query"]["criterion"].startswith(f"id:in:[{uids[0]}")
    assert rows[0]["query"]["paging"] == "false"
    assert "objectType" in rows[0]["query"]["fields"]
    assert answers[EXISTED]["exists"] is True


def test_uid_exists_sweeps_the_catalogue_once_per_type(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-NFR-02：扫描按类型批量过滤，而不是一个 UID 一次请求。"""
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    answers = api.uid_exists("dev", [EXISTED, ABSENT])

    supported = registry.profile_for_version("2.41.10").supported_types
    sweeps = _sweep_requests(directory)
    assert len(sweeps) == len(supported), "每个类型一次批量过滤，不多不少"
    assert {EXISTED, ABSENT} in [
        set(sweep["query"]["filter"].removeprefix("id:in:[").rstrip("]").split(","))
        for sweep in sweeps
    ], "整批 UID 必须一起问出去"
    for sweep in sweeps:
        asked = set(sweep["query"]["filter"].removeprefix("id:in:[").rstrip("]").split(","))
        assert asked <= {EXISTED, ABSENT}
    assert answers[EXISTED]["exists"] is True


def test_uid_exists_stops_sweeping_once_the_batch_is_accounted_for(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-NFR-02：全部命中就立刻收工，扫描不是每次都把目录走一遍。"""
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    assert bool(api.uid_exists("dev", [EXISTED])[EXISTED]) is True

    sweeps = _sweep_requests(directory)
    paths = [row["path"] for row in sweeps]
    assert paths[-1] == "dataElements", "找到之后就该停下"
    supported = registry.profile_for_version("2.41.10").supported_types
    assert len(sweeps) < len(supported), "只有一个 UID 时不该扫完整个目录"


def test_uid_exists_survives_a_missing_identifiable_objects_endpoint(
    directory: Any, dev: FakeDhis2Instance
) -> None:
    """DX-QRY-002：端点整个不存在（旧版本）也只是少一条路径，不是查不出来。"""
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    dev.fail_next("identifiableObjects", 404)
    answer = api.uid_exists("dev", [EXISTED])[EXISTED]
    assert bool(answer) is True
    assert answer["basis"] == "catalogue-sweep"


def test_uid_exists_trusts_the_endpoint_when_it_answers(directory: Any) -> None:
    """DX-QRY-002：接口给出答案时就用它，不做无谓的全目录扫描。"""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/api/identifiableObjects.json"):
            return httpx.Response(
                200,
                json={
                    "identifiableObjects": [
                        {"id": EXISTED, "code": "WEIGHT", "name": "体重", "objectType": "dataElement"}
                    ]
                },
            )
        return httpx.Response(200, json={})

    deps.set_transport(httpx.MockTransport(handler))
    answer = api.uid_exists("dev", [EXISTED])[EXISTED]

    assert bool(answer) is True
    assert answer["basis"] == "identifiableObjects"
    assert answer["dhis2Type"] == "dataElement" and answer["code"] == "WEIGHT"
    assert paths == ["/api/identifiableObjects.json"], "接口已答出就不该再扫目录"


def test_uid_exists_is_empty_input_free(directory: Any) -> None:
    """DX-QRY-002：空清单不发任何请求。"""
    assert api.uid_exists("dev", []) == {}
    assert directory.total_requests == 0


def test_uid_exists_resolves_per_environment(directory: Any) -> None:
    """DX-QRY-002 / DX-BR-02：存在性是环境级事实，同一个号在 dev 占用不等于在 uat 也占用。"""
    directory.instance("dev").put("dataElement", EXISTED, code="W-DEV", name="开发里的对象")

    in_dev = api.uid_exists("dev", [EXISTED])[EXISTED]
    in_uat = api.uid_exists("uat", [EXISTED])[EXISTED]

    assert bool(in_dev) is True and in_dev["code"] == "W-DEV"
    assert bool(in_uat) is False and in_uat["code"] is None
    assert directory.instance("dev").requests and directory.instance("uat").requests


# --- DX-QRY-003：code 占用 --------------------------------------------------


def test_code_exists_reports_the_owning_uid(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-003：按类型过滤 code，命中时给出 UID 与名称。"""
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    answers = api.code_exists("dev", "dataElement", ["WEIGHT", "HEIGHT"])

    assert dict(answers)["WEIGHT"] and answers["WEIGHT"]["uid"] == EXISTED
    assert answers["WEIGHT"]["name"] == "体重"
    assert answers["WEIGHT"]["basis"] == "metadata.read"
    assert not dict(answers)["HEIGHT"] and answers["HEIGHT"]["exists"] is False
    filters = [str(row["query"]["filter"]) for row in directory.requests("dev")]
    # 一批 code 一次请求：2.41 实测支持 code:in:[A,B]
    assert filters == ["code:in:[WEIGHT,HEIGHT]"]


def test_code_exists_of_an_unknown_type_conservatively_says_taken(directory: Any) -> None:
    """DX-QRY-003：目录里没有这个类型就判不了，宁可报“已占用”让调用方换 code。"""
    answers = api.code_exists("dev", "notInAnyCatalogue", ["A", "B"])

    assert len(answers) == 2
    assert all(bool(item) is True for item in answers.values())
    assert all("not in the resource catalogue" in item["basis"] for item in answers.values())
    assert directory.total_requests == 0, "判不了也不该去猜一个端点试写"


def test_code_exists_with_an_empty_list_asks_nothing(directory: Any) -> None:
    """DX-QRY-003：空清单不发任何请求。"""
    assert api.code_exists("dev", "dataElement", []) == {}
    assert directory.total_requests == 0


def test_code_exists_finds_a_code_beyond_the_first_page(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-NFR-02：命中与否按实例的过滤条件判定，不依赖“第一页里有没有”。"""
    for index in range(3):
        dev.put("dataElement", f"de{index}", code=f"C{index}", name=f"元素 {index}")
    answers = api.code_exists("dev", "dataElement", ["C2"])
    assert bool(answers["C2"]) is True and answers["C2"]["uid"] == "de2"


# --- DX-QRY-005：只读、不落库、只用 discovery 范围 --------------------------


def test_queries_never_write_to_the_instance_or_the_database(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-QRY-005：S2→S4 的只读通道除审计外不产生任何状态。"""
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    dev.generated_uids = valid_uids(2)
    with unit_of_work() as session:
        executions_before = session.query(ExecutionRow).count()

    assert api.generate_uids("dev", 2) == valid_uids(2)
    api.uid_exists("dev", [EXISTED, ABSENT])
    api.code_exists("dev", "dataElement", ["WEIGHT"])
    api.has_associated_data("dev", "trackedEntityAttribute", EXISTED)

    with unit_of_work() as session:
        assert session.query(ExecutionRow).count() == executions_before, "只读查询写了执行行"
    methods = {row["method"] for row in dev.requests}
    assert methods == {"GET"}, "只读通道发出了写请求"
    assert dev.imports_accepted == 0 and dev.deletes_accepted == 0


def test_queries_use_only_the_discovery_credential(directory: Any, dev: FakeDhis2Instance) -> None:
    """DX-BR-03 / FR-SEC-007：一次只读查询不得借用 import 范围的身份。"""
    seen: list[str] = []

    class Capturing(httpx.BaseTransport):
        def __init__(self, inner: httpx.BaseTransport) -> None:
            self._inner = inner

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization", ""))
            return self._inner.handle_request(request)

    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    dev.generated_uids = valid_uids(1)
    deps.set_transport(Capturing(directory.transport()))

    assert api.generate_uids("dev", 1) == valid_uids(1)
    api.uid_exists("dev", [EXISTED])
    api.code_exists("dev", "dataElement", ["WEIGHT"])
    api.has_associated_data("dev", "trackedEntityAttribute", EXISTED)

    assert seen, "查询根本没访问实例"
    assert set(seen) == {DISCOVERY_HEADER}
    assert CredentialScope.IMPORT.value not in "".join(seen)


def test_queries_refuse_an_unregistered_environment(directory: Any) -> None:
    """DX-ENV-004：没有登记过的环境就是没有，不猜地址也不猜凭证。"""
    from factory_dhis2.errors import EnvironmentInvalidError

    with pytest.raises(EnvironmentInvalidError) as excinfo:
        api.uid_exists("never-registered", [EXISTED])
    assert excinfo.value.details["environmentKey"] == "never-registered"
    assert directory.total_requests == 0


# --- 判不出来时的方向（DX-QRY-002/003 的安全侧）------------------------------


def test_an_inconclusive_sweep_says_taken_rather_than_free(
    directory: Any, dev: FakeDhis2Instance
) -> None:
    """扫描没做完时把未知 UID 报成"已占用"：宁可换号，也不能撞号后导入失败。"""
    dev.fail_next("dataElements", 500, times=40)
    answer = api.uid_exists("dev", [ABSENT])[ABSENT]
    assert answer["exists"] is True
    assert str(answer["basis"]).startswith("inconclusive")


def test_a_failed_code_read_says_taken_rather_than_absent(
    directory: Any, dev: FakeDhis2Instance
) -> None:
    """同一方向也适用于 code：读失败不等于"这个 code 还没人用"。"""
    dev.fail_next("dataElements", 500, times=3)
    answer = api.code_exists("dev", "dataElement", ["NEVER_SEEN"])["NEVER_SEEN"]
    assert answer.exists is True
    assert answer["basis"] == "inconclusive:read-failed"


def test_a_clean_sweep_still_reports_a_uid_as_free(directory: Any, dev: FakeDhis2Instance) -> None:
    """反过来也别夸大：目录扫完确实没有，就是说"未占用"。"""
    answer = api.uid_exists("dev", [ABSENT])[ABSENT]
    assert answer.exists is False
    assert answer["basis"] == "catalogue-sweep-none"


def test_uid_and_code_batches_dedupe_input(directory: Any, dev: FakeDhis2Instance) -> None:
    """重复输入不重复发问，也不丢结果。"""
    dev.put("dataElement", EXISTED, code="WEIGHT", name="体重")
    answers = api.uid_exists("dev", [EXISTED, EXISTED, ABSENT])
    assert list(answers) == [EXISTED, ABSENT]
    by_code = api.code_exists("dev", "dataElement", ["WEIGHT", "WEIGHT"])
    assert list(by_code) == ["WEIGHT"] and by_code["WEIGHT"].exists is True


def test_a_type_without_an_endpoint_is_a_complete_answer_not_a_failure(
    directory: Any, dev: FakeDhis2Instance
) -> None:
    """2.41.10 实测：目录里部分类型该实例没有端点（404），这是完整否定而非"扫不成"。

    把 404 算成失败会让每一个不存在的 UID 都被保守报成"已占用"，UID 池瞬间自满。
    """
    dev.fail_next("dataElements", 404, times=5)
    answer = api.uid_exists("dev", [ABSENT])[ABSENT]
    assert answer.exists is False, answer
    assert answer["basis"] == "catalogue-sweep-none"


def test_a_missing_type_endpoint_does_not_make_a_code_look_taken(
    directory: Any, dev: FakeDhis2Instance
) -> None:
    """同一口径用在 code 上。"""
    dev.fail_next("optionSets", 404, times=3)
    answer = api.code_exists("dev", "optionSet", ["NEVER_SEEN"])["NEVER_SEEN"]
    assert answer.exists is False
    assert answer["basis"] == "metadata.read-none"
