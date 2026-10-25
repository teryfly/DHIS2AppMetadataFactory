"""在线 DHIS2 契约测试（DX-NFR-05）。

这里证明的不是“代码自洽”，而是“我们对 2.41 的假设与真实实例一致”（0.2 权威顺序第 1、
2 条：实例能力优先于我们的假设）。假实例能永远通过，它不能证明 ``system/id`` 真的是
UID 生成端点——在线测试可以。
"""

from __future__ import annotations

import pytest
from tests.contract import live_run
from tests.contract.conftest import commits_allowed, idem, unique_code

pytestmark = pytest.mark.contract


def test_version_is_identified_through_system_info(live_environment: str) -> None:
    """版本识别走 ``system.info``：实测该实例不暴露 ``/api/about``。

    ``requiredScopes`` 必须带上 ``import``。这一条只关心版本，看上去只要 discovery 就够，
    但它写下的那一行 ``dx.connection_test`` 是后面每条用例读到的**最新**结果（DX-EXE-012 的
    权威来源就是"最近一次连接测试覆盖了哪些范围"）。只测 discovery 会让紧随其后的 VALIDATE /
    COMMIT 判成"import 范围从未验证过"而 BLOCKING——一条读版本的用例悄悄毒死了写数据的用例。
    """
    from factory_dhis2.api import test_connection

    check = test_connection(live_environment, ["discovery", "import"], idempotency_key=idem("conn:1"))
    assert check.version is not None and check.version.startswith("2.41"), check.checks
    assert check.ok is True, check


def test_capabilities_are_probed_and_persisted(live_environment: str) -> None:
    """DX-CAP-003：探测结果合成为 CAPABILITY_PROFILE 产物并持久化。"""
    from factory_dhis2.api import probe_capabilities
    from factory_dhis2.artifacts import read_json

    artifact_id = probe_capabilities(live_environment, idempotency_key=idem("cap:1"))
    assert artifact_id
    document = read_json(artifact_id)
    assert document["profileVersion"] == "Dhis241Profile"
    assert document["expressionGrammarVersion"] == "dhis2-2.41"
    names = {item["logicalName"] for item in document["resources"]}
    for required in ("system.info", "metadata.import", "system.id", "identifiableObjects"):
        assert required in names, required


def test_the_runtime_catalogue_is_the_persisted_one_not_the_bundled_baseline(
    live_environment: str,
) -> None:
    """DX-CLI-005：端点与字段元数据以环境**已持久化**的 Capability Profile 为准。

    随包的 2.41 基线里 ``dataElement.properties`` 是**空的**——离线时没人能知道这个实例真实
    的字段清单与 readOnly 标记；只有探测能把它们填上。所以「运行期读到非空 properties」就是
    这一份目录来自产物的证据（而不是来自代码里打包的那一份）。
    """
    from factory_foundation.api import unit_of_work

    from factory_dhis2.api import probe_capabilities
    from factory_dhis2.capability.runtime_profile import apply_environment_capability
    from factory_dhis2.capability.service import CORE_SCHEMA_TYPES
    from factory_dhis2.enums import CredentialScope
    from factory_dhis2.environment.loader import build_target

    artifact_id = probe_capabilities(live_environment, idempotency_key=idem("cap:3"))
    assert artifact_id
    with unit_of_work() as session:
        target = build_target(session, live_environment, scope_hint=CredentialScope.DISCOVERY)
    assert target.capability_profile_artifact_id == artifact_id

    bundled = target.profile.object_schema("dataElement")
    resolved_profile = apply_environment_capability(target).profile
    resolved = resolved_profile.object_schema("dataElement")
    assert bundled is not None and resolved is not None
    assert not bundled.properties, "基线本就没有字段清单（它不该被当成实例事实）"
    assert resolved.properties, "运行期目录应带实例真实字段元数据"
    assert resolved.properties["name"]["readOnly"] is False
    # 目录整体保留：产物只同步了核心那几类的 Schema，类型目录一类都不能少
    assert set(resolved_profile.supported_types) == set(target.profile.supported_types)
    # 同步过的类型也要保住项目决定：实例 Schema 里没有 forbidden/controlableFields 这个概念
    for dhis2_type in CORE_SCHEMA_TYPES:
        baseline = target.profile.object_schema(dhis2_type)
        if baseline is None:
            continue
        item = resolved_profile.object_schema(dhis2_type)
        assert item is not None, dhis2_type
        assert item.forbidden == baseline.forbidden, dhis2_type
        assert item.controlable_fields == baseline.controlable_fields, dhis2_type


def test_the_schema_pack_covers_the_whole_catalogue(live_environment: str) -> None:
    """DX-CAP-001：Schema 包同步**目录里的每一类**，不是只挑"核心的那十类"。

    S2 只读产物、不许再猜：产物里没有的类型，它的编译与校验就只能当"这类型没有字段清单"。
    实例答不出的那几类要看得见地记在 ``probed.unavailableResources`` 里，而不是静默缺席。

    复用 ``idem("cap:1")``：那一次探测已经把 34 类都取回来了，重放拿到同一份产物——这条
    测试要证明的是产物的**宽度**，不需要再花三十几次请求打实例。
    """
    from factory_dhis2.api import probe_capabilities
    from factory_dhis2.artifacts import read_json
    from factory_dhis2.capability.service import default_schema_types
    from factory_dhis2.profiles import registry

    registry.load_builtin_profiles()
    catalogue = set(default_schema_types(registry.profile_for_version("2.41.10")))

    artifact_id = probe_capabilities(live_environment, idempotency_key=idem("cap:1"))
    document = read_json(artifact_id)

    synced = {item["dhis2Type"] for item in document["schemas"]}
    unavailable = {
        item.split(":", 1)[1]
        for item in (document.get("probed") or {}).get("unavailableResources") or []
        if item.startswith("schemas:")
    }
    assert synced | unavailable == catalogue, (
        "每一类要么同步到 Schema，要么被记成实例答不出——不能不明不白地缺席"
    )
    assert len(synced) >= 20, f"这台实例至少该答出 20 类，实际 {sorted(synced)}"
    data_element = next(item for item in document["schemas"] if item["dhis2Type"] == "dataElement")
    assert data_element["properties"], "Schema 的意义是字段清单，不是类型名"
    assert data_element["controlableFields"], "可受控字段是项目决定，必须随产物发布"


def test_freshness_reports_valid_then_version_change_is_detected(live_environment: str) -> None:
    """DX-CAP-006：新鲜度检查在刚探测后必须是 VALID。"""
    from factory_dhis2.api import check_capability_freshness, probe_capabilities

    probe_capabilities(live_environment, idempotency_key=idem("cap:2"))
    freshness = check_capability_freshness(live_environment)
    assert freshness.state == "VALID", freshness.model_dump()


def test_metadata_discovery_reads_real_objects(
    live_environment: str,
    demo_baseline: live_run.DemoBaseline,
    live_credentials_holder: tuple[str, str],
) -> None:
    """DX-DSC-001/002：按类型发现既有元数据，索引带 lastUpdated 与内容哈希。

    这里读到的都是实例自带的演示对象，所以顺手记下它们的内容指纹（FR-TST-002② / DX-AC-15）：
    本次运行结束时再取一次，对不上就说明契约测试改了不属于它自己的对象——在公开共享实例上，
    那是别人的演示数据。
    """
    from factory_dhis2.api import discover

    outcome = discover(
        live_environment,
        None,
        {"dhis2Types": ["dataElement"], "pageSize": 20},
        None,
        idempotency_key=idem("dsc:1"),
    )
    assert outcome.discovery_result_artifact_id
    assert outcome.object_count > 0, "该实例带有演示元数据，发现结果不应为空"
    assert not outcome.blocking_issues

    sampled = _first_demo_objects(live_environment, outcome, limit=5)
    for label, document in sampled:
        demo_baseline.remember(label, document)
    assert sampled, "没有可核对的既有对象，就无法证明本次运行没有改动它们"


def test_default_objects_are_readable(live_environment: str) -> None:
    """DX-DSC-003：default CategoryCombo 等四个对象是 Greenfield 发现的底线。"""
    from factory_dhis2.api import discover

    outcome = discover(
        live_environment,
        None,
        {"greenfield": True},
        None,
        idempotency_key=idem("dsc:2"),
    )
    assert outcome.default_object_ids.get("categoryCombo"), outcome.default_object_ids


def test_generated_uids_are_valid_and_distinct(live_environment: str) -> None:
    """DX-AC-08 / DX-QRY-001：官方接口批量生成的 UID 形态正确且互不相同。"""
    from factory_dhis2.api import generate_uids

    uids = generate_uids(live_environment, 500)
    assert len(uids) == 500
    assert len(set(uids)) == 500
    for uid in uids:
        assert len(uid) == 11, uid
        assert uid[0].isalpha() and uid.isalnum(), uid


def test_uid_existence_uses_real_objects(live_environment: str) -> None:
    """DX-QRY-002：真实存在的 UID 报“已占用”，并带上类型与 code。"""
    from factory_dhis2.api import generate_uids, uid_exists

    probe = generate_uids(live_environment, 1)[0]
    absent = uid_exists(live_environment, [probe])
    assert bool(absent[probe]) is False

    from factory_dhis2.api import discover

    outcome = discover(
        live_environment,
        None,
        {"dhis2Types": ["dataElement"]},
        None,
        idempotency_key=idem("dsc:3"),
    )
    assert outcome.discovery_result_artifact_id
    # 从发现结果里取一个确实存在的 UID 再查一次
    from factory_dhis2.artifacts import read_json

    document = read_json(outcome.discovery_result_artifact_id)
    objects = [item for item in document.get("objects", []) if item.get("uid")]
    assert objects, "发现结果里应有可寻址的对象"
    known = objects[0]["uid"]
    occupied = uid_exists(live_environment, [known])
    assert bool(occupied[known]) is True
    assert occupied[known]["dhis2Type"] or occupied[known]["code"], occupied[known]


@pytest.mark.skipif(
    not commits_allowed(),
    reason="写入公开共享实例需要 FACTORY_DX_LIVE_ALLOW_COMMITS=1",
)
def test_validate_changes_nothing(live_environment: str) -> None:
    """AC-EXE-01 / DX-EXE-026：VALIDATE 之后目标环境没有新对象。"""
    from factory_dhis2.api import prepare_and_validate
    from factory_dhis2.testing.contract_packages import build_single_object_package

    code = unique_code("validate")
    artifact_id = build_single_object_package(
        environment_key=live_environment, code=code, name=f"Contract validate {code}"
    )
    outcome = prepare_and_validate(
        live_environment,
        "prj-contract",
        artifact_id,
        None,
        operator="contract-test",
        idempotency_key=idem(f"validate:{code}"),
    )
    assert outcome.passed is True, outcome.issues
    assert outcome.import_report_artifact_id

    # 真的没写进去：按 code 再查一次
    from factory_dhis2.api import code_exists

    found = code_exists(live_environment, "dataElement", [code])
    assert bool(found[code]) is False, "VALIDATE 改变了目标环境，AC-EXE-01 被破坏"


@pytest.mark.skipif(
    not commits_allowed(),
    reason="写入公开共享实例需要 FACTORY_DX_LIVE_ALLOW_COMMITS=1",
)
def test_commit_then_post_verify(
    live_environment: str, created: live_run.Created
) -> None:
    """DX-NFR-05 全链：VALIDATE 通过后 COMMIT，再读回比对（AC-EXE-06）。"""
    from factory_dhis2.api import (
        code_exists,
        commit,
        post_verify,
        prepare_and_validate,
    )
    from factory_dhis2.testing.contract_packages import build_single_object_package

    code = unique_code("commit")
    artifact_id = build_single_object_package(
        environment_key=live_environment, code=code, name=f"Contract commit {code}"
    )
    validation = prepare_and_validate(
        live_environment,
        "prj-contract",
        artifact_id,
        None,
        operator="contract-test",
        idempotency_key=idem(f"validate:{code}"),
    )
    assert validation.passed is True, validation.issues

    committed = commit(
        live_environment,
        "prj-contract",
        artifact_id,
        validation.payload_hash or "",
        operator="contract-test",
        idempotency_key=idem(f"commit:{code}"),
    )
    assert committed.status == "COMPLETED", committed.model_dump()
    assert committed.applied is True

    exists = code_exists(live_environment, "dataElement", [code])
    assert bool(exists[code]) is True, f"COMMIT 报告生效但实例上没有 {code}"

    verified = post_verify(
        live_environment,
        "prj-contract",
        artifact_id,
        committed.execution_id,
        operator="contract-test",
        idempotency_key=idem(f"verify:{code}"),
    )
    assert verified.consistent is True, verified.issues
    assert verified.report_artifact_id

    # ③ this run owns what it wrote: register it so the module teardown deletes it and
    # nobody has to guess later. An unregistered object would sit on the public instance
    # forever, which is the failure mode FR-TST-002③ exists to prevent.
    uid = _uid_of_code(live_environment, "dataElement", code)
    assert uid, f"COMMIT 生效却查不到 {code} 的 uid，清理就无从谈起"
    created.remember("dataElement", uid=uid, code=code)


def test_logical_resource_paths_match_the_live_instance(live_environment: str) -> None:
    """资源目录的每个读端点在真实例上都存在（DX-CLI-005 的反向验证）。

    这一条是本轮实测得到的教训：``reserved/ids`` 与 ``about`` 在 2.41 上并不存在，
    只有 ``system/id`` 与 ``system/info`` 在。假实例永远测不出这种差别。
    """
    from factory_foundation.api import unit_of_work

    from factory_dhis2.api import get_environment
    from factory_dhis2.client.session import Dhis2Client
    from factory_dhis2.enums import CredentialScope
    from factory_dhis2.environment.loader import build_target

    assert get_environment(live_environment) is not None
    with unit_of_work() as session:
        target = build_target(session, live_environment)
    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    from factory_dhis2.client.resources import resolve

    catalogue = target.profile
    for logical_name in ("system.info", "system.id", "me", "identifiableObjects", "schemas"):
        declared = catalogue.require_resource(logical_name)
        shown = client.url_for(resolve(catalogue, logical_name, dhis2_type="dataElement"))
        kwargs: dict[str, object] = {}
        if logical_name == "system.id":
            kwargs["params"] = {"limit": "1"}
        if "{" in declared.path:
            # 带槽的路径必须由调用点提供类型；否则解析层会明确报缺槽，而不是发一个错 URL
            kwargs["dhis2_type"] = "dataElement"
        response = client.call(logical_name, **kwargs)  # type: ignore[arg-type]
        assert response.status == 200, f"{logical_name} → {shown} 返回 {response.status}"


def test_pager_shape_is_page_count_not_pages(live_environment: str) -> None:
    """2.41 实测：``pager`` 给的是 ``pageCount``/``total``，没有 ``pages``/``isLast``。

    ``list_all`` 的终止条件因此必须是 ``page >= pageCount``。这一条假实例测不出来：pager 的
    字段名是实例定的，写错就会在多页集合上悄悄漏读对象（DX-NFR-02）。
    """
    from factory_foundation.api import unit_of_work

    from factory_dhis2.client.session import Dhis2Client
    from factory_dhis2.enums import CredentialScope
    from factory_dhis2.environment.loader import build_target

    with unit_of_work() as session:
        target = build_target(session, live_environment)
    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    first = client.call(
        "metadata.read",
        dhis2_type="dataElement",
        params={"fields": "id", "page": "1", "pageSize": "2", "totalPages": "true"},
    ).payload
    pager = first.get("pager") or {}
    assert "pageCount" in pager, sorted(pager)
    assert "pages" not in pager and "isLast" not in pager, sorted(pager)
    assert int(pager["pageCount"]) >= 2, "该实例的演示元数据足够多页，用来验证翻页"
    walked = list(client.list_all("dataElement", fields="id", page_size=2, limit=5))
    assert len(walked) == 5, len(walked)
    assert len({row["id"] for row in walked}) == 5, "翻页不能重复返回同一批对象"


def test_async_import_exposes_a_task_log_and_s4_synthesizes_the_report(
    live_environment: str,
) -> None:
    """2.41 实测：异步元数据导入不给 ImportReport，只给任务日志。

    ``Location`` 指向**任务类型集合**、任务 ID 只在响应体里、按 ID 查询返回日志条目数组——
    这三点决定了 :mod:`execution.jobs` 的写法（DX-EXE-033/034）。这里用 ``importMode=VALIDATE``
    走完整条链，因此不会改动共享实例（AC-EXE-01）。
    """
    from factory_foundation.api import unit_of_work

    from factory_dhis2.client.session import Dhis2Client
    from factory_dhis2.enums import CredentialScope
    from factory_dhis2.environment.loader import build_target
    from factory_dhis2.execution import jobs

    with unit_of_work() as session:
        target = build_target(session, live_environment)
    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    known = next(iter(client.list_all("dataElement", fields="id", page_size=1, limit=1)))["id"]

    import_client = client.with_scope(CredentialScope.IMPORT)
    submission = jobs.submit(
        import_client,
        resource="metadata.import",
        body={"dataElements": [{"id": known}]},
        params={"importMode": "VALIDATE", "identifier": "ASSIGNED", "importStrategy": "UPDATE"},
        use_async=True,
    )
    assert submission.job_ref and submission.job_ref != submission.poll_url, (
        "任务 ID 在响应体里，Location 只是类型集合：两者必须分开保存（DX-EXE-034）"
    )
    assert submission.poll_url and submission.poll_url.endswith(f"/{submission.job_ref}")

    response = jobs.poll(import_client, submission, timeout_seconds=240.0)
    report = jobs.report_of(response)
    assert report["reportSource"] == "TASK_LOG", report
    assert report["status"] in {"SUCCESS", "ERROR"}, report
    assert isinstance(report["detailedMessages"], list) and report["detailedMessages"]
    assert str(report["message"] or "").startswith("Import complete"), report["message"]


def test_a_rejected_validate_comes_back_as_409_with_a_mappable_report(
    live_environment: str,
) -> None:
    """2.41.10 实测：校验不通过的 VALIDATE 是 **HTTP 409 + 完整 ImportReport**。

    逐对象错误在 ``response.typeReports[].objectReports[].errorReports[]`` 里，对象本身没有
    ``status`` 字段，``klass`` 是 Java 全限定名。Client 若按状态码抛异常，AC-VAL-02 要求的
    “错误映射回 businessKey 与来源阶段”就没有输入可用了。VALIDATE 不写任何东西（AC-EXE-01）。
    """
    from factory_foundation.api import unit_of_work

    from factory_dhis2.client.session import Dhis2Client
    from factory_dhis2.enums import CredentialScope
    from factory_dhis2.environment.loader import build_target
    from factory_dhis2.execution.importreport import parse_report

    with unit_of_work() as session:
        target = build_target(session, live_environment)
    client = Dhis2Client(target, scope=CredentialScope.IMPORT)
    payload = {
        "dataElements": [
            {
                "id": "factoryBadRef1",
                "code": unique_code("badref"),
                "name": "契约引用错误",
                "shortName": "契约引用",
                "domainType": "AGGREGATE",
                "valueType": "NUMBER",
                "aggregationType": "SUM",
                "categoryCombo": {"id": "doesNotExist1"},
            }
        ]
    }
    response = client.call(
        "metadata.import",
        scope=CredentialScope.IMPORT,
        body=payload,
        params={"importMode": "VALIDATE", "identifier": "ASSIGNED", "importStrategy": "CREATE"},
    )
    assert response.status == 409, (response.status, response.payload)
    assert response.payload["response"]["status"] == "ERROR", response.payload

    summary = parse_report(response.payload, profile=target.profile)
    assert summary.passed is False
    assert summary.issues, "真实例的逐对象错误必须映射成问题清单"
    issue = summary.issues[0]
    assert issue.object_uid == "factoryBadRef1", issue.model_dump()
    assert issue.is_blocking, issue.model_dump()
    assert "doesNotExist1" in issue.description, issue.description


def _uid_of_code(environment_key: str, dhis2_type: str, code: str) -> str | None:
    """The uid the instance gives an object we created, looked up by its code."""
    from factory_dhis2.api import discover

    outcome = discover(
        environment_key,
        None,
        {"dhis2Types": [dhis2_type], "codePrefixes": [code]},
        None,
        idempotency_key=idem(f"uid:{code}"),
    )
    for row in _rows_of(outcome):
        if str(row.get("code") or "") == code:
            return str(row.get("uid") or "") or None
    return None


def _first_demo_objects(
    environment_key: str, outcome: object, *, limit: int
) -> list[tuple[str, dict[str, object]]]:
    """(label, content) for a few pre-existing objects, for the ② before/after check.

    Discovery indexes the rows it read; a row carrying its content is the one kind that can
    be fingerprinted without a second request, and on a public instance fewer requests to
    somebody else's metadata is the better default.
    """
    found: list[tuple[str, dict[str, object]]] = []
    for row in _rows_of(outcome):
        if len(found) >= limit:
            break
        uid = str(row.get("uid") or "")
        dhis2_type = str(row.get("dhis2Type") or "")
        content = row.get("content")
        if uid and isinstance(content, dict):
            found.append((f"{dhis2_type}:{uid}", dict(content)))
    return found


def _rows_of(outcome: object) -> list[dict[str, object]]:
    """The object rows of a DISCOVERY_RESULT artifact, as S4's contract publishes it."""
    from factory_dhis2.artifacts import read_json

    artifact_id = getattr(outcome, "discovery_result_artifact_id", None)
    if not artifact_id:
        return []
    document = read_json(str(artifact_id))
    rows = document.get("objects")
    return [row for row in (rows or []) if isinstance(row, dict)]
