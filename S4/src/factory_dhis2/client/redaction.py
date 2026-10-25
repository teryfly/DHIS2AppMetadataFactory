"""日志与异常中的凭证脱敏（DX-CLI-004、FR-SEC-008）。

两条防线：

1. **静态规则**：``Authorization``/``Cookie`` 头、``token``/``password``/``secret`` 字段，
   以及 ``dhp_pat_`` 这类令牌形态，无论内容是什么都替换。
2. **动态规则**：进程内解析过的凭证值本身登记进来，任何字符串只要含有它就替换。
   第二条是必要的——DHIS2 的错误信息可能把请求参数原样回显，而参数里就带着令牌。

凭证值只存在于进程内存（DX-CLI-002、DX-BR-03），本模块也只在内存里持有它们的副本，
并且永不写盘、永不出产物。
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any

#: 需要整体替换的键名（不区分大小写）
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "apikey",
        "x-api-key",
        "jwts",
    }
)

REDACTED = "***REDACTED***"

#: 令牌形态：DHIS2 个人访问令牌、JWT、基本认证串、常见长随机串
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"dhp_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{12,}=*"),
    re.compile(r"Basic\s+[A-Za-z0-9+/=]{12,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
)

_LOCK = threading.RLock()
#: 本进程解析过的凭证值（DX-CLI-002：只在内存）。按**值本身**做键并限定容量：用 ``id()``
#: 作键会在解释器复用地址时把两个不同令牌混为同一项，而无界增长会让长期进程里每一个
#: 轮换过的令牌都永远留在内存里。
_KNOWN_VALUES: OrderedDict[str, None] = OrderedDict()
_MAX_TRACKED_VALUES = 64


def register_secret_value(value: str) -> None:
    """登记一个解析出来的凭证值，供动态脱敏匹配。"""
    if not value or len(value) < 6:
        # 太短的值参与匹配会把普通文本吃掉
        return
    with _LOCK:
        _KNOWN_VALUES[value] = None
        _KNOWN_VALUES.move_to_end(value)
        while len(_KNOWN_VALUES) > _MAX_TRACKED_VALUES:
            _KNOWN_VALUES.popitem(last=False)


def forget_secret_values() -> None:
    with _LOCK:
        _KNOWN_VALUES.clear()


def redact_text(text: str) -> str:
    """替换令牌形态与已知凭证值。"""
    out = text
    for pattern in _VALUE_PATTERNS:
        out = pattern.sub(REDACTED, out)
    with _LOCK:
        values = list(_KNOWN_VALUES.values())
    for value in values:
        if value and value in out:
            out = out.replace(value, REDACTED)
    return out


def redact_structure(value: Any) -> Any:
    """递归脱敏一个结构（用于记录请求/响应正文的调试日志）。"""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower().replace("_", "-") in SENSITIVE_KEYS:
                out[key] = REDACTED
            else:
                out[key] = redact_structure(item)
        return out
    if isinstance(value, (list, tuple, set, frozenset, Sequence)):
        if isinstance(value, (bytes, bytearray)):
            return f"<{len(value)} bytes>"
        return [redact_structure(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return value


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """认证头永远只留下机制名，不留下值（DX-CLI-004）。"""
    out: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in SENSITIVE_KEYS:
            scheme = value.split(" ", 1)[0] if lowered == "authorization" else ""
            out[key] = f"{scheme} {REDACTED}".strip()
        else:
            out[key] = redact_text(value)
    return out


__all__ = [
    "REDACTED",
    "SENSITIVE_KEYS",
    "forget_secret_values",
    "redact_headers",
    "redact_structure",
    "redact_text",
    "register_secret_value",
]
