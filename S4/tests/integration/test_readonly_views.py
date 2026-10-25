"""四个"只读 ``dx``、零 DHIS2 调用"的查询入口（DX-CON-005、DX-CAP-009、DX-DSC-010/011）。

这组函数存在的理由就是 **DX-AC-11**：聚合视图与人工选择界面要在完全不访问目标实例的前提
下显示连接状态、能力新鲜度与候选对象。因此本文件的核心断言不是"返回了什么"，而是
**调用前后 ``instance.requests`` 一条都没多**——返回内容对了但偷偷打了实例，等于把
一次部署控制台的刷新变成一次 DHIS2 风暴。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import install_development_backends, store_secret, unit_of_work
from sqlalchemy import select

from factory_dhis2 import deps
from factory_dhis2.api import (
    create_environment,
    discover,
    get_capability_profile_index,
    get_last_connection_test,
    list_discovery_results,
    probe_capabilities,
    search_discovery_objects,
)
from factory_dhis2.api import (
    test_connection as run_connection_check,
)
from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.capability.service import freshness as check_freshness
from factory_dhis2.dto.connection import CapabilityProfileIndexInfo, ConnectionCheck
from factory_dhis2.enums import CapabilityFreshness
from factory_dhis2.errors import EnvironmentInvalidError
from factory_dhis2.models.environment import ConnectionTestRow
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport

pytestmark = [pytest.mark.usefixtures("harness"), pytest.mark.integration]

ENVIRONMENT = "readonly"
BASE = "https://readonly.local/dhis2"


def rows_where(model: Any, condition: Any) -> Any:
    return select(model).where(condition)


def _spec() -> dict[str, Any]:
    install_development_backends()
    discovery = store_secret("memory://dx/readonly/discovery", "svcacct:ro")
    importing = store_secret("memory://dx/readonly/import", "svcacct:ro")
    return {
        "environmentKey": ENVIRONMENT,
        "name": "只读视图测试实例",
        "baseUrl": BASE,
        "environmentClass": "TEST",
        "versionConstraint": "2.41.x",
        "authMode": "BASIC",
        "tlsRequired": True,
        "llmDataPolicy": "PRIVATE_NETWORK",
        "credentials": [
            {"scope": "discovery", "secretRef": discovery},
            {"scope": "import", "secretRef": importing},
        ],
    }


@pytest.fixture
def instance() -> Iterator[FakeDhis2Instance]:
    fake = FakeDhis2Instance()
    fake.authorities = ["F_METADATA_ADD", "ALL"]
    fake.roles = [{"id": "r1", "name": "Superuser"}]
    fake.put("dataElement", "de1", code="WEIGHT_KG", name="体重（公斤）")
    fake.put("dataElement", "de2", code="HEIGHT_CM", name="身高（厘米）")
    fake.put("dataElement", "de3", code="BLOOD_PRESSURE", name="血压")
    fake.put("optionSet", "os1", code="YES_NO", name="是 / 否")
    deps.set_transport(FakeDhis2Transport(fake))
    create_environment(_spec())
    yield fake


def _seed_state(instance: FakeDhis2Instance) -> str:
    """跑一次连接测试与一次发现，返回发现结果产物 ID。"""
    run_connection_check(ENVIRONMENT, ["discovery", "import"], idempotency_key="ro:conn")
    outcome = discover(
        ENVIRONMENT,
        None,
        {"kind": "RUN_TARGET", "dhis2Types": ["dataElement", "optionSet"]},
        idempotency_key="ro:discover",
    )
    assert outcome.discovery_result_artifact_id, outcome
    assert len(instance.requests) > 0, "前置的种子步骤本身应该打过实例"
    return str(outcome.discovery_result_artifact_id)


def test_the_four_readonly_queries_call_dhis2_zero_times(instance: FakeDhis2Instance) -> None:
    """DX-AC-11：四个入口都返回结果，且对 DHIS2 的调用次数为 0。"""
    artifact_id = _seed_state(instance)
    calls_before = len(instance.requests)

    check = get_last_connection_test(ENVIRONMENT)
    index = get_capability_profile_index(ENVIRONMENT)
    listed = list_discovery_results(ENVIRONMENT)
    found = search_discovery_objects(artifact_id, "dataElement", "体重")

    extra = [item["path"] for item in instance.requests[calls_before:]]
    assert not extra, f"只读视图偷偷访问了实例：{extra}"
    assert isinstance(check, ConnectionCheck) and check.ok is True
    assert isinstance(index, CapabilityProfileIndexInfo)
    assert listed and listed[0].artifact_id == artifact_id
    # DX-DSC-010：索引必须给出"当时那个实例的版本"，此前这一栏恒为 None
    assert listed[0].version == instance.version, listed[0]
    assert listed[0].discovered_at is not None and listed[0].content_hash
    assert [item.uid for item in found.items] == ["de1"]


def test_get_last_connection_test_gives_the_persisted_fact(instance: FakeDhis2Instance) -> None:
    """DX-CON-005 读的是 DX-CON-004 那份记录：时间、结论、版本、缺失权限、操作者。"""
    assert get_last_connection_test(ENVIRONMENT) is None, "没有记录时要报出「从未测过」"

    run_connection_check(
        ENVIRONMENT, ["discovery", "import"], idempotency_key="ro:conn:2", operator="bob"
    )
    check = get_last_connection_test(ENVIRONMENT)
    assert check is not None
    assert check.result == "PASSED" and check.ok is True
    assert check.dhis2_version == instance.version
    assert check.required_scopes == ["discovery", "import"]
    assert check.operator == "bob"
    assert check.connection_test_id
    assert check.capability_profile_artifact_id
    # 报告给出的是**当时**该范围实际具备的权限，而不是现在再问一次
    assert set(check.granted_authorities) == {"discovery", "import"}

    with unit_of_work() as session:
        stored = session.execute(
            rows_where(ConnectionTestRow, ConnectionTestRow.environment_key == ENVIRONMENT)
        ).scalars().all()
    assert [row.id for row in stored] == [check.connection_test_id], (
        "读的必须是 ``dx.connection_test`` 那一行"
    )


def test_never_tested_environment_has_no_row(instance: FakeDhis2Instance) -> None:
    """环境存在但从未测过时返回 ``None``，而不是抛错或假装通过。"""
    assert get_last_connection_test(ENVIRONMENT) is None
    assert get_capability_profile_index(ENVIRONMENT) is None
    assert list_discovery_results(ENVIRONMENT) == []
    assert len(instance.requests) == 0


def test_unknown_environment_is_rejected_by_the_lookup(instance: FakeDhis2Instance) -> None:
    """索引查询按 DX-ENV-004 显式绑环境：没登记过的键不能悄悄返回一份"空结果也算通过"。"""
    with pytest.raises(EnvironmentInvalidError):
        get_last_connection_test("nope-not-registered")
    assert len(instance.requests) == 0


def test_capability_profile_index_reports_staleness_without_probing(
    instance: FakeDhis2Instance,
) -> None:
    """DX-CAP-009：Profile 版本、实例版本、探测时间、是否过期及原因、产物 ID 与内容哈希。"""
    assert get_capability_profile_index(ENVIRONMENT) is None
    probe_capabilities(ENVIRONMENT, idempotency_key="ro:probe")

    index = get_capability_profile_index(ENVIRONMENT)
    assert index is not None
    assert index.profile_version == "Dhis241Profile"
    assert index.dhis2_version == instance.version
    assert index.stale is False and index.stale_reason is None
    assert index.content_hash.startswith("sha256:")
    assert index.artifact_id
    assert CapabilityFreshness.VALID is not None

    # 实例升级 → 旧能力被标记过期；索引读出的就是"已过期 + 原因"，而读它这件事不打实例
    instance.version = "2.42.0"
    check_freshness(ENVIRONMENT)
    calls_before = len(instance.requests)
    after = get_capability_profile_index(ENVIRONMENT)
    assert len(instance.requests) == calls_before
    assert after is not None and after.stale is True
    assert after.stale_reason == "version-changed"


def test_search_discovery_objects_filters_pages_and_counts(
    instance: FakeDhis2Instance,
) -> None:
    """DX-DSC-011：按类型与 code / name 关键字检索，分页返回 UID、code、name、lastUpdated、内容哈希与总数。"""
    artifact_id = _seed_state(instance)
    calls_before = len(instance.requests)

    by_type = search_discovery_objects(artifact_id, "dataElement")
    assert by_type.total == 3
    assert [item.uid for item in by_type.items] == ["de1", "de2", "de3"]
    first = by_type.items[0]
    assert first.code == "WEIGHT_KG" and first.name == "体重（公斤）"
    assert first.last_updated, "选择界面要显示「这个对象上次改于何时」"
    assert first.content_hash, "选择界面要显示「这个对象现在是什么内容」"

    by_code = search_discovery_objects(artifact_id, None, "HEIGHT")
    assert [item.uid for item in by_code.items] == ["de2"] and by_code.total == 1

    by_name = search_discovery_objects(artifact_id, "dataElement", "血压")
    assert [item.uid for item in by_name.items] == ["de3"]

    paged = search_discovery_objects(artifact_id, "dataElement", limit=2, offset=2)
    assert [item.uid for item in paged.items] == ["de3"]
    assert (paged.total, paged.offset, paged.limit) == (3, 2, 2)

    empty = search_discovery_objects(artifact_id, "dataElement", "不存在的关键字")
    assert empty.items == [] and empty.total == 0
    assert len(instance.requests) == calls_before, "检索只读产物内容（DX-DSC-011）"


def test_search_over_five_thousand_objects_is_paged(instance: FakeDhis2Instance) -> None:
    """DX-AC-12：在含 5,000 个对象的发现结果里按类型与关键字检索，分页返回匹配对象与总数。"""
    objects = [
        {
            "dhis2Type": "optionSet" if index % 5 == 0 else "dataElement",
            "uid": f"de{index:05d}",
            "code": "TARGET" if index % 7 == 0 else f"CODE{index:05d}",
            "name": f"对象 {index}",
            "lastUpdated": "2026-01-01T00:00:00.000",
            "contentHash": f"sha256:{index:064x}",
        }
        for index in range(5000)
    ]
    handle = stage_document(
        {
            "schemaVersion": 1,
            "environmentKey": ENVIRONMENT,
            "discoveredAt": "2026-01-01T00:00:00+00:00",
            "version": instance.version,
            "objects": objects,
        },
        project_id="scale",
        artifact_type="DISCOVERY_RESULT",
    )
    with unit_of_work() as session:
        info = register_staged(
            handle, project_id="scale", artifact_type="DISCOVERY_RESULT", session=session
        )
    calls_before = len(instance.requests)

    by_type = search_discovery_objects(info.id, "optionSet")
    assert by_type.total == sum(1 for item in objects if item["dhis2Type"] == "optionSet")
    assert [item.uid for item in by_type.items] == [
        item["uid"] for item in objects if item["dhis2Type"] == "optionSet"
    ][:50]

    by_code = search_discovery_objects(info.id, None, "TARGET", limit=100)
    assert by_code.total == sum(1 for item in objects if item["code"] == "TARGET")
    assert len(by_code.items) == 100
    assert all(item.code == "TARGET" for item in by_code.items)

    elements = [item["uid"] for item in objects if item["dhis2Type"] == "dataElement"]
    tail = search_discovery_objects(info.id, "dataElement", limit=10, offset=len(elements) - 3)
    assert [item.uid for item in tail.items] == elements[-3:]
    assert tail.total == len(elements)
    assert len(instance.requests) == calls_before
