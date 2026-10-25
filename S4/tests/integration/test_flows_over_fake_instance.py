"""环境流程走真实 Client：连接测试、能力探测、新鲜度、发现（DX-CON/CAP/DSC）。

这里刻意**不用** ScriptedClient：这些函数的输出取决于实例的响应形状（pager、``/api/me`` 的
字段裁剪、未知路径回 HTML），只有让请求真的经 ``httpx`` 打到 :class:`FakeDhis2Transport`
上，才能证明 Client + 资源目录 + 这三项服务之间的接线是对的。数据库是真的（``dx`` 行与产物
索引都落库），实例是假的（不改动任何共享环境）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import (
    AuditQuery,
    install_development_backends,
    query_audit,
    store_secret,
    unit_of_work,
)
from sqlalchemy import select

from factory_dhis2 import deps
from factory_dhis2.api import (
    check_capability_freshness,
    create_environment,
    discover,
    probe_capabilities,
)
from factory_dhis2.api import (
    test_connection as run_connection_check,
)
from factory_dhis2.artifacts import read_json
from factory_dhis2.models.profile import CapabilityProfileIndexRow, DiscoveryIndexRow
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport

pytestmark = [pytest.mark.usefixtures("harness"), pytest.mark.integration]

ENVIRONMENT = "fakeflow"
BASE = "https://fake-flow.local/dhis2"


def rows_where(model: Any, condition: Any) -> Any:
    return select(model).where(condition)


def _spec(**changes: Any) -> dict[str, Any]:
    install_development_backends()
    discovery = store_secret("memory://dx/flow/discovery", "svcacct:district")
    importing = store_secret("memory://dx/flow/import", "svcacct:district")
    spec: dict[str, Any] = {
        "environmentKey": ENVIRONMENT,
        "name": "流程测试实例",
        "baseUrl": BASE,
        "environmentClass": "TEST",
        "versionConstraint": "2.41.x",
        "authMode": "BASIC",
        "tlsRequired": True,
        "llmDataPolicy": "PRIVATE_NETWORK",
        "timeoutSeconds": 30,
        "credentials": [
            {"scope": "discovery", "secretRef": discovery},
            {"scope": "import", "secretRef": importing},
        ],
    }
    spec.update(changes)
    return spec


@pytest.fixture
def instance() -> Iterator[FakeDhis2Instance]:
    """一个已登记的环境 + 挂在 transport 上的内存实例。"""
    fake = FakeDhis2Instance()
    fake.authorities = ["F_DATAELEMENT_ADD", "ALL"]
    fake.roles = [{"id": "r1", "name": "Superuser"}]
    fake.put("dataElement", "de1", code="WEIGHT", name="体重", value_type="NUMBER")
    fake.put("dataElement", "de2", code="HEIGHT", name="身高", value_type="NUMBER")
    fake.put("categoryCombo", "cc1", code="default", name="default")
    fake.generated_uids = [f"uid{index:06d}xx" for index in range(10)]
    deps.set_transport(FakeDhis2Transport(fake))
    create_environment(_spec())
    yield fake


def test_connection_report_covers_every_declared_scope(instance: FakeDhis2Instance) -> None:
    """DX-CON-001/002：声明需要的范围逐项给结论，缺权限要列出缺什么。"""
    check = run_connection_check(
        ENVIRONMENT, ["discovery", "import"], idempotency_key="flow:conn:1", operator="alice"
    )
    assert check.ok is True, check.checks
    assert check.version == instance.version
    names = {item["name"] for item in check.checks}
    for required in ("address", "tls", "auth", "version", "versionProfile", "account"):
        assert required in names, required
    states = {item["name"]: item["state"] for item in check.checks}
    assert states["scope.discovery"] == "PASSED"
    assert states["scope.import"] == "PASSED"
    # 未声明的范围报 SKIPPED：把没检查的说成检查过，等于伪造结论
    assert states["scope.tracker"] == "SKIPPED"
    assert states["scope.datastore"] == "SKIPPED"


def test_account_check_reports_roles_and_org_scope(instance: FakeDhis2Instance) -> None:
    """DX-CON-001：服务账户的角色与组织范围是连接测试要报告的事实。"""
    check = run_connection_check(ENVIRONMENT, ["discovery"], idempotency_key="flow:conn:2")
    account = next(item for item in check.checks if item["name"] == "account")
    assert "授权 2 项" in str(account["detail"]), account
    assert check.account_org_scope, "组织范围为空会让后面的数据范围判断失去依据"


def test_a_rejected_credentials_probe_fails_the_connection_test(
    instance: FakeDhis2Instance,
) -> None:
    """DX-CON-002：401 必须让结论变成不通过，并带上原因本身。"""
    instance.fail_next("system/info", 401)
    check = run_connection_check(ENVIRONMENT, ["discovery"], idempotency_key="flow:conn:3")
    assert check.ok is False
    auth = next(item for item in check.checks if item["name"] == "auth")
    assert auth["state"] == "FAILED"
    assert "DHIS2_AUTH_ERROR" in str(auth["detail"]), auth


def test_connection_result_is_persisted_and_audited(instance: FakeDhis2Instance) -> None:
    """DX-CON-004：审批人看到的“这个环境能用”是一条有日期的记录，不是界面现问的。"""

    from factory_dhis2.models.environment import ConnectionTestRow

    run_connection_check(ENVIRONMENT, ["discovery"], idempotency_key="flow:conn:4", operator="bob")
    with unit_of_work() as session:
        rows = session.execute(
            rows_where(ConnectionTestRow, ConnectionTestRow.environment_key == ENVIRONMENT)
        ).scalars().all()
    assert len(rows) == 1, rows
    assert rows[0].result == "PASSED"
    assert rows[0].dhis2_version == instance.version
    assert rows[0].capability_profile_artifact_id, "连接通过就要合成 Capability Profile"
    events = query_audit(AuditQuery(object_id=rows[0].id))
    assert events, "连接测试必须留审计"


def test_probe_persists_a_capability_profile_artifact(instance: FakeDhis2Instance) -> None:
    """DX-CAP-003：探测结果合成 CAPABILITY_PROFILE 产物并登记索引。"""
    artifact_id = probe_capabilities(ENVIRONMENT, idempotency_key="flow:cap:1")
    assert artifact_id
    document = read_json(artifact_id)
    assert document["profileVersion"] == "Dhis241Profile"
    names = {item["logicalName"] for item in document["resources"]}
    assert {"system.info", "metadata.import", "system.id"} <= names
    with unit_of_work() as session:
        row = session.execute(
            rows_where(
                CapabilityProfileIndexRow, CapabilityProfileIndexRow.environment_key == ENVIRONMENT
            )
        ).scalars().one()
    assert row.artifact_id == artifact_id
    assert row.stale is False
    assert row.content_hash.startswith("sha256:")


def test_the_probe_syncs_the_whole_catalogue_into_the_artifact(
    instance: FakeDhis2Instance,
) -> None:
    """DX-CAP-001：Schema 包同步目录里的**每一类**。

    产物是 S2 唯一可读的那一份，而它不许再猜（DX-CAP-003）。范围如果收窄到「核心那十类」，
    另外二十几类的字段清单在产物里就等于「不存在」——那不是实例的事实，是我们的同步范围。
    """
    from factory_dhis2.capability.service import default_schema_types
    from factory_dhis2.profiles import registry

    registry.load_builtin_profiles()
    artifact_id = probe_capabilities(ENVIRONMENT, idempotency_key="flow:cap:scope")
    document = read_json(artifact_id)

    catalogue = set(default_schema_types(registry.profile_for_version("2.41.10")))
    synced = {item["dhis2Type"] for item in document["schemas"]}
    assert synced == catalogue, sorted(catalogue - synced)
    assert all(item["properties"] for item in document["schemas"]), "空 Schema 等于没同步"
    # 覆盖与实例答案并存：forbidden/controlableFields 来自随包 Profile，properties 来自实例
    data_element = next(item for item in document["schemas"] if item["dhis2Type"] == "dataElement")
    assert data_element["forbidden"], "项目侧的字段清单不能因为同步而被抹掉"


def test_freshness_is_valid_right_after_probe_and_expires_on_version_change(
    instance: FakeDhis2Instance,
) -> None:
    """DX-CAP-006、DX-AC-06：刚探测后是 VALID；实例版本一变就要报 VERSION_CHANGED。"""
    probe_capabilities(ENVIRONMENT, idempotency_key="flow:cap:2")
    fresh = check_capability_freshness(ENVIRONMENT)
    assert fresh.state == "VALID", fresh.model_dump()

    instance.version = "2.42.0"
    changed = check_capability_freshness(ENVIRONMENT)
    assert changed.state == "VERSION_CHANGED", changed.model_dump()
    # ``version`` 是刚从实例读到的，``dhis2Version`` 是已持久化的那份基准：两者不同正是结论本身
    assert changed.version == "2.42.0", changed.version
    assert changed.dhis2_version == "2.41.10", changed.dhis2_version


def test_unreachable_instance_is_reported_expired_not_ok(instance: FakeDhis2Instance) -> None:
    """DX-CAP-006：问不到实例时不能说能力有效。"""
    probe_capabilities(ENVIRONMENT, idempotency_key="flow:cap:3")
    instance.fail_next("system/info", 503, times=99)
    fresh = check_capability_freshness(ENVIRONMENT)
    assert fresh.state == "EXPIRED", fresh.model_dump()
    assert fresh.reason, "过期原因要说得清：是没探测过还是问不到"


@pytest.fixture
def probed(instance: FakeDhis2Instance) -> FakeDhis2Instance:
    """发现与执行都以已持久化的 Capability Profile 为前提（DX-CAP-003）。"""
    probe_capabilities(ENVIRONMENT, idempotency_key="flow:cap:probe-fixture")
    return instance


def test_discovery_reads_objects_and_indexes_them(probed: FakeDhis2Instance) -> None:
    """DX-DSC-001/002 + DX-DSC-005：按类型发现、带 lastUpdated 与内容哈希，并登记索引。"""
    outcome = discover(
        ENVIRONMENT,
        "prj-flow",
        {"dhis2Types": ["dataElement"]},
        None,
        idempotency_key="flow:dsc:1",
    )
    assert outcome.object_count == 2, outcome.model_dump()
    assert outcome.discovery_result_artifact_id
    document = read_json(outcome.discovery_result_artifact_id)
    by_uid = {item["uid"]: item for item in document["objects"]}
    assert set(by_uid) == {"de1", "de2"}
    assert all(item["contentHash"].startswith("sha256:") for item in document["objects"])
    with unit_of_work() as session:
        row = session.execute(
            rows_where(DiscoveryIndexRow, DiscoveryIndexRow.environment_key == ENVIRONMENT)
        ).scalars().one()
    assert row.project_id == "prj-flow"
    assert row.artifact_id == outcome.discovery_result_artifact_id


def test_discovery_of_an_explicit_list_reads_only_those_objects(
    probed: FakeDhis2Instance,
) -> None:
    """DX-DSC-005：环境绑定编译给的是明确清单，读取就不能扫全表。"""
    outcome = discover(
        ENVIRONMENT,
        "prj-flow",
        "RUN_TARGET",
        [{"dhis2Type": "dataElement", "uid": "de1"}],
        idempotency_key="flow:dsc:2",
    )
    assert outcome.object_count == 1, outcome.model_dump()
    assert not outcome.blocking_issues


def test_the_same_idempotency_key_returns_the_first_discovery(
    probed: FakeDhis2Instance,
) -> None:
    """DX-EXE-004 的同一条规矩也适用于发现：同键重复调用不重做。"""
    first = discover(
        ENVIRONMENT, None, {"dhis2Types": ["dataElement"]}, None, idempotency_key="flow:dsc:3"
    )
    requests_before = len(probed.requests)
    second = discover(ENVIRONMENT, None, {"dhis2Types": ["dataElement"]}, None, idempotency_key="flow:dsc:3")
    assert second.discovery_result_artifact_id == first.discovery_result_artifact_id
    assert len(probed.requests) == requests_before, "重放不该再打实例"


def test_an_explicit_code_list_is_matched_by_code_not_uid(
    probed: FakeDhis2Instance,
) -> None:
    """AC-ENV-01（S4 部分）：传入明确对象清单时，发现结果要包含这些对象的 UID 与当前内容。

    清单以 **code** 给出是环境绑定编译的常见形态（Registry 里存的是 code）。以前它被当成
    UID 用 ``id:in:`` 发问，于是「绑定了 3 个对象」的发现结果只有 0 个，还不报错。
    """
    outcome = discover(
        ENVIRONMENT,
        "prj-binding",
        "RUN_TARGET",
        [{"dhis2Type": "dataElement", "code": "HEIGHT"}],
        idempotency_key="flow:dsc:code",
    )
    assert outcome.object_count == 1, outcome.model_dump()
    document = read_json(outcome.discovery_result_artifact_id)
    found = {item["uid"]: item for item in document["objects"]}
    assert set(found) == {"de2"}
    assert found["de2"]["code"] == "HEIGHT"
    # DX-DSC-001【新增】：范围内对象的当前完整内容也要在，供 S2 合并 UPDATE 与生成 Diff
    assert found["de2"]["content"]["name"] == "身高"
    assert found["de2"]["contentHash"].startswith("sha256:")


def test_a_code_prefix_selects_only_real_prefixes(probed: FakeDhis2Instance) -> None:
    """DX-DSC-002：按 code 前缀收窄。``ilike`` 是"包含"，所以前缀要在本地再判一次。"""
    probed.put("dataElement", "de9", code="X_WEIGHT_KG", name="多余")
    outcome = discover(
        ENVIRONMENT,
        None,
        {"dhis2Types": ["dataElement"], "codePrefixes": ["WEIGHT"]},
        None,
        idempotency_key="flow:dsc:prefix",
    )
    document = read_json(outcome.discovery_result_artifact_id)
    codes = sorted(str(item["code"]) for item in document["objects"])
    assert codes == ["WEIGHT"], f"前缀匹配把包含但不以它开头的对象也收了进来：{codes}"


def test_org_units_are_read_to_the_configured_depth(probed: FakeDhis2Instance) -> None:
    """DX-DSC-006：组织层级按深度读取，并带回 UID、code、name、层级与父节点。"""
    outcome = discover(
        ENVIRONMENT,
        None,
        {"dhis2Types": ["dataElement"], "orgUnitDepth": 2},
        None,
        idempotency_key="flow:dsc:org",
    )
    document = read_json(outcome.discovery_result_artifact_id)
    units = {item["uid"]: item for item in document["orgUnits"]}
    assert set(units) == {"ou1", "ou2"}, f"深度 2 不该读到第三层：{sorted(units)}"
    assert units["ou2"]["level"] == 2 and units["ou2"]["parent"] == "ou1"
    assert units["ou2"]["code"] == "OU_DISTRICT"


def test_discovery_writes_nothing_to_the_instance(probed: FakeDhis2Instance) -> None:
    """DX-DSC-004 / DX-BR-04：发现只读，一条写请求都不许有。"""
    discover(
        ENVIRONMENT,
        "prj-flow",
        {"dhis2Types": ["dataElement", "optionSet"], "orgUnitDepth": 1},
        None,
        idempotency_key="flow:dsc:readonly",
    )
    methods = {str(item["method"]) for item in probed.requests}
    assert methods == {"GET"}, f"发现发出了写请求：{sorted(methods)}"


def test_references_of_a_program_cover_its_stage_array(probed: FakeDhis2Instance) -> None:
    """DX-DSC-001：关键引用关系要包含引用数组，不只是单对象引用。"""
    probed.put(
        "program",
        "pg1",
        code="ANC",
        name="ANC",
        categoryCombo={"id": "cc1", "name": "default"},
        programStages=[{"id": "ps2", "name": "Visit 2"}, {"id": "ps1", "name": "Visit 1"}],
    )
    outcome = discover(
        ENVIRONMENT, None, {"dhis2Types": ["program"]}, None, idempotency_key="flow:dsc:refs"
    )
    document = read_json(outcome.discovery_result_artifact_id)
    program = next(item for item in document["objects"] if item["uid"] == "pg1")
    assert program["references"]["categoryCombo"] == "cc1"
    assert program["references"]["programStages"] == ["ps1", "ps2"]


def test_connection_test_and_probe_issue_only_get_requests(instance: FakeDhis2Instance) -> None:
    """DX-CON-003 / DX-BR-04 / DX-AC-01：连接测试与能力探测都不许改变目标环境。

    这两条链路每次都会被运维多点几下，一旦其中藏着一次试写，"我只是测了一下连接"就会变成
    "实例里多了一个没人认领的对象"。断言的是**全部**请求都是 GET。
    """
    run_connection_check(ENVIRONMENT, ["discovery"], idempotency_key="flow:conn:readonly")
    probe_capabilities(ENVIRONMENT, idempotency_key="flow:cap:readonly")
    methods = {str(item["method"]) for item in instance.requests}
    assert methods == {"GET"}, f"连接测试或探测发出了写请求：{sorted(methods)}"


def test_the_runtime_catalogue_is_the_probed_one_not_the_bundled_one(
    instance: FakeDhis2Instance,
) -> None:
    """DX-CLI-005 / DX-CAP-003：运行期端点目录来自**这个环境探测确认过的那份能力**。

    这里让实例对 ``identifiableObjects`` 回 404（真实部署里经代理的实例就少这些端点）。
    探测把这条负面事实记进产物之后：随包 Profile 里仍有该资源，而运行期装配出来的目录
    必须没有它——否则 S4 会继续对一个已知不存在的端点发请求，并把 404 解释成"没人占用"。
    """
    from factory_dhis2.api import uid_exists
    from factory_dhis2.capability.runtime_profile import apply_environment_capability
    from factory_dhis2.environment.loader import build_target
    from factory_dhis2.profiles import registry

    instance.fail_next("identifiableObjects", 404, times=10)
    probe_capabilities(ENVIRONMENT, idempotency_key="flow:cap:runtime")

    with unit_of_work() as session:
        target = build_target(session, ENVIRONMENT, require_probe=True)
    runtime = apply_environment_capability(target)
    bundled = registry.profile_for_version("2.41.10")

    assert "identifiableObjects" in {item.logical_name for item in bundled.resources}
    assert "identifiableObjects" not in {item.logical_name for item in runtime.profile.resources}

    # 运行期目录里没有的资源不会被再问一次：UID 存在性直接走目录扫描
    instance.put("dataElement", "de7", code="BMI", name="体质指数")
    answers = uid_exists(ENVIRONMENT, ["de7"])
    assert answers["de7"].exists is True
    assert answers["de7"]["basis"] == "catalogue-sweep"
    asked_after = [row["path"] for row in instance.requests if row["path"] == "identifiableObjects"]
    probed, used = asked_after[:1], asked_after[1:]
    assert probed and not used, f"探测之后仍在调用已知不存在的端点：{used}"
