"""同一版本在两个环境中的部署状态对比（DX-DIFF-001 之二、总体 SRS 15.5）。

多环境晋级时最贵的问题是「UAT 和 PROD 到底差在哪」。答案的取材规则都在这里，所以逐条钉：

* 候选是该项目**已完成且已生效**的执行——失败的执行、只跑 VALIDATE 没生效的执行、别的项目
  的执行，都不能代表「那个环境部署成了什么」（DX-EXE-035 的 applied 同一件事）；
* 指定 Blueprint 版本时沿最近的成功执行往前找那一次，版本不同的两份状态没有比较意义；
* 每一侧优先取 POST_COMMIT（部署后读回），没有它才退回提交前 Snapshot，并在
  ``artifacts.snapshotKind`` 里如实标出用的是哪一种——不假装两侧同一口径；
* 没部署过的那一侧就是空的一侧，报告里要有这句话。两个环境都没部署过就没法比，**这不是
  「没有差异」**：把后者当成结论，等于宣布 UAT 与 PROD 一致。

``diff_environments`` 全程只读 Factory 里已经记录的状态，一次 DHIS2 请求都不发（DX-BR-04）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from factory_foundation.api import ArtifactQuery, query_artifacts

from factory_dhis2 import api, deps
from factory_dhis2.artifacts import read_json, read_metadata
from factory_dhis2.diff.environments import latest_successful_snapshot
from factory_dhis2.enums import ExecutionStatus, ObjectChangeKind
from factory_dhis2.errors import EnvironmentInvalidError
from factory_dhis2.execution.snapshot import KIND_POST_COMMIT, KIND_PRE_COMMIT
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport
from tests.unit.diff import (
    BASE,
    PROFILE,
    PROJECT,
    add_execution,
    add_snapshot_index,
    content_of,
    entry_of,
    payload_object,
    register_environment,
)
from tests.unit.execution import register_json_artifact, snapshot_document

pytestmark = pytest.mark.usefixtures("harness")

UAT = "diff-uat"
PROD = "diff-prod"
REFRESHED = "2026-09-09T09:09:09.000"
WEIGHT = "de000000001"
HEIGHT = "de000000002"
RETIRED = "de000000003"
VOLATILE = "de000000004"
COMBO = "qAZsZ5kBwLk"
NEW = "de0000000009"
OTHER_PROJECT = "prj-diff-other"


def key_of(uid: str, dhis2_type: str = "dataElement") -> str:
    return f"{dhis2_type}/{uid}"


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {key_of(str(item["uid"]), str(item["dhis2Type"])): item for item in rows}


def _reports(project_id: str = PROJECT) -> list[str]:
    """该项目已有的 DIFF_REPORT 产物 ID（``core`` 跨测试累积，所以只数不加断言旧的）。"""
    page = query_artifacts(
        ArtifactQuery(project_id=project_id, type="DIFF_REPORT", limit=200, descending=False)
    )
    return [item.id for item in page.items]


@pytest.fixture
def fake(harness: Any) -> Iterator[FakeDhis2Instance]:
    """两台已登记环境 + 一台挂在 transport 上的实例：任何越界的请求都会在这里留下记录。"""
    del harness
    made = FakeDhis2Instance()
    deps.set_transport(FakeDhis2Transport(made))
    register_environment(UAT)
    register_environment(PROD)
    yield made


# --- 取材：哪些执行算「那个环境的部署状态」 --------------------------------


def _plan(blueprint_version: str) -> str:
    return register_json_artifact(
        {"kind": "EXECUTION_PLAN", "blueprintVersion": blueprint_version, "steps": []},
        "EXECUTION_PLAN",
        project_id=PROJECT,
    )


def _snapshot(
    environment_key: str,
    objects: list[dict[str, Any]],
    *,
    kind: str = KIND_POST_COMMIT,
    payload_objects: list[dict[str, Any]] | None = None,
) -> str:
    return register_json_artifact(
        snapshot_document(
            objects,
            kind=kind,
            environment_key=environment_key,
            payload_objects=payload_objects or [],
        ),
        "SNAPSHOT",
        project_id=PROJECT,
    )


def _deployed(
    environment_key: str,
    objects: list[dict[str, Any]],
    *,
    blueprint_version: str = "3.1.0",
    completed_at: Any = BASE,
    status: str = ExecutionStatus.COMPLETED.value,
    applied: bool | None = True,
    project_id: str | None = PROJECT,
    payload_objects: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """造一次「这个环境的部署状态」：计划产物 + Snapshot 产物 + 执行行 + 索引行。"""
    plan_id = _plan(blueprint_version)
    snapshot_id = _snapshot(environment_key, objects, payload_objects=payload_objects)
    execution_id = add_execution(
        environment_key=environment_key,
        project_id=project_id,
        plan_artifact_id=plan_id,
        snapshot_artifact_id=snapshot_id,
        status=status,
        applied=applied,
        completed_at=completed_at,
    )
    add_snapshot_index(
        snapshot_id,
        environment_key=environment_key,
        execution_id=execution_id,
        kind=KIND_POST_COMMIT,
        at=completed_at,
    )
    return execution_id, snapshot_id


def test_only_completed_and_applied_executions_of_the_same_project_are_candidates(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：基准只取该项目已完成**且已生效**的执行——失败、没生效或别的项目的执行不算那个环境的部署状态。"""
    chosen, chosen_snapshot = _deployed(UAT, [entry_of(WEIGHT)], completed_at=BASE)
    _deployed(
        UAT,
        [entry_of(HEIGHT)],
        completed_at=BASE + timedelta(hours=1),
        status=ExecutionStatus.FAILED.value,
        applied=None,
    )
    _deployed(
        UAT,
        [entry_of(VOLATILE)],
        completed_at=BASE + timedelta(hours=2),
        applied=False,
    )
    _deployed(
        UAT,
        [entry_of(NEW)],
        completed_at=BASE + timedelta(hours=3),
        project_id=OTHER_PROJECT,
    )

    side = latest_successful_snapshot(UAT, PROJECT)

    assert side["executionId"] == chosen
    assert side["artifacts"]["snapshot"] == chosen_snapshot
    assert set(side["entries"]) == {key_of(WEIGHT)}


def test_the_requested_blueprint_version_is_picked_even_when_it_is_not_the_newest_run(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：只有同一版本在两个环境才可比——指定版本时沿最近的成功执行往前找那一次。"""
    older, _ = _deployed(UAT, [entry_of(WEIGHT)], blueprint_version="3.1.0", completed_at=BASE)
    newer, _ = _deployed(
        UAT, [entry_of(HEIGHT)], blueprint_version="3.2.0", completed_at=BASE + timedelta(hours=1)
    )

    assert latest_successful_snapshot(UAT, PROJECT)["executionId"] == newer
    assert latest_successful_snapshot(UAT, PROJECT, "3.1.0")["executionId"] == older
    # 找不到那个版本就是「没有」：不回落到最新那份，否则报告在比较两个根本没部署过的版本
    assert latest_successful_snapshot(UAT, PROJECT, "9.9.9")["executionId"] is None


def test_the_candidate_scan_is_bounded_and_a_miss_is_reported_as_a_miss(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：候选扫描有上界——落在界外的旧版本就是找不到，而不是无界往前翻。"""
    shared_plan = _plan("3.1.0")
    shared_snapshot = _snapshot(UAT, [entry_of(WEIGHT)])
    newest = ""
    for index in range(20):
        newest = add_execution(
            environment_key=UAT,
            plan_artifact_id=shared_plan,
            snapshot_artifact_id=shared_snapshot,
            completed_at=BASE + timedelta(hours=index),
        )
    # 第 21 次（最早的那次）是这个旧版本：它落在 20 条窗口之外
    add_execution(
        environment_key=UAT,
        plan_artifact_id=_plan("2.9.0"),
        snapshot_artifact_id=shared_snapshot,
        completed_at=BASE - timedelta(days=1),
    )

    assert latest_successful_snapshot(UAT, PROJECT)["executionId"] == newest
    assert latest_successful_snapshot(UAT, PROJECT, "2.9.0")["executionId"] is None


def test_the_post_commit_snapshot_wins_even_when_a_newer_pre_commit_exists(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：读回结果比提交前基准更贴近「现在长什么样」，所以 POST_COMMIT 优先，哪怕索引里 PRE_COMMIT 更新。"""
    post = _snapshot(UAT, [entry_of(WEIGHT)], kind=KIND_POST_COMMIT)
    pre = _snapshot(UAT, [entry_of(HEIGHT)], kind=KIND_PRE_COMMIT)
    execution_id = add_execution(
        environment_key=UAT, snapshot_artifact_id=pre, completed_at=BASE
    )
    add_snapshot_index(
        post,
        environment_key=UAT,
        execution_id=execution_id,
        kind=KIND_POST_COMMIT,
        at=BASE,
    )
    add_snapshot_index(
        pre,
        environment_key=UAT,
        execution_id=execution_id,
        kind=KIND_PRE_COMMIT,
        at=BASE + timedelta(hours=1),
    )

    side = latest_successful_snapshot(UAT, PROJECT)

    assert side["artifacts"]["snapshot"] == post
    assert side["artifacts"]["snapshotKind"] == KIND_POST_COMMIT
    assert set(side["entries"]) == {key_of(WEIGHT)}


def test_an_execution_without_index_rows_falls_back_to_the_snapshot_on_the_execution_row(
    fake: FakeDhis2Instance,
) -> None:
    """DX-EXE-022：没有索引行时退回执行行上的 Snapshot 引用，并如实标成 PRE_COMMIT。"""
    snapshot_id = _snapshot(UAT, [entry_of(WEIGHT)], kind=KIND_PRE_COMMIT)
    execution_id = add_execution(
        environment_key=UAT, snapshot_artifact_id=snapshot_id, completed_at=BASE
    )

    side = latest_successful_snapshot(UAT, PROJECT)

    assert side["executionId"] == execution_id
    assert side["artifacts"]["snapshot"] == snapshot_id
    assert side["artifacts"]["snapshotKind"] == KIND_PRE_COMMIT


def test_an_environment_with_nothing_recorded_yields_an_empty_side_not_an_error(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：没部署过的一侧就是**空的一侧**——这本身就是答案，既不编造基准也不当成错误。"""
    side = latest_successful_snapshot(PROD, PROJECT)

    assert side == {
        "executionId": None,
        "entries": {},
        "document": None,
        "artifacts": {
            "executionId": None,
            "snapshot": None,
            "snapshotKind": None,
            "environmentKey": PROD,
        },
        "environmentKey": PROD,
    }


# --- 逐对象对比 -------------------------------------------------------------


def _uat_state() -> list[dict[str, Any]]:
    return [
        entry_of(WEIGHT, content=content_of(WEIGHT, code="WEIGHT"), business_key="bk.weight"),
        entry_of(HEIGHT, content=content_of(HEIGHT, code="HEIGHT", name="身高"), business_key="bk.height"),
        entry_of(RETIRED, content=content_of(RETIRED, code="OLD", name="旧指标"), business_key="bk.old"),
        entry_of(VOLATILE, content=content_of(VOLATILE, code="BMI"), business_key="bk.bmi"),
        entry_of(
            COMBO,
            dhis2_type="categoryCombo",
            content=content_of(COMBO, code="default", name="default"),
            business_key="default.categoryCombo",
        ),
    ]


def _prod_state() -> list[dict[str, Any]]:
    return [
        entry_of(WEIGHT, content=content_of(WEIGHT, code="WEIGHT"), business_key="bk.weight"),
        entry_of(
            HEIGHT,
            content=content_of(HEIGHT, code="HEIGHT", name="身高（厘米）"),
            business_key="bk.height",
        ),
        entry_of(
            VOLATILE,
            content=content_of(VOLATILE, code="BMI", lastUpdated=REFRESHED),
            business_key="bk.bmi",
        ),
        entry_of(
            COMBO,
            dhis2_type="categoryCombo",
            content=content_of(COMBO, code="default", name="default"),
            business_key="default.categoryCombo",
        ),
        entry_of(NEW, content=content_of(NEW, code="NEW", name="新指标"), business_key="bk.new"),
    ]


def _payload_objects() -> list[dict[str, Any]]:
    return [
        payload_object(WEIGHT, business_key="bk.weight", disposition="CREATE"),
        payload_object(HEIGHT, business_key="bk.height", disposition="UPDATE"),
        payload_object(RETIRED, business_key="bk.old", disposition="UPDATE"),
        payload_object(VOLATILE, business_key="bk.bmi", disposition="UPDATE"),
        payload_object(
            COMBO,
            dhis2_type="categoryCombo",
            business_key="default.categoryCombo",
            disposition="REFERENCE",
        ),
    ]


def test_the_two_environments_are_compared_object_by_object_into_one_registered_report(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001、总体 SRS 15.5：两个环境的部署状态逐对象对比，四类展示与摘要同账，产出一份 DIFF_REPORT。"""
    _deployed(UAT, _uat_state(), payload_objects=_payload_objects())
    _deployed(PROD, _prod_state(), payload_objects=_payload_objects())

    outcome = api.diff_environments(PROJECT, UAT, PROD, blueprint_version="3.1.0")

    rows = _index(outcome.objects)
    assert rows[key_of(WEIGHT)]["kind"] == ObjectChangeKind.UNCHANGED.value
    assert rows[key_of(HEIGHT)]["changedFields"] == [
        {"field": "name", "from": "身高", "to": "身高（厘米）"}
    ]
    # 只在 UAT 存在的对象：SRS 只给四种展示，所以是「新建」，方向由两个布尔字段说明
    assert rows[key_of(RETIRED)]["kind"] == ObjectChangeKind.CREATED.value
    assert rows[key_of(RETIRED)]["presentInBaseline"] is True
    assert rows[key_of(RETIRED)]["presentInCurrent"] is False
    assert "仅存在于 A 侧" in str(rows[key_of(RETIRED)]["note"])
    assert rows[key_of(NEW)]["kind"] == ObjectChangeKind.CREATED.value
    assert rows[key_of(NEW)]["presentInBaseline"] is False
    # PROD 那边只是刷新了 lastUpdated：不是差异
    assert rows[key_of(VOLATILE)]["kind"] == ObjectChangeKind.UNCHANGED.value
    assert rows[key_of(COMBO, "categoryCombo")]["kind"] == ObjectChangeKind.REFERENCE_ONLY.value
    assert rows[key_of(WEIGHT)]["businessKey"] == "bk.weight"

    assert outcome.summary["byKind"] == {
        ObjectChangeKind.CREATED.value: 2,
        ObjectChangeKind.UPDATED.value: 1,
        ObjectChangeKind.UNCHANGED.value: 2,
        ObjectChangeKind.REFERENCE_ONLY.value: 1,
    }
    assert outcome.summary["objects"] == len(outcome.objects) == 6
    assert sum(outcome.summary["byKind"].values()) == outcome.summary["objects"]
    assert outcome.summary["changed"] is True
    assert outcome.summary["notes"] == [], "两侧都有成功执行时不该出现「按空的一侧对比」"


def test_the_report_is_registered_with_both_snapshots_and_names_both_environments(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001、DX-ART-005：报告的来源就是这两份 Snapshot，项目与环境键就是被比的那一对。"""
    _, uat_snapshot = _deployed(UAT, _uat_state(), payload_objects=_payload_objects())
    _, prod_snapshot = _deployed(PROD, _prod_state(), payload_objects=_payload_objects())

    outcome = api.diff_environments(PROJECT, UAT, PROD, blueprint_version="3.1.0")

    metadata = read_metadata(str(outcome.diff_report_artifact_id))
    assert metadata.type == "DIFF_REPORT"
    assert metadata.creator_subsystem == "S4"
    assert metadata.project_id == PROJECT
    assert sorted(metadata.source_artifact_ids) == sorted([uat_snapshot, prod_snapshot])

    document = read_json(str(outcome.diff_report_artifact_id))
    assert document["comparison"] == "ENVIRONMENTS"
    assert document["projectKey"] == PROJECT
    assert document["environmentKeys"] == [UAT, PROD]
    assert document["blueprintVersion"] == "3.1.0"
    assert document["profileVersions"] == [PROFILE.profile_version]
    assert document["artifacts"]["environmentA"] == UAT
    assert document["artifacts"]["environmentB"] == PROD
    assert document["artifacts"]["A"]["snapshot"] == uat_snapshot
    assert document["artifacts"]["A"]["snapshotKind"] == KIND_POST_COMMIT
    assert document["artifacts"]["B"]["snapshot"] == prod_snapshot
    assert document["objects"] == outcome.objects
    assert document["summary"] == outcome.summary


def test_an_environment_that_never_deployed_this_project_is_said_out_loud(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001、DX-EXE-022：一边没部署过要说成「按空的一侧对比」，而不是「两边一样」。"""
    _, uat_snapshot = _deployed(UAT, _uat_state(), payload_objects=_payload_objects())
    reports_before = len(_reports())

    outcome = api.diff_environments(PROJECT, UAT, PROD)

    assert outcome.diff_report_artifact_id
    assert any(PROD in str(item) and "成功执行" in str(item) for item in outcome.summary["notes"])
    assert all(row["presentInCurrent"] is False for row in outcome.objects)
    assert outcome.summary["byKind"][ObjectChangeKind.CREATED.value] == len(
        [row for row in outcome.objects if row["presentInBaseline"]]
    )
    document = read_json(str(outcome.diff_report_artifact_id))
    assert document["artifacts"]["B"]["snapshot"] is None
    assert document["blueprintVersion"] is None
    metadata = read_metadata(str(outcome.diff_report_artifact_id))
    assert metadata.source_artifact_ids == [uat_snapshot], "只有一侧有基准就只登记那一份"
    assert len(_reports()) == reports_before + 1


def test_neither_environment_having_run_this_project_is_refused_not_reported_equal(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：两个环境都没有该项目的成功执行就没法比——如实拒绝，绝不给一份「没有差异」。"""
    reports_before = len(_reports())

    with pytest.raises(EnvironmentInvalidError) as refused:
        api.diff_environments(PROJECT, UAT, PROD)

    assert refused.value.details.get("rule") == "DX-DIFF-001"
    assert len(_reports()) == reports_before, "拒绝就是什么都不产出"


def test_a_newer_run_of_the_other_version_is_not_used_when_a_version_is_asked_for(
    fake: FakeDhis2Instance,
) -> None:
    """DX-DIFF-001：指定版本时另一侧没有那个版本，就说空的一侧，而不是拿别的版本的状态凑。"""
    _deployed(UAT, _uat_state(), blueprint_version="3.1.0")
    _deployed(PROD, _prod_state(), blueprint_version="3.2.0")

    outcome = api.diff_environments(PROJECT, UAT, PROD, blueprint_version="3.1.0")

    assert any(PROD in str(item) for item in outcome.summary["notes"])
    assert outcome.summary["objects"] == len(_uat_state())
    assert all(row["presentInCurrent"] is False for row in outcome.objects)


def test_comparing_two_environments_never_touches_either_instance(
    fake: FakeDhis2Instance,
) -> None:
    """DX-BR-04、DX-CAP-003：环境对比只用 Factory 里已记录的状态与已持久化的 Profile，一次请求都不发。"""
    _deployed(UAT, _uat_state(), payload_objects=_payload_objects())
    _deployed(PROD, _prod_state(), payload_objects=_payload_objects())

    api.diff_environments(PROJECT, UAT, PROD)

    assert fake.requests == []
    assert fake.imports_accepted == 0
    assert fake.deletes_accepted == 0
