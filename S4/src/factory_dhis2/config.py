"""S4 运行参数（DX-CLI-001/003、DX-EXE-033、DX-QRY-001、DX-NFR-01）。

全部数值都有 SRS 出处，且都能经环境变量覆盖（前缀 ``FACTORY_DX_``），因为“默认 60 秒
可按环境配置”这类要求必须能在不改代码的前提下调整。环境级的 ``timeoutSeconds`` 仍然
优先于这里的默认值（DX-ENV-001）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, replace
from threading import RLock
from typing import Any

ENV_PREFIX = "FACTORY_DX_"

#: DX-CLI-003：只有这些状态码与传输类失败可自动重试；导入类写请求永不重试。
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})


@dataclass(frozen=True)
class Dhis2Settings:
    #: DX-NFR-01
    default_timeout_seconds: int = 60
    #: DX-CLI-003：指数退避的次数上限
    retry_max_attempts: int = 3
    retry_backoff_base_seconds: float = 0.5
    retry_backoff_factor: float = 2.0
    retryable_status_codes: frozenset[int] = field(default=RETRYABLE_STATUS_CODES)

    #: DX-QRY-001：官方 UID 生成接口单次上限，超出则分批
    uid_batch_limit: int = 100
    #: DX-QRY-002/003：存在性检查的单批取值数（``id:in:[…]`` / ``code:in:[…]``）。
    #  批大小是 URL 长度与请求次数之间的折中，2.41 实测 50 个 UID 一段稳定可用。
    presence_batch_size: int = 50
    #: DX-EXE-033：载荷对象数超过该阈值改用异步导入（可按环境覆盖）
    async_import_threshold: int = 500
    async_poll_interval_seconds: float = 1.0
    async_poll_timeout_seconds: float = 1800.0

    #: DX-NFR-02：1,000 对象的漂移检查与 Snapshot 各 < 60 秒。达成手段是**分页 + 批量按 UID
    #  读回**（性能冒烟测试 tests/integration/test_performance_smoke.py 实测并锁定）。这里
    #  刻意不声明「并发读取」这类还没实现的参数：写了没人读的旋钮，比没有旋钮更误导人。
    discovery_page_size: int = 200
    drift_batch_size: int = 200

    #: DX-ENV-006：即将到期凭证的默认提前天数
    credential_expiry_warning_days: int = 14

    #: DX-CLI-004：请求与响应正文**默认不记录**。打开后只在 DEBUG 记录，且记录前必经脱敏
    #  （Authorization/Cookie/令牌/密码，以及本进程解析过的凭证值本身）并按长度截断。
    #  留这个开关而不是"debug 就全记"，是因为正文里可能带业务数据（个人姓名、卫生数据），
    #  部署方必须显式选择承担这件事。
    log_request_bodies: bool = False
    #: DX-CLI-006：校验服务端证书用的 CA bundle（企业自签 CA 场景）。``None`` 用系统信任库。
    tls_ca_bundle: str | None = None

    # --- 契约测试（DX-NFR-05）：默认不联网，写类另需显式开关 ----------------
    live_dhis2_base_url: str | None = None
    live_dhis2_username: str | None = None
    live_dhis2_password: str | None = None
    live_allow_commits: bool = False
    live_code_prefix: str = "factory_contract_"

    def with_overrides(self, **changes: Any) -> Dhis2Settings:
        return replace(self, **changes)


_INT_KEYS = (
    "DEFAULT_TIMEOUT_SECONDS",
    "RETRY_MAX_ATTEMPTS",
    "UID_BATCH_LIMIT",
    "ASYNC_IMPORT_THRESHOLD",
    "DISCOVERY_PAGE_SIZE",
    "READ_CONCURRENCY",
    "CREDENTIAL_EXPIRY_WARNING_DAYS",
    "EXPRESSION_CHECK_CONCURRENCY",
)
_FLOAT_KEYS = (
    "RETRY_BACKOFF_BASE_SECONDS",
    "RETRY_BACKOFF_FACTOR",
    "ASYNC_POLL_INTERVAL_SECONDS",
    "ASYNC_POLL_TIMEOUT_SECONDS",
)
_STR_KEYS = (
    "LIVE_DHIS2_BASE_URL",
    "LIVE_DHIS2_USERNAME",
    "LIVE_DHIS2_PASSWORD",
    "TLS_CA_BUNDLE",
)
_BOOL_KEYS = ("LIVE_ALLOW_COMMITS", "LOG_REQUEST_BODIES")

_LOCK = RLock()
_SETTINGS = Dhis2Settings()

#: ``Dhis2Settings`` 的字段名。``load_settings()`` 用它过滤环境里读到的键。
_FIELD_NAMES = frozenset(f.name for f in fields(Dhis2Settings))


def _flag(name: str) -> str | None:
    return os.environ.get(f"{ENV_PREFIX}{name}")


def load_settings() -> Dhis2Settings:
    """从环境读取覆盖值，返回新的设置对象（不改全局）。"""
    settings = Dhis2Settings()
    changes: dict[str, object] = {}
    for key in _INT_KEYS:
        raw = _flag(key)
        if raw is not None:
            changes[key.lower()] = int(raw)
    for key in _FLOAT_KEYS:
        raw = _flag(key)
        if raw is not None:
            changes[key.lower()] = float(raw)
    for key in _STR_KEYS:
        raw = _flag(key)
        if raw is not None:
            changes[key.lower()] = raw
    for key in _BOOL_KEYS:
        raw = _flag(key)
        if raw is not None:
            changes[key.lower()] = raw.strip().lower() in {"1", "true", "yes", "on"}
    # S1 在进程启动时调用 ``initialize()``，而它现在会经 ``configure(None)`` 走到这里：
    # 一个 ``_INT_KEYS`` 里列了、``Dhis2Settings`` 却没有对应字段的环境变量（历史上
    # ``READ_CONCURRENCY`` 与 ``EXPRESSION_CHECK_CONCURRENCY`` 就是这样留下的），过去只是被
    # 忽略，现在会让**整个进程**在启动时抛 TypeError。未知键继续忽略——按名字报错是配置文档
    # 该做的事，不是启动该做的事。
    applied = {name: value for name, value in changes.items() if name in _FIELD_NAMES}
    return settings.with_overrides(**applied) if applied else settings


def get_settings() -> Dhis2Settings:
    with _LOCK:
        return _SETTINGS


def configure(settings: Dhis2Settings | None = None) -> Dhis2Settings:
    """安装一组设置；``None`` 表示从环境重新加载。"""
    global _SETTINGS
    resolved = load_settings() if settings is None else settings
    with _LOCK:
        _SETTINGS = resolved
    return resolved


def reset() -> None:
    global _SETTINGS
    with _LOCK:
        _SETTINGS = Dhis2Settings()


__all__ = [
    "Dhis2Settings",
    "ENV_PREFIX",
    "RETRYABLE_STATUS_CODES",
    "configure",
    "get_settings",
    "load_settings",
    "reset",
]
