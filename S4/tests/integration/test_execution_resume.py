"""执行管线的续跑、失败分支与异步提交（DX-EXE-004/029/032/033/036、AC-EXE-02/03/05、DX-AC-03/04）。

`test_execute_pipeline.py` 走的是最顺的那条路。这里补的是**不顺的时候**：

* 同一幂等键的重复调用要回放（DX-EXE-004），已经做完的步骤不再重做——Snapshot 会覆盖、
  VALIDATE 记录会多出无意义的一行；
* 计划不成立、漂移读不回来、服务端校验打不出去，都是**明确的未通过**，不是重试的理由
  （DX-BR-09）；
* COMMIT 只认那次 VALIDATE 用过的参数与那一刻的状态（DX-BR-05、DX-EXE-029）；
* 异步提交的结论要靠**先落库的任务引用**去问，问不到就是待人工核实（DX-AC-03、DX-EXE-035）；
* 一次程序崩溃留下的半成品执行要被接着做完，而不是被当成重复提交（DX-EXE-036）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from factory_foundation.api import new_id, unit_of_work

from factory_dhis2 import artifacts
from factory_dhis2.config import configure, get_settings
from factory_dhis2.contract.capability_profile import build_capability_profile_document
from factory_dhis2.dto.environment import CredentialRef, EnvironmentSpec
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    ExecutionMode,
    ExecutionStatus,
    LlmDataPolicy,
    StepName,
)
from factory_dhis2.environment import service as environment_service
from factory_dhis2.envlock import manager as envlock
from factory_dhis2.errors import (
    Dhis2UnreachableError,
    PayloadHashMismatchError,
    ValidationStaleError,
)
from factory_dhis2.execution import commit as commit_module
from factory_dhis2.execution import drift as drift_module
from factory_dhis2.execution import pipeline as pipeline_module
from factory_dhis2.execution.pipeline import prepare_and_validate
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.models.profile import CapabilityProfileIndexRow
from factory_dhis2.payload.package import read_package
from factory_dhis2.profiles import registry
from factory_dhis2.testing import contract_packages, record_connection_test
from tests.integration.test_execute_pipeline import (
    CATEGORY_COMBO,
    ENVIRONMENT,
    UID,
    ScriptedClient,
    _data_element,
    _Response,
    _target,
)

pytestmark = pytest.mark.usefixtures("harness")

TASK_ID = "task-async-1"
TASK_ROOT = "https://dev.example.org/api"
TERMINAL_MESSAGE = "Import complete with status OK, 1 created, 0 updated, 0 deleted, 0 ignored"


def task_document(*, completed: bool = True) -> list[dict[str, Any]]:
    """2.41 实测：按任务 ID 查回来的是**日志条目数组**。"""
    entries: list[dict[str, Any]] = [
        {"id": "log1", "uid": "log1", "level": "INFO", "message": "importing dataElements", "completed": False}
    ]
    if completed:
        entries.insert(
            0,
            {
                "id": "log0",
                "uid": "log0",
                "level": "INFO",
                "message": TERMINAL_MESSAGE,
                "completed": True,
            },
        )
    return entries


class _TaskResponse(_Response):
    """带 ``Location`` 的提交响应：任务地址按实例给的原样保存（DX-EXE-034）。"""

    def __init__(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        super().__init__(status, payload)
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}

    def job_ref(self) -> str | None:
        return self.headers.get("location")


class AsyncScriptedClient(ScriptedClient):
    """异步提交形态的脚本化 Client：``metadata.import`` 只给任务地址，结论在任务里。"""

    def __init__(self, target: Any, **kwargs: Any) -> None:
        super().__init__(target, **kwargs)
        self.task_documents: list[list[dict[str, Any]]] = [task_document(), task_document()]
        self.task_reads: list[dict[str, Any]] = []
        self.no_response = False

    def with_scope(self, scope: CredentialScope) -> AsyncScriptedClient:
        clone = type(self)(self.target, fail_commit=self.fail_commit, drift=self.drift)
        clone.calls = self.calls
        clone.import_modes = self.import_modes
        clone.task_documents = self.task_documents
        clone.task_reads = self.task_reads
        clone.no_response = self.no_response
        clone.default_scope = scope
        return clone

    def call(self, logical_name: str, **kwargs: Any) -> Any:
        params = dict(kwargs.get("params") or {})
        if logical_name == "metadata.import":
            mode = str(params.get("importMode", ""))
            self.import_modes.append(mode)
            self.calls.append((logical_name, "import", params))
            if self.no_response and mode == "COMMIT":
                return None
            return _TaskResponse(
                200,
                {"status": "OK", "message": "Initiated METADATA_IMPORT (1)", "response": {"id": TASK_ID}},
                {"Location": f"{TASK_ROOT}/system/tasks/METADATA_IMPORT"},
            )
        if logical_name == "system.tasks.one":
            self.calls.append((logical_name, "import", {"path": kwargs.get("path_args")}))
            # 记录轮询这一刻库里已经存下的任务引用：这就是「先落库再轮询」的证据
            with unit_of_work() as session:
                rows = session.query(ExecutionRow).all()
                self.task_reads.append(
                    {row.id: (row.async_job_ref, row.poll_url) for row in rows if row.async_job_ref}
                )
            return _TaskResponse(200, self.task_documents.pop(0) if self.task_documents else task_document())
        return super().call(logical_name, **kwargs)


class DriftBlindClient(ScriptedClient):
    """按 UID 的单对象读回与批量读回给出不同答案：漂移检查读不回来时走哪条路。"""

    def __init__(self, target: Any, *, fail_reads: bool = False, **kwargs: Any) -> None:
        super().__init__(target, **kwargs)
        self.fail_reads = fail_reads

    def with_scope(self, scope: CredentialScope) -> DriftBlindClient:
        clone = type(self)(self.target, fail_reads=self.fail_reads, fail_commit=self.fail_commit, drift=self.drift)
        clone.calls = self.calls
        clone.import_modes = self.import_modes
        clone.default_scope = scope
        return clone

    def list_all(self, *args: Any, **kwargs: Any) -> Any:
        if self.fail_reads:
            raise Dhis2UnreachableError("the instance did not answer the batch read", details={})
        return super().list_all(*args, **kwargs)


class ImportBrokenClient(ScriptedClient):
    """服务端校验打不出去：VALIDATE 这一步就是一次实例调用，它可能失败。"""

    def with_scope(self, scope: CredentialScope) -> ImportBrokenClient:
        clone = type(self)(self.target, fail_commit=self.fail_commit, drift=self.drift)
        clone.calls = self.calls
        clone.import_modes = self.import_modes
        clone.default_scope = scope
        return clone

    def call(self, logical_name: str, **kwargs: Any) -> Any:
        if logical_name == "metadata.import":
            self.calls.append((logical_name, "import", dict(kwargs.get("params") or {})))
            raise Dhis2UnreachableError("the validate request never reached the instance", details={})
        return super().call(logical_name, **kwargs)


@pytest.fixture
def stage(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """登记环境、Capability Profile 与载荷包，并把管线接到脚本化 Client 上。"""
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
            CredentialRef(scope=scope, secret_ref=f"memory://dx/{scope.value}") for scope in CredentialScope
        ],
        tls_required=True,
        llm_data_policy=LlmDataPolicy.PRIVATE_NETWORK,
        timeout_seconds=60,
        status=EnvironmentStatus.ACTIVE,
    )
    with unit_of_work() as session:
        environment_service.create_environment(spec, session=session, operator="tester")
        record_connection_test(ENVIRONMENT, session=session)

    target = _target(profile)
    profile_document = build_capability_profile_document(
        profile,
        environment_key=ENVIRONMENT,
        dhis2_version="2.41.10",
        probed_at="2026-01-01T00:00:00+00:00",
    )
    document = artifacts.stage_document(
        profile_document.model_dump(mode="json", by_alias=True),
        project_id="prj-contract",
        artifact_type="CAPABILITY_PROFILE",
    )
    with unit_of_work() as session:
        info = artifacts.register_staged(
            document, project_id="prj-contract", artifact_type="CAPABILITY_PROFILE", session=session
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

    state: dict[str, Any] = {"client": ScriptedClient(target), "target": target, "async": False}

    def fake_load_context(
        environment_key: str,
        package_artifact_id: str,
        *,
        project_id: str | None = None,
        scope: CredentialScope = CredentialScope.DISCOVERY,
        require_probe: bool = True,
        complete_manifest: bool = True,
    ) -> Any:
        from factory_dhis2.execution.context import RunContext

        package = read_package(package_artifact_id)
        base = state["client"]
        if state["async"] and isinstance(base, ScriptedClient) and not isinstance(base, AsyncScriptedClient):
            base = AsyncScriptedClient(target, fail_commit=base.fail_commit, drift=base.drift)
            state["client"] = base
        return RunContext(
            environment_key=environment_key,
            project_id=project_id or package.project_id,
            target=target,
            package=package,
            client=base.with_scope(scope),
        )

    def make_package(**kwargs: Any) -> str:
        return contract_packages.build_single_object_package(
            environment_key=ENVIRONMENT,
            code=kwargs.pop("code", "contract_code"),
            name="Contract element",
            uid=UID,
            category_combo=CATEGORY_COMBO,
            **kwargs,
        )

    monkeypatch.setattr(pipeline_module, "load_context", fake_load_context)
    monkeypatch.setattr(commit_module, "load_context", fake_load_context)
    state["package_artifact_id"] = make_package()
    state["make_package"] = make_package
    state["package"] = read_package(state["package_artifact_id"])
    return state


def _validate(stage: dict[str, Any], key: str = "k1", **changes: Any) -> Any:
    return prepare_and_validate(
        ENVIRONMENT,
        "prj-contract",
        stage["package_artifact_id"],
        stage["package"].artifact_hash,
        operator="tester",
        idempotency_key=f"{key}:validate",
        **changes,
    )


def _commit(stage: dict[str, Any], key: str = "k1", **changes: Any) -> Any:
    return commit_module.commit(
        ENVIRONMENT,
        "prj-contract",
        stage["package_artifact_id"],
        stage["package"].artifact_hash,
        operator="tester",
        idempotency_key=key,
        **changes,
    )


def _watched_package(stage: dict[str, Any]) -> str:
    """一个带观测基准的 UPDATE 包：漂移检查真的有东西可比。"""
    from factory_dhis2.discovery.canonical import object_hash
    from factory_dhis2.profiles import registry as profile_registry

    baseline_hash = object_hash(
        _data_element(last_updated="2026-01-01T00:00:00.000"),
        profile_registry.profile_for_version("2.41.10").volatile_fields,
    )
    return stage["make_package"](
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


# --- 幂等与续跑 --------------------------------------------------------------


def test_a_repeated_validate_with_the_same_key_replays_the_first_outcome(
    stage: dict[str, Any],
) -> None:
    """DX-EXE-004：同一幂等键的第二次调用拿到第一次的结论，一次实例调用都不发。"""
    first = _validate(stage)
    calls_before = len(stage["client"].calls)

    second = _validate(stage)

    assert second.execution_id == first.execution_id
    assert second.passed == first.passed
    assert len(stage["client"].calls) == calls_before


def test_a_resume_completes_only_the_steps_that_are_missing(stage: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """DX-EXE-004：续跑是**查表**——已完成的步骤从库里恢复结论，不重打实例。"""
    first = _validate(stage)
    calls_after_first = len(stage["client"].calls)

    # 幂等记录被抹掉（模拟上一次调用在写回结论之前崩溃），但步骤事实已经落库
    class _Begun:
        already_completed = False
        first_result = None
        created = True

    monkeypatch.setattr(pipeline_module.idempotency, "begin", lambda *_args, **_kwargs: _Begun())
    second = _validate(stage)

    assert second.execution_id == first.execution_id
    assert second.passed is True
    assert len(stage["client"].calls) == calls_after_first, "已完成的 VALIDATE 被重打了一次"
    assert second.snapshot_artifact_id == first.snapshot_artifact_id
    assert second.import_report_artifact_id == first.import_report_artifact_id
    assert second.environment_state_hash == first.environment_state_hash
    completed = {item.step for item in second.steps if item.status == "COMPLETED"}
    assert {"PLAN", "DRIFT_CHECK", "SNAPSHOT", "VALIDATE"} <= completed


def test_progress_is_reported_for_each_stage_of_the_validate(stage: dict[str, Any]) -> None:
    """DX-EXE-004：R3 的每一步都对外报进度，S1 才知道现在走到哪里。"""
    seen: list[str] = []

    class _Progress:
        def report(self, *, step: str, completed_fraction: float | None = None, message: str | None = None) -> None:
            del completed_fraction, message
            seen.append(step)

    _validate(stage, progress=_Progress())

    # 载荷里没有服务端表达式，EXPRESSION_VALIDATE 这一步就不该被"报"出来
    assert seen == ["PLAN", "DRIFT_CHECK", "SNAPSHOT", "VALIDATE"]


# --- 明确的未通过，而不是重试 ------------------------------------------------


def test_a_plan_that_does_not_stand_fails_the_validate_without_touching_the_instance(
    stage: dict[str, Any],
) -> None:
    """DX-EXE-012：计划不成立时不打实例，也不假装做过 Snapshot 与校验。"""
    from tests.unit.execution import register_package_artifact

    manifest_missing = dict(stage["package"].manifest)
    del manifest_missing["controlledFields"]
    artifact_id = register_package_artifact(
        {"dataElements": [_data_element(last_updated="2026-01-01")]}, manifest_missing
    )
    stage["package_artifact_id"] = artifact_id
    stage["package"] = read_package(artifact_id)

    outcome = _validate(stage, key="k-blocked")

    assert outcome.passed is False
    assert [issue.rule_id for issue in outcome.issues] == ["DX-ASM-02"]
    assert outcome.execution_plan_artifact_id, "计划仍然要产出并保存，拒绝的是它的结论而不是不留痕迹"
    assert outcome.snapshot_artifact_id is None
    assert outcome.import_report_artifact_id is None
    assert stage["client"].import_modes == []
    with unit_of_work() as session:
        row = session.get(ExecutionRow, str(outcome.execution_id))
        assert row is not None and row.status == ExecutionStatus.FAILED.value
        assert row.detail == "执行计划不成立（DX-EXE-012）"


def test_a_blocked_plan_still_blocks_when_the_execution_resumes(
    stage: dict[str, Any],
) -> None:
    """DX-EXE-012 / DX-EXE-033：续跑读的是持久化的计划文档，阻塞项必须跟着它一起存下来。

    第一次调用在 PLAN 步就落库并失败；进程崩溃后重试走的是恢复路径 —— 它不再重新生成计划，
    只把那份文档读回来。所以计划里的问题若只存在于 ``build_plan`` 的返回值里，第二次尝试就会
    带着「零阻塞」的结论直通 VALIDATE，而实例并没有变得可以提交。
    """
    from factory_dhis2.execution import steps
    from factory_dhis2.execution.pipeline import _issues_from_document
    from tests.unit.execution import register_package_artifact

    manifest_missing = dict(stage["package"].manifest)
    del manifest_missing["controlledFields"]
    artifact_id = register_package_artifact(
        {"dataElements": [_data_element(last_updated="2026-01-01")]}, manifest_missing
    )
    stage["package_artifact_id"] = artifact_id
    stage["package"] = read_package(artifact_id)

    outcome = _validate(stage, key="k-blocked-once")
    assert outcome.passed is False

    with unit_of_work() as session:
        detail = steps.step_detail(session, str(outcome.execution_id), StepName.PLAN.value)
    document = dict(detail.get("document") or {})

    recovered = [issue for issue in _issues_from_document(document) if issue.is_blocking]
    assert [issue.rule_id for issue in recovered] == ["DX-ASM-02"], (
        "the plan step persisted its document but not its verdict, so a resumed execution "
        "would read an unblocked plan and go straight to VALIDATE"
    )
    assert stage["client"].import_modes == [], "the first attempt never asked the instance"


def test_a_drift_check_that_cannot_read_the_environment_is_a_failing_validate(
    stage: dict[str, Any],
) -> None:
    """DX-CLI-001：漂移检查读不回来是未通过，不是「当作没漂移」。"""
    stage["package_artifact_id"] = _watched_package(stage)
    stage["package"] = read_package(stage["package_artifact_id"])
    stage["client"] = DriftBlindClient(stage["target"], fail_reads=True)

    outcome = _validate(stage, key="k-unreachable")

    assert outcome.passed is False
    assert [issue.rule_id for issue in outcome.issues] == ["DX-CLI-001"]
    assert "漂移检查无法完成" in outcome.issues[0].description
    assert stage["client"].import_modes == []
    with unit_of_work() as session:
        row = session.get(ExecutionRow, str(outcome.execution_id))
        assert row is not None and row.detail == "漂移检查无法读取目标环境"


def test_drift_found_before_validate_is_reported_per_object(stage: dict[str, Any]) -> None:
    """DX-EXE-020 / AC-EXE-03：审批前的漂移逐个对象列出，并把校验判为未通过。"""
    stage["package_artifact_id"] = _watched_package(stage)
    stage["package"] = read_package(stage["package_artifact_id"])
    stage["client"] = ScriptedClient(stage["target"], drift=True)

    outcome = _validate(stage, key="k-drifted")

    assert outcome.passed is False
    assert outcome.drift is not None and outcome.drift.drifted is True
    assert [issue.rule_id for issue in outcome.issues] == ["DX-EXE-020"]
    assert outcome.issues[0].object_uid == UID
    assert "CONTENT_CHANGED" in outcome.issues[0].description
    assert outcome.import_report_artifact_id is None, "漂移动了就不该再打实例校验"
    assert outcome.snapshot_artifact_id, "Snapshot 在漂移检查之后仍要留下那一刻的状态"


def test_a_server_validation_that_never_arrives_is_a_failing_validate(stage: dict[str, Any]) -> None:
    """DX-EXE-029：校验请求没送达就是「没验过」，绝不能被当成通过。"""
    stage["client"] = ImportBrokenClient(stage["target"])

    outcome = _validate(stage, key="k-broken-import")

    assert outcome.passed is False
    assert outcome.issues[0].rule_id == "DHIS2_UNREACHABLE"
    assert "服务端校验无法完成" in outcome.issues[0].description
    assert outcome.drift is not None and outcome.drift.drifted is False


# --- 异步提交（DX-AC-03、DX-EXE-033/034）------------------------------------


def test_async_commit_persists_the_task_reference_before_it_asks_for_the_result(
    stage: dict[str, Any],
) -> None:
    """DX-AC-03：异步提交先落任务引用与查询地址，再去问结果——恢复时才不用重提交。"""
    stage["async"] = True
    outcome = _validate(stage, key="k-async", import_options={"async": True})
    assert outcome.passed is True, outcome.issues

    client = stage["client"]
    assert isinstance(client, AsyncScriptedClient), "VALIDATE 也走了异步提交，用的是同一个 Client"
    committed = _commit(stage, key="k-async")

    assert committed.status is ExecutionStatus.COMPLETED, committed.model_dump()
    assert committed.applied is True
    assert client.import_modes == ["VALIDATE", "COMMIT"]
    assert client.task_reads, "轮询开始了却还没有已落库的任务引用"
    persisted = next(iter(client.task_reads[-1].values()))
    assert persisted[0] == TASK_ID, "任务引用必须在第一次轮询之前就已经落库"
    assert persisted[1] and persisted[1].endswith(f"/{TASK_ID}")
    with unit_of_work() as session:
        row = session.get(ExecutionRow, str(committed.execution_id))
        assert row is not None
        assert row.async_job_ref == TASK_ID
        commit_step = next(item for item in steps_info(session, str(committed.execution_id)) if item.step == "COMMIT")
        assert commit_step.detail["jobRef"] == TASK_ID
    assert read_json_artifact(str(committed.import_report_artifact_id))["report"]["reportSource"] == "TASK_LOG"


def test_an_async_validate_persists_its_task_reference_too(stage: dict[str, Any]) -> None:
    """DX-EXE-033 / FR-EXE-009：VALIDATE 的异步任务引用也必须先落库。

    COMMIT 一侧早就这样做了，校验一侧没有——一次大包的异步校验若在轮询中途崩溃，恢复时
    找不到任务引用，只能重新提交。重新校验本身无害，但它意味着那次崩溃前后的两次运行给出
    两个结论，而 DX-AC-03 承诺的是"接着问同一个任务"，不是"再问一次"。
    """
    stage["async"] = True
    outcome = _validate(stage, key="k-async-validate", import_options={"async": True})
    assert outcome.passed is True, outcome.issues

    with unit_of_work() as session:
        row = session.get(ExecutionRow, str(outcome.execution_id))
        assert row is not None
        assert row.async_job_ref == TASK_ID, (
            "the VALIDATE run polled an asynchronous task it never recorded"
        )
        assert row.poll_url and row.poll_url.endswith(f"/{TASK_ID}")


def test_a_failing_execution_still_produces_an_execution_report(stage: dict[str, Any]) -> None:
    """DX-EXE-046：执行报告是产物，失败的那一次同样要留档。

    成功路径写报告、失败路径不写，结果是追溯链上只有"跑成了"的执行——AC-AUD-01 要的是任一
    COMMIT 可追溯，审批人重新提交前要看恰恰是上一次为什么被拒。
    """
    from factory_dhis2.artifacts import read_json

    stage["client"] = ImportBrokenClient(stage["target"])
    outcome = _validate(stage, key="k-report-when-failed")
    assert outcome.passed is False

    with unit_of_work() as session:
        row = session.get(ExecutionRow, str(outcome.execution_id))
        assert row is not None and row.status == ExecutionStatus.FAILED.value
        assert row.report_artifact_id, "a failed execution left no EXECUTION_REPORT behind"
        report_artifact_id = row.report_artifact_id
    # reading artifact bytes is external I/O and must happen outside the unit of work
    document = read_json(report_artifact_id)
    assert document["kind"] == "EXECUTION_REPORT"
    assert document["status"] == ExecutionStatus.FAILED.value
    assert document["executionId"] == str(outcome.execution_id)
    assert document["extra"]["failureReason"], "the report must say why it ended this way"
    assert document["issues"], "a failing report has to carry what made it fail"


def steps_info(session: Any, execution_id: str) -> Any:
    from factory_dhis2.execution import steps

    return steps.steps_as_info(session, execution_id)


def read_json_artifact(artifact_id: str) -> dict[str, Any]:
    from factory_dhis2.artifacts import read_json

    return read_json(artifact_id)


def test_an_async_task_that_never_finishes_needs_a_manual_check_and_keeps_the_job(
    stage: dict[str, Any],
) -> None:
    """DX-EXE-035：任务到点没终态 = 无法确认。锁保持，任务引用留着给人问。"""
    from factory_dhis2 import deps

    configure(get_settings().with_overrides(async_poll_timeout_seconds=0.001, async_poll_interval_seconds=0.0))
    deps.set_sleeper(lambda _seconds: None)
    stage["async"] = True
    outcome = _validate(stage, key="k-lost-async", import_options={"async": True})
    assert outcome.passed is True, outcome.issues

    client = stage["client"]
    client.task_documents = [task_document(completed=False) for _ in range(20)]
    committed = _commit(stage, key="k-lost-async")

    assert committed.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert committed.applied is None
    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) == committed.execution_id
        row = session.get(ExecutionRow, str(committed.execution_id))
        assert row is not None
        assert row.async_job_ref == TASK_ID, "待核实的执行必须带着能问的地址"
        assert row.poll_url and row.poll_url.endswith(f"/{TASK_ID}")


def test_a_sync_commit_that_returned_no_response_at_all_is_unconfirmed(
    stage: dict[str, Any],
) -> None:
    """DX-EXE-031：同步提交没拿到任何响应同样是「不知道」，不能按失败释放锁后重提。"""
    _validate(stage, key="k-empty")
    client = AsyncScriptedClient(stage["target"])
    client.no_response = True
    stage["client"] = client

    committed = _commit(stage, key="k-empty")

    assert committed.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert committed.applied is None
    assert committed.detail
    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) == committed.execution_id


def _crashed_async_execution(stage: dict[str, Any], key: str) -> str:
    """造出"提交已经发生、任务引用已落库、进程在轮询中间被杀"留下的那一行。"""
    from factory_dhis2.execution import steps

    with unit_of_work() as session:
        execution = steps.create_execution(
            session,
            environment_key=ENVIRONMENT,
            project_id="prj-contract",
            mode=ExecutionMode.COMMIT,
            payload_artifact_id=stage["package_artifact_id"],
            payload_hash=stage["package"].artifact_hash,
            idempotency_key=key,
            operator="tester",
        )
        steps.mark_running(session, execution)
        envlock.acquire(session, ENVIRONMENT, execution.id, operator="tester", note="COMMIT")
        steps.remember_job(
            session,
            execution,
            job_ref=TASK_ID,
            poll_url=f"{TASK_ROOT}/system/tasks/METADATA_IMPORT/{TASK_ID}",
        )
        execution.snapshot_artifact_id = "art-crash-snapshot"
        return str(execution.id)


def test_a_crash_during_polling_resumes_the_task_instead_of_resubmitting(
    stage: dict[str, Any],
) -> None:
    """DX-AC-03 / DX-EXE-034 / DX-BR-08：崩在轮询途中后，同幂等键重入**继续查询原任务**。

    这是整条恢复语义的验收点：重提交一次不可逆的元数据导入，等于把已经生效的东西再做一遍；
    直接判定 NEEDS_MANUAL_CHECK 又把一个本可以自动问出来的结果推给人工。两条都不许。
    """
    stage["async"] = True
    outcome = _validate(stage, key="k-crash", import_options={"async": True})
    assert outcome.passed is True, outcome.issues
    client = stage["client"]
    assert isinstance(client, AsyncScriptedClient)

    execution_id = _crashed_async_execution(stage, "k-crash")
    reads_before = len(client.task_reads)
    committed = _commit(stage, key="k-crash")

    assert committed.execution_id == execution_id, "必须接着上一次执行，而不是新建一次"
    assert committed.status is ExecutionStatus.COMPLETED, committed.model_dump()
    assert committed.applied is True
    assert client.import_modes == ["VALIDATE"], (
        f"恢复时重新提交了导入：{client.import_modes}"
    )
    assert len(client.task_reads) == reads_before + 1, "恢复应当且仅当去问那一个已存的任务"
    assert committed.import_report_artifact_id

    with unit_of_work() as session:
        row = session.get(ExecutionRow, execution_id)
        assert row is not None
        assert row.snapshot_artifact_id == "art-crash-snapshot", "恢复沿用的是上一次提交的 Snapshot"
        assert row.status == ExecutionStatus.COMPLETED.value
        assert envlock.holder(session, ENVIRONMENT) is None, "终态已确认，锁必须释放"
        poll_steps = [item for item in steps_info(session, execution_id) if item.step == "ASYNC_POLL"]
        assert poll_steps and poll_steps[0].detail.get("resumed") is True


def test_a_resumed_commit_that_still_cannot_be_confirmed_keeps_the_lock(
    stage: dict[str, Any],
) -> None:
    """DX-EXE-034 的另一半：恢复后仍然问不到终态时，回到 NEEDS_MANUAL_CHECK 且锁保持。"""
    from factory_dhis2 import deps

    configure(get_settings().with_overrides(
        async_poll_timeout_seconds=0.001, async_poll_interval_seconds=0.0
    ))
    deps.set_sleeper(lambda _seconds: None)
    stage["async"] = True
    _validate(stage, key="k-crash-lost", import_options={"async": True})
    client = stage["client"]
    assert isinstance(client, AsyncScriptedClient)
    execution_id = _crashed_async_execution(stage, "k-crash-lost")
    client.task_documents = [task_document(completed=False) for _ in range(20)]

    committed = _commit(stage, key="k-crash-lost")

    assert committed.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert committed.execution_id == execution_id
    assert client.import_modes == ["VALIDATE"], "恢复路径永远不许重新提交"
    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) == execution_id
        row = session.get(ExecutionRow, execution_id)
        assert row is not None and row.async_job_ref == TASK_ID


# --- COMMIT 的准入 ----------------------------------------------------------


def test_commit_never_takes_new_import_parameters(stage: dict[str, Any]) -> None:
    """DX-BR-05：COMMIT 用的参数就是那次 VALIDATE 用的那份，调用方不能临场改。"""
    _validate(stage, key="k-options")

    with pytest.raises(PayloadHashMismatchError) as excinfo:
        _commit(stage, key="k-options-2", import_options={"atomicMode": "NONE"})

    assert excinfo.value.details["rule"] == "DX-EXE-032"
    assert excinfo.value.details["requested"] == {"atomicMode": "NONE"}
    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) is None, "被拒绝的一方不该留下锁"


def test_a_state_that_changed_since_validate_is_refused(stage: dict[str, Any]) -> None:
    """DX-EXE-029：校验记录里的「那一刻状态」与提交时不一致，就要求重新校验。"""
    outcome = _validate(stage, key="k-stale")
    with unit_of_work() as session:
        from factory_dhis2.models.execution import ValidationRecordRow

        record = session.get(ValidationRecordRow, str(outcome.validation_record_id))
        assert record is not None
        record.env_state_hash = "sha256:" + "f" * 64

    with pytest.raises(ValidationStaleError) as excinfo:
        _commit(stage, key="k-stale-commit")

    assert excinfo.value.details["validatedState"] == "sha256:" + "f" * 64
    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) is None, "被挡住时必须释放锁"
        assert stage["client"].import_modes == ["VALIDATE"], "状态过期后不许再打 COMMIT"


def test_a_commit_without_a_stored_plan_falls_back_to_the_profile_defaults(stage: dict[str, Any]) -> None:
    """DX-BR-05：那次 VALIDATE 的计划读不到时退回 Profile 默认值，而不是自己编一套参数。"""
    outcome = _validate(stage, key="k-noplan")
    with unit_of_work() as session:
        row = session.get(ExecutionRow, str(outcome.execution_id))
        assert row is not None
        row.plan_artifact_id = None

    committed = _commit(stage, key="k-noplan-commit")

    assert committed.status is ExecutionStatus.COMPLETED, committed.model_dump()
    params = [item[2] for item in stage["client"].calls if item[0] == "metadata.import"][-1]
    assert params["importStrategy"] == "CREATE_AND_UPDATE"
    assert params["identifier"] == "ASSIGNED"
    assert params["importMode"] == "COMMIT"


def test_an_execution_left_planned_by_a_crash_is_resumed_not_replayed(stage: dict[str, Any]) -> None:
    """DX-EXE-036：上一次调用在装配前就崩了（执行停在 PLANNED），这次接着做完而不是回放空结论。"""
    _validate(stage, key="k-crashed")
    crash_key = "k-crashed-commit"
    with unit_of_work() as session:
        session.add(
            ExecutionRow(
                id="dxexe-crash-0000000000001",
                project_id="prj-contract",
                environment_key=ENVIRONMENT,
                executor_type="METADATA",
                mode="COMMIT",
                status="PLANNED",
                idempotency_key=crash_key,
                operator="tester",
            )
        )

    committed = _commit(stage, key=crash_key)

    assert committed.execution_id == "dxexe-crash-0000000000001"
    assert committed.status is ExecutionStatus.COMPLETED
    assert committed.applied is True


def test_an_unexpected_failure_releases_the_lock_and_fails_the_execution(
    stage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """DX-EXE-031：一次程序缺陷不能让这台环境永久无法提交。"""
    _validate(stage, key="k-boom")

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("a bug, not a DHIS2 answer")

    monkeypatch.setattr(drift_module, "check", explode)

    with pytest.raises(RuntimeError):
        _commit(stage, key="k-boom-commit")

    with unit_of_work() as session:
        assert envlock.holder(session, ENVIRONMENT) is None
        row = session.query(ExecutionRow).filter_by(idempotency_key="k-boom-commit").one()
        assert row.status == ExecutionStatus.FAILED.value
        assert row.detail == "执行异常终止"
