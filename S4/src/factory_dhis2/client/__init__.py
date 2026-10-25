"""DHIS2 Client（DX-CLI）。

全部 DHIS2 请求都必须经由 :class:`factory_dhis2.client.session.Dhis2Client` 发出，
本包之外的模块不应直接使用 ``httpx``（DX-CLI-001、DX-BR-01）。

这里用惰性属性导出而不是直接 ``import``：``client.session`` 依赖 ``factory_dhis2.target``，
而 ``target`` 又依赖 ``client.auth``——包导入时 eager 地拉一遍就会形成循环。惰性 ``__getattr__``
让 ``from factory_dhis2.client import Dhis2Client`` 照常工作，导入顺序由 Python 在首次访问
时解析。
"""

from __future__ import annotations

from typing import Any

__exports = {
    "CredentialProvider": "factory_dhis2.client.auth",
    "Credentials": "factory_dhis2.client.auth",
    "Dhis2Client": "factory_dhis2.client.session",
    "Dhis2Response": "factory_dhis2.client.session",
    "ResolvedRequest": "factory_dhis2.client.resources",
    "resolve": "factory_dhis2.client.resources",
    "RetryPolicy": "factory_dhis2.client.retry",
    "is_retryable": "factory_dhis2.client.retry",
    "should_retry": "factory_dhis2.client.retry",
    "api_root": "factory_dhis2.client.tls",
    "normalize_base_url": "factory_dhis2.client.tls",
    "policy_for": "factory_dhis2.client.tls",
    "clear_verification_cache": "factory_dhis2.client.tls",
    "verification_for": "factory_dhis2.client.tls",
    "redact_headers": "factory_dhis2.client.redaction",
    "redact_structure": "factory_dhis2.client.redaction",
    "redact_text": "factory_dhis2.client.redaction",
}

__all__ = sorted(__exports)


def __getattr__(name: str) -> Any:
    import importlib

    module_name = __exports.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_name), name)

