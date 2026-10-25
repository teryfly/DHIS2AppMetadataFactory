"""异步导入任务的提交与轮询（DX-EXE-033/034、FR-EXE-008/009、DX-AC-03）。

不可逆的分工：**提交是一次性的，轮询是可重复的**。因此任务引用与查询地址必须在提交
返回后立刻落库——进程在轮询中途崩溃时，恢复只需要拿着已存的地址继续问，而不是再提交
一遍（NFR-REL-003/004、AC-EXE-05）。

2.41.10 实测到的三件事决定了这里的写法（在线契约测试
``tests/contract/test_dhis2_241_live.py`` 把它们钉住）：

1. 异步提交的响应是 ``200``（不是 202），``Location`` 指向的是**任务类型集合**
   ``…/api/system/tasks/METADATA_IMPORT``，任务 ID 在响应体的 ``response.id`` 里；因此
   只存 ``Location`` 无法定位这一次任务，必须把两者合起来存；
2. 按任务 ID 查询返回的是**日志条目数组**，不是 ImportReport：实例对异步任务不提供逐对象
   报告，``importReportMode=FULL`` 也不改变这一点；
3. 终态由一条 ``completed=true`` 的条目给出，结论与计数在那条目的 ``message`` 文本里
   （``Import complete with status OK, 1 created, 0 updated, 0 deleted, 0 ignored``）。

所以 :func:`report_of` 在异步任务上**合成**一份报告：状态与计数取自那条终态日志，全部日志
条目放进 ``detailedMessages``，并打上 ``reportSource=TASK_LOG`` 标记。这个标记很重要——
它告诉下游（以及读产物的人）这份报告没有逐对象明细，不要把它当成同步 ImportReport 用。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from factory_foundation.api import unit_of_work

from factory_dhis2.client.session import Dhis2Client, Dhis2Response
from factory_dhis2.config import get_settings
from factory_dhis2.deps import get_sleeper
from factory_dhis2.enums import CredentialScope
from factory_dhis2.errors import Dhis2TaskFailedError, Dhis2TaskLostError
from factory_dhis2.execution import steps
from factory_dhis2.models.execution import ExecutionRow

#: 任务的终态（由 Profile 的 ``async.terminalStatuses`` 给出，这里是兜底值）
TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETED", "ERROR", "FINISHED", "FAILED"})
#: 2.41 异步任务日志里那条终态消息的形状
_TASK_SUMMARY_RE = re.compile(
    r"Import complete with status (?P<status>[A-Za-z]+),\s*"
    r"(?P<created>\d+) created,\s*(?P<updated>\d+) updated,\s*"
    r"(?P<deleted>\d+) deleted,\s*(?P<ignored>\d+) ignored",
    re.IGNORECASE,
)
#: 日志条目里代表失败的级别
_FAILURE_LEVELS: frozenset[str] = frozenset({"ERROR", "WARN"})


@dataclass
class Submission:
    """一次导入提交的结果。"""

    response: Dhis2Response | None = None
    job_ref: str | None = None
    poll_url: str | None = None
    async_import: bool = False
    polls: int = 0

    @property
    def immediate(self) -> bool:
        return self.response is not None and not self.async_import


def submit(
    client: Dhis2Client,
    *,
    resource: str,
    body: Any,
    params: dict[str, str],
    use_async: bool,
    execution_id: str | None = None,
) -> Submission:
    """提交导入；异步时立即持久化任务引用。

    写请求**不重试**（DX-CLI-003）：Client 的重试判定只看读请求，这里无需额外开关。

    这里只接**执行 ID**而不是 ORM 行：调用点在创建执行的事务之外，把那一行传进来会变成
    一个脱离会话的对象，而落库需要的是同一个 ID。
    """
    call_params = dict(params)
    if use_async:
        call_params["async"] = "true"
    response = client.call(
        resource, scope=CredentialScope.IMPORT, params=call_params, body=body
    )
    if not use_async:
        return Submission(response=response, async_import=False)

    job_ref = _job_reference(response)
    poll_url = _job_poll_url(response, job_ref)
    submission = Submission(job_ref=job_ref, poll_url=poll_url, async_import=True)
    if execution_id is not None:
        # 先落库再轮询：这就是 DX-AC-03 能够成立的全部原因
        with unit_of_work() as session:
            row = steps.require(session, execution_id)
            steps.remember_job(session, row, job_ref=job_ref, poll_url=poll_url)
    return submission


def resume(client: Dhis2Client, execution: ExecutionRow) -> Submission | None:
    """恢复：从已持久化的任务引用继续查询，绝不重新提交（DX-EXE-034）。"""
    if execution.async_job_ref is None and execution.poll_url is None:
        return None
    return Submission(
        job_ref=execution.async_job_ref,
        poll_url=execution.poll_url,
        async_import=True,
    )


def poll(
    client: Dhis2Client,
    submission: Submission,
    *,
    cancel: Any | None = None,
    timeout_seconds: float | None = None,
) -> Dhis2Response:
    """轮询到终态；期间每一步都可在 ``cancel`` 上响应取消。"""
    settings = get_settings()
    deadline = time.monotonic() + (timeout_seconds or settings.async_poll_timeout_seconds)
    sleeper = get_sleeper()
    delay = settings.async_poll_interval_seconds
    while True:
        if cancel is not None:
            cancel.raise_if_cancelled()
        response = _poll_once(client, submission)
        submission.polls += 1
        if _is_terminal(response.payload):
            return response
        if time.monotonic() + delay > deadline:
            raise Dhis2TaskLostError(
                "the asynchronous import task did not reach a terminal state before the "
                "polling deadline; the outcome is unconfirmed (DX-EXE-035)",
                details={
                    "jobRef": submission.job_ref,
                    "polls": submission.polls,
                    "deadlineSeconds": settings.async_poll_timeout_seconds,
                },
            )
        sleeper(delay)
        delay = min(delay * 2, 30.0)


def _poll_once(client: Dhis2Client, submission: Submission) -> Dhis2Response:
    target = submission.poll_url or submission.job_ref
    if target is None:
        raise Dhis2TaskLostError(
            "the import was submitted asynchronously but the instance returned no task "
            "reference; the outcome cannot be confirmed (DX-EXE-035)",
            details={"logicalResource": "system.tasks"},
        )
    if "://" in str(target):
        # 实例给了绝对 URL：按原样请求（不重新推导，见模块说明）
        return _absolute_poll(client, str(target))
    return client.call(
        "system.tasks.one",
        scope=CredentialScope.IMPORT,
        path_args={"taskPath": _task_path_of(str(target), client.target.root)},
    )


def _absolute_poll(client: Dhis2Client, url: str) -> Dhis2Response:

    root = client.target.root
    if not url.startswith(root):
        raise Dhis2TaskLostError(
            f"the task url {url!r} points outside the bound environment",
            details={"url": url, "expectedRoot": root, "rule": "DX-BR-02"},
        )
    tail = url[len(root) :].lstrip("/")
    return client.call(
        "system.tasks.one",
        scope=CredentialScope.IMPORT,
        path_args={"taskPath": tail},
    )


def _task_path_of(value: str, root: str) -> str:
    path = value
    for prefix in (root, "/api", value.split("?")[0] if "?" in value else ""):
        if prefix and path.startswith(prefix):
            path = path[len(prefix) :]
            break
    if "?" in path:
        path = path.split("?", 1)[0]
    return path.strip("/") or value.strip("/")


def _job_reference(response: Dhis2Response) -> str | None:
    """任务 ID：实测 2.41 把它放在响应体的 ``response.id``，而不是 URL 里。

    拿不到 ID 时退回 ``Location`` 原值——那至少是实例自己给的一个引用，比猜测一个强。
    """
    payload = response.payload
    if isinstance(payload, dict):
        inner = payload.get("response")
        if isinstance(inner, dict) and inner.get("id"):
            return str(inner["id"])
        if payload.get("id"):
            return str(payload["id"])
    return response.job_ref()


def _job_poll_url(response: Dhis2Response, job_ref: str | None) -> str | None:
    """这次任务的查询地址。

    ``Location`` 指向任务**类型集合**（``…/system/tasks/METADATA_IMPORT``），所以有任务 ID
    时把它接在后面，得到真正能定位这一次导入的地址。地址前缀本身仍按实例给的原样保留。

    ``job_ref`` 可能就是 ``Location`` 本身（实例没在响应体里给任务 ID 时的退路，见
    :func:`_job_reference`）：那种情况下拼接只会得到 ``…/tasks/X/…/tasks/X`` 这个谁也不指向
    的地址，所以引用即地址时直接用它。
    """
    location = response.header("location") or response.job_ref()
    if location is None or location == job_ref:
        return job_ref
    if job_ref and f"/{job_ref}" not in location.rstrip("/"):
        return f"{location.rstrip('/')}/{job_ref}"
    return location


def _is_terminal(payload: Any) -> bool:
    if isinstance(payload, list):
        # 2.41 的异步任务查询回的是日志条目数组：终态就是一条 completed=true 的条目
        return any(
            isinstance(entry, dict) and entry.get("completed") is True for entry in payload
        )
    if not isinstance(payload, dict):
        return False
    for key in ("status", "jobStatus", "state"):
        value = payload.get(key)
        if isinstance(value, str) and value.upper() in TERMINAL_STATUSES:
            return True
    items = payload.get("systemJobs")
    if isinstance(items, list) and items:
        return _is_terminal(items[0])
    inner = payload.get("response")
    if isinstance(inner, dict):
        return _is_terminal(inner)
    return False


def _terminal_entry(payload: list[Any]) -> dict[str, Any] | None:
    for entry in payload:
        if isinstance(entry, dict) and entry.get("completed") is True:
            return entry
    return None


def report_of(response: Dhis2Response) -> dict[str, Any]:
    """从任务响应里取出 ImportReport 本体。"""
    payload = response.payload
    if isinstance(payload, list):
        return _report_from_task_log(payload)
    if not isinstance(payload, dict):
        raise Dhis2TaskFailedError(
            "the task response carries no import report",
            details={"payloadType": type(payload).__name__},
        )
    # 任务记录把报告放在 response / jobParameters 里，形态随版本不同
    for key in ("response", "importReport", "result"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and candidate:
            return candidate
    return payload


def _report_from_task_log(entries: list[Any]) -> dict[str, Any]:
    """把任务日志合成为一份报告：计数来自终态条目，明细就是这些条目本身。

    实例对异步任务不提供逐对象报告（模块说明第 2 条），所以这里**不假装**有：
    ``reportSource`` 标记与 ``taskLog`` 原文都在产物里，下游能看出自己拿到的是摘要。
    """
    shaped = [entry for entry in entries if isinstance(entry, dict)]
    terminal = _terminal_entry(shaped) or {}
    message = str(terminal.get("message") or "")
    match = _TASK_SUMMARY_RE.search(message)
    level = str(terminal.get("level") or "").upper()
    counts = {
        key: int(match.group(key)) if match else 0
        for key in ("created", "updated", "deleted", "ignored")
    }
    failed = bool(match and match.group("status").upper() != "OK") or level in _FAILURE_LEVELS
    total = sum(counts.values())
    return {
        "status": "ERROR" if failed else "SUCCESS",
        "httpStatus": "200",
        "imported": counts["created"],
        "created": counts["created"],
        "updated": counts["updated"],
        "deleted": counts["deleted"],
        "ignored": counts["ignored"],
        "total": total,
        "type": "METADATA",
        "message": message or None,
        "detailedMessages": shaped,
        "reportSource": "TASK_LOG",
    }


__all__ = [
    "TERMINAL_STATUSES",
    "Submission",
    "poll",
    "report_of",
    "resume",
    "submit",
]
