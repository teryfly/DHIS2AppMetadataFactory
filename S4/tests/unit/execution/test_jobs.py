"""异步导入任务的提交与轮询（DX-EXE-033/034、AC-EXE-03、DX-EXE-035）。

这一层的要害是**分工**：提交不可重复、轮询可重复。所以「任务引用先落库、再去问结果」
不是一个性能问题，而是恢复能不能不重做的唯一依据（DX-AC-03、NFR-REL-003）。
测试因此盯四件事：落库的**顺序**、恢复时**不再提交**、拿不到终态时说的是「不知道」而不是
「失败」（DX-EXE-035），以及 2.41 实测形态（``Location`` 指向任务类型集合、任务查询回**日志
条目数组**）被原样接住而不是被重新推导。
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import unit_of_work

from factory_dhis2 import deps
from factory_dhis2.enums import CredentialScope, ExecutionMode
from factory_dhis2.errors import Dhis2TaskFailedError, Dhis2TaskLostError
from factory_dhis2.execution import jobs, steps
from tests.unit.execution import PROJECT, Call, FakeClient, FakeResponse, make_target

ROOT = "https://dhis2.example/test/api"
TASK_TYPE_URL = f"{ROOT}/system/tasks/METADATA_IMPORT"
TERMINAL_MESSAGE = "Import complete with status OK, 2 created, 1 updated, 0 deleted, 3 ignored"


def log_entry(*, completed: bool = False, message: str = "working", level: str = "INFO") -> dict[str, Any]:
    return {
        "id": "log1",
        "uid": "log1",
        "category": "METADATA_IMPORT",
        "level": level,
        "message": message,
        "completed": completed,
    }


class TaskClient(FakeClient):
    """会按 2.41 异步语义回话的假 Client：``Location`` 指类型集合，任务 ID 在响应体里。"""

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
        tasks: list[Any] | None = None,
        location: str | None = TASK_TYPE_URL,
        job_id: str | None = "task9",
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
        # 与 ``calls`` 一样共享同一个列表对象：换凭证范围不能换掉任务队列
        self.tasks = tasks if tasks is not None else []
        self.location = location
        self.job_id = job_id

    def with_scope(self, scope: CredentialScope) -> TaskClient:
        return type(self)(
            self.target,
            scope=scope,
            objects=self.objects,
            reports=self.reports,
            expression_verdicts=self.expression_verdicts,
            calls=self.calls,
            responses=self.responses,
            tasks=self.tasks,
            location=self.location,
            job_id=self.job_id,
        )

    def call(self, logical_name: str, **kwargs: Any) -> FakeResponse:
        response = super().call(logical_name, **kwargs)
        if logical_name == "metadata.import" and self.location is not None:
            payload = {"status": "OK", "message": "Initiated METADATA_IMPORT (1)"}
            if self.job_id:
                payload["response"] = {"id": self.job_id, "jobStatus": "RUNNING"}
            return FakeResponse(200, payload, {"Location": self.location})
        if logical_name == "system.tasks.one":
            payload = self.tasks.pop(0) if self.tasks else [log_entry(completed=True, message=TERMINAL_MESSAGE)]
            return FakeResponse(200, payload)
        return response


class Cancelled(Exception):
    """取消信号的载体：轮询必须在每一次读之前响应它。"""


class _Cancel:
    def __init__(self, *, after: int = 0) -> None:
        self.after = after
        self.seen = 0

    def raise_if_cancelled(self) -> None:
        self.seen += 1
        if self.seen > self.after:
            raise Cancelled()


def _client(**changes: Any) -> TaskClient:
    return TaskClient(make_target(), **changes)


def _submission(**changes: Any) -> jobs.Submission:
    base: dict[str, Any] = {"poll_url": "system/tasks/dataImport/task9", "job_ref": "task9"}
    base.update(changes)
    return jobs.Submission(**base)


def _new_execution() -> str:
    with unit_of_work() as session:
        return steps.create_execution(
            session,
            environment_key="env-unit",
            project_id=PROJECT,
            mode=ExecutionMode.COMMIT,
            operator="ops-1",
        ).id


# --- 提交 ------------------------------------------------------------------


def test_sync_submit_returns_the_response_and_claims_no_task() -> None:
    """DX-EXE-033：低于阈值时同步提交，结论就在响应里，没有任务可问。"""
    client = _client(location=None)

    submission = jobs.submit(
        client, resource="metadata.import", body={}, params={"importMode": "COMMIT"}, use_async=False
    )

    assert submission.async_import is False
    assert submission.immediate is True
    assert submission.response is not None and submission.response.status == 200
    assert submission.job_ref is None and submission.poll_url is None


def test_async_submit_adds_the_async_parameter_the_client_must_send() -> None:
    """DX-EXE-033：异步是 ``async=true`` 这个查询参数，不是换一个端点。"""
    client = _client()

    jobs.submit(
        client, resource="metadata.import", body={}, params={"importMode": "COMMIT"}, use_async=True
    )

    submit_call = client.calls[0]
    assert submit_call.params["async"] == "true"
    assert submit_call.scope is CredentialScope.IMPORT


def test_async_submit_persists_task_reference_before_any_polling(harness: Any) -> None:
    """DX-AC-03 / DX-EXE-033：任务引用与查询地址在轮询**之前**就必须已经落库。

    这条断言是「崩溃后能恢复」的全部依据：如果轮询才开始写库，进程在两次轮询之间被杀掉
    就再也问不到那个任务，只能重放一次不可逆的提交。
    """
    del harness
    execution_id = _new_execution()
    client = _client()

    submission = jobs.submit(
        client,
        resource="metadata.import",
        body={},
        params={"importMode": "COMMIT"},
        use_async=True,
        execution_id=execution_id,
    )

    assert submission.async_import is True
    assert [item for item in client.calls if item.logical_name == "system.tasks.one"] == [], (
        "任务引用还没落库就已经开始轮询了"
    )
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        assert row.async_job_ref == submission.job_ref == "task9"
        assert row.poll_url == submission.poll_url


def test_the_task_id_is_taken_from_the_body_and_the_url_is_the_type_collection_plus_id() -> None:
    """DX-EXE-034：2.41 的 ``Location`` 只到任务**类型**集合，定位这一次导入要靠响应体里的 ID。

    只存 Location 会去问“这个类型的所有任务”，问出来的不是本次提交的结果——因此这里必须
    把实例给的两半拼起来，且拼接不改动实例给的地址前缀。
    """
    client = _client()

    submission = jobs.submit(
        client, resource="metadata.import", body={}, params={"importMode": "COMMIT"}, use_async=True
    )

    assert submission.job_ref == "task9"
    assert submission.poll_url == f"{TASK_TYPE_URL}/task9"


def test_an_already_addressable_location_is_kept_verbatim() -> None:
    """DX-EXE-034：实例如果直接给了带 ID 的地址，就原样保存，不再拼第二次。"""
    client = _client(location=f"{TASK_TYPE_URL}/task9")

    submission = jobs.submit(
        client, resource="metadata.import", body={}, params={"importMode": "COMMIT"}, use_async=True
    )

    assert submission.poll_url == f"{TASK_TYPE_URL}/task9"


def test_a_location_without_any_task_id_is_still_kept_as_the_reference() -> None:
    """实例没给任务 ID 时退回 ``Location`` 原值：实例自己给的一个引用，比猜一个强。"""
    client = _client(job_id=None)

    submission = jobs.submit(
        client, resource="metadata.import", body={}, params={"importMode": "COMMIT"}, use_async=True
    )

    assert submission.job_ref == TASK_TYPE_URL
    assert submission.poll_url == TASK_TYPE_URL


def test_an_instance_that_gives_neither_a_location_nor_a_task_id_leaves_nothing_to_poll() -> None:
    """DX-EXE-035：两半都没有就是不给引用，这里造不出一个“大概能问到”的地址。"""
    client = TaskClient(make_target(), location=None, job_id=None)

    submission = jobs.submit(
        client, resource="metadata.import", body={}, params={"importMode": "COMMIT"}, use_async=True
    )

    assert submission.job_ref is None and submission.poll_url is None
    with pytest.raises(Dhis2TaskLostError):
        jobs.poll(client, submission, timeout_seconds=30.0)


def test_async_submit_without_an_execution_still_carries_the_reference() -> None:
    """VALIDATE 也走异步，但它没有执行行可写：引用仍然原样带回调用方。"""
    client = _client()

    submission = jobs.submit(
        client, resource="metadata.import", body={}, params={"importMode": "VALIDATE"}, use_async=True
    )

    assert submission.poll_url == f"{TASK_TYPE_URL}/task9"
    polls = [item for item in client.calls if item.logical_name == "system.tasks.one"]
    assert polls == []


# --- 恢复 ------------------------------------------------------------------


def test_resume_rebuilds_the_submission_from_the_stored_row(harness: Any) -> None:
    """DX-EXE-034：恢复读的是已持久化的引用，而不是重新提交一次。"""
    del harness
    execution_id = _new_execution()
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        steps.remember_job(session, row, job_ref="task42", poll_url=f"{TASK_TYPE_URL}/task42")

    with unit_of_work() as session:
        execution = steps.require(session, execution_id)
        submission = jobs.resume(_client(), execution)

    assert submission is not None
    assert (submission.job_ref, submission.poll_url) == ("task42", f"{TASK_TYPE_URL}/task42")
    assert submission.async_import is True


def test_resume_answers_with_the_stored_url_and_never_resubmits(harness: Any) -> None:
    """DX-EXE-034 / NFR-REL-003：恢复只问那一个任务，一次提交都不再发出。"""
    del harness
    execution_id = _new_execution()
    with unit_of_work() as session:
        row = steps.require(session, execution_id)
        steps.remember_job(session, row, job_ref="task42", poll_url=f"{TASK_TYPE_URL}/task42")

    client = _client(tasks=[[log_entry(completed=True, message=TERMINAL_MESSAGE)]])
    with unit_of_work() as session:
        submission = jobs.resume(client, steps.require(session, execution_id))
    assert submission is not None
    response = jobs.poll(client, submission, timeout_seconds=30.0)

    assert response.payload[0]["completed"] is True
    assert [item.logical_name for item in client.calls] == ["system.tasks.one"]


def test_resume_without_a_persisted_task_says_there_is_nothing_to_resume(harness: Any) -> None:
    """没有任务引用可恢复时返回 ``None``：同步提交的恢复不是瞎轮询。"""
    del harness
    execution_id = _new_execution()
    with unit_of_work() as session:
        execution = steps.require(session, execution_id)

    assert jobs.resume(_client(), execution) is None


# --- 轮询 ------------------------------------------------------------------


def test_poll_stops_at_the_first_terminal_log_entry() -> None:
    """DX-EXE-033：2.41 的异步任务查询回的是日志条目数组，``completed=true`` 就是终态。"""
    client = _client(tasks=[[log_entry(completed=True, message=TERMINAL_MESSAGE)]])

    response = jobs.poll(client, _submission(poll_url=f"{TASK_TYPE_URL}/task9"), timeout_seconds=30.0)

    assert response.payload[0]["completed"] is True
    assert [item.logical_name for item in client.calls] == ["system.tasks.one"]


def test_poll_waits_with_a_backoff_between_reads() -> None:
    """DX-EXE-033：任务没跑完时按退避节奏重问，而不是把实例打成一个忙循环。"""
    delays: list[float] = []
    deps.set_sleeper(delays.append)
    client = _client(tasks=[[log_entry()], [log_entry()], [log_entry(completed=True, message=TERMINAL_MESSAGE)]])

    response = jobs.poll(client, _submission(), timeout_seconds=30.0)

    assert response.payload[0]["completed"] is True
    assert delays == [1.0, 2.0], "轮询间隔必须按设置起步并指数退避"


def test_poll_cancels_before_every_read() -> None:
    """取消在每一次读之前检查：一次长轮询不能变成取消不了的阻塞。"""
    deps.set_sleeper(lambda _seconds: None)
    client = _client(tasks=[[log_entry()], [log_entry()]])
    cancel = _Cancel(after=1)

    with pytest.raises(Cancelled):
        jobs.poll(client, _submission(), cancel=cancel, timeout_seconds=30.0)

    assert cancel.seen == 2
    assert len([item for item in client.calls if item.logical_name == "system.tasks.one"]) == 1


def test_poll_reports_lost_when_the_task_never_finishes() -> None:
    """DX-EXE-035：到点没有终态就是「无法确认」，不是「失败」也不是「未生效」。"""
    deps.set_sleeper(lambda _seconds: None)
    client = _client(tasks=[[log_entry()], [log_entry()]])

    with pytest.raises(Dhis2TaskLostError) as excinfo:
        jobs.poll(client, _submission(job_ref="task9"), timeout_seconds=0.001)

    details = excinfo.value.details
    assert details["jobRef"] == "task9"
    assert details["polls"] == 1
    assert "unconfirmed" in str(excinfo.value)


def test_a_submission_without_any_task_reference_is_lost_immediately() -> None:
    """DX-EXE-035：实例接了异步却没给任务地址——问都没地方问，只能承认无法确认。"""
    client = _client()

    with pytest.raises(Dhis2TaskLostError) as excinfo:
        jobs.poll(client, jobs.Submission(async_import=True), timeout_seconds=30.0)

    assert excinfo.value.details["logicalResource"] == "system.tasks"
    assert client.calls == []


def test_absolute_task_url_is_polled_as_the_instance_gave_it() -> None:
    """DX-EXE-034：绝对 URL 只剥掉环境根，不在这里重新推导路径。"""
    client = _client(tasks=[[log_entry(completed=True, message=TERMINAL_MESSAGE)]])

    jobs.poll(client, _submission(poll_url=f"{TASK_TYPE_URL}/task9"), timeout_seconds=30.0)

    call = client.calls[0]
    assert call.logical_name == "system.tasks.one"
    assert call.path_args == {"taskPath": "system/tasks/METADATA_IMPORT/task9"}
    assert call.scope is CredentialScope.IMPORT


def test_a_task_url_pointing_outside_the_environment_is_refused() -> None:
    """DX-BR-02：任务地址必须落在绑定的环境里，否则就是跨环境的读写。"""
    client = _client()

    with pytest.raises(Dhis2TaskLostError) as excinfo:
        jobs.poll(
            client,
            _submission(poll_url="https://other.example.org/api/system/tasks/dataImport/task9"),
            timeout_seconds=30.0,
        )

    assert excinfo.value.details["rule"] == "DX-BR-02"
    assert excinfo.value.details["expectedRoot"] == ROOT


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("system/tasks/dataImport/task9", "system/tasks/dataImport/task9"),
        ("/api/system/tasks/dataImport/task9", "system/tasks/dataImport/task9"),
        (f"{ROOT}/system/tasks/dataImport/task9", "system/tasks/dataImport/task9"),
        ("/api/system/tasks/dataImport/task9?page=2", "system/tasks/dataImport/task9"),
        ("task9", "task9"),
    ],
)
def test_task_path_is_normalised_to_the_catalog_slot(value: str, expected: str) -> None:
    """DX-EXE-034：``{taskPath}`` 是目录里的槽位，实例给的形态五花八门都要归一。"""
    assert jobs._task_path_of(value, ROOT) == expected


def test_a_bare_job_reference_still_addresses_the_task() -> None:
    """只给一个任务 ID 时不猜路径：按实例给的原样进槽位。"""
    client = _client(tasks=[[log_entry(completed=True, message=TERMINAL_MESSAGE)]])

    jobs.poll(client, jobs.Submission(job_ref="task9", async_import=True), timeout_seconds=30.0)

    assert client.calls[0].path_args == {"taskPath": "task9"}


@pytest.mark.parametrize(
    ("payload", "terminal"),
    [
        ({"status": "COMPLETED"}, True),
        ({"jobStatus": "error"}, True),
        ({"state": "FINISHED"}, True),
        ({"status": "RUNNING"}, False),
        ({"systemJobs": [{"status": "FAILED"}]}, True),
        ({"systemJobs": []}, False),
        ({"response": {"status": "ERROR"}}, True),
        ([log_entry(completed=True)], True),
        ([log_entry()], False),
        ([], False),
        ([{"message": "no completion flag"}], False),
        ("not a document", False),
        (None, False),
    ],
)
def test_terminal_status_vocabulary(payload: Any, terminal: bool) -> None:
    """DX-EXE-033：终态判定只看实例给的状态词汇，认不出来就继续等。"""
    assert jobs._is_terminal(payload) is terminal


# --- ImportReport 的取出 ----------------------------------------------------


def test_report_of_unwraps_the_task_document() -> None:
    """DX-EXE-033：报告包在 ``response`` 里时取包内的 ImportReport。"""
    inner = {"status": "SUCCESS", "stats": {"created": 1}}
    response = FakeResponse(200, {"id": "task9", "status": "COMPLETED", "response": inner})

    assert jobs.report_of(response) == inner


@pytest.mark.parametrize("key", ["response", "importReport", "result"])
def test_report_of_accepts_the_shapes_versions_actually_return(key: str) -> None:
    """DX-EXE-033：不同版本把报告放在不同键下，这里按实例实际给的形态取。"""
    inner = {"status": "SUCCESS", "stats": {"created": 2}}
    response = FakeResponse(200, {"id": "task9", key: inner})

    assert jobs.report_of(response) == inner


def test_report_of_falls_back_to_the_payload_itself() -> None:
    """实例直接回一份裸 ImportReport 时，不要在包装这一步丢掉结论。"""
    response = FakeResponse(200, {"status": "SUCCESS", "stats": {"created": 1}})

    assert jobs.report_of(response)["status"] == "SUCCESS"


def test_report_of_without_any_document_is_a_failed_task() -> None:
    """任务连报告都没有：这是「任务失败」而不是「报告为空」，两者对调用方含义不同。"""
    with pytest.raises(Dhis2TaskFailedError):
        jobs.report_of(FakeResponse(200, "text/plain"))


def test_report_of_synthesises_a_report_from_the_task_log_and_marks_it() -> None:
    """DX-EXE-033：2.41 的异步任务只给日志条目，报告按那条终态日志合成并打上标记。

    ``reportSource=TASK_LOG`` 是要害：下游看得见这份报告没有逐对象明细，不会把它当成
    同步 ImportReport 用（实例对异步任务不提供 FULL 报告，这是实测结论）。
    """
    entries = [
        log_entry(completed=True, message=TERMINAL_MESSAGE),
        log_entry(message="importing dataElements"),
        "a stray non dict entry",
    ]

    document = jobs.report_of(FakeResponse(200, entries))

    assert document["reportSource"] == "TASK_LOG"
    assert document["status"] == "SUCCESS"
    assert document["created"] == 2 and document["updated"] == 1
    assert document["ignored"] == 3 and document["deleted"] == 0
    assert document["total"] == 6
    assert [item["message"] for item in document["detailedMessages"]] == [
        TERMINAL_MESSAGE,
        "importing dataElements",
    ]


@pytest.mark.parametrize(
    ("message", "level", "status"),
    [
        ("Import complete with status ERROR, 0 created, 0 updated, 0 deleted, 1 ignored", "INFO", "ERROR"),
        ("Import complete with status WARNING, 1 created, 0 updated, 0 deleted, 0 ignored", "INFO", "ERROR"),
        ("Import complete with status OK, 1 created, 0 updated, 0 deleted, 0 ignored", "INFO", "SUCCESS"),
        ("something else entirely", "ERROR", "ERROR"),
        ("something else entirely", "WARN", "ERROR"),
        ("something else entirely", "INFO", "SUCCESS"),
    ],
)
def test_the_synthesised_status_follows_the_instance_not_the_wish(
    message: str, level: str, status: str
) -> None:
    """DX-EXE-033：终态条目的消息文本与级别一起决定合成结论，不看它是不是“跑完了”。

    实例说的不是 ``OK`` 就是不是 OK（``WARNING`` 也算）：异步任务的合成结论取保守方向，
    不由 S4 替实例解释成成功。
    """
    document = jobs.report_of(FakeResponse(200, [log_entry(completed=True, message=message, level=level)]))

    assert document["status"] == status


def test_the_counts_of_an_unparsable_message_are_zero_not_invented() -> None:
    """消息不是已知形状时计数给 0：合成报告可以有摘要，不能有编造的统计。"""
    document = jobs.report_of(FakeResponse(200, [log_entry(completed=True, message="Import complete")]))

    assert document["message"] == "Import complete"
    assert [document[key] for key in ("created", "updated", "deleted", "ignored", "total")] == [0] * 5


def test_terminal_entry_of_a_log_without_completion_is_none() -> None:
    """没有 ``completed=true`` 条目时不猜哪一条是结论。"""
    assert jobs._terminal_entry([log_entry()]) is None
    assert jobs._terminal_entry([{"message": "x"}, log_entry(completed=True)]) == log_entry(completed=True)


def test_submission_immediate_only_when_sync_with_a_response() -> None:
    """``immediate`` 是「结论已经在手上」的判定：异步或空响应都不算。"""
    assert jobs.Submission(response=FakeResponse(200, {})).immediate is True
    assert jobs.Submission(response=FakeResponse(200, {}), async_import=True).immediate is False
    assert jobs.Submission().immediate is False


def test_poll_reads_the_task_with_the_import_scope() -> None:
    """任务属于那次导入：查询用 IMPORT 范围，不换成观察用的 discovery 账户。"""
    client = _client(tasks=[[log_entry(completed=True, message=TERMINAL_MESSAGE)]])

    jobs.poll(client, _submission(), timeout_seconds=30.0)

    recorded: Call = client.calls[0]
    assert recorded.scope is CredentialScope.IMPORT
    assert recorded.path_args == {"taskPath": "system/tasks/dataImport/task9"}
