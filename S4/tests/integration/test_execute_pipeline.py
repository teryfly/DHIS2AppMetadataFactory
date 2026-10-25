"""执行管线的端到端集成测试（离线，AC-EXE-02/04/05、DX-AC-03/04/05）。

真实 DHIS2 由一个脚本化的假 Client 代替：**跑的是真的管线**——计划、漂移、Snapshot、
VALIDATE、COMMIT、锁、幂等、对账入口全部执行，只有最后的 HTTP 是被编排的。这样
“提交被挡住”这类结论来自代码而不是测试的自我暗示。

在线真实例的同一批断言在 ``tests/contract/test_dhis2_241_live.py``。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from factory_foundation.api import new_id, unit_of_work

from factory_dhis2 import artifacts
from factory_dhis2.contract.capability_profile import build_capability_profile_document
from factory_dhis2.dto.environment import CredentialRef, EnvironmentSpec
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    ExecutionStatus,
    LlmDataPolicy,
)
from factory_dhis2.environment import service as environment_service
from factory_dhis2.envlock import manager as envlock
from factory_dhis2.errors import (
    ConcurrencyError,
    Dhis2UnreachableError,
    DriftDetectedError,
    EnvironmentLockedError,
    OutsideCommitWindowError,
    PayloadHashMismatchError,
)
from factory_dhis2.execution import commit as commit_module
from factory_dhis2.execution import steps as steps_module
from factory_dhis2.execution.context import RunContext
from factory_dhis2.execution.pipeline import prepare_and_validate
from factory_dhis2.models.execution import ExecutionRow, ValidationRecordRow
from factory_dhis2.models.profile import CapabilityProfileIndexRow
from factory_dhis2.payload.package import read_package
from factory_dhis2.profiles import registry
from factory_dhis2.testing import contract_packages, record_connection_test
from tests import window_not_now
from tests.unit.client.test_client_behaviour import make_target

pytestmark = pytest.mark.integration

ENVIRONMENT = "dev"
CATEGORY_COMBO = "qAZsZ5kBwLk"
UID = "dxContract0"


class ScriptedClient:
    """按逻辑资源名应答的假 Client；同时记录每一次调用。"""

    def __init__(self, target: Any, *, fail_commit: bool = False, drift: bool = False) -> None:
        self.target = target
        self.default_scope = CredentialScope.DISCOVERY
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail_commit = fail_commit
        self.drift = drift
        self.import_modes: list[str] = []

    def with_scope(self, scope: CredentialScope) -> ScriptedClient:
        clone = ScriptedClient(self.target, fail_commit=self.fail_commit, drift=self.drift)
        clone.calls = self.calls
        clone.import_modes = self.import_modes
        clone.default_scope = scope
        return clone

    def call(
        self,
        logical_name: str,
        *,
        scope: CredentialScope | None = None,
        dhis2_type: str | None = None,
        path_args: Any | None = None,
        params: Any | None = None,
        body: Any | None = None,
        extra_headers: Any | None = None,
    ) -> Any:
        used_scope = scope or self.default_scope
        self.calls.append((logical_name, used_scope.value, dict(params or {})))
        if logical_name == "metadata.import":
            mode = str((params or {}).get("importMode", ""))
            self.import_modes.append(mode)
            if mode == "COMMIT" and self.fail_commit:
                raise Dhis2UnreachableError(
                    "the connection dropped after the request was sent",
                    details={"simulated": True},
                )
            return _Response(200, _import_report(mode))
        if logical_name == "metadata.read.one":
            return _Response(200, _data_element(last_updated="2026-01-01T00:00:00.000"))
        if logical_name == "me":
            return _Response(200, {"id": "me", "authorities": [{"name": "ALL"}]})
        if logical_name == "system.info":
            return _Response(200, {"version": "2.41.10"})
        raise AssertionError(f"脚本没有准备 {logical_name} 的应答")

    def list_all(
        self,
        dhis2_type: str,
        *,
        fields: str,
        filters: Any = (),
        page_size: int | None = None,
        limit: int | None = None,
        progress: Any = None,
        cancel: Any = None,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(("metadata.read", self.default_scope.value, {"filters": list(filters)}))
        last_updated = "2026-02-02T10:00:00.000" if self.drift else "2026-01-01T00:00:00.000"
        return iter([_data_element(last_updated=last_updated, drifted=self.drift)])


class _Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        self.headers: dict[str, str] = {}
        self.raw = b""

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def job_ref(self) -> str | None:
        return None


def _data_element(*, last_updated: str, drifted: bool = False) -> dict[str, Any]:
    return {
        "id": UID,
        "code": "contract_code",
        "name": "Drifted name" if drifted else "Contract element",
        "created": "2025-01-01T00:00:00.000",
        "lastUpdated": last_updated,
    }


def _import_report(mode: str) -> dict[str, Any]:
    return {
        "status": "OK" if mode == "VALIDATE" else "SUCCESS",
        "response": {
            "stats": {"created": 1, "updated": 0, "deleted": 0, "ignored": 0},
            "objects": [
                {
                    "klass": "dataElement",
                    "name": "Contract element",
                    "status": "OK",
                    "params": {"id": UID},
                }
            ],
        },
    }


@pytest.fixture
def pipeline(harness: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """登记环境、Capability Profile 索引与一个载荷包，并把管线接到脚本化 Client 上。"""
    registry.load_builtin_profiles()
    profile = registry.profile_for_version("2.41.10")

    spec = EnvironmentSpec(
        environment_key=ENVIRONMENT,
        name="本地脚本化环境",
        base_url="https://dev.example.org",
        environment_class=EnvironmentClass.TEST,
        version_constraint="2.41.x",
        auth_mode=AuthMode.BASIC,
        credentials=[
            CredentialRef(scope=scope, secret_ref=f"memory://dx/{scope.value}")
            for scope in CredentialScope
        ],
        tls_required=True,
        llm_data_policy=LlmDataPolicy.PRIVATE_NETWORK,
        timeout_seconds=60,
        status=EnvironmentStatus.ACTIVE,
    )
    with unit_of_work() as session:
        environment_service.create_environment(spec, session=session, operator="tester")
        # DX-EXE-012：计划生成需要一行连接测试结果作为权限依据
        record_connection_test(ENVIRONMENT, session=session)

    target = _target(profile)
    document = build_capability_profile_document(
        profile,
        environment_key=ENVIRONMENT,
        dhis2_version="2.41.10",
        probed_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    )
    handle = artifacts.stage_document(
        document.model_dump(mode="json", by_alias=True),
        project_id="prj-contract",
        artifact_type="CAPABILITY_PROFILE",
    )
    with unit_of_work() as session:
        info = artifacts.register_staged(
            handle,
            project_id="prj-contract",
            artifact_type="CAPABILITY_PROFILE",
            session=session,
        )
        session.add(
            CapabilityProfileIndexRow(
                id=new_id("dxcap"),
                environment_key=ENVIRONMENT,
                profile_version=profile.profile_version,
                dhis2_version="2.41.10",
                probed_at=datetime(2026, 1, 1, tzinfo=UTC),
                stale=False,
                artifact_id=info.id,
                content_hash=info.content_hash,
            )
        )

    state: dict[str, Any] = {"client": ScriptedClient(target), "target": target}

    def fake_load_context(
        environment_key: str,
        package_artifact_id: str,
        *,
        project_id: str | None = None,
        scope: CredentialScope = CredentialScope.DISCOVERY,
        require_probe: bool = True,
        complete_manifest: bool = True,
    ) -> RunContext:
        package = read_package(package_artifact_id)
        client = state["client"].with_scope(scope)
        return RunContext(
            environment_key=environment_key,
            project_id=project_id or package.project_id,
            target=state["target"],
            package=package,
            client=client,
        )

    def make_package(
        *,
        code: str,
        disposition: str = "CREATE",
        observation_baselines: list[dict[str, Any]] | None = None,
    ) -> str:
        return contract_packages.build_single_object_package(
            environment_key=ENVIRONMENT,
            code=code,
            name="Contract element",
            uid=UID,
            category_combo=CATEGORY_COMBO,
            disposition=disposition,
            observation_baselines=observation_baselines,
        )

    monkeypatch.setattr("factory_dhis2.execution.pipeline.load_context", fake_load_context)
    monkeypatch.setattr("factory_dhis2.execution.commit.load_context", fake_load_context)
    state["package_artifact_id"] = make_package(code="contract_code")
    state["make_package"] = make_package
    return state


def _target(profile: Any) -> Any:
    target = make_target()
    from factory_dhis2.target import EnvironmentTarget

    return EnvironmentTarget(
        key=ENVIRONMENT,
        name="本地脚本化环境",
        base_url="https://dev.example.org",
        environment_class=EnvironmentClass.TEST,
        auth_mode=AuthMode.BASIC,
        tls_required=True,
        llm_data_policy=LlmDataPolicy.PRIVATE_NETWORK,
        timeout_seconds=60,
        status=EnvironmentStatus.ACTIVE,
        profile=profile,
        dhis2_version="2.41.10",
        credentials=target.credentials,
    )


def _validate(pipeline: dict[str, Any], key: str = "k1") -> Any:
    return prepare_and_validate(
        ENVIRONMENT,
        "prj-contract",
        pipeline["package_artifact_id"],
        pipeline["package"].artifact_hash,
        operator="tester",
        idempotency_key=f"{key}:validate",
    )


def test_prepare_and_validate_produces_plan_snapshot_and_report(pipeline: dict[str, Any]) -> None:
    """SRS 6.3：R3 产出计划、Snapshot、ImportReport 与校验记录。"""
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    outcome = _validate(pipeline)
    assert outcome.passed is True, outcome.issues
    assert outcome.execution_plan_artifact_id and outcome.snapshot_artifact_id
    assert outcome.import_report_artifact_id
    assert outcome.payload_hash == pipeline["package"].artifact_hash
    assert not outcome.drift or not outcome.drift.drifted

    with unit_of_work() as session:
        row = session.get(ExecutionRow, outcome.execution_id or "")
        assert row is not None and row.mode == "VALIDATE"
        records = list(
            session.query(ValidationRecordRow).filter_by(
                environment_key=ENVIRONMENT, result="PASSED"
            )
        )
        assert records, "VALIDATE 通过却没有校验记录，COMMIT 的准入依据就断了"
    # VALIDATE 只用只读语义 + import 范围的只读参数（AC-EXE-01）
    assert pipeline["client"].import_modes == ["VALIDATE"]


def test_commit_is_refused_without_a_matching_validate(pipeline: dict[str, Any]) -> None:
    """AC-EXE-02：没有同哈希的成功 VALIDATE 就不许提交。"""
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    with pytest.raises(PayloadHashMismatchError):
        commit_module.commit(
            ENVIRONMENT,
            "prj-contract",
            pipeline["package_artifact_id"],
            pipeline["package"].artifact_hash,
            operator="tester",
            idempotency_key="fresh-key",
        )


def test_commit_locks_validates_and_releases(pipeline: dict[str, Any]) -> None:
    """DX-EXE-030/032/046：提交期间持锁，成功后释放并生成执行报告。"""
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    validate_outcome = _validate(pipeline)
    committed = commit_module.commit(
        ENVIRONMENT,
        "prj-contract",
        pipeline["package_artifact_id"],
        validate_outcome.payload_hash or "",
        operator="tester",
        idempotency_key="k1",
    )
    assert committed.status is ExecutionStatus.COMPLETED, committed.model_dump()
    assert committed.applied is True
    assert committed.import_report_artifact_id and committed.report_artifact_id
    assert "COMMIT" in pipeline["client"].import_modes

    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) is None, "锁没有释放"
        row = session.get(ExecutionRow, committed.execution_id or "")
        assert row is not None and row.report_artifact_id == committed.report_artifact_id


def test_second_committing_execution_is_refused(pipeline: dict[str, Any]) -> None:
    """AC-EXE-04：同一环境同时只允许一个提交类执行。"""
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    _validate(pipeline)
    with unit_of_work() as session:
        blocker = steps_module.create_execution(
            session,
            environment_key=ENVIRONMENT,
            project_id="prj-other",
            mode=commit_module.ExecutionMode.COMMIT,
            operator="other",
        )
        steps_module.mark_running(session, blocker)
        envlock.acquire(session, ENVIRONMENT, blocker.id, operator="other")
        blocker_id = blocker.id

    with pytest.raises(ConcurrencyError):
        commit_module.commit(
            ENVIRONMENT,
            "prj-contract",
            pipeline["package_artifact_id"],
            pipeline["package"].artifact_hash,
            operator="tester",
            idempotency_key="k2",
        )
    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) == blocker_id, "被拒绝的一方不该动锁"


def test_lost_response_keeps_the_lock_and_needs_manual_check(pipeline: dict[str, Any]) -> None:
    """DX-EXE-035 / DX-AC-04：结果不明时锁保持，后续提交被挡住。"""
    pipeline["client"] = ScriptedClient(pipeline["target"], fail_commit=True)
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    _validate(pipeline)
    committed = commit_module.commit(
        ENVIRONMENT,
        "prj-contract",
        pipeline["package_artifact_id"],
        pipeline["package"].artifact_hash,
        operator="tester",
        idempotency_key="k3",
    )
    assert committed.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert committed.applied is None, "结果不明不能声称未生效"
    with unit_of_work() as session:
        holder = envlock.holder(session, ENVIRONMENT)
        assert holder == committed.execution_id, "锁必须在待核实的执行手上"

    # 另一个项目此时提交：不是排队，而是明确的并发错误
    pipeline["client"] = ScriptedClient(pipeline["target"])
    with pytest.raises(ConcurrencyError) as refused:
        commit_module.commit(
            ENVIRONMENT,
            "prj-contract",
            pipeline["package_artifact_id"],
            pipeline["package"].artifact_hash,
            operator="other",
            idempotency_key="k4",
        )
    # DX-AC-04 逐字要求的是 CONCURRENCY_ERROR；"待人工核实"作为原因放在 details 里
    assert refused.value.code == "CONCURRENCY_ERROR"
    assert refused.value.details["reason"] == "MANUAL_CHECK_PENDING"
    assert refused.value.details["holderExecutionId"] == committed.execution_id


def test_commit_replays_the_same_key_without_a_second_import(pipeline: dict[str, Any]) -> None:
    """DX-EXE-036 / NFR-REL-003：同幂等键重复调用只回放，不再提交一次。"""
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    _validate(pipeline)
    first = commit_module.commit(
        ENVIRONMENT,
        "prj-contract",
        pipeline["package_artifact_id"],
        pipeline["package"].artifact_hash,
        operator="tester",
        idempotency_key="k5",
    )
    commits_before = pipeline["client"].import_modes.count("COMMIT")
    second = commit_module.commit(
        ENVIRONMENT,
        "prj-contract",
        pipeline["package_artifact_id"],
        pipeline["package"].artifact_hash,
        operator="tester",
        idempotency_key="k5",
    )
    assert second.execution_id == first.execution_id
    assert pipeline["client"].import_modes.count("COMMIT") == commits_before


def test_drift_between_validate_and_commit_blocks_the_commit(pipeline: dict[str, Any]) -> None:
    """DX-EXE-032 / AC-EXE-03：提交前的再次漂移检查挡住提交。

    被观察的对象带着观测基准（disposition=UPDATE），所以“别人改了它”是漂移检查真正在比
    较的东西，而不是状态哈希不一致的副产品——后者的错误信息对审批人没有价值。
    """
    from factory_dhis2.discovery.canonical import object_hash
    from factory_dhis2.profiles import registry

    profile = registry.profile_for_version("2.41.10")
    baseline_hash = object_hash(
        _data_element(last_updated="2026-01-01T00:00:00.000"), profile.volatile_fields
    )
    artifact_id = pipeline["make_package"](
        code="contract_code",
        disposition="UPDATE",
        observation_baselines=[
            {
                "businessKey": "contract.contract_code",
                "uid": UID,
                "observedLastUpdated": "2026-01-01T00:00:00.000",
                "observedHash": baseline_hash,
                "discoveryResultId": None,
            }
        ],
    )
    outcome = prepare_and_validate(
        ENVIRONMENT,
        "prj-contract",
        artifact_id,
        read_package(artifact_id).artifact_hash,
        operator="tester",
        idempotency_key="kdr:validate",
    )
    assert outcome.passed is True, outcome.issues
    assert not (outcome.drift and outcome.drift.drifted)

    pipeline["client"] = ScriptedClient(pipeline["target"], drift=True)
    with pytest.raises(DriftDetectedError) as excinfo:
        commit_module.commit(
            ENVIRONMENT,
            "prj-contract",
            artifact_id,
            read_package(artifact_id).artifact_hash,
            operator="tester",
            idempotency_key="k6",
        )
    assert excinfo.value.details["objects"], "漂移错误必须给出差异对象清单"
    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) is None, "被漂移挡住时必须释放锁"


def test_emergency_lock_blocks_commit(pipeline: dict[str, Any]) -> None:
    """DX-AC-05 / DX-BR-11：紧急锁定开启时提交被拒。"""
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    _validate(pipeline)
    with unit_of_work() as session:
        environment_service_set_lock(session)
    with pytest.raises(EnvironmentLockedError):
        commit_module.commit(
            ENVIRONMENT,
            "prj-contract",
            pipeline["package_artifact_id"],
            pipeline["package"].artifact_hash,
            operator="tester",
            idempotency_key="k7",
        )


def environment_service_set_lock(session: Any) -> None:
    from factory_dhis2.environment import protection

    protection.set_emergency_lock(ENVIRONMENT, True, "演练", "tester", session=session)


def test_outside_commit_window_blocks_production_like_env(pipeline: dict[str, Any]) -> None:
    """DX-ENV-002：窗口外不允许提交（这里直接改保护行的窗口，避免依赖真实时钟）。"""
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    _validate(pipeline)
    with unit_of_work() as session:
        from factory_dhis2.models.environment import EnvironmentProtectionRow

        row = session.get(EnvironmentProtectionRow, ENVIRONMENT)
        assert row is not None
        row.commit_windows = [window_not_now()]
        row.timezone = "UTC"
    with pytest.raises(OutsideCommitWindowError):
        commit_module.commit(
            ENVIRONMENT,
            "prj-contract",
            pipeline["package_artifact_id"],
            pipeline["package"].artifact_hash,
            operator="tester",
            idempotency_key="k8",
        )


def test_validate_records_the_three_elements(pipeline: dict[str, Any]) -> None:
    """DX-EXE-029：哈希、环境、那一刻状态一起记录。"""
    pipeline["package"] = read_package(pipeline["package_artifact_id"])
    outcome = _validate(pipeline)
    assert outcome.environment_state_hash
    with unit_of_work() as session:
        record = session.get(ValidationRecordRow, outcome.validation_record_id or "")
        assert record is not None
        assert record.payload_hash == pipeline["package"].artifact_hash
        assert record.env_state_hash == outcome.environment_state_hash
        assert record.environment_key == ENVIRONMENT
