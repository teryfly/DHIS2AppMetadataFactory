"""TLS 与传输安全（DX-CLI-006、总体 SRS 2.8）。

要求 TLS 的环境必须校验证书，证书无效时**请求失败**；不要求时也必须走 HTTPS，明文
HTTP 只在开发环境显式允许（DX-DATA/总体 SRS 2.8“工作进程必须能以 HTTPS 访问目标实例”，
DX-NFR-08）。
"""

from __future__ import annotations

import ssl
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

from factory_dhis2.errors import Dhis2TlsError

HTTPS_SCHEMES: frozenset[str] = frozenset({"https"})
ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "http"})


@dataclass(frozen=True)
class TlsPolicy:
    #: 是否校验证书：``True`` 走 :func:`verification_for` 建的那个强制校验的 SSLContext，
    #  ``False`` 只在环境明确写了 ``tlsRequired=false`` 时才出现（仅跳过校验，不降级到明文）
    verify: bool
    base_url: str


def normalize_base_url(base_url: str) -> str:
    """去掉尾部斜杠；``http://`` 只在环境明确不要求 TLS 时允许。"""
    return base_url.rstrip("/")


def policy_for(base_url: str, *, tls_required: bool) -> TlsPolicy:
    url = normalize_base_url(base_url)
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise Dhis2TlsError(
            f"environment base url must be http(s): {url!r}",
            details={"baseUrl": url, "scheme": parsed.scheme},
        )
    if parsed.scheme == "http" and tls_required:
        raise Dhis2TlsError(
            "this environment requires TLS, but its base url is plain http",
            details={"baseUrl": url, "tlsRequired": True},
        )
    return TlsPolicy(verify=tls_required, base_url=url)


_CONTEXTS: dict[str | None, ssl.SSLContext] = {}
_CONTEXTS_LOCK = threading.Lock()


def verification_for(
    base_url: str, *, tls_required: bool, ca_bundle: str | None = None
) -> bool | ssl.SSLContext:
    """真正交给 ``httpx`` 的 ``verify`` 值（DX-CLI-006）。

    要求 TLS 时返回一个**强制校验**的 :class:`ssl.SSLContext`，而不是简单的 ``True``：
    两者都会让自签名/过期/域名不符的证书在握手阶段失败，但只有前者能让部署方指定企业 CA
    （``ca_bundle``）。不要求 TLS 时返回 ``False``——``policy_for`` 已经保证了这种环境不会
    是 ``https`` 之外的意外组合。

    这里刻意不做「证书无效就降级重试一次」的事：证书问题是配置事实，静默放行才是漏洞。
    """
    policy = policy_for(base_url, tls_required=tls_required)
    if not policy.verify:
        return False
    with _CONTEXTS_LOCK:
        cached = _CONTEXTS.get(ca_bundle)
        if cached is not None:
            return cached
        context = ssl.create_default_context()
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        if ca_bundle:
            context.load_verify_locations(cafile=ca_bundle)
        _CONTEXTS[ca_bundle] = context
        return context


def clear_verification_cache() -> None:
    """丢掉缓存的校验上下文（换 CA bundle 配置或测试收尾时用）。"""
    with _CONTEXTS_LOCK:
        _CONTEXTS.clear()


def api_root(base_url: str, api_prefix: str = "api") -> str:
    """实例根 + API 前缀。

    环境档案里的 baseUrl 可能是实例根（``https://host/instance``）也可能已经带上
    ``/api``；两种写法都归一到同一个 API 根，避免同一个实例被登记成两个不同目标。
    """
    url = normalize_base_url(base_url)
    prefix = f"/{api_prefix.strip('/')}"
    if url.endswith(prefix):
        url = url[: -len(prefix)]
    return f"{url}{prefix}"


__all__ = [
    "TlsPolicy",
    "api_root",
    "clear_verification_cache",
    "normalize_base_url",
    "policy_for",
    "verification_for",
]
