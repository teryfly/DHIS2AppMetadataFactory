"""提交前 Snapshot ↔ 提交后状态的对比（DX-DIFF-001 之一、总体 SRS 15.5）。

这里测的是**一次部署到底把环境改成了什么样**，所以两侧的来源都必须钉住：

* 基准是执行自己的提交前 Snapshot（DX-EXE-022）。没有它就是「无法对比」，而不是
  「没有差异」——把前者说成后者，等于把一次没有记录的部署藏进一份看起来干净的报告里；
* 另一侧优先用部署后验证留下的 POST_COMMIT（同一次部署读回的状态，两侧同一套读取口径），
  没有它才当场读一次环境，并在产物的 ``artifacts.currentReadBack`` 里如实标出是
  ``LIVE`` 还是 ``SNAPSHOT``；
* 现场读回走 **discovery 凭证范围**并且只发 GET（DX-BR-04：观察环境不等于修改环境）。

因此实例是真的内存 2.41（挂在真实 ``httpx`` transport 上）：只有让请求真的发出去，才能证明
「Diff 不写环境」与「没有 POST_COMMIT 时确实读了环境」这两件事，而不是在替身上自证。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import (
    ArtifactQuery,
    NotFoundError,
    query_artifacts,
    unit_of_work,
)

from factory_dhis2 import api, deps
from factory_dhis2.artifacts import read_json, read_metadata
from factory_dhis2.diff.report import PRESENCE_FIELD
from factory_dhis2.diff.snapshot_vs_current import (
    content_hash_of,
    execution_view,
    live_document,
)
from factory_dhis2.discovery.canonical import object_hash
from factory_dhis2.enums import (
    CredentialScope,
    Disposition,
    ObjectChangeKind,
)
from factory_dhis2.execution.context import load_context
from factory_dhis2.execution.snapshot import KIND_POST_COMMIT, KIND_PRE_COMMIT
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport
from tests.unit.diff import (
    PAYLOAD_HASH,
    PROFILE,
    PROJECT,
    STAMP,
    add_execution,
    add_snapshot_index,
    content_of,
    entry_of,
    payload_object,
    register_environment,
)
from tests.unit.execution import (
    build_manifest,
    register_json_artifact,
    register_package_artifact,
    snapshot_document,
)

pytestmark = pytest.mark.usefixtures("harness")

ENVIRONMENT = "diff-live"
REFRESHED = "2026-09-09T09:09:09.000"
WEIGHT = "de000000001"
HEIGHT = "de000000002"
RETIRED = "de000000003"
VOLATILE = "de000000004"
COMBO = "qAZsZ5kBwLk"
NEW = "de0000000009"


def key_of(uid: str, dhis2_type: str = "dataElement") -> str:
    return f"{dhis2_type}/{uid}"


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {key_of(str(item["uid"]), str(item["dhis2Type"])): item for item in rows}


def _reports(project_id: str = PROJECT) -> list[str]:
    """该项目已有的 DIFF_REPORT 产物 ID（``core`` 跨测试累积，所以只能数而不能读旧的）。"""
    page = query_artifacts(
        ArtifactQuery(project_id=project_id, type="DIFF_REPORT", limit=200, descending=False)
    )
    return [item.id for item in page.items]


# --- 环境、实例与两份 Snapshot ---------------------------------------------


@pytest.fixture
def instance(harness: Any) -> Iterator[FakeDhis2Instance]:
    """一台已登记、已探测的 2.41：环境里现在长什么样是这里说定的。"""
    del harness
    made = FakeDhis2Instance()
    made.authorities = ["F_DATAELEMENT_ADD", "ALL"]
    made.roles = [{"id": "r1", "name": "Superuser"}]
    made.put("dataElement", WEIGHT, code="WEIGHT", name="体重", created=STAMP)
    made.put("dataElement", HEIGHT, code="HEIGHT", name="身高（厘米）", created=STAMP)
    made.put("dataElement", VOLATILE, code="BMI", name="体质指数", created=STAMP)
    made.put("categoryCombo", COMBO, code="default", name="default", created=STAMP)
    deps.set_transport(FakeDhis2Transport(made))
    deps.set_sleeper(lambda seconds: None)
    register_environment(ENVIRONMENT)
    # Diff 的现场读取要 Capability Profile 才能装配 Client（DX-CAP-003）
    api.probe_capabilities(ENVIRONMENT, idempotency_key="diff:cap")
    yield made


def _live(uid: str, **changes: Any) -> dict[str, Any]:
    """环境里现在的内容，按 ``FULL_READ_FIELDS`` 投影后的形态。"""
    return content_of(uid, **changes)


def _baseline_objects() -> list[dict[str, Any]]:
    """提交前 Snapshot 的条目：四个对象 + 一个只被引用的 default 组合。"""
    return [
        entry_of(WEIGHT, content=_live(WEIGHT, code="WEIGHT"), business_key="bk.weight"),
        entry_of(
            HEIGHT,
            content=_live(HEIGHT, code="HEIGHT", name="身高"),
            business_key="bk.height",
        ),
        entry_of(RETIRED, content=_live(RETIRED, code="OLD", name="旧指标"), business_key="bk.old"),
        entry_of(
            VOLATILE,
            content=_live(VOLATILE, code="BMI", name="体质指数"),
            business_key="bk.bmi",
        ),
        entry_of(
            COMBO,
            dhis2_type="categoryCombo",
            content=_live(COMBO, code="default", name="default"),
            business_key="default.categoryCombo",
        ),
    ]


def _baseline_document() -> dict[str, Any]:
    document = snapshot_document(
        _baseline_objects(),
        kind=KIND_PRE_COMMIT,
        environment_key=ENVIRONMENT,
        payload_objects=[
            payload_object(WEIGHT, business_key="bk.weight", disposition=Disposition.CREATE.value),
            payload_object(HEIGHT, business_key="bk.height", disposition=Disposition.UPDATE.value),
            payload_object(RETIRED, business_key="bk.old", disposition=Disposition.CREATE.value),
            payload_object(VOLATILE, business_key="bk.bmi", disposition=Disposition.UPDATE.value),
            payload_object(
                COMBO,
                dhis2_type="categoryCombo",
                business_key="default.categoryCombo",
                disposition=Disposition.REFERENCE.value,
            ),
        ],
    )
    return document


def _post_commit_document() -> dict[str, Any]:
    """部署后验证留下的读回结果：体重没变、身高改了、旧指标没了、本次新建的对象出现了。"""
    return snapshot_document(
        [
            entry_of(WEIGHT, content=_live(WEIGHT, code="WEIGHT"), business_key="bk.weight"),
            entry_of(
                HEIGHT,
                content=_live(HEIGHT, code="HEIGHT", name="身高（厘米）"),
                business_key="bk.height",
            ),
            entry_of(RETIRED, present=False, business_key="bk.old"),
            entry_of(
                VOLATILE,
                content=_live(VOLATILE, code="BMI", name="体质指数"),
                business_key="bk.bmi",
            ),
            entry_of(
                COMBO,
                dhis2_type="categoryCombo",
                content=_live(COMBO, code="default", name="default"),
                business_key="default.categoryCombo",
            ),
            entry_of(NEW, content=content_of(NEW, code="NEW"), business_key="bk.new"),
        ],
        kind=KIND_POST_COMMIT,
        environment_key=ENVIRONMENT,
        payload_objects=[
            payload_object(NEW, business_key="bk.new", disposition=Disposition.CREATE.value)
        ],
    )


def _package() -> str:
    return register_package_artifact(
        {"dataElements": [_live(WEIGHT, code="WEIGHT")]},
        build_manifest(
            objects=[
                {"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": WEIGHT, "disposition": "CREATE"}
            ],
            uid_map=[
                {"businessKey": "bk.weight", "uid": WEIGHT, "dhis2Type": "dataElement", "disposition": "CREATE"}
            ],
            dispositions={"bk.weight": "CREATE"},
            controlled_fields={},
            project_id=PROJECT,
            environment_key=ENVIRONMENT,
        ),
        project_id=PROJECT,
    )


def _deploy(
    *, snapshot_artifact_id: str | None, post_artifact_id: str | None = None
) -> tuple[str, str]:
    """一次「已提交」的执行：载荷产物 + 提交前 Snapshot（可选：部署后读回的 POST_COMMIT）。"""
    package_id = _package()
    execution_id = add_execution(
        environment_key=ENVIRONMENT,
        payload_artifact_id=package_id,
        snapshot_artifact_id=snapshot_artifact_id,
    )
    if post_artifact_id is not None:
        add_snapshot_index(
            post_artifact_id,
            environment_key=ENVIRONMENT,
            execution_id=execution_id,
            kind=KIND_POST_COMMIT,
        )
    return execution_id, package_id


def _post_commit_artifact() -> str:
    return register_json_artifact(_post_commit_document(), "SNAPSHOT", project_id=PROJECT)


# --- 没有基准：不能说成没有差异 ---------------------------------------------


def test_without_a_pre_commit_snapshot_the_diff_refuses_to_claim_no_difference(
    instance: FakeDhis2Instance,
) -> None:
    """DX-EXE-022、DX-DIFF-001：没有提交前 Snapshot 就报告「无法对比」并给原因，绝不说成没有差异。"""
    execution_id = add_execution(environment_key=ENVIRONMENT, payload_artifact_id=_package())
    reports_before = len(_reports())
    requests_before = len(instance.requests)

    outcome = api.diff_snapshot_vs_current(execution_id)

    assert outcome.diff_report_artifact_id is None
    assert outcome.objects == []
    assert "无法对比" in str(outcome.summary["reason"])
    assert outcome.summary["executionId"] == execution_id
    # 没有四种展示的计数、也没有 changed=False 的结论：那会让一次没记录的部署看起来像一次干净的部署
    assert "byKind" not in outcome.summary and "changed" not in outcome.summary
    assert len(_reports()) == reports_before, "无法对比时不产出 DIFF_REPORT"
    assert len(instance.requests) == requests_before, "连读都不该读：基准都没有"


def test_the_missing_baseline_is_not_recovered_by_guessing_one_from_the_index(
    instance: FakeDhis2Instance,
) -> None:
    """DX-EXE-022：只有 POST_COMMIT 也不算有基准——拿提交后的状态和自己比会永远「没有差异」。"""
    artifact_id = register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT)
    execution_id = add_execution(
        environment_key=ENVIRONMENT, payload_artifact_id=_package(), snapshot_artifact_id=None
    )
    add_snapshot_index(
        artifact_id,
        environment_key=ENVIRONMENT,
        execution_id=execution_id,
        kind=KIND_POST_COMMIT,
    )

    outcome = api.diff_snapshot_vs_current(execution_id)

    assert outcome.diff_report_artifact_id is None
    assert "无法对比" in str(outcome.summary["reason"])


def test_a_baseline_without_any_way_to_read_the_current_state_also_refuses(
    instance: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：有基准但既无 POST_COMMIT 又无载荷可回读，同样报「无法对比」。

    把空集合当成「现在的环境」会产出一份“所有对象都不见了”的报告——那是把“我没法知道”
    说成了一次大规模删除，比说成没有差异更危险。
    """
    execution_id = add_execution(
        environment_key=ENVIRONMENT,
        payload_artifact_id=None,
        snapshot_artifact_id=register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT),
    )
    requests_before = len(instance.requests)

    outcome = api.diff_snapshot_vs_current(execution_id)

    assert outcome.diff_report_artifact_id is None
    assert outcome.objects == []
    assert "无法对比" in str(outcome.summary["reason"]), outcome.summary
    assert len(instance.requests) == requests_before, "读不到就不假装读过：一个请求都不发"
    assert "byKind" not in outcome.summary


def test_an_unknown_execution_is_an_error_rather_than_an_empty_report(
    instance: FakeDhis2Instance,
) -> None:
    """DX-BR-02：对比显式绑定一次执行，没有这次执行就如实失败，不给一份空报告。"""
    with pytest.raises(NotFoundError):
        api.diff_snapshot_vs_current("dxexe-never-ran-0000000000")


# --- 有 POST_COMMIT：不再打扰环境 ------------------------------------------


def test_a_post_commit_snapshot_is_compared_without_reading_the_environment_again(
    instance: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：部署后验证留下的 POST_COMMIT 就是提交后状态，有它就不再读第二次。"""
    baseline_id = register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT)
    post_id = _post_commit_artifact()
    execution_id, _ = _deploy(snapshot_artifact_id=baseline_id, post_artifact_id=post_id)
    requests_before = len(instance.requests)

    outcome = api.diff_snapshot_vs_current(execution_id)

    rows = _index(outcome.objects)
    assert rows[key_of(WEIGHT)]["kind"] == ObjectChangeKind.UNCHANGED.value
    assert rows[key_of(HEIGHT)]["changedFields"] == [
        {"field": "name", "from": "身高", "to": "身高（厘米）"}
    ]
    assert rows[key_of(RETIRED)]["changedFields"] == [
        {"field": PRESENCE_FIELD, "from": True, "to": False}
    ]
    assert rows[key_of(NEW)]["kind"] == ObjectChangeKind.CREATED.value
    assert rows[key_of(COMBO, "categoryCombo")]["kind"] == ObjectChangeKind.REFERENCE_ONLY.value
    assert rows[key_of(VOLATILE)]["kind"] == ObjectChangeKind.UNCHANGED.value
    # 摘要与行同账：四类展示的计数之和就是行数
    assert outcome.summary["byKind"] == {
        ObjectChangeKind.CREATED.value: 1,
        ObjectChangeKind.UPDATED.value: 2,
        ObjectChangeKind.UNCHANGED.value: 2,
        ObjectChangeKind.REFERENCE_ONLY.value: 1,
    }
    assert outcome.summary["objects"] == len(outcome.objects) == 6
    assert len(instance.requests) == requests_before, "有读回结果还去问环境，就是把外部 I/O 当成了默认动作"

    document = read_json(str(outcome.diff_report_artifact_id))
    assert document["artifacts"]["currentReadBack"] == "SNAPSHOT"
    assert document["artifacts"]["currentSnapshot"] == post_id


def test_the_baseline_may_be_taken_from_the_snapshot_index(
    instance: FakeDhis2Instance,
) -> None:
    """DX-EXE-022：执行行没带引用时，基准从该执行最近的 PRE_COMMIT 索引里取，而不是判成没有基准。"""
    baseline_id = register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT)
    execution_id = add_execution(
        environment_key=ENVIRONMENT, payload_artifact_id=_package(), snapshot_artifact_id=None
    )
    add_snapshot_index(
        baseline_id,
        environment_key=ENVIRONMENT,
        execution_id=execution_id,
        kind=KIND_PRE_COMMIT,
    )

    outcome = api.diff_snapshot_vs_current(execution_id)

    document = read_json(str(outcome.diff_report_artifact_id))
    assert document["artifacts"]["baselineSnapshot"] == baseline_id


# --- 没有 POST_COMMIT：当场读一次环境 --------------------------------------


def test_without_a_post_commit_snapshot_the_current_state_is_read_back_live(
    instance: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：没有读回结果就当场按 discovery 范围读一次环境，并在产物里标成 LIVE。"""
    baseline_id = register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT)
    execution_id, _ = _deploy(snapshot_artifact_id=baseline_id)
    requests_before = len(instance.requests)

    outcome = api.diff_snapshot_vs_current(execution_id)

    rows = _index(outcome.objects)
    assert rows[key_of(WEIGHT)]["kind"] == ObjectChangeKind.UNCHANGED.value
    assert rows[key_of(HEIGHT)]["changedFields"] == [
        {"field": "name", "from": "身高", "to": "身高（厘米）"}
    ]
    assert rows[key_of(RETIRED)]["presentInCurrent"] is False
    assert rows[key_of(RETIRED)]["kind"] == ObjectChangeKind.UPDATED.value
    assert rows[key_of(COMBO, "categoryCombo")]["kind"] == (
        ObjectChangeKind.REFERENCE_ONLY.value
    ), "只被引用的对象没有因为本次读回而变成差异"
    assert rows[key_of(WEIGHT)]["businessKey"] == "bk.weight"

    document = read_json(str(outcome.diff_report_artifact_id))
    assert document["artifacts"]["currentReadBack"] == "LIVE"
    assert document["artifacts"]["currentSnapshot"] is None
    assert document["summary"]["objects"] == len(document["objects"])

    made = instance.requests[requests_before:]
    assert {item["method"] for item in made} == {"GET"}
    assert {item["path"] for item in made} == {
        f"dataElements/{WEIGHT}",
        f"dataElements/{HEIGHT}",
        f"dataElements/{RETIRED}",
        f"dataElements/{VOLATILE}",
        f"categoryCombos/{COMBO}",
    }
    assert made, "现场读回必须真的发过请求"


def test_a_volatile_refresh_in_the_live_environment_is_not_a_change(
    instance: FakeDhis2Instance,
) -> None:
    """DX-DSC-007、AC-EXE-07：环境里只刷新了 lastUpdated 的对象，读回来仍然是不变。"""
    instance.objects["dataElement"][VOLATILE]["lastUpdated"] = REFRESHED
    baseline_id = register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT)
    execution_id, _ = _deploy(snapshot_artifact_id=baseline_id)

    outcome = api.diff_snapshot_vs_current(execution_id)

    rows = _index(outcome.objects)
    assert rows[key_of(VOLATILE)]["kind"] == ObjectChangeKind.UNCHANGED.value
    assert rows[key_of(VOLATILE)]["changedFields"] == []


def test_the_diff_never_writes_to_the_environment(
    instance: FakeDhis2Instance,
) -> None:
    """DX-BR-04：Diff 是观察环境，一次写入、一次导入都没有。"""
    baseline_id = register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT)
    execution_id, _ = _deploy(snapshot_artifact_id=baseline_id)
    state_before = {key: dict(items) for key, items in instance.objects.items()}

    api.diff_snapshot_vs_current(execution_id)

    assert instance.imports_accepted == 0
    assert instance.deletes_accepted == 0
    assert {item["method"] for item in instance.requests} == {"GET"}
    assert instance.objects == state_before, "环境状态一点没动"


# --- 现场读回的文档形状 -----------------------------------------------------


def test_the_live_read_back_is_a_snapshot_shaped_document_with_the_same_hash_definition(
    instance: FakeDhis2Instance,
) -> None:
    """DX-EXE-022、DX-DSC-007：读回的文档要与 Snapshot 同形、同哈希定义，否则两侧根本不可比。"""
    context = load_context(
        ENVIRONMENT,
        _package(),
        project_id=PROJECT,
        scope=CredentialScope.DISCOVERY,
    )
    baseline = _baseline_document()
    baseline["objects"].append({"uid": "noType000001", "dhis2Type": "", "present": True})

    document = live_document(context, baseline)

    assert document["kind"] == "SNAPSHOT"
    assert document["snapshotKind"] == "LIVE_READ_BACK"
    assert document["environmentKey"] == ENVIRONMENT
    assert document["dhis2Version"] == instance.version
    assert document["payloadObjects"] == baseline["payloadObjects"], "处置仍然来自基准那份"
    # 没有类型的条目没法寻址，不参与读回（宁可少一项，也不按空 UID 去读错东西）
    assert document["objectCount"] == len(baseline["objects"]) - 1
    entries = _index(document["objects"])
    weight = entries[key_of(WEIGHT)]
    assert weight["present"] is True
    assert weight["businessKey"] == "bk.weight"
    assert weight["content"] == _live(WEIGHT, code="WEIGHT")
    assert weight["contentHash"] == content_hash_of(weight["content"], PROFILE)
    assert weight["contentHash"] == object_hash(_live(WEIGHT, code="WEIGHT"), PROFILE.volatile_fields)
    assert entries[key_of(RETIRED)]["present"] is False
    assert entries[key_of(RETIRED)]["contentHash"] is None
    assert [(item["dhis2Type"], item["uid"]) for item in document["objects"]] == [
        ("categoryCombo", COMBO),
        ("dataElement", WEIGHT),
        ("dataElement", HEIGHT),
        ("dataElement", RETIRED),
        ("dataElement", VOLATILE),
    ]


def test_a_minimal_baseline_entry_is_still_enough_to_read_one_object_back(
    instance: FakeDhis2Instance,
) -> None:
    """DX-EXE-022：读回只需要「类型 + UID」这一对，其余字段都由环境现状补齐。"""
    context = load_context(ENVIRONMENT, _package(), project_id=PROJECT)
    baseline = {
        "objects": [{"dhis2Type": "dataElement", "uid": WEIGHT, "present": True}],
        "payloadObjects": [],
    }

    document = live_document(context, baseline)

    assert [(item["dhis2Type"], item["uid"], item["present"]) for item in document["objects"]] == [
        ("dataElement", WEIGHT, True)
    ]
    assert document["payloadObjects"] == []


# --- 产物的来源与归属 -------------------------------------------------------


def test_the_diff_report_is_registered_from_both_snapshots_and_names_the_environment(
    instance: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001、DX-ART-005：DIFF_REPORT 的来源就是这两份 Snapshot，项目与环境是被比的那一对。"""
    baseline_id = register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT)
    post_id = _post_commit_artifact()
    execution_id, package_id = _deploy(snapshot_artifact_id=baseline_id, post_artifact_id=post_id)

    outcome = api.diff_snapshot_vs_current(execution_id)

    metadata = read_metadata(str(outcome.diff_report_artifact_id))
    assert metadata.type == "DIFF_REPORT"
    assert metadata.creator_subsystem == "S4"
    assert metadata.project_id == PROJECT
    assert sorted(metadata.source_artifact_ids) == sorted([baseline_id, post_id])
    document = read_json(str(outcome.diff_report_artifact_id))
    assert document["comparison"] == "SNAPSHOT_VS_CURRENT"
    assert document["projectKey"] == PROJECT
    assert document["environmentKeys"] == [ENVIRONMENT]
    assert document["profileVersions"] == ["Dhis241Profile"]
    assert document["artifacts"] == {
        "baselineSnapshot": baseline_id,
        "currentSnapshot": post_id,
        "currentReadBack": "SNAPSHOT",
        "payload": package_id,
        "payloadHash": PAYLOAD_HASH,
        "executionId": execution_id,
    }
    assert document["summary"]["byKind"][ObjectChangeKind.CREATED.value] == 1
    assert document["summary"]["byKind"][ObjectChangeKind.UPDATED.value] == 2
    assert document["summary"]["changed"] is True


def test_the_execution_view_reports_only_persisted_facts(
    instance: FakeDhis2Instance,
) -> None:
    """DX-EXE-036：给日志与产物引用的执行视图只取已落库的那几个字段，不去问实例。"""
    baseline_id = register_json_artifact(_baseline_document(), "SNAPSHOT", project_id=PROJECT)
    execution_id, package_id = _deploy(snapshot_artifact_id=baseline_id)
    requests_before = len(instance.requests)

    with unit_of_work() as session:
        row = session.get(ExecutionRow, execution_id)
        assert row is not None
        view = execution_view(session, row)

    assert view == {
        "executionId": execution_id,
        "environmentKey": ENVIRONMENT,
        "payloadArtifactId": package_id,
        "snapshotArtifactId": baseline_id,
    }
    assert len(instance.requests) == requests_before
