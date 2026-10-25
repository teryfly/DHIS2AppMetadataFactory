"""执行器注册表与里程碑拒绝（DX-EXE-001/002/037、SRS 12 章）。

DX-EXE-001 要求每个执行器都是同一张脸（``plan`` / ``validate`` / ``commit`` / ``verify``），
S1 因此可以按固定顺序逐个推进而不知道哪一格实现了。本轮（M2）只有 Metadata 是真的，所以
这里要证明的是**拒绝的形状**：

* 未启用的执行器照样注册、照样解析得到（否则“名字都没有”就成了第二个 bug）；
* 调用它得到 ``DX_FEATURE_NOT_ENABLED``，消息点名它属于哪个里程碑；
* 拒绝发生在**任何一次 DHIS2 访问之前**——一个还没实现的执行器先去问实例，就是拿真实
  环境替未交付的功能猜行为。所以每条拒绝都同时断言“实例一次请求都没收到”，并且用一次
  真实调用作正对照，证明这个断言不是空的。
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_dhis2 import deps
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.dto.execution import ExecutionOutcome, PostVerifyOutcome
from factory_dhis2.enums import CredentialScope, ExecutorType
from factory_dhis2.errors import FeatureNotEnabledError
from factory_dhis2.execution.context import RunContext
from factory_dhis2.executors import (
    DEMO_USER_PROVISIONER,
    DEMO_USERS_FEATURE,
    ENABLED_EXECUTORS,
    EXECUTOR_MILESTONES,
    EXECUTOR_RULES,
    EXECUTORS,
    DemoUserProvisioner,
    Executor,
    NotEnabledExecutor,
    executor_for,
    ordered_executors,
    refuse,
    require_enabled,
)
from factory_dhis2.executors.apps import APP_EXECUTOR
from factory_dhis2.executors.base import DEMO_USERS_MILESTONE, DEMO_USERS_RULE
from factory_dhis2.executors.datastore import DATASTORE_EXECUTOR
from factory_dhis2.executors.tracker import TRACKER_EXECUTOR
from factory_dhis2.registries import DX_ERROR_CODES
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport
from tests.unit.client.test_client_behaviour import make_target

#: 每个未启用执行器：实例、里程碑、SRS 出处
NOT_ENABLED: list[tuple[Any, str, str]] = [
    (TRACKER_EXECUTOR, "M3", "DX-TRK-001"),
    (DATASTORE_EXECUTOR, "M5", "DX-M5-001"),
    (APP_EXECUTOR, "M5", "DX-M5-002"),
]


def live_context(instance: FakeDhis2Instance) -> RunContext:
    """一个**真的**能打到实例的上下文：拒绝必须发生在用它之前。"""
    deps.set_transport(FakeDhis2Transport(instance))
    deps.set_sleeper(lambda seconds: None)
    target = make_target()
    return RunContext(
        environment_key="dev",
        project_id="prj-1",
        target=target,
        package=None,  # 未启用的执行器不允许碰载荷
        client=Dhis2Client(target, scope=CredentialScope.DISCOVERY),
    )


def test_the_transport_really_reaches_the_instance() -> None:
    """正对照：下面的“零请求”断言必须有牙齿。"""
    instance = FakeDhis2Instance()
    context = live_context(instance)
    assert context.client.call("system.info").payload["version"] == instance.version
    assert [row["path"] for row in instance.requests] == ["system/info"]


# --- 注册表 ---------------------------------------------------------------


def test_every_executor_type_is_registered_even_when_disabled() -> None:
    """DX-EXE-002：本轮未实现的执行器照样登记，缺席的是能力而不是名字。"""
    assert set(EXECUTORS) == set(ExecutorType)
    assert [executor_for(item) for item in ExecutorType] == [
        EXECUTORS[item] for item in ExecutorType
    ]
    assert EXECUTOR_MILESTONES == {
        ExecutorType.METADATA: "M2",
        ExecutorType.TRACKER_DATA: "M3",
        ExecutorType.DATASTORE: "M5",
        ExecutorType.APP_UPLOAD: "M5",
    }


def test_only_the_metadata_executor_is_enabled_in_m2() -> None:
    """SRS 12 章：M2 只有 Metadata 可用，这一事实是可查询的而不是靠试调用发现。"""
    assert frozenset({ExecutorType.METADATA}) == ENABLED_EXECUTORS
    assert TRACKER_EXECUTOR.enabled is False
    assert DATASTORE_EXECUTOR.enabled is False
    assert APP_EXECUTOR.enabled is False
    assert EXECUTORS[ExecutorType.METADATA].enabled is True


def test_executor_for_accepts_the_type_name_as_text_too() -> None:
    """DX-EXE-001：计划文档里写的是字符串，注册表按同一套词汇解析。"""
    assert executor_for("METADATA") is EXECUTORS[ExecutorType.METADATA]
    assert executor_for(ExecutorType.TRACKER_DATA) is TRACKER_EXECUTOR
    with pytest.raises(ValueError):
        executor_for("SOMETHING_ELSE")


def test_ordered_executors_follows_the_fixed_sequence() -> None:
    """DX-EXE-037 / DX-BR-12：Metadata → DataStore → Tracker Data → App Upload。"""
    assert [item.executor_type for item in ordered_executors()] == [
        ExecutorType.METADATA,
        ExecutorType.DATASTORE,
        ExecutorType.TRACKER_DATA,
        ExecutorType.APP_UPLOAD,
    ]


def test_ordered_executors_keeps_the_sequence_when_filtered() -> None:
    """DX-EXE-037：调用方给的顺序不能改执行顺序，只决定哪些格子参与。"""
    chosen = ordered_executors(["TRACKER_DATA", "METADATA"])
    assert [item.executor_type for item in chosen] == [
        ExecutorType.METADATA,
        ExecutorType.TRACKER_DATA,
    ]
    assert ordered_executors([]) == [], "没计划任何执行器就没有任何一格"
    assert ordered_executors(["NOT_A_TYPE"]) == []
    assert [item.executor_type for item in ordered_executors(None)] == [
        ExecutorType.METADATA,
        ExecutorType.DATASTORE,
        ExecutorType.TRACKER_DATA,
        ExecutorType.APP_UPLOAD,
    ], "planned=None 就是全部四格，按固定顺序"


# --- require_enabled ------------------------------------------------------


def test_require_enabled_returns_only_the_delivered_executor() -> None:
    """DX-EXE-002：编排前的检查放过 M2、挡住后面每一个。"""
    assert require_enabled(ExecutorType.METADATA) is EXECUTORS[ExecutorType.METADATA]
    assert require_enabled("METADATA") is require_enabled(ExecutorType.METADATA)


@pytest.mark.parametrize("executor_type", [ExecutorType.TRACKER_DATA, ExecutorType.DATASTORE, ExecutorType.APP_UPLOAD])
def test_require_enabled_names_the_milestone_and_makes_no_call(executor_type: ExecutorType) -> None:
    """DX-EXE-002 / SRS 12 章：拒绝里点名里程碑，且一次 DHIS2 请求都不发。"""
    instance = FakeDhis2Instance()
    live_context(instance)

    with pytest.raises(FeatureNotEnabledError) as excinfo:
        require_enabled(executor_type)

    error = excinfo.value
    assert error.code == "DX_FEATURE_NOT_ENABLED"
    assert EXECUTOR_MILESTONES[executor_type] in str(error)
    assert EXECUTOR_RULES[executor_type] in str(error)
    assert error.details["milestone"] == EXECUTOR_MILESTONES[executor_type]
    assert error.details["feature"] == f"{executor_type.value} executor"
    assert error.details["enabled"] == ["METADATA"]
    assert instance.requests == [], "未启用的执行器碰了实例"


def test_feature_not_enabled_is_registered_as_non_retryable() -> None:
    """SRS 12 章 / registries：重试一个还没交付的能力只会得到同一个拒绝。"""
    assert DX_ERROR_CODES["DX_FEATURE_NOT_ENABLED"][1] is False


# --- 四个方法的拒绝 --------------------------------------------------------


@pytest.mark.parametrize("executor,milestone,rule", NOT_ENABLED)
def test_disabled_executors_refuse_all_four_operations_without_touching_dhis2(
    executor: NotEnabledExecutor, milestone: str, rule: str
) -> None:
    """DX-EXE-001/002：统一接口的每一格都拒绝，拒绝里点名里程碑与 SRS 出处。"""
    instance = FakeDhis2Instance()
    context = live_context(instance)
    options: dict[str, Any] = {"importMode": "COMMIT"}

    calls = [
        ("plan", lambda: executor.plan(context)),
        ("validate", lambda: executor.validate(context, options, {}, state_hash="sha256:" + "0" * 64)),
        (
            "commit",
            lambda: executor.commit(
                context, options, {}, state_hash="sha256:" + "0" * 64, execution_id="exe-1"
            ),
        ),
        ("verify", lambda: executor.verify(context, execution_id="exe-1")),
    ]
    assert [item.executor_type for item in (executor,)] == [executor.executor_type]

    for operation, call in calls:
        with pytest.raises(FeatureNotEnabledError) as excinfo:
            call()
        message = str(excinfo.value)
        assert f"executor {operation}" in message, message
        assert milestone in message and rule in message
        assert excinfo.value.details == {
            "feature": f"{executor.executor_type.value} executor {operation}",
            "milestone": milestone,
            "rule": rule,
            "executorType": executor.executor_type.value,
        }

    assert instance.requests == [], "拒绝必须发生在任何一次 DHIS2 访问之前"


def test_the_disabled_executors_still_satisfy_the_uniform_interface() -> None:
    """DX-EXE-001：拒绝也要按同一张脸拒绝——签名齐备，S1 才不需要为它分支。"""
    import inspect

    for executor, _milestone, _rule in NOT_ENABLED:
        assert isinstance(executor, Executor), type(executor)
        for operation in ("plan", "validate", "commit", "verify"):
            bound = getattr(executor, operation)
            parameters = inspect.signature(bound).parameters
            assert "context" in parameters, f"{executor.executor_type}.{operation}"
        assert isinstance(executor, NotEnabledExecutor)


def test_refuse_helper_is_the_single_point_of_the_convention() -> None:
    """SRS 12 章：所有拒绝走同一个助手，消息形状只有一种。"""
    with pytest.raises(FeatureNotEnabledError) as excinfo:
        refuse("dataValues.query", "M4", rule="DX-EXE-046", extra="x")
    assert excinfo.value.details == {
        "feature": "dataValues.query",
        "milestone": "M4",
        "rule": "DX-EXE-046",
        "extra": "x",
    }
    assert "SRS 12 章里程碑分工" in str(excinfo.value)


# --- 演示用户（M3）--------------------------------------------------------


def test_demo_users_are_refused_as_an_m3_feature() -> None:
    """DX-TRK-003：演示用户属 M3，任何动作都拒绝，且不创建任何东西。"""
    instance = FakeDhis2Instance()
    live_context(instance)

    assert isinstance(DEMO_USER_PROVISIONER, DemoUserProvisioner)
    assert DEMO_USER_PROVISIONER.enabled is False
    assert (DEMO_USER_PROVISIONER.feature, DEMO_USER_PROVISIONER.milestone) == (
        DEMO_USERS_FEATURE,
        DEMO_USERS_MILESTONE,
    )
    assert DEMO_USERS_MILESTONE == "M3" and DEMO_USERS_RULE == "DX-TRK-003"

    with pytest.raises(FeatureNotEnabledError) as planned:
        DEMO_USER_PROVISIONER.plan({"users": [{"username": "demo"}]})
    assert planned.value.details["feature"] == "demo_users plan"
    assert planned.value.details["fixtureKeys"] == ["users"]
    assert planned.value.code == "DX_FEATURE_NOT_ENABLED"
    assert "M3" in str(planned.value)

    with pytest.raises(FeatureNotEnabledError) as created:
        DEMO_USER_PROVISIONER.create("dev", {"users": []}, execution_id="exe-1")
    assert created.value.details == {
        "feature": "demo_users create",
        "milestone": "M3",
        "rule": "DX-TRK-003",
        "environmentKey": "dev",
        "executionId": "exe-1",
    }
    assert instance.requests == [], "演示用户被拒绝时不该碰实例"


def test_demo_users_are_not_an_executor_type() -> None:
    """DX-TRK-003：它属于 Fixture 导入的一步，因此不占 ``ExecutorType`` 的一格。"""
    assert DEMO_USERS_FEATURE not in {item.value for item in ExecutorType}
    assert "demo_users" not in {item.value for item in ExecutorType}


# --- Metadata：唯一真正启用的一格 -----------------------------------------


def test_metadata_executor_is_the_enabled_one_and_reports_m2() -> None:
    """DX-EXE-002：M2 交付的就是 Metadata 这一格。"""
    from factory_dhis2.executors.metadata import METADATA_EXECUTOR, MetadataExecutor

    assert isinstance(METADATA_EXECUTOR, MetadataExecutor)
    assert METADATA_EXECUTOR.enabled is True and METADATA_EXECUTOR.milestone == "M2"
    assert METADATA_EXECUTOR.executor_type is ExecutorType.METADATA
    assert require_enabled(ExecutorType.METADATA) is METADATA_EXECUTOR
    assert ordered_executors()[0] is METADATA_EXECUTOR


def test_metadata_executor_delegates_plan_and_saves_it_on_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """DX-EXE-010：执行器的 plan 就是管线那一份实现，不另写一遍。"""
    from factory_dhis2.execution import plan as plan_module
    from factory_dhis2.executors import metadata as metadata_module

    seen: dict[str, Any] = {}

    class _Build:
        def __init__(self) -> None:
            self.document: dict[str, Any] = {"objects": [], "hash": "sha256:" + "1" * 64}

    def build_plan(context: RunContext, **kwargs: Any) -> Any:
        seen["kwargs"] = kwargs
        return _Build()

    monkeypatch.setattr(plan_module, "build_plan", build_plan)
    monkeypatch.setattr(
        plan_module,
        "save_plan",
        lambda context, build: (seen.__setitem__("saved", build.document), "art-plan")[1],
    )
    context = _bare_context()
    document = metadata_module.METADATA_EXECUTOR.plan(
        context, save=True, expected_hash="sha256:" + "2" * 64, executor_types=["METADATA"]
    )

    assert document["planArtifactId"] == "art-plan"
    assert seen["kwargs"]["expected_hash"] == "sha256:" + "2" * 64
    assert seen["kwargs"]["executor_types"] == ["METADATA"]
    assert seen["kwargs"]["deletion_payload"] is False


def test_metadata_executor_validate_commit_verify_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    """DX-EXE-025/032/040：四个方法各自委派给已实现并测过的那一条路径。"""
    from factory_dhis2.execution import commit as commit_module
    from factory_dhis2.execution import post_verify as post_verify_module
    from factory_dhis2.execution import validate as validate_module
    from factory_dhis2.executors.metadata import METADATA_EXECUTOR

    calls: dict[str, Any] = {}

    def fake_validate(context: RunContext, **kwargs: Any) -> str:
        calls["validate"] = kwargs
        return "validated"

    def fake_commit(*args: Any, **kwargs: Any) -> ExecutionOutcome:
        calls["commit"] = (args, kwargs)
        return ExecutionOutcome(execution_id="exe-9", applied=True)

    def fake_post_verify(*args: Any, **kwargs: Any) -> PostVerifyOutcome:
        calls["verify"] = (args, kwargs)
        return PostVerifyOutcome(consistent=True)

    monkeypatch.setattr(validate_module, "validate", fake_validate)
    monkeypatch.setattr(commit_module, "commit", fake_commit)
    monkeypatch.setattr(post_verify_module, "post_verify", fake_post_verify)

    context = _bare_context()
    assert METADATA_EXECUTOR.validate(
        context, {"importMode": "VALIDATE"}, {"objects": []}, state_hash="sha256:" + "3" * 64
    ) == "validated"
    assert calls["validate"]["env_state_hash"] == "sha256:" + "3" * 64

    committed = METADATA_EXECUTOR.commit(
        context, {"atomicMode": "ALL"}, {}, state_hash="sha256:" + "4" * 64, execution_id="exe-1"
    )
    assert committed.execution_id == "exe-9"
    args, kwargs = calls["commit"]
    assert args[0] == "dev" and args[1] == "prj-1"
    assert kwargs["idempotency_key"] == "commit:exe-1"
    assert kwargs["import_options"] == {"atomicMode": "ALL"}

    verified = METADATA_EXECUTOR.verify(context, execution_id="exe-1")
    assert verified.consistent is True
    assert calls["verify"][1]["idempotency_key"] == "post-verify:exe-1"


def _stub_package() -> Any:
    """执行器只需要 ``artifact_id`` 与 ``payload_hash``；这里给一个可断言的替身。"""

    class _Package:
        artifact_id = "art-pkg"
        artifact_hash = "sha256:" + "7" * 64

    return _Package()


def _bare_context() -> RunContext:
    """一个只用来走委派路径的上下文（委派之外不碰它的内容）。"""
    target = make_target()
    return RunContext(
        environment_key="dev",
        project_id="prj-1",
        target=target,
        package=_stub_package(),
        client=Dhis2Client(target, scope=CredentialScope.DISCOVERY),
    )


def test_commit_via_executor_still_uses_the_guarded_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """DX-EXE-032：执行器这条路不采信调用方给的 plan_document，重走幂等与准入检查。"""
    from factory_dhis2.execution import commit as commit_module
    from factory_dhis2.executors.metadata import METADATA_EXECUTOR

    recorded: list[Any] = []

    def fake_commit(*args: Any, **kwargs: Any) -> ExecutionOutcome:
        recorded.append(kwargs)
        return ExecutionOutcome(execution_id="exe-1", applied=True)

    monkeypatch.setattr(commit_module, "commit", fake_commit)
    METADATA_EXECUTOR.commit(
        _bare_context(),
        {},
        {"plan": "由调用方伪造的文档"},
        state_hash="sha256:" + "5" * 64,
        execution_id="exe-1",
        idempotency_key="same-key",
    )
    assert recorded[0]["idempotency_key"] == "same-key"
    assert "plan_document" not in recorded[0], "提交路径不接受调用方给的计划文档"


def test_a_refusal_never_opens_a_client_call_even_when_the_context_is_reused() -> None:
    """DX-EXE-002：同一个上下文被四个方法轮流拒绝，实例仍然什么都没收到。"""
    instance = FakeDhis2Instance()
    context = live_context(instance)
    # 正对照：先用它发一次真实请求，证明 transport 是通的
    assert context.client.call("system.info").status == 200
    baseline = len(instance.requests)

    for executor, _milestone, _rule in NOT_ENABLED:
        with pytest.raises(FeatureNotEnabledError):
            executor.plan(context)
        with pytest.raises(FeatureNotEnabledError):
            executor.verify(context)
        with pytest.raises(FeatureNotEnabledError):
            DEMO_USER_PROVISIONER.create("dev", {}, execution_id="exe-1")

    assert len(instance.requests) == baseline
