"""重试策略（DX-CLI-003、总体 SRS 19.2.2）。

**只有读请求与任务状态查询会自动重试**：超时、429、502、503、504 与网络中断时按指数
退避重试，次数上限可配置。导入类写请求禁止自动重试——一次 COMMIT 重发就是重复写入，
失败后由执行管线按 DX-EXE-030/035 处理（锁、幂等、人工核实），而不是让传输层悄悄再试。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from factory_dhis2.client.resources import ResolvedRequest
from factory_dhis2.config import Dhis2Settings, get_settings

RETRYABLE_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})

#: 任务状态查询的逻辑资源（DX-EXE-033/034）——虽然用 GET，但单列出来以便说明理由。
TASK_QUERY_RESOURCES: frozenset[str] = frozenset({"system.tasks", "system.tasks.one"})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_seconds: float
    factor: float
    status_codes: frozenset[int]

    @classmethod
    def from_settings(cls, settings: Dhis2Settings | None = None) -> RetryPolicy:
        resolved = settings or get_settings()
        return cls(
            max_attempts=max(1, resolved.retry_max_attempts),
            base_seconds=resolved.retry_backoff_base_seconds,
            factor=resolved.retry_backoff_factor,
            status_codes=resolved.retryable_status_codes,
        )

    def delays(self) -> Iterator[float]:
        """退避延迟序列：``base, base*factor, base*factor² …``（不含最后一次尝试后）。"""
        delay = self.base_seconds
        for _ in range(self.max_attempts - 1):
            yield delay
            delay *= self.factor


def is_retryable(request: ResolvedRequest) -> bool:
    """写请求永远不自动重试；读请求与任务查询按状态码判断。"""
    return request.method in RETRYABLE_METHODS


def should_retry(request: ResolvedRequest, status_code: int | None, policy: RetryPolicy) -> bool:
    """``status_code is None`` 表示传输层失败（超时、连接中断）。"""
    if not is_retryable(request):
        return False
    if status_code is None:
        return True
    return status_code in policy.status_codes


def run_with_retry(
    send: Callable[[], object],
    *,
    request: ResolvedRequest,
    policy: RetryPolicy,
    sleeper: Callable[[float], None],
    is_failure: Callable[[object], tuple[bool, int | None]],
    on_retry: Callable[[int, int | None, float], None] | None = None,
) -> object:
    """执行 ``send``，按策略重试。

    ``is_failure`` 由 Client 提供：它决定“这次响应算不算需要重试的失败”，并给出状态码
    （传输层失败时返回 ``None``）。这样重试判断与 HTTP 细节各归各位。
    """
    attempts = 0
    delays = list(policy.delays())
    while True:
        attempts += 1
        result = send()
        failed, status = is_failure(result)
        if not failed:
            return result
        if attempts >= policy.max_attempts or not should_retry(request, status, policy):
            return result
        delay = delays[attempts - 1] if attempts - 1 < len(delays) else 0.0
        if on_retry is not None:
            on_retry(attempts, status, delay)
        sleeper(delay)


__all__ = [
    "RETRYABLE_METHODS",
    "TASK_QUERY_RESOURCES",
    "RetryPolicy",
    "is_retryable",
    "run_with_retry",
    "should_retry",
]
