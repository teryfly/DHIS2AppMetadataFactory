"""漂移检查（DX-EXE-020/021、AC-EXE-03/07）。

漂移的定义很窄：**REUSE / REFERENCE / UPDATE 对象在观测基准之后被别人改了**。自己刚提交的
不算漂移，所以比对的是 Registry 带来的观测基准。判定比两件事：内容哈希（排除易变字段）与
``lastUpdated``——只看后者会在无关字段刷新时误报，只看前者会漏掉“改过又改回来”的状态迁移
（AC-EXE-07：仅 ``lastUpdated`` 变化不报漂移）。

判不出来就不下结论：没有观测基准的对象报 ``NO_BASELINE``、只被改过时间戳的报
``UPDATED_ONLY``，两者都**不算漂移**，但必须出现在差异清单里让人看得见。
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_dhis2.artifacts import read_json
from factory_dhis2.discovery.canonical import object_hash
from factory_dhis2.errors import Dhis2TimeoutError
from factory_dhis2.execution import drift
from factory_dhis2.execution.drift import NO_BASELINE, check, drift_watch, save_drift_report
from factory_dhis2.execution.reads import DEFAULT_READ_FIELDS
from tests.unit.execution import (
    ENVIRONMENT,
    PROJECT,
    FakeClient,
    build_manifest,
    make_run_context,
    make_target,
    package_of,
    register_package_artifact,
)

UID = "de000000001"
OTHER_UID = "de000000002"
COMBO_UID = "qAZsZ5kBwLk"
BASELINE_STAMP = "2026-01-01T00:00:00.000"


class ReadingClient(FakeClient):
    """``list_all`` 也要留下调用记录：批量读是这条链路上唯一的一次实例访问。"""

    def list_all(  # type: ignore[override]
        self,
        dhis2_type: str,
        *,
        fields: str,
        filters: Any = (),
        page_size: int | None = None,
        limit: int | None = None,
        progress: Any = None,
        cancel: Any = None,
    ) -> Any:
        from tests.unit.execution import Call

        self.calls.append(
            Call("metadata.read", self.default_scope, dhis2_type=dhis2_type,
                 params={"fields": fields, "filters": list(filters)})
        )
        return super().list_all(
            dhis2_type,
            fields=fields,
            filters=filters,
            page_size=page_size,
            limit=limit,
            progress=progress,
            cancel=cancel,
        )


def _row(uid: str, *, name: str = "Weight", stamp: str = BASELINE_STAMP) -> dict[str, Any]:
    return {"id": uid, "code": f"code-{uid}", "name": name, "lastUpdated": stamp, "created": stamp}


def _package(*, with_change_summary: bool = False) -> Any:
    manifest = build_manifest(
        objects=[
            {"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "UPDATE"},
            {"businessKey": "bk.height", "dhis2Type": "dataElement", "uid": OTHER_UID, "disposition": "REUSE"},
            {"businessKey": "bk.new", "dhis2Type": "dataElement", "uid": "de000000003", "disposition": "CREATE"},
            {"businessKey": "bk.nameless", "dhis2Type": "dataElement", "uid": None, "disposition": "REFERENCE"},
        ],
        uid_map=[
            {"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "UPDATE"},
            {"businessKey": "bk.height", "uid": OTHER_UID, "dhis2Type": "dataElement", "disposition": "REUSE"},
            {"businessKey": "bk.new", "uid": "de000000003", "dhis2Type": "dataElement", "disposition": "CREATE"},
            {"businessKey": "bk.nameless", "uid": None, "dhis2Type": "dataElement", "disposition": "REFERENCE"},
        ],
        dispositions={"bk.weight": "UPDATE", "bk.height": "REUSE"},
        controlled_fields={"bk.weight": ["name"]},
        baselines=[
            {
                "businessKey": "bk.weight",
                "uid": UID,
                "observedLastUpdated": BASELINE_STAMP,
                "observedHash": object_hash(_row(UID), make_target().profile.volatile_fields),
            }
        ],
    )
    if with_change_summary:
        manifest["changeSummary"] = {
            "referencesOutsideManagedScope": [
                {"businessKey": "default.categoryCombo", "dhis2Type": "categoryCombo", "uid": COMBO_UID}
            ]
        }
    return package_of(manifest, {"dataElements": [_row(UID)]})


def _context(objects: dict[tuple[str, str], dict[str, Any]], *, client: FakeClient | None = None) -> Any:
    return make_run_context(
        _package(), objects=objects, client=client or ReadingClient(make_target(), objects=objects)
    )


# --- 关注清单 ---------------------------------------------------------------


def test_only_observed_dispositions_are_watch_list() -> None:
    """DX-EXE-020：漂移检查看的是 REUSE / REFERENCE / UPDATE，不是载荷里的每一个对象。"""
    watch = drift_watch(_package(with_change_summary=True))

    assert [item["uid"] for item in watch] == [UID, OTHER_UID, COMBO_UID]
    assert watch[0]["disposition"] == "UPDATE"
    assert watch[0]["observedHash"], "基准哈希要跟进来，否则比较无从做起"
    assert watch[1]["observedHash"] is None, "Registry 没给的基准不凭空造"
    assert watch[2]["disposition"] == "REFERENCE", "范围外的引用默认按被引用对象看待"


def test_a_watched_object_without_a_uid_is_not_watch_list() -> None:
    """DX-EXE-020：寻不到 UID 就无从读回，这样的对象不进清单而不是比出一个假结论。"""
    watch = drift_watch(_package())

    assert "de000000003" not in [item["uid"] for item in watch]
    assert all(item["uid"] for item in watch)


def test_the_same_object_reaching_the_watch_list_twice_is_watched_once() -> None:
    """S2 的真实包里，REUSE / REFERENCE 条目同时出现在 ``uidToBusinessKey`` 与
    ``referencesOutsideManagedScope`` —— 它们是同一个对象，清单是对象集合而不是两段来源的
    拼接：重复监测不改变 ``drifted``，但会把同一个对象写进执行报告、执行计划与 Snapshot 的
    状态指纹两次（DX-EXE-029），报告上也读成"这个对象测了两遍"。"""
    hash_of_baseline = object_hash(_row(OTHER_UID), make_target().profile.volatile_fields)
    watch = drift_watch(_both_sources(hash_of_baseline))

    assert [(item["uid"], item["businessKey"]) for item in watch] == [(OTHER_UID, "bk.sex")]
    assert watch[0]["observedHash"] == hash_of_baseline, "留下带基准的那份，比对才做得成"


def test_a_reference_row_with_a_baseline_replaces_a_watch_row_without_one() -> None:
    """去重的取舍是**能比对的那份优先**：Registry 侧没带基准、范围外引用侧带了，就留下
    能真正读出差异的那一份，而不是按来源顺序随便留一份。"""
    hash_of_baseline = object_hash(_row(OTHER_UID), make_target().profile.volatile_fields)
    manifest = _manifest_with_both_sources(None, hash_of_baseline)

    watch = drift_watch(package_of(manifest, {}))
    assert [item["observedHash"] for item in watch] == [hash_of_baseline]


def _both_sources(observed_hash: str) -> Any:
    manifest = _manifest_with_both_sources(observed_hash, observed_hash)
    return package_of(manifest, {})


def _manifest_with_both_sources(
    registry_baseline: str | None, reference_baseline: str | None
) -> dict[str, Any]:
    """一份同时把对象写进 uidToBusinessKey 与范围外引用的 manifest（S2 的真实形状）。"""
    entry = {
        "businessKey": "bk.sex",
        "dhis2Type": "optionSet",
        "uid": OTHER_UID,
        "disposition": "REUSE",
    }
    manifest = build_manifest(
        objects=[],
        uid_map=[entry],
        dispositions={"bk.sex": "REUSE"},
        controlled_fields={},
        baselines=[
            {
                "businessKey": "bk.sex",
                "dhis2Type": "optionSet",
                "uid": OTHER_UID,
                "observedLastUpdated": BASELINE_STAMP,
                "observedHash": registry_baseline,
            }
        ],
    )
    manifest["changeSummary"]["referencesOutsideManagedScope"] = [
        {
            **entry,
            "observedLastUpdated": BASELINE_STAMP,
            "observedHash": reference_baseline,
        }
    ]
    return manifest


# --- 判定 -------------------------------------------------------------------


def test_nothing_to_watch_is_a_clean_answer_without_reading_the_instance() -> None:
    """DX-EXE-021：没有关注对象就不打 DHIS2——空清单不是「读不到」。"""
    context = make_run_context(package_of(build_manifest(
        objects=[], uid_map=[], dispositions={}, controlled_fields={}), {}))

    result = check(context)

    assert result.drifted is False
    assert result.objects == []
    assert context.client.calls == []


def test_a_changed_content_hash_is_drift_with_both_hashes_as_evidence() -> None:
    """AC-EXE-03：基准之后内容变了就是漂移，证据是两个哈希而不是「感觉变了」。"""
    context = _context({("dataElement", UID): _row(UID, name="Renamed by someone else")})

    result = check(context)

    assert result.drifted is True
    entry = next(item for item in result.objects if item["uid"] == UID)
    assert entry["kind"] == "CONTENT_CHANGED"
    assert entry["businessKey"] == "bk.weight"
    assert entry["currentHash"] != entry["observedHash"]
    assert result.environment_key == ENVIRONMENT


def test_a_volatile_only_change_does_not_count_as_drift() -> None:
    """AC-EXE-07：只有 ``lastUpdated`` 变了不报漂移，但仍要报出 UPDATED_ONLY 这一事实。"""
    context = _context(
        {
            ("dataElement", UID): _row(UID, stamp="2026-02-02T10:00:00.000"),
            ("dataElement", OTHER_UID): _row(OTHER_UID),
        }
    )

    result = check(context)

    assert result.drifted is False, "仅时间戳变化不该把提交挡住"
    entry = next(item for item in result.objects if item["uid"] == UID)
    assert entry["kind"] == "UPDATED_ONLY"
    assert entry["currentLastUpdated"] == "2026-02-02T10:00:00.000"


def test_an_object_that_is_gone_is_a_drift_that_must_be_shown() -> None:
    """DX-EXE-020：被观察的对象被人删了也是漂移（读不到不等于没变化）。"""
    context = _context({("dataElement", UID): _row(UID)})

    result = check(context, [{"businessKey": "bk.gone", "dhis2Type": "dataElement", "uid": OTHER_UID,
                              "disposition": "REUSE", "observedHash": "sha256:whatever"}])

    entry = result.objects[0]
    assert entry["kind"] == "MISSING"
    assert entry["uid"] == OTHER_UID
    assert "已不存在" in entry["detail"]
    assert result.drifted is True


def test_an_object_without_a_baseline_is_reported_but_not_counted_as_drift() -> None:
    """DX-EXE-020：没有基准就无法判定——报出来，但不把它算成漂移（不凭空造结论）。"""
    context = _context({("dataElement", UID): _row(UID), ("dataElement", OTHER_UID): _row(OTHER_UID)})

    result = check(context)

    baseline_free = next(item for item in result.objects if item["uid"] == OTHER_UID)
    assert baseline_free["kind"] == NO_BASELINE
    assert "无法比较" in baseline_free["detail"]
    assert result.drifted is False, "只有 NO_BASELINE 时不能把提交挡住"


def test_the_batched_read_carries_the_watched_uids_per_type() -> None:
    """DX-NFR-02：同一类型的关注对象一次批量按 UID 读回，而不是一个对象一个请求。"""
    context = _context({("dataElement", UID): _row(UID), ("dataElement", OTHER_UID): _row(OTHER_UID)})

    check(context)

    reads = [item for item in context.client.calls if item.logical_name == "metadata.read"]
    assert len(reads) == 1
    assert f"id:in:[{UID},{OTHER_UID}]" in str(reads[0].params["filters"])
    assert reads[0].params["fields"] == DEFAULT_READ_FIELDS


def test_the_check_honours_cancellation_between_types() -> None:
    """DX-EXE-021：漂移检查要能被取消，不能变成一次停不下来的全量读。"""
    watch = [
        {"businessKey": "bk.a", "dhis2Type": "dataElement", "uid": UID, "disposition": "UPDATE",
         "observedHash": "sha256:a"},
        {"businessKey": "bk.b", "dhis2Type": "categoryCombo", "uid": COMBO_UID, "disposition": "REUSE",
         "observedHash": "sha256:b"},
    ]

    class _Cancel:
        def __init__(self) -> None:
            self.seen = 0

        def raise_if_cancelled(self) -> None:
            self.seen += 1
            if self.seen > 1:
                raise Dhis2TimeoutError("cancelled on the second type", details={})

    context = _context({("dataElement", UID): _row(UID)})

    with pytest.raises(Dhis2TimeoutError):
        check(context, watch, cancel=_Cancel())

    reads = [item for item in context.client.calls if item.logical_name == "metadata.read"]
    assert [item.dhis2_type for item in reads] == ["categoryCombo"], "取消必须停在下一类之前"


def test_a_read_that_fails_propagates_as_the_callers_problem() -> None:
    """DX-CLI-001：读不回来不是「没漂移」，管线要能把它转成明确的阻塞结论。"""

    class Failing(FakeClient):
        def list_all(self, *_args: Any, **_kwargs: Any) -> Any:
            raise Dhis2TimeoutError("instance did not answer", details={})
            yield {}

    context = _context({}, client=Failing(make_target()))

    with pytest.raises(Dhis2TimeoutError):
        check(context)


# --- 状态指纹与公共入口 ------------------------------------------------------


def test_the_observation_state_hash_follows_the_baselines_not_the_clock() -> None:
    """DX-EXE-029：「那一刻的状态」由基准集合折算而成，同样的观察给出同样的指纹。"""
    watch = [{"uid": UID, "observedHash": "sha256:one"}]
    differences = [{"uid": UID, "kind": "CONTENT_CHANGED"}]

    first = drift.state_hash_of_observation(differences, watch)
    second = drift.state_hash_of_observation(list(reversed(differences)), list(reversed(watch)))
    changed = drift.state_hash_of_observation(differences, [{"uid": UID, "observedHash": "sha256:two"}])

    assert first == second
    assert first != changed


def test_check_drift_is_callable_on_its_own(harness: Any) -> None:
    """DX-EXE-021：S1 在审批与提交时各查一次，这个入口不需要跑整条管线。"""
    del harness
    artifact_id = register_package_artifact({"dataElements": [_row(UID)]}, _manifest_for_artifact())
    context = make_run_context(package_of(_manifest_for_artifact(), {"dataElements": [_row(UID)]}))

    monkey = pytest.MonkeyPatch()
    monkey.setattr(drift, "load_context", lambda *_args, **_kwargs: context)
    try:
        result = drift.check_drift(ENVIRONMENT, artifact_id)
    finally:
        monkey.undo()

    assert result.environment_key == ENVIRONMENT
    assert result.checked_at


def test_the_drift_result_is_saved_as_part_of_an_execution_report(harness: Any) -> None:
    """DX-EXE-046：漂移结论作为产物保存下来，审批人看到的差异清单与提交时用的是同一份。"""
    result = check(_context({("dataElement", UID): _row(UID, name="Renamed")}))

    artifact_id = save_drift_report(
        project_id=PROJECT, environment_key=ENVIRONMENT, result=result, watch_count=2
    )
    document = read_json(artifact_id)

    assert document["kind"] == "DRIFT_CHECK"
    assert document["drifted"] is True
    assert document["watchedObjects"] == 2
    assert document["objects"] == result.objects
    assert document["checkedAt"] == result.checked_at


def _manifest_for_artifact() -> dict[str, Any]:
    manifest = build_manifest(
        objects=[{"businessKey": "bk.weight", "dhis2Type": "dataElement", "uid": UID, "disposition": "UPDATE"}],
        uid_map=[{"businessKey": "bk.weight", "uid": UID, "dhis2Type": "dataElement", "disposition": "UPDATE"}],
        dispositions={"bk.weight": "UPDATE"},
        controlled_fields={},
        baselines=[
            {
                "businessKey": "bk.weight",
                "uid": UID,
                "observedLastUpdated": BASELINE_STAMP,
                "observedHash": object_hash(_row(UID), make_target().profile.volatile_fields),
            }
        ],
    )
    return manifest


@pytest.fixture
def deletion_monkeypatch() -> Any:
    """占位夹具：本文件的用例自己管自己的 MonkeyPatch，不留全局状态。"""
    return None
