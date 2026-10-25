"""规模与耗时（DX-NFR-02、DX-NFR-03）。

这两条是"承诺了但没人量过"最容易烂掉的一类需求：漂移检查与 Snapshot 在演示环境的十个对象
上当然快，真正的问题在 1,000 与 5,00 个对象上——那正是分页大小、批量读、异步阈值这些
参数唯一会被证明有用的地方。所以这里量的不是"功能对不对"，而是**参数配得对不对**：

* ``perf`` 标记默认跳过（需要显式 ``FACTORY_DX_RUN_PERF=1``），因为它按设计要跑几十秒；
* 但跳过必须是**显式的**，配置项说明"skipped by default"而代码里没有这道闸，等于骗人。
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest
from factory_foundation.api import install_development_backends, store_secret, unit_of_work

from factory_dhis2 import deps
from factory_dhis2.api import create_environment
from factory_dhis2.config import get_settings
from factory_dhis2.contract.capability_profile import build_capability_profile_document
from factory_dhis2.dto.environment import CredentialRef, EnvironmentSpec
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.execution import drift as drift_module
from factory_dhis2.execution import snapshot as snapshot_module
from factory_dhis2.execution.context import RunContext
from factory_dhis2.models.profile import CapabilityProfileIndexRow
from factory_dhis2.payload.package import MetadataPackage
from factory_dhis2.profiles import registry
from factory_dhis2.testing import record_connection_test
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport

pytestmark = [
    pytest.mark.usefixtures("harness"),
    pytest.mark.integration,
    pytest.mark.perf,
    pytest.mark.skipif(
        os.environ.get("FACTORY_DX_RUN_PERF") != "1",
        reason="性能冒烟：显式设 FACTORY_DX_RUN_PERF=1 才跑（CI 上按发布节奏跑一次）",
    ),
]

ENVIRONMENT = "perf"
SCALE_OBJECTS = 1000
ASYNC_SCALE_OBJECTS = 5000


def _data_element(index: int) -> dict[str, Any]:
    return {
        "id": f"pe{index:09d}",
        "code": f"PERF_{index:05d}",
        "name": f"Performance element {index}",
        "valueType": "NUMBER",
        "lastUpdated": "2026-01-01T00:00:00.000",
    }


@pytest.fixture
def big_instance() -> FakeDhis2Instance:
    """一台装着 1,000 个 dataElement 的内存实例 + 已登记的真实 ``dx`` 环境。"""
    install_development_backends()
    registry.load_builtin_profiles()
    profile = registry.profile_for_version("2.41.10")
    fake = FakeDhis2Instance()
    for index in range(SCALE_OBJECTS):
        row = _data_element(index)
        fake.put("dataElement", row["id"], code=row["code"], name=row["name"])
    deps.set_transport(FakeDhis2Transport(fake))

    discovery = store_secret("memory://dx/perf/discovery", "svcacct:perf")
    importing = store_secret("memory://dx/perf/import", "svcacct:perf")
    create_environment(
        EnvironmentSpec(
            environment_key=ENVIRONMENT,
            name="性能冒烟环境",
            base_url="https://perf.local/dhis2",
            environment_class=EnvironmentClass.TEST,
            version_constraint="2.41.x",
            auth_mode=AuthMode.BASIC,
            credentials=[
                CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref=discovery),
                CredentialRef(scope=CredentialScope.IMPORT, secret_ref=importing),
            ],
            tls_required=True,
            llm_data_policy=LlmDataPolicy.PRIVATE_NETWORK,
            timeout_seconds=60,
            status=EnvironmentStatus.ACTIVE,
        )
    )
    # DX-EXE-012 的准入输入：计划生成要知道服务账户的权限是谁验证过的
    record_connection_test(ENVIRONMENT)
    document = build_capability_profile_document(
        profile,
        environment_key=ENVIRONMENT,
        dhis2_version="2.41.10",
        probed_at="2026-01-01T00:00:00+00:00",
    )
    handle = _stage(document.model_dump(mode="json", by_alias=True))
    with unit_of_work() as session:
        info = _register(handle, session)
        session.add(
            CapabilityProfileIndexRow(
                id="dxcapperf000000000000000001",
                environment_key=ENVIRONMENT,
                profile_version=profile.profile_version,
                dhis2_version="2.41.10",
                probed_at=document.probed_at,
                stale=False,
                artifact_id=info.id,
                content_hash=info.content_hash,
            )
        )
    return fake


def _stage(document: Any) -> Any:
    from factory_dhis2.artifacts import stage_document

    return stage_document(document, project_id="prj-perf", artifact_type="CAPABILITY_PROFILE")


def _register(handle: Any, session: Any) -> Any:
    from factory_dhis2.artifacts import register_staged

    return register_staged(
        handle, project_id="prj-perf", artifact_type="CAPABILITY_PROFILE", session=session
    )


def _context(size: int, *, disposition: str = "UPDATE") -> RunContext:
    """一个含 ``size`` 个受控对象的运行上下文（漂移检查与 Snapshot 都按它工作）。"""
    from factory_dhis2.environment.loader import build_target

    rows = [_data_element(index) for index in range(size)]
    manifest = {
        "schemaVersion": 1,
        "projectId": "prj-perf",
        "blueprintVersion": "3.1.0",
        "environmentKey": ENVIRONMENT,
        "objects": [
            {
                "businessKey": f"bk.{row['code']}",
                "dhis2Type": "dataElement",
                "uid": row["id"],
                "disposition": disposition,
            }
            for row in rows
        ],
        "uidToBusinessKey": [
            {
                "businessKey": f"bk.{row['code']}",
                "uid": row["id"],
                "dhis2Type": "dataElement",
                "disposition": disposition,
            }
            for row in rows
        ],
        "dispositions": {f"bk.{row['code']}": disposition for row in rows},
        "observationBaselines": [
            {
                "uid": row["id"],
                "lastUpdated": row["lastUpdated"],
                "contentHash": None,
            }
            for row in rows
        ],
        "managedScope": [
            {"uid": row["id"], "dhis2Type": "dataElement", "businessKey": f"bk.{row['code']}"}
            for row in rows
        ],
        "controlledFields": {f"bk.{row['code']}": ["name", "valueType"] for row in rows},
        "expressionValidation": [],
        "deletionCandidates": [],
        "changeSummary": {"updated": [f"bk.{row['code']}" for row in rows]},
    }
    package = MetadataPackage(
        artifact_id="dxperfpackage000000000000001",
        artifact_hash="sha256:" + "e" * 64,
        payload={"dataElements": rows},
        manifest=manifest,
        missing_sections=[],
    )
    with unit_of_work() as session:
        target = build_target(session, ENVIRONMENT)
    return RunContext(
        environment_key=ENVIRONMENT,
        project_id="prj-perf",
        target=target,
        package=package,
        client=_scoped_client(target),
    )


def _scoped_client(target: Any) -> Any:
    from factory_dhis2.client.session import Dhis2Client

    return Dhis2Client(target, scope=CredentialScope.DISCOVERY)


def test_drift_check_over_a_thousand_objects_stays_under_the_budget(
    big_instance: FakeDhis2Instance,
) -> None:
    """DX-NFR-02：1,000 个对象的漂移检查 < 60 秒。"""
    context = _context(SCALE_OBJECTS)
    started = time.perf_counter()
    result = drift_module.check(context)
    elapsed = time.perf_counter() - started
    assert result.drifted is False, result.model_dump()
    assert elapsed < 60.0, f"1,000 对象的漂移检查用了 {elapsed:.1f}s"
    # 靠分页达成，不是靠一次全量拉取：请求条数与页大小要能对上
    assert len(big_instance.requests) >= SCALE_OBJECTS // get_settings().discovery_page_size


def test_snapshot_over_a_thousand_objects_stays_under_the_budget(
    big_instance: FakeDhis2Instance,
) -> None:
    """DX-NFR-02：1,000 个对象的 Snapshot < 60 秒。"""
    context = _context(SCALE_OBJECTS)
    started = time.perf_counter()
    document = snapshot_module.build(context)
    elapsed = time.perf_counter() - started
    assert len(document["objects"]) == SCALE_OBJECTS
    assert elapsed < 60.0, f"1,000 对象的 Snapshot 用了 {elapsed:.1f}s"


def test_five_thousand_objects_are_planned_as_a_single_async_import(
    big_instance: FakeDhis2Instance,
) -> None:
    """DX-NFR-03：5,000 个对象的包按**单次**导入执行，并且走异步通道。"""
    from factory_dhis2.execution.plan import build_plan

    context = _context(ASYNC_SCALE_OBJECTS, disposition="CREATE")
    plan = build_plan(context, expected_hash=context.package.artifact_hash)
    assert plan.supported is True, plan.blockers
    assert plan.document["objectCount"] == ASYNC_SCALE_OBJECTS
    decision = plan.document["asyncImport"]
    assert decision["async"] is True, (
        f"超过阈值还不异步，一次同步导入会把工作进程钉死在请求上：{decision}"
    )
    assert decision["thresholdObjects"] == get_settings().async_import_threshold
    assert decision["objectCount"] == ASYNC_SCALE_OBJECTS
    assert decision["decidedBy"] == "threshold"
