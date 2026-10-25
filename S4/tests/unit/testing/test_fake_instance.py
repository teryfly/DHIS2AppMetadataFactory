"""内存实例 + transport 的语义（DX-API-001、DX-NFR-05）。

这组测试证明的是**假实例本身可信**：它必须复刻 2.41 那些会让人踩坑的行为，否则“本地全绿、
上线全红”。每条断言都对应一个实测事实（见 ``README.md`` 的“在线实例实测”表）。

真实代码路径也在被测范围内：请求全部经 :class:`Dhis2Client` 发出，因此 URL 拼装、资源目录
解析、pager 翻页、非 JSON 响应的处理、重试与错误映射都真的执行了一遍。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import install_development_backends, store_secret

from factory_dhis2 import deps
from factory_dhis2.client.auth import CredentialProvider
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.config import Dhis2Settings, configure
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.errors import Dhis2AuthError, Dhis2NotFoundError
from factory_dhis2.execution import jobs
from factory_dhis2.profiles import registry
from factory_dhis2.target import EnvironmentTarget
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport

BASE = "https://fake.local/dhis2"


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture(autouse=True)
def _secrets() -> Iterator[None]:
    install_development_backends()
    deps.reset_providers()
    yield
    deps.reset_providers()


def make_target() -> EnvironmentTarget:
    registry.load_builtin_profiles()
    refs = {scope.value: store_secret(f"memory://dx/fake/{scope.value}", "svcacct:district")
            for scope in CredentialScope}
    provider = CredentialProvider(refs, auth_mode=AuthMode.BASIC)
    return EnvironmentTarget(
        key="fake",
        name="Fake",
        base_url=BASE,
        environment_class=EnvironmentClass.DEV,
        auth_mode=AuthMode.BASIC,
        tls_required=True,
        llm_data_policy=LlmDataPolicy.LOCAL_ONLY,
        timeout_seconds=30,
        status=EnvironmentStatus.ACTIVE,
        profile=registry.newest_profile(),
        dhis2_version="2.41.10",
        credentials=provider,
    )


def install(instance: FakeDhis2Instance) -> Dhis2Client:
    """把内存实例接到 transport 上，返回一个真的 Client。"""
    deps.set_transport(FakeDhis2Transport(instance))
    deps.set_sleeper(RecordingSleeper())
    return Dhis2Client(make_target(), scope=CredentialScope.DISCOVERY)


def test_paged_read_walks_the_pager_and_projects_fields() -> None:
    """DX-NFR-02：1,000 个对象靠分页读完，而不是靠一次大请求。"""
    instance = FakeDhis2Instance()
    for index in range(5):
        instance.put("dataElement", f"de{index}", code=f"C{index}", name=f"元素 {index}")
    client = install(instance)
    rows = list(client.list_all("dataElement", fields="id,name", page_size=2))
    assert [row["id"] for row in rows] == [f"de{index}" for index in range(5)]
    assert all("code" not in row for row in rows), "fields=id,name 不该带回 code"


def test_filters_are_applied_like_the_instance_applies_them() -> None:
    """DX-DSC-005：明确对象清单按 code 过滤时，只取命中的那些。"""
    instance = FakeDhis2Instance()
    instance.put("dataElement", "de1", code="WEIGHT", name="体重")
    instance.put("dataElement", "de2", code="HEIGHT", name="身高")
    client = install(instance)
    hit = list(client.list_all("dataElement", fields="id,code", filters=["code:eq:WEIGHT"]))
    assert [row["id"] for row in hit] == ["de1"]
    both = list(client.list_all("dataElement", fields="id", filters=["code:in:[WEIGHT,HEIGHT]"]))
    assert len(both) == 2


def test_me_omits_authorities_unless_fields_are_requested() -> None:
    """2.41 实测：``/api/me`` 不带 ``authorities`` 字段时就是不给。

    如果假实例无条件返回权限，连接测试里“账户一项权限都没有”这条真实故障就永远测不出来。
    """
    instance = FakeDhis2Instance(authorities=["F_DATA_MART.Export", "ALL"])
    client = install(instance)
    bare = client.call("me", params={"fields": "id"}).payload
    assert "authorities" not in bare, bare
    with_fields = client.call(
        "me", params={"fields": "id,authorities[:is],userRoles[id,name]"}
    ).payload
    assert with_fields["authorities"] == ["F_DATA_MART.Export", "ALL"], "授权必须是字符串列表"


def test_uid_generation_returns_exactly_the_requested_count() -> None:
    """DX-QRY-001：UID 由实例官方接口产生。"""
    instance = FakeDhis2Instance(generated_uids=["AAAAAAAAAAA", "BBBBBBBBBBB"])
    client = install(instance)
    payload = client.call("system.id", params={"limit": "2"}).payload
    assert list(payload) == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    more = client.call("system.id", params={"limit": "3"}).payload
    assert len(list(more)) == 3, "队列用完后实例自己续发确定性 UID"


def test_validate_import_does_not_change_the_environment() -> None:
    """AC-EXE-01：VALIDATE 与 COMMIT 的区别在假实例上同样成立。"""
    instance = FakeDhis2Instance()
    instance.put("dataElement", "de1", code="OLD", name="旧名")
    client = install(instance)
    response = client.call(
        "metadata.import",
        scope=CredentialScope.IMPORT,
        body={"dataElements": [{"id": "de1", "name": "新名"}]},
        params={"importMode": "VALIDATE", "identifier": "ASSIGNED"},
    )
    report = response.payload["response"]
    assert report["status"] == "OK", report
    assert instance.objects["dataElement"]["de1"]["name"] == "旧名"
    assert instance.imports_accepted == 1


def test_assigned_uid_for_a_new_object_is_accepted() -> None:
    """2.41.10 实测：``identifier=ASSIGNED`` + 新 UID 的 CREATE 是**成功**路径。

    S2 的 UID 本来就来自实例的 ``system/id``，所以这是元数据部署的正常形态。假实例一开始
    把这条判成 E1134 失败，是我写错的“实测事实”——契约测试把它纠正了，这条测试就是防止它
    再被写回去。
    """
    instance = FakeDhis2Instance()
    client = install(instance)
    payload = client.call(
        "metadata.import",
        scope=CredentialScope.IMPORT,
        body={"dataElements": [{"id": "brandNewUid", "code": "NEW", "name": "新元素"}]},
        params={"importMode": "COMMIT", "identifier": "ASSIGNED", "importStrategy": "CREATE"},
    ).payload
    assert payload["status"] == "OK", payload
    assert payload["response"]["stats"]["created"] == 1, payload["response"]["stats"]
    assert instance.objects["dataElement"]["brandNewUid"]["code"] == "NEW"


def test_a_rejected_import_comes_back_as_409_with_a_full_report() -> None:
    """2.41.10 实测：校验不通过是 **HTTP 409 + 完整报告**，不是异常。

    Client 如果按状态码 raise，逐对象错误就全丢了，DX-EXE-027 的错误映射无从谈起。
    """
    from factory_dhis2.execution.importreport import parse_report

    instance = FakeDhis2Instance()
    client = install(instance)
    response = client.call(
        "metadata.import",
        scope=CredentialScope.IMPORT,
        body={"dataElements": [{"name": "没有标识符"}]},
        params={"importMode": "VALIDATE", "identifier": "ASSIGNED"},
    )
    assert response.status == 409, response.status
    assert response.payload["response"]["status"] == "ERROR", response.payload
    summary = parse_report(response.payload)
    assert summary.passed is False
    assert summary.issues, "逐对象错误必须映射成问题清单"
    assert any("identifier" in issue.description.lower() for issue in summary.issues), [
        issue.description for issue in summary.issues
    ]


def test_commit_import_applies_to_instance_state() -> None:
    """DX-EXE-032：COMMIT 后实例状态真的变了（否则部署后回读验证没有意义）。"""
    instance = FakeDhis2Instance()
    instance.put("dataElement", "de1", code="OLD", name="旧名")
    client = install(instance)
    payload = client.call(
        "metadata.import",
        scope=CredentialScope.IMPORT,
        body={"dataElements": [{"id": "de1", "name": "新名"}]},
        params={"importMode": "COMMIT", "identifier": "ASSIGNED", "importStrategy": "UPDATE"},
    ).payload
    assert payload["response"]["stats"]["updated"] == 1, payload["response"]["stats"]
    assert instance.objects["dataElement"]["de1"]["name"] == "新名"


def test_async_import_is_polled_through_the_job_scoped_task_url() -> None:
    """DX-EXE-033/034 + 2.41 实测：任务 ID 在响应体里，Location 只指向类型集合。

    假实例按实测形状响应（200、Location=…/system/tasks/METADATA_IMPORT、任务查询回日志
    数组），因此这里能真的验证 S4 有没有把“这一次任务”的地址拼对——只存 Location 的实现
    会去轮询整个类型的集合。
    """
    instance = FakeDhis2Instance()
    instance.next_task("job77", completed=False)
    client = install(instance)
    import_client = client.with_scope(CredentialScope.IMPORT)
    submission = jobs.submit(
        import_client,
        resource="metadata.import",
        body={"dataElements": [{"id": "de1", "name": "新"}]},
        params={"importMode": "COMMIT", "async": "true"},
        use_async=True,
    )
    assert submission.async_import
    assert submission.job_ref == "job77", submission.job_ref
    assert submission.poll_url and submission.poll_url.endswith("/system/tasks/METADATA_IMPORT/job77")
    assert submission.poll_url.startswith(BASE), submission.poll_url
    # 未跑完：日志里没有 completed=true 的条目，轮询不能提前收工
    pending = client.call(
        "system.tasks.one", path_args={"taskPath": "system/tasks/METADATA_IMPORT/job77"}
    ).payload
    assert isinstance(pending, list) and not any(e.get("completed") for e in pending)
    instance.tasks["job77"].completed = True
    instance.tasks["job77"].report = {"status": "SUCCESS", "imported": 2, "updated": 1}
    response = jobs.poll(import_client, submission, timeout_seconds=5.0)
    assert response.ok
    report = jobs.report_of(response)
    assert report["status"] == "SUCCESS", report
    assert (report["created"], report["updated"]) == (2, 1), report
    assert report["reportSource"] == "TASK_LOG", "异步报告必须标明它来自任务日志"
    assert isinstance(report["detailedMessages"], list) and report["detailedMessages"]


def test_async_failure_is_reported_from_the_terminal_log_level() -> None:
    """DX-EXE-035：异步任务失败也必须有可判定的结论，而不是“轮询成功=没问题”。"""
    instance = FakeDhis2Instance()
    instance.next_task(
        "job88", completed=True, report={"status": "ERROR", "ignored": 3}
    )
    client = install(instance)
    submission = jobs.submit(
        client.with_scope(CredentialScope.IMPORT),
        resource="metadata.import",
        body={"dataElements": [{"id": "de1"}]},
        params={"importMode": "COMMIT", "async": "true"},
        use_async=True,
    )
    response = jobs.poll(client.with_scope(CredentialScope.IMPORT), submission, timeout_seconds=5.0)
    report = jobs.report_of(response)
    assert report["status"] == "ERROR", report
    assert report["ignored"] == 3, report


def test_unknown_type_is_refused_by_the_profile_not_by_guessing_a_path() -> None:
    """DX-CLI-005：目录里没有的逻辑资源就是不支持，不发一个猜出来的请求。"""
    from factory_dhis2.errors import VersionUnsupportedError

    instance = FakeDhis2Instance()
    client = install(instance)
    with pytest.raises(VersionUnsupportedError):
        client.call("metadata.read", dhis2_type="somethingAbsent")
    assert instance.requests == [], "拒绝必须发生在任何请求之前"


def test_html_error_body_is_reported_as_not_found_with_the_status() -> None:
    """实例在错误上回 HTML 而不是 JSON：Client 必须给出可诊断的错误而不是解析异常。"""
    instance = FakeDhis2Instance()
    instance.put("dataElement", "de1", code="A", name="甲")
    instance.fail_next("dataElements", 404)
    client = install(instance)
    with pytest.raises(Dhis2NotFoundError) as excinfo:
        client.call("metadata.read", dhis2_type="dataElement", params={"fields": "id"})
    assert excinfo.value.details.get("status") == 404, excinfo.value.details


def test_unauthorized_credentials_map_to_the_auth_error() -> None:
    """DX-CLI-002：401 是认证问题，不是“环境里没有这个对象”。"""
    instance = FakeDhis2Instance()
    instance.fail_next("me", 401)
    client = install(instance)
    with pytest.raises(Dhis2AuthError):
        client.call("me")


def test_service_unavailable_is_retried_only_for_reads() -> None:
    """DX-CLI-003：读请求按指数退避重试；假实例用计数脚本模拟抖动。"""
    instance = FakeDhis2Instance()
    instance.put("dataElement", "de1", code="A", name="甲")
    instance.fail_next("dataElements", 503, times=2)
    sleeper = RecordingSleeper()
    deps.set_transport(FakeDhis2Transport(instance))
    deps.set_sleeper(sleeper)
    deps.set_clock(None)
    configure(Dhis2Settings(retry_max_attempts=4, retry_backoff_base_seconds=0.25))
    client = Dhis2Client(make_target(), scope=CredentialScope.DISCOVERY)
    rows = list(client.list_all("dataElement", fields="id"))
    assert [row["id"] for row in rows] == ["de1"]
    assert sleeper.delays == [0.25, 0.5], sleeper.delays


def test_scripted_failure_is_consumed_only_by_matching_paths() -> None:
    """脚本化干预按路径片段匹配：坏一个端点不能把整个实例的读都打挂。"""
    from factory_dhis2.errors import Dhis2UnreachableError

    instance = FakeDhis2Instance()
    instance.fail_next("dataElement", 500, times=99)
    client = install(instance)
    assert client.call("system.info").payload["version"] == instance.version
    with pytest.raises(Dhis2UnreachableError):
        # 500 对读请求可重试（DX-CLI-003），所以这里是“重试到次数上限后仍失败”
        client.call("metadata.read", dhis2_type="dataElement", params={"fields": "id"})


def test_import_report_can_be_forced_for_a_test() -> None:
    """DX-EXE-027：报告映射需要特定形状时用 queue_import_report，而不是改实例逻辑。

    给的是**内层** ImportReport；外层信封由假实例按实测形状包好，这样测试给的形状与真实
    实例给的一致，客户端看到的也一致。
    """
    instance = FakeDhis2Instance()
    instance.queue_import_report(
        {"responseType": "ImportReport", "status": "OK", "stats": {"created": 3, "total": 3}}
    )
    client = install(instance)
    payload = client.call(
        "metadata.import",
        scope=CredentialScope.IMPORT,
        body={"dataElements": [{"id": "de1"}]},
        params={"importMode": "VALIDATE"},
    ).payload
    assert payload["httpStatus"] == "OK", payload
    assert payload["response"]["stats"] == {"created": 3, "total": 3}, payload["response"]


def test_transport_exposes_the_parsed_query_and_body() -> None:
    """请求记录让测试能断言“到底发了什么”，这是 DX-CON-003 只读性的检查手段。"""
    instance = FakeDhis2Instance()
    client = install(instance)
    client.call("me", params={"fields": "id"})
    client.call(
        "metadata.import",
        scope=CredentialScope.IMPORT,
        body={"dataSets": [{"id": "ds1"}]},
        params={"importMode": "VALIDATE"},
    )
    recorded: list[dict[str, Any]] = instance.requests
    assert [item["method"] for item in recorded] == ["GET", "POST"]
    assert recorded[1]["body"] == {"dataSets": [{"id": "ds1"}]}
    assert recorded[1]["query"]["importMode"] == "VALIDATE"
    assert all(item["path"] != "metadata" or item["method"] != "GET" for item in recorded)
