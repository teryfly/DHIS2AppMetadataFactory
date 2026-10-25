"""进程级依赖注入（对齐 ``factory_blueprint.deps`` / ``factory_intelligence.deps``）。

S4 的服务从不直接抓协作者，而是来这里取。这就是“真实 httpx 调用 / 内存假实例 /
在线契约测试”三条链路共用同一套 Client 与管线代码的原因（DX-CLI-001、DX-API-001）：
接缝在 :mod:`httpx` 的 **transport** 层，而不是把 Client 换掉。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import RLock
from typing import Any

from factory_foundation.api import Clock, SystemClock

_LOCK = RLock()

_transport: Any | None = None
_clock: Clock | None = None
_sleeper: Callable[[float], None] | None = None
_tls_adapter_factory: Callable[[Any], Any] | None = None

#: 由 :mod:`factory_dhis2.client.session` 注册：给定环境档案与凭证范围，产出认证头。
#: 放在这里是为了让 transport 与 client 之间不出现循环导入。
_auth_header_builder: Callable[[str, str], dict[str, str]] | None = None


def set_transport(transport: Any | None) -> None:
    """安装一个 ``httpx.BaseTransport``；``None`` 表示走真实网络。"""
    global _transport
    with _LOCK:
        _transport = transport


def get_transport() -> Any | None:
    with _LOCK:
        return _transport


def set_clock(clock: Clock | None) -> None:
    global _clock
    with _LOCK:
        _clock = clock


def get_clock() -> Clock:
    with _LOCK:
        if _clock is not None:
            return _clock
    return SystemClock()


def set_sleeper(sleeper: Callable[[float], None] | None) -> None:
    """退避与轮询的休眠函数。

    可注入是为了让 DX-EXE-033 的轮询与 DX-CLI-003 的指数退避在测试里不需要真的等：
    测试断言**延迟序列**，运行仍然瞬间完成。
    """
    global _sleeper
    with _LOCK:
        _sleeper = sleeper


def get_sleeper() -> Callable[[float], None]:
    with _LOCK:
        if _sleeper is not None:
            return _sleeper
    return time.sleep


def set_tls_adapter_factory(factory: Callable[[Any], Any] | None) -> None:
    """证书校验策略的构造点（DX-CLI-006）。

    给定 :class:`~factory_dhis2.target.EnvironmentTarget`，返回真正交给 ``httpx`` 的
    ``verify`` 值——``ssl.SSLContext``（企业 CA、mTLS 客户端证书）或 ``False``。默认走
    :func:`factory_dhis2.client.tls.verification_for`：要求 TLS 的环境强制校验证书，证书
    无效时握手失败、请求随之失败。
    """
    global _tls_adapter_factory
    with _LOCK:
        _tls_adapter_factory = factory


def get_tls_adapter_factory() -> Callable[[Any], Any] | None:
    with _LOCK:
        return _tls_adapter_factory


def set_auth_header_builder(builder: Callable[[str, str], dict[str, str]] | None) -> None:
    global _auth_header_builder
    with _LOCK:
        _auth_header_builder = builder


def get_auth_header_builder() -> Callable[[str, str], dict[str, str]] | None:
    with _LOCK:
        return _auth_header_builder


def reset_providers() -> None:
    """回到未配置状态；测试之间使用。

    也清掉两份**进程内**缓存：动态脱敏表里记着的凭证值（DX-CLI-002，留着上一个测试的
    令牌既没意义也把敏感值留在内存里），以及 TLS 校验用的 ``SSLContext``（它按 ca_bundle
    缓存，而测试里的 CA 文件路径会被复用——不清就有信任旧证书的风险）。
    """
    global _transport, _clock, _sleeper, _tls_adapter_factory, _auth_header_builder
    with _LOCK:
        _transport = None
        _clock = None
        _sleeper = None
        _tls_adapter_factory = None
        _auth_header_builder = None
    from factory_dhis2.client.redaction import forget_secret_values
    from factory_dhis2.client.tls import clear_verification_cache

    forget_secret_values()
    clear_verification_cache()


__all__ = [
    "get_auth_header_builder",
    "get_clock",
    "get_sleeper",
    "get_tls_adapter_factory",
    "get_transport",
    "reset_providers",
    "set_auth_header_builder",
    "set_clock",
    "set_sleeper",
    "set_tls_adapter_factory",
    "set_transport",
]
