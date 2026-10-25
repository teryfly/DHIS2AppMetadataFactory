"""定向删除（DX-EXE-038、DX-QRY-004、FR-CMP-009）。

删除是这条管线里唯一不可逆、实例又不给回滚的动作，所以这里的断言全部围绕**顺序与准入**：
候选集是它自己的载荷（不是把上次部署反过来）；服务端 VALIDATE 先跑，与 COMMIT 同一载荷同一
参数，只换 ``importMode``；VALIDATE 之后还要**当场**重做一次关联数据检查（那段时间里别人可能
已经往环境里写了业务数据）；PRODUCTION 的 ``deletionPolicy`` 与紧急锁定都在发请求之前判定；
只要有一个候选判不出「没有关联数据」，整批就不删。被拒、被挡住、结果不明都是**报告出来的
事实**，不是被吞掉的异常。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import AuditQuery, query_audit, unit_of_work

from factory_dhis2.artifacts import read_json
from factory_dhis2.enums import CredentialScope, ExecutionStatus
from factory_dhis2.errors import (
    DeletionForbiddenError,
    Dhis2TimeoutError,
    Dhis2UnreachableError,
    EnvironmentLockedError,
    InputArtifactInvalidError,
)
from factory_dhis2.execution import deletion as deletion_module
from factory_dhis2.execution import steps
from factory_dhis2.execution.context import RunContext
from factory_dhis2.execution.deletion import (
    DELETION_COLLECTION,
    delete_objects,
    deletion_candidates,
    snapshot_document,
    verdict_has_data,
)
from factory_dhis2.payload.package import MetadataPackage
from tests.unit.execution import (
    ENVIRONMENT,
    PROJECT,
    FakeClient,
    FakeResponse,
    build_manifest,
    error_report,
    make_target,
    package_from_artifact,
    package_of,
    register_package_artifact,
    seed_environment,
)

UID = "de000000009"
OTHER_UID = "de000000010"
TASK_URL = "https://dhis2.example/test/api/system/tasks/METADATA_IMPORT"
TERMINAL_MESSAGE = "Import complete with status OK, 0 created, 0 updated, 2 deleted, 0 ignored"


def _manifest(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "objects": [
            {
                "businessKey": "bk.retired",
                "dhis2Type": "dataElement",
                "uid": UID,
                "disposition": "DEPRECATED",
            }
        ],
        "uid_map": [
            {"businessKey": "bk.retired", "uid": UID, "dhis2Type": "dataElement", "disposition": "DEPRECATED"}
        ],
        "dispositions": {"bk.retired": "DEPRECATED"},
        "controlled_fields": {},
        "deletion_candidates": [
            {"businessKey": "bk.retired", "dhis2Type": "dataElement", "uid": UID, "reason": "指标口径下线"}
        ],
        "managed_scope": [
            {"businessKey": "bk.retired", "dhis2Type": "dataElement", "uid": UID}
        ],
    }
    base.update(changes)
    return build_manifest(**base)


def _payload() -> dict[str, Any]:
    return {
        DELETION_COLLECTION: [
            {"businessKey": "bk.retired", "dhis2Type": "dataElement", "uid": UID},
            {"businessKey": "bk.retired.later", "dhis2Type": "dataElement", "uid": OTHER_UID},
        ]
    }


class DeletionClient(FakeClient):
    """删除这条链路的假实例：可以指定同步/异步/掉线三种结局。"""

    def __init__(
        self,
        target: Any,
        *,
        scope: CredentialScope = CredentialScope.DISCOVERY,
        objects: dict[tuple[str, str], dict[str, Any]] | None = None,
        reports: dict[str, dict[str, Any]] | None = None,
        expression_verdicts: dict[str, Any] | None = None,
        calls: list[Any] | None = None,
        responses: dict[str, Any] | None = None,
        mode: str = "sync",
        tasks: list[Any] | None = None,
    ) -> None:
        super().__init__(
            target,
            scope=scope,
            objects=objects,
            reports=reports,
            expression_verdicts=expression_verdicts,
            calls=calls,
            responses=responses,
        )
        self.mode = mode
        self.tasks = tasks if tasks is not None else []

    def with_scope(self, scope: CredentialScope) -> DeletionClient:
        return type(self)(
            self.target,
            scope=scope,
            objects=self.objects,
            reports=self.reports,
            expression_verdicts=self.expression_verdicts,
            calls=self.calls,
            responses=self.responses,
            mode=self.mode,
            tasks=self.tasks,
        )

    def call(self, logical_name: str, **kwargs: Any) -> Any:
        if self.mode == "lost" and logical_name == "metadata.delete":
            super().call(logical_name, **kwargs)
            raise Dhis2UnreachableError("the connection dropped after the delete was sent")
        response = super().call(logical_name, **kwargs)
        if logical_name == "system.tasks.one":
            return FakeResponse(200, self.tasks.pop(0) if self.tasks else [])
        if logical_name in {"metadata.delete", "metadata.import"} and self.mode == "async":
            return FakeResponse(
                200,
                {"status": "OK", "response": {"id": "task9", "jobStatus": "RUNNING"}},
                {"Location": TASK_URL},
            )
        return response


def _terminal_log() -> list[dict[str, Any]]:
    return [
        {
            "id": "log0",
            "uid": "log0",
            "level": "INFO",
            "category": "METADATA_IMPORT",
            "message": TERMINAL_MESSAGE,
            "completed": True,
        }
    ]


@pytest.fixture
def deletion(harness: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把删除管线接到脚本化实例上：环境、载荷、关联数据检查的答案都在这里换。"""
    seed_environment(harness)
    artifact_id = register_package_artifact(_payload(), _manifest())
    package = package_from_artifact(artifact_id)
    target = make_target()
    state: dict[str, Any] = {
        "artifact_id": artifact_id,
        "package": package,
        "target": target,
        "client": DeletionClient(target),
        "has_data": False,
        "safety": [],
        "verdict_error": None,
    }

    def fake_load_context(
        environment_key: str,
        package_artifact_id: str,
        *,
        project_id: str | None = None,
        scope: CredentialScope = CredentialScope.DISCOVERY,
        require_probe: bool = True,
        complete_manifest: bool = True,
    ) -> RunContext:
        # 从 state 取包：有的用例要换一个「manifest 缺段」的载荷进来
        current: MetadataPackage = state["package"]
        return RunContext(
            environment_key=environment_key,
            project_id=project_id or current.project_id,
            target=target,
            package=current,
            client=state["client"].with_scope(scope),
        )

    def fake_safety_check(
        environment_key: str, dhis2_type: str, uid: str, *, program: str | None = None
    ) -> Any:
        # 记录检查发生时实例已经被调用过什么：这就是「先 VALIDATE 再检查再提交」的证据
        state["safety"].append(
            {
                "uid": uid,
                "type": dhis2_type,
                "program": program,
                "resources_before": [item.logical_name for item in state["client"].calls],
            }
        )
        if state["verdict_error"] is not None:
            raise state["verdict_error"]
        return {"hasData": state["has_data"], "basis": "dataValues.query 命中 0 条"}

    monkeypatch.setattr(deletion_module, "load_context", fake_load_context)
    monkeypatch.setattr(deletion_module, "associated_data_check", fake_safety_check)
    return state


def _delete(state: dict[str, Any], key: str = "k-delete", **kwargs: Any) -> Any:
    return delete_objects(
        ENVIRONMENT,
        PROJECT,
        state["artifact_id"],
        state["package"].artifact_hash,
        operator="ops-1",
        idempotency_key=key,
        **kwargs,
    )


def _resources(state: dict[str, Any]) -> list[str]:
    return [item.logical_name for item in state["client"].calls]


def _params_for(state: dict[str, Any], logical_name: str, *, occurrence: int = 0) -> dict[str, str]:
    matches = [item.params for item in state["client"].calls if item.logical_name == logical_name]
    return matches[occurrence]


# --- 顺序与准入：VALIDATE → 当场安全检查 → COMMIT ----------------------------


def test_deletion_validates_before_committing_the_same_candidate_payload(
    deletion: dict[str, Any],
) -> None:
    """DX-EXE-038 / DX-BR-05：删除载荷先按同一载荷同一参数 VALIDATE，再换 importMode 提交。"""
    outcome = _delete(deletion)

    assert outcome.status is ExecutionStatus.COMPLETED, outcome.model_dump()
    assert outcome.applied is True
    resources = _resources(deletion)
    assert resources.index("metadata.import") < resources.index("metadata.delete")
    validated = _params_for(deletion, "metadata.import")
    committed = _params_for(deletion, "metadata.delete")
    assert validated["importMode"] == "VALIDATE"
    assert committed["importMode"] == "COMMIT"
    # 除 importMode 之外两份参数一致：验过的就是提交的
    assert {key: value for key, value in validated.items() if key != "importMode"} == {
        key: value for key, value in committed.items() if key != "importMode"
    }
    assert committed["importStrategy"] == "DELETE"
    assert committed["atomicMode"] == "ALL", "删一半比不删更难解释：删除按原子模式提交"


def test_associated_data_check_is_redone_between_validate_and_commit(
    deletion: dict[str, Any],
) -> None:
    """DX-QRY-004：VALIDATE 之后、发删除请求之前，当场再查一次关联数据。"""
    _delete(deletion)

    assert deletion["safety"], "删除安全检查一次也没做"
    first = deletion["safety"][0]
    assert "metadata.import" in first["resources_before"], "安全检查发生在 VALIDATE 之前"
    assert "metadata.delete" not in first["resources_before"], "安全检查必须挡住提交，而不是跟着提交之后"
    assert {item["uid"] for item in deletion["safety"]} == {UID, OTHER_UID}


def test_deletion_records_every_step_and_reports_the_facts(deletion: dict[str, Any]) -> None:
    """DX-EXE-046 / DX-EXE-029：删除留下 Snapshot、ImportReport、执行报告与审计。"""
    outcome = _delete(deletion)

    assert outcome.import_report_artifact_id and outcome.report_artifact_id
    assert outcome.payload_hash == deletion["package"].artifact_hash
    document = read_json(outcome.report_artifact_id)
    assert document["status"] == ExecutionStatus.COMPLETED.value
    assert [row["uid"] for row in document["extra"]["deletionCandidates"]] == [UID, OTHER_UID]
    assert document["extra"]["importOptions"]["importStrategy"] == "DELETE"
    assert document["artifacts"]["snapshot"] and document["artifacts"]["importReport"]
    with unit_of_work() as session:
        row = steps.require(session, str(outcome.execution_id))
        assert row.mode == "DELETE"
        assert row.snapshot_artifact_id and row.env_state_hash
        completed = {item.step: item.status for item in outcome.steps}
        assert completed["SNAPSHOT"] == "COMPLETED"
        assert completed["VALIDATE"] == "COMPLETED"
        assert completed["COMMIT"] == "COMPLETED"
        assert holder_is_none(session, ENVIRONMENT), "删除结束必须释放环境锁"
    events = query_audit(
        AuditQuery(event_type="dx.delete.executed", object_id=str(outcome.execution_id))
    )
    assert events.items, "删除必须有审计（附录 A）"
    assert events.items[0].after["candidates"] == 2


def holder_is_none(session: Any, environment_key: str) -> bool:
    from factory_dhis2.envlock import manager as envlock

    return envlock.holder(session, environment_key) is None


def test_deletion_reports_progress_for_each_step(deletion: dict[str, Any]) -> None:
    """DX-EXE-046：删除也要能报进度，步骤名与实际执行的那一步一致。"""
    seen: list[str] = []

    class _Progress:
        def report(self, *, step: str, completed_fraction: float | None = None, message: str | None = None) -> None:
            del completed_fraction, message
            seen.append(step)

    _delete(deletion, progress=_Progress())

    assert seen == ["SNAPSHOT", "VALIDATE", "DRIFT_CHECK", "COMMIT"]


def test_second_delete_with_the_same_key_replays_the_first(deletion: dict[str, Any]) -> None:
    """NFR-REL-003：删除不可逆，同幂等键的第二次调用只回放第一次的结论。"""
    first = _delete(deletion, key="k-once")
    deletes_before = _resources(deletion).count("metadata.delete")

    second = _delete(deletion, key="k-once")

    assert second.execution_id == first.execution_id
    assert _resources(deletion).count("metadata.delete") == deletes_before


# --- 拒绝：策略、锁定、关联数据、校验 ---------------------------------------


def test_forbid_policy_refuses_the_deletion_before_anything_is_sent(
    deletion: dict[str, Any], harness: Any
) -> None:
    """DX-EXE-038：deletionPolicy=FORBID 一律拒绝，连一次请求都不发。"""
    seed_environment(harness, deletion_policy="FORBID")

    with pytest.raises(DeletionForbiddenError) as excinfo:
        _delete(deletion)

    assert excinfo.value.error_code == "DX_DELETION_FORBIDDEN"
    details = excinfo.value.details
    assert details["rule"] == "DX-EXE-038"
    assert details["deletionPolicy"] == "FORBID"
    assert details["candidates"] == 2
    assert deletion["client"].calls == []
    with unit_of_work() as session:
        assert steps.find_by_idempotency_key(session, "k-forbid") is None


def test_production_environment_follows_its_deletion_policy(deletion: dict[str, Any]) -> None:
    """DX-EXE-038：PRODUCTION 按自己的 deletionPolicy 走——SAFE_ONLY 放行安全检查过的删除。"""
    from sqlalchemy import select

    from factory_dhis2.models.environment import EnvironmentRow

    with unit_of_work() as session:
        row = session.execute(
            select(EnvironmentRow).where(EnvironmentRow.key == ENVIRONMENT)
        ).scalars().one()
        row.environment_class = "PRODUCTION"

    outcome = _delete(deletion, key="k-prod")

    assert outcome.status is ExecutionStatus.COMPLETED
    events = query_audit(AuditQuery(event_type="dx.delete.executed", object_id=str(outcome.execution_id)))
    assert events.items[0].after["environmentClass"] == "PRODUCTION"
    assert events.items[0].after["deletionPolicy"] == "SAFE_ONLY"


def test_emergency_lock_refuses_the_deletion(deletion: dict[str, Any], harness: Any) -> None:
    """DX-ENV-007 / DX-BR-11：紧急锁定期间不执行删除。"""
    seed_environment(harness, emergency_lock=True, lock_reason="演练")

    with pytest.raises(EnvironmentLockedError):
        _delete(deletion)

    assert deletion["client"].calls == []


def test_one_candidate_with_associated_data_refuses_the_whole_batch(
    deletion: dict[str, Any],
) -> None:
    """DX-EXE-038 / DX-QRY-004：判出关联数据就整批不删——删一半比不删更难解释。"""
    deletion["has_data"] = True

    with pytest.raises(DeletionForbiddenError) as excinfo:
        _delete(deletion)

    refused = excinfo.value.details["refused"]
    assert {item["uid"] for item in refused} == {UID, OTHER_UID}
    assert refused[0]["evidence"]["basis"] == "dataValues.query 命中 0 条"
    assert "metadata.delete" not in _resources(deletion)
    with unit_of_work() as session:
        row = steps.require(session, str(excinfo.value.details["executionId"]))
        assert row.status == ExecutionStatus.FAILED.value
        assert row.applied is False
        assert holder_is_none(session, ENVIRONMENT), "拒绝之后锁必须释放"


def test_an_unjudgeable_candidate_refuses_the_deletion(
    deletion: dict[str, Any],
) -> None:
    """DX-QRY-004 的保守方向：判不出来就当有关联数据，不许删。"""
    deletion["verdict_error"] = Dhis2TimeoutError("实例没有回答", details={})

    with pytest.raises(DeletionForbiddenError) as excinfo:
        _delete(deletion)

    refused = excinfo.value.details["refused"]
    assert all("关联数据检查无法完成" in item["evidence"] for item in refused)
    assert "metadata.delete" not in _resources(deletion)


def test_a_failing_server_validation_blocks_the_commit(deletion: dict[str, Any]) -> None:
    """DX-EXE-038：服务端校验没过就不提交删除，但已经留下的事实要进报告。"""
    deletion["client"] = DeletionClient(
        deletion["target"], reports={"metadata.import": error_report("delete would break references")}
    )

    outcome = _delete(deletion, key="k-badvalidate")

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.applied is False
    assert "服务端校验未通过" in (outcome.detail or "")
    assert "metadata.delete" not in _resources(deletion)
    assert outcome.report_artifact_id and outcome.import_report_artifact_id
    document = read_json(outcome.report_artifact_id)
    assert document["artifacts"]["snapshot"], "已经做出的事实必须出现在拒绝报告里"
    with unit_of_work() as session:
        assert holder_is_none(session, ENVIRONMENT)


def test_a_plan_that_does_not_stand_refuses_the_deletion(deletion: dict[str, Any], harness: Any) -> None:
    """DX-EXE-012 / DX-EXE-038：计划不成立（manifest 缺段）时连 VALIDATE 都不发。"""
    del harness
    manifest = _manifest()
    del manifest["controlledFields"]
    artifact_id = register_package_artifact(_payload(), manifest)
    package = package_from_artifact(artifact_id)
    deletion["artifact_id"] = artifact_id
    deletion["package"] = package

    outcome = _delete(deletion, key="k-noplan")

    assert outcome.status is ExecutionStatus.FAILED
    assert "删除计划不成立" in (outcome.detail or "")
    assert [issue.rule_id for issue in outcome.issues] == ["DX-ASM-02"]
    assert deletion["client"].calls == []
    assert outcome.report_artifact_id is None, "什么都没做出来时不该产出一份看起来像结论的报告"


# --- 结果与恢复 -------------------------------------------------------------


def test_a_deletion_the_instance_refuses_is_reported_not_swallowed(
    deletion: dict[str, Any],
) -> None:
    """DX-EXE-038：实例自己拒了这次删除，结论与它的说明要出现在报告里。"""
    deletion["client"] = DeletionClient(
        deletion["target"], reports={"metadata.delete": error_report("data values exist")}
    )

    outcome = _delete(deletion, key="k-refused-commit")

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.applied is False
    assert outcome.issues, "实例的拒绝必须变成问题清单，而不是一个空的失败"
    assert "data values exist" in outcome.issues[0].description
    with unit_of_work() as session:
        row = steps.require(session, str(outcome.execution_id))
        assert row.detail == "实例拒绝了本次删除"
        assert holder_is_none(session, ENVIRONMENT)
    assert read_json(outcome.report_artifact_id)["extra"]["deletionCandidates"]


def test_async_deletion_polls_the_task_it_persisted(deletion: dict[str, Any]) -> None:
    """DX-EXE-033/046：异步删除同样先拿任务地址再问结论，报告由任务日志合成。"""
    deletion["client"] = DeletionClient(deletion["target"], mode="async", tasks=[_terminal_log(), _terminal_log()])
    monkey = pytest.MonkeyPatch()
    real_build_plan = deletion_module.build_plan

    def forced_async(context: Any, **kwargs: Any) -> Any:
        plan = real_build_plan(context, **kwargs)
        plan.document["asyncImport"] = {"async": True}
        return plan

    monkey.setattr(deletion_module, "build_plan", forced_async)
    try:
        outcome = _delete(deletion, key="k-async")
    finally:
        monkey.undo()

    assert outcome.status is ExecutionStatus.COMPLETED, outcome.model_dump()
    resources = _resources(deletion)
    assert resources.count("system.tasks.one") == 2, "VALIDATE 与 COMMIT 各问一次任务"
    report_document = read_json(str(outcome.import_report_artifact_id))
    assert report_document["report"]["reportSource"] == "TASK_LOG"
    assert report_document["report"]["deleted"] == 2


def test_lost_delete_response_keeps_the_lock_and_asks_for_manual_check(
    deletion: dict[str, Any],
) -> None:
    """DX-EXE-031 / DX-EXE-035：删除请求发出去之后拿不到结论 → 锁保持、不猜结果。"""
    deletion["client"] = DeletionClient(deletion["target"], mode="lost")

    outcome = _delete(deletion, key="k-lost")

    assert outcome.status is ExecutionStatus.NEEDS_MANUAL_CHECK
    assert outcome.applied is None, "结果不明不能说成未生效"
    with unit_of_work() as session:
        from factory_dhis2.envlock import manager as envlock

        assert envlock.holder(session, ENVIRONMENT) == outcome.execution_id
        events = query_audit(
            AuditQuery(event_type="dx.commit.needs_manual_check", object_id=str(outcome.execution_id))
        )
        assert events.items
        assert events.items[0].after["mode"] == "DELETE"
        assert events.items[0].after["lockHeld"] is True


def test_an_unexpected_failure_still_releases_the_lock(deletion: dict[str, Any]) -> None:
    """DX-EXE-031：一次程序缺陷不该让这台环境永久无法删除。"""
    deletion["verdict_error"] = RuntimeError("a bug, not a DHIS2 answer")

    with pytest.raises(RuntimeError):
        _delete(deletion, key="k-crash")

    with unit_of_work() as session:
        row = steps.find_by_idempotency_key(session, "k-crash")
        assert row is not None
        assert row.status == ExecutionStatus.FAILED.value
        assert row.detail == "执行异常终止"
        assert holder_is_none(session, ENVIRONMENT)


# --- 候选集与判定词汇（纯函数） ---------------------------------------------


def test_candidates_come_from_the_manifest_and_the_payload_collection() -> None:
    """DX-EXE-038：候选集来自载荷自己（manifest 段 ∪ ``@DELETE``），不是上次部署的反向。"""
    package = package_of(_manifest(), _payload())

    candidates = deletion_candidates(package)

    assert [item["uid"] for item in candidates] == [UID, OTHER_UID]
    assert candidates[0]["dhis2_type"] == "dataElement"
    assert candidates[0]["reason"] == "指标口径下线"


def test_a_candidate_written_only_as_a_business_key_is_still_addressable() -> None:
    """DX-EXE-038：只写了 businessKey 的候选要能从 manifest objects 段补齐 UID 与类型。"""
    package = package_of(
        _manifest(deletion_candidates=[{"businessKey": "bk.retired", "reason": "下线"}]),
        {DELETION_COLLECTION: [{"businessKey": "bk.retired"}]},
    )

    candidates = deletion_candidates(package)

    assert len(candidates) == 1
    assert (candidates[0]["uid"], candidates[0]["dhis2_type"]) == (UID, "dataElement")


def test_an_unaddressable_candidate_is_refused_instead_of_being_guessed() -> None:
    """DX-EXE-038：对不上 UID 的候选绝不能送去删除——那只会删错东西。"""
    package = package_of(
        _manifest(deletion_candidates=[]),
        {DELETION_COLLECTION: [{"businessKey": "bk.unknown", "dhis2Type": "dataElement"}]},
    )

    with pytest.raises(InputArtifactInvalidError) as excinfo:
        deletion_candidates(package)

    assert excinfo.value.details["rule"] == "DX-EXE-038"
    assert "no resolvable" in str(excinfo.value)


def test_duplicate_candidates_are_collapsed_per_type_and_uid() -> None:
    """同一对象写了两遍也只删一次，且顺序稳定（报告要能复现）。"""
    package = package_of(
        _manifest(deletion_candidates=[{"uid": UID, "dhis2Type": "dataElement"}]),
        {
            DELETION_COLLECTION: [
                {"uid": UID, "dhis2Type": "dataElement"},
                {"uid": OTHER_UID, "dhis2Type": "dataElement"},
            ]
        },
    )

    assert [item["uid"] for item in deletion_candidates(package)] == [UID, OTHER_UID]


@pytest.mark.parametrize(
    ("result", "has_data"),
    [
        (True, True),
        (False, False),
        ({"hasData": False}, False),
        ({"has_data": True}, True),
        ({"associated": False}, False),
        ({"hasAssociatedData": True}, True),
        ({"basis": "命中一条"}, True),
        (object(), True),
    ],
)
def test_the_verdict_normalises_to_the_conservative_direction(result: Any, has_data: bool) -> None:
    """DX-QRY-004：认不出来的形态一律当「有关联数据」，判不出来就不许删。"""
    assert verdict_has_data(result) is has_data


def test_refusal_detail_carries_the_evidence_that_explains_it(deletion: dict[str, Any]) -> None:
    """DX-QRY-004：被拒的对象要能被解释——依据跟着拒绝一起进细节。"""
    deletion["has_data"] = True
    deletion["verdict_error"] = None

    class _Verdict(dict[str, Any]):
        pass

    from factory_dhis2.execution.deletion import _evidence_of

    assert _evidence_of({"evidence": {"dataValues": 3}}) == {"evidence": {"dataValues": 3}}
    assert _evidence_of({"basis": "命中 dataValue"}) == {"basis": "命中 dataValue"}
    assert _evidence_of(_Verdict()) == "关联数据检查判定为“有关联数据”"

    class _Attribute:
        detail = "嵌套查询命中 1 条"

    assert _evidence_of(_Attribute()) == "嵌套查询命中 1 条"


def test_the_deletion_snapshot_uses_the_one_hash_definition(deletion: dict[str, Any]) -> None:
    """DX-EXE-022/029：删除前也留一份 PRE_COMMIT Snapshot，哈希定义只有一处。"""
    from factory_dhis2.execution import snapshot
    from factory_dhis2.execution.context import RunContext

    context: RunContext = RunContext(
        environment_key=ENVIRONMENT,
        project_id=PROJECT,
        target=deletion["target"],
        package=deletion["package"],
        client=deletion["client"],
    )

    document = snapshot_document(context)

    assert document["snapshotKind"] == snapshot.KIND_PRE_COMMIT
    assert {item["uid"] for item in document["objects"]} == {UID}
    assert snapshot.env_state_hash(document)
