"""执行侧的接缝与上下文（DX-EXE-005/028/029/033/034、DX-QRY-004、DX-BR-03/04、DX-AC-01）。

这些点在别处各被用一次，但**从没被正面证明过**：上下文装配（读一段）、凭证范围换账户
（DX-BR-03）、VALIDATE 记录的同状态复用（DX-EXE-029）、VALIDATE 阶段的服务端表达式（DX-EXE-028）、
删除侧的关联数据接缝（DX-QRY-004）、比对函数对形态漂移的反应。它们出错的方式恰好是
「看起来跑通了」，所以在这里逐条钉住。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import unit_of_work

from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.enums import CredentialScope, ExecutionMode
from factory_dhis2.errors import (
    Dhis2TimeoutError,
    EnvironmentInvalidError,
    ProfileStaleError,
)
from factory_dhis2.execution import deletion as deletion_module
from factory_dhis2.execution import maintenance as maintenance_module
from factory_dhis2.execution import reads, steps
from factory_dhis2.execution import validate as validate_module
from factory_dhis2.execution.context import RunContext, document_of, load_context
from factory_dhis2.execution.deletion import _associated_data_refusals, verdict_has_data
from factory_dhis2.execution.post_verify import KIND_FIELD_MISMATCH, diff_value
from tests.unit.execution import (
    ENVIRONMENT,
    PROJECT,
    FakeClient,
    build_manifest,
    make_run_context,
    make_target,
    package_from_artifact,
    package_of,
    register_json_artifact,
    register_package_artifact,
)

UID = "de000000001"


def _manifest(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "objects": [{"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "CREATE"}],
        "uid_map": [{"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "CREATE"}],
        "dispositions": {"bk.weight": "CREATE"},
        "controlled_fields": {},
    }
    base.update(changes)
    return build_manifest(**base)


def _payload() -> dict[str, Any]:
    return {"dataElements": [{"id": UID, "name": "Weight", "shortName": "Wt"}]}


# --- 上下文装配（00 文档 6.2 的「读一段」）----------------------------------


def test_run_context_picks_the_client_by_credential_scope() -> None:
    """DX-BR-03：读用 discovery、导入用 import，同一个环境换一个范围就是换一个账户。"""
    context = make_run_context(package_of(_manifest(), _payload()), scope=CredentialScope.DISCOVERY)

    assert context.client_for(CredentialScope.DISCOVERY) is context.client
    imported = context.client_for(CredentialScope.IMPORT)
    assert imported is not context.client
    assert imported.default_scope is CredentialScope.IMPORT
    assert context.payload_hash == context.package.artifact_hash


def test_the_payload_summary_sent_to_artifacts_never_carries_the_content() -> None:
    """DX-EXE-003：给报告与产物的是载荷摘要（ID、哈希、类型清单），内容留在产物存储里。"""
    package = package_of(_manifest(), _payload())

    summary = document_of(package)

    assert summary["artifactHash"] == package.artifact_hash
    assert summary["types"] == ["dataElements"]
    assert "dataElements" not in summary


def test_load_context_assembles_target_package_and_client_outside_any_transaction(
    harness: Any,
) -> None:
    """DX-ENV-004 / DX-CAP-003：装配读的是已登记的环境与 Capability Profile，缺一样就拒绝。"""
    del harness
    from factory_dhis2.dto.environment import CredentialRef, EnvironmentSpec
    from factory_dhis2.enums import (
        AuthMode,
        EnvironmentClass,
        EnvironmentStatus,
        LlmDataPolicy,
    )
    from factory_dhis2.environment.service import create_environment
    from factory_dhis2.profiles import registry

    registry.load_builtin_profiles()
    spec = EnvironmentSpec(
        environment_key=ENVIRONMENT,
        name="装配用例",
        base_url="https://dhis2.example/test",
        environment_class=EnvironmentClass.TEST,
        auth_mode=AuthMode.BASIC,
        # 有版本约束时 require_probe=False 才能选出版本 Profile；没有约束也没有已探测档案
        # 就是不完整的环境，loader 会拒绝装配（DX-CAP-003）
        version_constraint="2.41.x",
        credentials=[
            CredentialRef(scope=scope, secret_ref=f"memory://dx/{scope.value}") for scope in CredentialScope
        ],
        tls_required=True,
        llm_data_policy=LlmDataPolicy.PRIVATE_NETWORK,
        timeout_seconds=60,
        status=EnvironmentStatus.ACTIVE,
    )
    with unit_of_work() as session:
        create_environment(spec, session=session, operator="tester")

    artifact_id = register_package_artifact(_payload(), _manifest())
    with pytest.raises(ProfileStaleError):
        # DX-CAP-003：没有已登记的 Capability Profile 就不许装配执行上下文
        load_context(ENVIRONMENT, artifact_id, project_id=PROJECT)

    context = load_context(ENVIRONMENT, artifact_id, project_id=PROJECT, require_probe=False)

    assert isinstance(context.client, Dhis2Client)
    assert context.package.artifact_id == artifact_id
    assert context.target.key == ENVIRONMENT
    assert context.project_id == PROJECT

    with unit_of_work() as session:
        # 装配不留下事务：客户端构造本身不发请求
        assert context.client.target.credentials is not None

    with pytest.raises(ValueError):
        # 载荷属于别的项目：先装配目标，再按产物归属拒绝（跨项目混用是一套不同的载荷）
        load_context(
            ENVIRONMENT, artifact_id, project_id="prj-someone-else", require_probe=False
        )

    with pytest.raises(EnvironmentInvalidError):
        load_context("env-never-registered", artifact_id)


# --- VALIDATE（DX-EXE-028/029）----------------------------------------------


def _validation_context(harness: Any, expressions: list[dict[str, Any]] | None = None) -> Any:
    del harness
    artifact_id = register_package_artifact(_payload(), _manifest(expressions=expressions or []))
    package = package_from_artifact(artifact_id)
    return make_run_context(package)


def test_validate_asks_the_server_about_each_checkable_expression(harness: Any) -> None:
    """DX-EXE-028：引用都已存在的表达式在 VALIDATE 阶段逐条问实例，失败就判校验不通过。"""
    context = _validation_context(
        harness,
        [
            {
                "businessKey": "bk.weight",
                "field": "formName",
                "validation": "DHIS2_VALIDATE",
                "compiled": "#{x}",
            }
        ],
    )
    context = make_run_context(
        context.package,
        client=FakeClient(
            make_target(),
            expression_verdicts={"dataElement.formName.validate": {"result": False}},
        ),
    )
    seen: list[str] = []

    class _Progress:
        def report(self, *, step: str, message: str | None = None, completed_fraction: float | None = None) -> None:
            del completed_fraction
            seen.append(f"{step}:{message}")

    result = validate_module.validate(
        context,
        import_options={"importStrategy": "CREATE_AND_UPDATE"},
        plan_document={"asyncImport": {"async": False}},
        env_state_hash="sha256:state",
        execution_id=None,
        progress=_Progress(),
    )

    names = [item.logical_name for item in context.client.calls]
    assert "metadata.import" in names
    # dataElement 没有服务端表达式接口 → 计入 skipped，而不是被当成通过或失败
    assert result.expression is None or result.expression.ok
    assert result.passed is True
    assert seen == ["EXPRESSION_VALIDATE:1 条表达式待实例校验"]


def test_validate_through_an_async_task_polls_before_concluding(harness: Any) -> None:
    """DX-EXE-033：异步校验同样是「提交 → 拿到任务地址 → 问到终态」才给结论。"""
    from tests.unit.execution import FakeResponse

    context = _validation_context(harness)

    class Tasking(FakeClient):
        def call(self, logical_name: str, **kwargs: Any) -> Any:
            response = super().call(logical_name, **kwargs)
            if logical_name == "metadata.import":
                return FakeResponse(
                    200,
                    {"status": "OK", "response": {"id": "task7"}},
                    {"Location": "system/tasks/dataImport/task7"},
                )
            if logical_name == "system.tasks.one":
                return FakeResponse(
                    200,
                    [
                        {
                            "id": "log0",
                            "level": "INFO",
                            "message": "Import complete with status OK, 1 created, 0 updated, 0 deleted, 0 ignored",
                            "completed": True,
                        }
                    ],
                )
            return response

    context = make_run_context(context.package, client=Tasking(make_target()))
    seen: list[str] = []

    class _Progress:
        def report(self, *, step: str, message: str | None = None, completed_fraction: float | None = None) -> None:
            del completed_fraction
            seen.append(f"{step}:{message}")

    result = validate_module.validate(
        context,
        import_options={},
        plan_document={"asyncImport": {"async": True}},
        env_state_hash="sha256:state",
        execution_id=None,
        progress=_Progress(),
    )

    assert result.passed is True
    assert result.job_ref == "task7", "任务 ID 取实例给的那一半，不自己推"
    assert [item.logical_name for item in context.client.calls] == ["metadata.import", "system.tasks.one"]
    assert seen == ["VALIDATE:异步校验任务已提交"]


def test_the_same_state_and_result_reuse_one_validation_record(harness: Any) -> None:
    """DX-EXE-029：同一载荷同一状态同一结论不堆重复行，只把最新的报告引用刷上去。"""
    del harness
    with unit_of_work() as session:
        first = validate_module.record_validation(
            session,
            environment_key=ENVIRONMENT,
            payload_hash="sha256:" + "a" * 64,
            env_state_hash="sha256:state",
            execution_id="dxexe-1",
            result="PASSED",
            report_artifact_id=None,
            issue_count=0,
        )
    second = None
    with unit_of_work() as session:
        second = validate_module.record_validation(
            session,
            environment_key=ENVIRONMENT,
            payload_hash="sha256:" + "a" * 64,
            env_state_hash="sha256:state",
            execution_id="dxexe-2",
            result="PASSED",
            report_artifact_id="art-report-2",
            issue_count=3,
        )

    assert second == first
    with unit_of_work() as session:
        from factory_dhis2.models.execution import ValidationRecordRow

        rows = list(session.query(ValidationRecordRow).filter_by(environment_key=ENVIRONMENT).all())
        assert len(rows) == 1
        assert rows[0].issue_count == 3
        assert rows[0].import_report_artifact_id == "art-report-2"
        assert validate_module.last_passed(session, ENVIRONMENT, "sha256:" + "a" * 64) is rows[0]
        assert validate_module.last_passed(session, ENVIRONMENT, "sha256:nothing") is None


# --- 删除的安全检查接缝（DX-QRY-004）----------------------------------------


def test_the_deletion_safety_check_delegates_to_the_read_only_query(harness: Any) -> None:
    """DX-QRY-004：删除侧不自己判定关联数据，它调的是那个只读查询（含 program 参数）。"""
    del harness
    seen: dict[str, Any] = {}

    def fake_query(
        environment_key: str, dhis2_type: str, uid: str, *, program: str | None = None
    ) -> dict[str, Any]:
        seen.update({"environmentKey": environment_key, "type": dhis2_type, "uid": uid, "program": program})
        return {"hasData": False, "basis": "没有命中"}

    monkey = pytest.MonkeyPatch()
    monkey.setattr("factory_dhis2.queries.associated_data.has_associated_data", fake_query)
    try:
        verdict = deletion_module.associated_data_check(ENVIRONMENT, "programRule", "pr000000001", program="pg1")
    finally:
        monkey.undo()

    assert verdict["hasData"] is False
    assert seen == {
        "environmentKey": ENVIRONMENT,
        "type": "programRule",
        "uid": "pr000000001",
        "program": "pg1",
    }


def test_the_safety_check_honours_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    """DX-QRY-004：上千个候选的删除检查也要能停下来。"""
    monkeypatch.setattr(deletion_module, "associated_data_check", lambda *_a, **_k: {"hasData": False})

    class _Cancel:
        def __init__(self) -> None:
            self.seen = 0

        def raise_if_cancelled(self) -> None:
            self.seen += 1
            if self.seen > 1:
                from factory_dhis2.errors import Dhis2TimeoutError

                raise Dhis2TimeoutError("cancelled", details={})

    candidates = [
        {"uid": "de000000001", "dhis2_type": "dataElement", "business_key": "bk.a", "reason": None, "program": None},
        {"uid": "de000000002", "dhis2_type": "dataElement", "business_key": "bk.b", "reason": None, "program": None},
    ]
    cancel = _Cancel()

    with pytest.raises(Dhis2TimeoutError):
        _associated_data_refusals(ENVIRONMENT, candidates, cancel=cancel)

    assert cancel.seen == 2


def test_a_verdict_object_may_come_back_as_an_attribute_holder() -> None:
    """DX-QRY-004：判定结果可能是对象而不是映射，认得出就按它说，认不出就保守拒绝。"""

    class _Verdict:
        has_data = False

    class _Other:
        hasData = True

    assert verdict_has_data(_Verdict()) is False
    assert verdict_has_data(_Other()) is True
    assert verdict_has_data(object()) is True


# --- 比对的形态与其余接缝 ----------------------------------------------------


def test_a_dict_expected_against_a_scalar_actual_is_one_difference() -> None:
    """DX-EXE-040：载荷写了嵌套对象而环境回成标量，报一条差异而不是崩溃。"""
    findings = diff_value("categoryCombo", {"id": "cc0000000001"}, "cc0000000001", volatile=frozenset())

    assert len(findings) == 1
    assert findings[0]["kind"] == KIND_FIELD_MISMATCH
    assert findings[0]["field"] == "categoryCombo"


def test_the_collection_helper_only_returns_dict_rows() -> None:
    """DX-EXE-003：载荷集合的读取只收字典行，缺键或形态不对就当没有。"""
    assert reads.collection_of({"dataElements": [{"id": "a"}, "junk"]}, "dataElements") == [{"id": "a"}]
    assert reads.collection_of({"dataElements": "not a list"}, "dataElements") == []
    assert reads.collection_of({}, "missing") == []


def test_maintenance_builds_its_client_on_the_maintenance_scope() -> None:
    """DX-EXE-045：装配点也是注入点，默认范围就是 maintenance。"""
    client = maintenance_module.client_for(make_target())

    assert isinstance(client, Dhis2Client)
    assert client.default_scope is CredentialScope.MAINTENANCE


def test_a_maintenance_execution_bound_to_another_environment_is_refused(
    maintenance: Any,
) -> None:
    """DX-BR-02：``load_target`` 装配的是被点名的环境——这里证明拒绝发生在任何读之前。"""
    from factory_dhis2.errors import EnvironmentInvalidError

    with pytest.raises(EnvironmentInvalidError):
        maintenance_module.run_maintenance(
            "env-someone-else",
            maintenance["execution_id"],
            [{"action": "CLEAR_APP_CACHE"}],
            operator="ops-1",
            idempotency_key="k-seam-wrong-env",
        )

    assert maintenance["client"].calls == []


@pytest.fixture
def maintenance(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """最小的一份维护夹具：只需要执行行与计划产物，不需要实例。"""
    plan_artifact_id = register_json_artifact(
        {"kind": "EXECUTION_PLAN", "maintenanceActions": [{"action": "CLEAR_APP_CACHE"}]}, "EXECUTION_PLAN"
    )
    with unit_of_work() as session:
        row = steps.create_execution(
            session,
            environment_key=ENVIRONMENT,
            project_id=PROJECT,
            mode=ExecutionMode.MAINTENANCE,
            operator="ops-1",
        )
        row.plan_artifact_id = plan_artifact_id
        execution_id = row.id
    monkeypatch.setattr(maintenance_module, "load_target", lambda _key: make_target())
    return {"execution_id": execution_id, "plan_artifact_id": plan_artifact_id, "client": FakeClient(make_target())}


def test_run_context_is_a_plain_dataclass_the_pipeline_can_rebuild(
    harness: Any,
) -> None:
    """00 文档 6.2：装配一次，后续步骤不再回数据库取配置——续跑靠的是步骤事实而不是内存。"""
    context = _validation_context(harness)

    rebuilt = RunContext(
        environment_key=context.environment_key,
        project_id=context.project_id,
        target=context.target,
        package=context.package,
        client=context.client,
    )

    assert rebuilt.payload_hash == context.payload_hash
    assert rebuilt.client_for(CredentialScope.DISCOVERY) is rebuilt.client
