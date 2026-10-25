"""一次完整 Deployment Run 走**公共 API**（00 文档 8.2、SRS 6.3-6.5、DX-EXE-001…042）。

前面的管线测试直接叫 `execution.*` 的模块函数，而 S1 拿到的是 `factory_dhis2.api` 上的名字
（它的 ExecutionAdapter 按名字解析）。这个文件因此只经 api 调用：它证明的是“对外那张脸”真的
接得上——参数顺序、外部 I/O 标记、幂等键、产物引用与状态映射，一处接错就红。

目标环境是挂在 httpx transport 上的内存实例（不是 ScriptedClient），所以 URL 拼装、分页、
ImportReport 解析也一并被走了一遍。
"""

from __future__ import annotations

import pytest
from factory_foundation.api import (
    IdempotencyConflictError,
    install_development_backends,
    store_secret,
    unit_of_work,
)

from factory_dhis2 import api, deps
from factory_dhis2.artifacts import read_json
from factory_dhis2.enums import ExecutionStatus
from factory_dhis2.errors import FeatureNotEnabledError
from factory_dhis2.execution import steps
from factory_dhis2.testing import record_connection_test
from factory_dhis2.testing.contract_packages import build_single_object_package
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport

pytestmark = [pytest.mark.usefixtures("harness"), pytest.mark.integration]

ENVIRONMENT = "run1"
PROJECT = "prj-run"
UID = "factoryRunObj1"


def _instance() -> FakeDhis2Instance:
    fake = FakeDhis2Instance()
    fake.authorities = ["ALL", "F_METADATA_ADD"]
    fake.put("categoryCombo", "ccDefault", code="default", name="default")
    deps.set_transport(FakeDhis2Transport(fake))
    return fake


def _register_environment(fake: FakeDhis2Instance) -> None:
    install_development_backends()
    discovery = store_secret("memory://dx/run/discovery", "svcacct:district")
    importing = store_secret("memory://dx/run/import", "svcacct:district")
    api.create_environment(
        {
            "environmentKey": ENVIRONMENT,
            "name": "Run 测试环境",
            "baseUrl": "https://run.local/dhis2",
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
        },
        operator="alice",
    )
    record_connection_test(ENVIRONMENT)


def _package(fake: FakeDhis2Instance, *, code: str, name: str) -> str:
    return build_single_object_package(
        environment_key=ENVIRONMENT,
        project_id=PROJECT,
        code=code,
        name=name,
        uid=UID,
        category_combo="ccDefault",
        disposition="CREATE",
        observation_baselines=[],
    )


def test_the_whole_run_progresses_through_the_public_api() -> None:
    """DX-EXE-005/018/032/040 与 AC-EXE-01/06：一条链上的每一步都经 api 可达且有产物。"""
    fake = _instance()
    _register_environment(fake)

    check = api.test_connection(ENVIRONMENT, ["discovery", "import"], idempotency_key="run:conn")
    assert check.ok is True, check.checks
    assert check.capability_profile_artifact_id, "连接通过就要合成能力档案（DX-CAP-003）"

    discovered = api.discover(
        ENVIRONMENT, PROJECT, {"dhis2Types": ["categoryCombo"]}, None, idempotency_key="run:dsc"
    )
    assert discovered.discovery_result_artifact_id
    listed = api.list_discovery_results(ENVIRONMENT, PROJECT)
    assert [item.artifact_id for item in listed] and listed[0].artifact_id

    artifact_id = _package(fake, code="run_weight", name="随访体重")
    validation = api.prepare_and_validate(
        ENVIRONMENT,
        PROJECT,
        artifact_id,
        None,
        operator="alice",
        idempotency_key="run:validate",
    )
    assert validation.passed is True, validation.issues
    assert validation.execution_plan_artifact_id and validation.snapshot_artifact_id
    assert validation.import_report_artifact_id
    payload_hash = validation.payload_hash

    drift = api.check_drift(ENVIRONMENT, artifact_id, project_id=PROJECT)
    assert drift.drifted is False, drift.objects

    committed = api.commit(
        ENVIRONMENT, PROJECT, artifact_id, payload_hash, operator="alice", idempotency_key="run:commit"
    )
    assert committed.status == ExecutionStatus.COMPLETED.value, committed.model_dump()
    assert committed.applied is True
    assert committed.report_artifact_id
    assert UID in fake.objects.get("dataElement", {}), "COMMIT 之后实例上应真的有了对象"

    verified = api.post_verify(
        ENVIRONMENT,
        PROJECT,
        artifact_id,
        committed.execution_id,
        operator="alice",
        idempotency_key="run:verify",
    )
    assert verified.consistent is True, verified.issues
    assert verified.report_artifact_id

    reconciled = api.reconcile(committed.execution_id)
    assert reconciled.outcome.value == "APPLIED", reconciled.model_dump()

    by_key = api.get_execution_by_key("run:commit")
    assert by_key is not None and by_key.execution_id == committed.execution_id
    status = api.get_execution_status(committed.execution_id or "")
    assert status is not None and status.status is ExecutionStatus.COMPLETED

    comparison = api.diff_snapshot_vs_current(committed.execution_id)
    assert comparison.diff_report_artifact_id
    document = read_json(comparison.diff_report_artifact_id)
    assert document["comparison"] == "SNAPSHOT_VS_CURRENT"
    assert sum(item for item in (comparison.summary or {}).values() if isinstance(item, int)) >= 0


def test_the_execution_records_the_steps_the_pipeline_promised() -> None:
    """DX-EXE-004：步骤逐条落库，续跑与审计都以这张表为准。"""
    fake = _instance()
    _register_environment(fake)
    api.test_connection(ENVIRONMENT, ["discovery", "import"], idempotency_key="run:conn2")
    artifact_id = _package(fake, code="run_steps", name="步骤检查")
    validation = api.prepare_and_validate(
        ENVIRONMENT, PROJECT, artifact_id, None, operator="bob", idempotency_key="run:validate2"
    )
    recorded = {item.step for item in validation.steps}
    for required in ("PLAN", "DRIFT_CHECK", "SNAPSHOT", "VALIDATE", "EXPRESSION_VALIDATE"):
        assert required in recorded, (required, sorted(recorded))


def test_the_same_idempotency_key_with_a_different_payload_is_refused() -> None:
    """DX-EXE-004：同键不同请求是冲突，不是“覆盖上一次”。"""
    fake = _instance()
    _register_environment(fake)
    api.test_connection(ENVIRONMENT, ["discovery", "import"], idempotency_key="run:conn3")
    first = _package(fake, code="run_a", name="甲")
    api.prepare_and_validate(
        ENVIRONMENT, PROJECT, first, None, operator="bob", idempotency_key="run:same"
    )
    second = _package(fake, code="run_b", name="乙")
    with pytest.raises(IdempotencyConflictError):
        api.prepare_and_validate(
            ENVIRONMENT, PROJECT, second, None, operator="bob", idempotency_key="run:same"
        )


def test_m3_features_are_registered_but_refuse_without_touching_the_instance() -> None:
    """DX-TRK-002、SRS 12 章：本轮未交付的能力一律拒绝，且不发一次外部调用。"""
    fake = _instance()
    _register_environment(fake)
    requests_before = len(fake.requests)
    with pytest.raises(FeatureNotEnabledError) as excinfo:
        api.import_fixtures(
            ENVIRONMENT, PROJECT, "art-fixture", operator="alice", idempotency_key="run:fixture"
        )
    assert excinfo.value.code == "DX_FEATURE_NOT_ENABLED", excinfo.value
    assert len(fake.requests) == requests_before


def test_a_running_execution_is_reported_as_needing_a_manual_check() -> None:
    """DX-EXE-036：S1 的 ``ExecutionStatus`` 只有三个终态成员，内部进行态不能伪装成 COMPLETED。

    一条还在跑（或崩在半路）的执行，对外只能说“需要人工核实”，否则 S1 会把未确认的写入
    当成已完成而继续推进 Gate。
    """
    fake = _instance()
    _register_environment(fake)
    api.test_connection(ENVIRONMENT, ["discovery", "import"], idempotency_key="run:conn4")
    artifact_id = _package(fake, code="run_map", name="状态映射")
    validation = api.prepare_and_validate(
        ENVIRONMENT, PROJECT, artifact_id, None, operator="alice", idempotency_key="run:validate4"
    )
    with unit_of_work() as session:
        row = steps.require(session, validation.execution_id)
        steps.mark_running(session, row)

    reported = api.get_execution_status(validation.execution_id or "")
    assert reported is not None
    assert reported.status is ExecutionStatus.NEEDS_MANUAL_CHECK, reported.model_dump()
