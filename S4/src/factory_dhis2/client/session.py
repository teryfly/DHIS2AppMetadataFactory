"""DHIS2 Client（DX-CLI-001…007、FR-IF-006、总体 SRS 19.2.2）。

**全部** DHIS2 请求都从这里发出，认证、超时、重试、脱敏与 correlationId 只在这一处处理。
接缝在 :mod:`httpx` 的 transport：真实网络、内存假实例与在线契约测试都走同一个 Client、
同一份逻辑资源目录、同一套错误映射，因此执行管线不需要为“这是真实例还是假的”分支。

凭证只在构造请求头的瞬间存在（DX-CLI-002）；异常与日志里的认证头一律脱敏
（DX-CLI-004）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from factory_foundation.api import CancelledError, get_logger, get_operation_context

from factory_dhis2 import deps
from factory_dhis2.client import redaction, tls, tracing
from factory_dhis2.client import retry as retry_module
from factory_dhis2.client.auth import Credentials
from factory_dhis2.client.resources import (
    PAGING_PAGER,
    PAGING_TOKEN,
    ResolvedRequest,
    resolve,
)
from factory_dhis2.config import Dhis2Settings, get_settings
from factory_dhis2.enums import CredentialScope
from factory_dhis2.errors import (
    Dhis2AuthError,
    Dhis2ImportError,
    Dhis2NotFoundError,
    Dhis2PermissionError,
    Dhis2ResponseInvalidError,
    Dhis2TimeoutError,
    Dhis2TlsError,
    Dhis2UnreachableError,
)
from factory_dhis2.target import EnvironmentTarget

LOG = get_logger("factory_dhis2.client")

CORRELATION_HEADER = "X-Correlation-Id"
#: 默认不记录请求与响应正文（DX-CLI-004）；调试级别记录时同样先脱敏
MAX_LOG_BODY_CHARS = 2000


@dataclass
class Dhis2Response:
    status: int
    payload: Any
    headers: Mapping[str, str] = field(default_factory=dict)
    raw: bytes = b""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def job_ref(self) -> str | None:
        """异步任务的引用：优先用实例返回的 Location 原样保存（DX-EXE-034）。"""
        location = self.header("location")
        if location:
            return location.strip()
        if isinstance(self.payload, dict):
            for key in ("jobId", "id", "jobId "):
                value = self.payload.get(key)
                if isinstance(value, str) and value:
                    return value
        return None


class Dhis2Client:
    """一个环境一个 Client：环境键在构造时就固定（DX-CLI-002“每个请求绑定唯一环境”）。"""

    def __init__(
        self,
        target: EnvironmentTarget,
        *,
        scope: CredentialScope = CredentialScope.DISCOVERY,
        settings: Dhis2Settings | None = None,
    ) -> None:
        self.target = target
        self.default_scope = scope
        self.settings = settings or get_settings()
        self._policy = retry_module.RetryPolicy.from_settings(self.settings)

    # --- 对外 ---------------------------------------------------------------

    def with_scope(self, scope: CredentialScope) -> Dhis2Client:
        """同一环境、换一个凭证范围（读用 discovery，导入用 import）。"""
        return Dhis2Client(self.target, scope=scope, settings=self.settings)

    def url_for(self, request: ResolvedRequest) -> str:
        return f"{self.target.root}/{request.path}.json"

    def call(
        self,
        logical_name: str,
        *,
        scope: CredentialScope | None = None,
        dhis2_type: str | None = None,
        path_args: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        body: Any | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Dhis2Response:
        """按逻辑资源名调用（DX-CLI-005）。"""
        request = resolve(
            self.target.profile,
            logical_name,
            dhis2_type=dhis2_type,
            path_args=dict(path_args or {}),
            params=dict(params or {}),
        )
        resolved_scope = scope or self.default_scope
        return self._send(request, resolved_scope, body=body, extra_headers=extra_headers)

    def call_json(self, logical_name: str, **kwargs: Any) -> Any:
        return self.call(logical_name, **kwargs).payload

    def list_all(
        self,
        dhis2_type: str,
        *,
        fields: str,
        filters: Iterable[str] = (),
        page_size: int | None = None,
        limit: int | None = None,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        """按 pager 分页读完某类型的所有对象（DX-NFR-02 靠分页 + 只取必要字段达成）。"""
        resolved_page_size = page_size or self.settings.discovery_page_size
        page = 1
        seen = 0
        collected_filters = [item for item in filters if item]
        collection = self._collection_name(dhis2_type)
        while True:
            if cancel is not None:
                cancel.raise_if_cancelled()
            params: dict[str, str | list[str]] = {
                "fields": fields,
                "page": str(page),
                "pageSize": str(resolved_page_size),
                "totalPages": "true",
            }
            if collected_filters:
                # DHIS2 的多个条件是**重复的 filter 参数**（AND）。把它们逗号拼成一个值，
                # 实例会把整串当成一个过滤器的取值，条件组合在真实例上静默失效。
                params["filter"] = (
                    collected_filters[0]
                    if len(collected_filters) == 1
                    else collected_filters
                )
            response = self.call("metadata.read", dhis2_type=dhis2_type, params=params)
            payload = response.payload if isinstance(response.payload, dict) else {}
            rows = payload.get(collection)
            if rows is None:
                rows = payload.get(dhis2_type, [])
            if not isinstance(rows, list):
                raise Dhis2ResponseInvalidError(
                    f"read of {dhis2_type} returned no collection",
                    details={"dhis2Type": dhis2_type, "keys": sorted(payload)[:12]},
                )
            for row in rows:
                if isinstance(row, dict):
                    yield row
                    seen += 1
                    if limit is not None and seen >= limit:
                        return
            raw_pager = payload.get("pager")
            pager: dict[str, Any] = raw_pager if isinstance(raw_pager, dict) else {}
            page_count = int(pager.get("pageCount") or 0)
            if not rows or page >= page_count:
                return
            page += 1
            if progress is not None:
                progress.report(step=f"discover.{dhis2_type}", message=f"read {seen}")

    def iter_paged_tracker(
        self,
        logical_name: str,
        *,
        params: Mapping[str, str] | None = None,
        collection: str,
        cancel: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        """新 Tracker API 的分页读取（token 分页，DX-CAP-004）。"""
        cursor: str | None = None
        while True:
            if cancel is not None:
                cancel.raise_if_cancelled()
            query = dict(params or {})
            if cursor:
                query["pageToken"] = cursor
            response = self.call(logical_name, params=query)
            payload = response.payload if isinstance(response.payload, dict) else {}
            rows = payload.get(collection) or []
            for row in rows:
                if isinstance(row, dict):
                    yield row
            cursor = payload.get("nextPageToken") or payload.get("total") and None
            if not rows or cursor is None:
                return

    # --- 内部 ---------------------------------------------------------------

    def _collection_name(self, dhis2_type: str) -> str:
        try:
            entry = self.target.profile.type_resource(dhis2_type)
        except Exception:  # noqa: BLE001 - 类型不在目录里时用 DHIS2 的常见复数形式
            return f"{dhis2_type}s"
        return entry.path

    def _send(
        self,
        request: ResolvedRequest,
        scope: CredentialScope,
        *,
        body: Any | None,
        extra_headers: Mapping[str, str] | None,
    ) -> Dhis2Response:
        credentials = self.target.credentials.resolve(self.target.key, scope)
        with tracing.dhis2_span(
            environment_key=self.target.key,
            logical_resource=request.logical_name,
            method=request.method,
        ) as span_record:
            attempts = 0

            def send() -> Dhis2Response | _TransportFailure:
                nonlocal attempts
                attempts += 1
                span_record["attempts"] = attempts
                return self._once(request, credentials, body=body, extra_headers=extra_headers)

            def is_failure(result: object) -> tuple[bool, int | None]:
                if isinstance(result, _TransportFailure):
                    return result.retryable, None
                if isinstance(result, Dhis2Response):
                    span_record["status"] = result.status
                    return result.status in self._policy.status_codes, result.status
                return False, None

            sleeper = deps.get_sleeper()
            result = retry_module.run_with_retry(
                send,
                request=request,
                policy=self._policy,
                sleeper=sleeper,
                is_failure=is_failure,
                on_retry=lambda attempt, status, delay: LOG.warning(
                    "retrying a read-only DHIS2 request",
                    # S0 的 formatter 只把 ``record.extra_fields`` 这个字典并进 JSON，并且
                    # 对它再做一遍脱敏（FD-COM-007）。直接把字段平铺进 ``extra=`` 会设置到
                    # LogRecord 的属性上而**不会出现在日志行里**——结构化日志静默丢失。
                    extra={
                        "extra_fields": {
                            "environmentKey": self.target.key,
                            "logicalResource": request.logical_name,
                            "attempt": attempt,
                            "status": status,
                            "delaySeconds": delay,
                        }
                    },
                ),
            )
        if isinstance(result, _TransportFailure):
            raise result.error
        if not isinstance(result, Dhis2Response):  # pragma: no cover - 只在重试层返回类型被改坏时触发
            raise Dhis2ResponseInvalidError(
                "the transport produced an unexpected result object",
                details={"environmentKey": self.target.key, "type": type(result).__name__},
            )
        if not result.ok:
            _raise_for_status(request, result, self.target.key)
        return result

    def _verify(self) -> Any:
        """TLS 校验配置（DX-CLI-006）。

        部署方可以注入自己的适配器（``deps.set_tls_adapter_factory``）——mTLS 客户端证书、
        企业 PKI 都在这一层；没注入时按环境档案：要求 TLS 就构造一个**强制校验**的
        ``SSLContext``，证书无效时握手失败、请求随之失败。
        """
        factory = deps.get_tls_adapter_factory()
        if factory is not None:
            return factory(self.target)
        return tls.verification_for(
            self.target.base_url,
            tls_required=self.target.tls_required,
            ca_bundle=self.settings.tls_ca_bundle,
        )

    def _log_exchange(
        self,
        request: ResolvedRequest,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Any | None,
        response: Dhis2Response | None = None,
    ) -> None:
        """调试级别的正文记录（DX-CLI-004）：默认关闭，开着也先脱敏再截断。

        三层都在：``redact_headers`` 把认证头压成机制名，``redact_structure`` 递归处理正文
        里的 ``password``/``token`` 这类键与本进程解析过的凭证值，S0 的 formatter 最后再压
        一遍。正文可能含个人健康数据，所以这个开关默认关。
        """
        if not self.settings.log_request_bodies:
            return
        fields: dict[str, Any] = {
            "environmentKey": self.target.key,
            "logicalResource": request.logical_name,
            "method": request.method,
            "url": url,
            "headers": redaction.redact_headers(headers),
            "params": redaction.redact_structure(dict(request.params)),
        }
        if body is not None:
            fields["requestBody"] = _truncate(redaction.redact_structure(_body_for_log(body)))
        if response is not None:
            fields["status"] = response.status
            fields["responseBody"] = _truncate(
                redaction.redact_structure(
                    response.payload if response.payload is not None else response.raw
                )
            )
        LOG.debug("DHIS2 request exchange", extra={"extra_fields": fields})

    def _once(
        self,
        request: ResolvedRequest,
        credentials: Credentials,
        *,
        body: Any | None,
        extra_headers: Mapping[str, str] | None,
    ) -> Dhis2Response | _TransportFailure:
        url = self.url_for(request)
        headers = {
            "Accept": "application/json",
            **credentials.header(),
            **(extra_headers or {}),
        }
        if body is not None:
            # 在线实测：不带 Content-Type 的 JSON POST 会被实例以 415 拒掉。``httpx`` 只在
            # 用 ``json=`` 传参时自动补这个头，而这里用 ``content=``（为了确定性字节序），
            # 所以必须自己带上。
            headers.setdefault("Content-Type", "application/json")
        context = get_operation_context()
        if context is not None:
            headers[CORRELATION_HEADER] = context.correlation_id
        self._log_exchange(request, url=url, headers=headers, body=body)
        try:
            with httpx.Client(
                transport=deps.get_transport(),
                timeout=httpx.Timeout(self.target.timeout_seconds),
                verify=self._verify(),
            ) as client:
                response = client.request(
                    request.method,
                    url,
                    params=request.params or None,
                    content=_encode_body(body),
                    headers=headers,
                )
        except httpx.TimeoutException:
            LOG.info(
                "DHIS2 request timed out",
                extra={
                    "extra_fields": {
                        "environmentKey": self.target.key,
                        "request": request.describe(),
                    }
                },
            )
            return _TransportFailure(
                Dhis2TimeoutError(
                    f"DHIS2 request timed out after {self.target.timeout_seconds}s: "
                    f"{request.describe()}",
                    details={
                        "environmentKey": self.target.key,
                        "logicalResource": request.logical_name,
                    },
                )
            )
        except httpx.ConnectError as exc:
            message = str(exc)
            if _looks_like_certificate_problem(message):
                return _TransportFailure(
                    Dhis2TlsError(
                        f"TLS validation failed for {self.target.key}: "
                        f"{redaction.redact_text(message)}",
                        details={"environmentKey": self.target.key},
                    ),
                    # 证书问题要立刻失败：这是「环境配错了」，不是「实例现在忙」
                    retryable=False,
                )
            return _TransportFailure(
                Dhis2UnreachableError(
                    f"cannot reach the DHIS2 instance of {self.target.key}: "
                    f"{redaction.redact_text(message)}",
                    details={"environmentKey": self.target.key, "baseUrl": self.target.base_url},
                )
            )
        except httpx.HTTPError as exc:  # pragma: no cover - 其它 httpx 传输类错误
            return _TransportFailure(
                Dhis2UnreachableError(
                    f"transport error talking to {self.target.key}: "
                    f"{redaction.redact_text(str(exc))}",
                    details={"environmentKey": self.target.key},
                )
            )
        payload, raw = _decode(response)
        result = Dhis2Response(
            status=response.status_code,
            payload=payload,
            headers=dict(response.headers),
            raw=raw,
        )
        self._log_exchange(
            request, url=url, headers=headers, body=body, response=result
        )
        return result


class _TransportFailure:
    """一次传输层失败的包装：让重试判断与抛错分开，而异常本身仍带 S4 的错误码。

    ``retryable`` 由失败类型决定：证书无效不是瞬时故障，重试三次只会把同一次握手失败做三
    遍（DX-CLI-003 列举的重试条件是超时、429/5xx 与网络中断，不含 TLS）。
    """

    def __init__(self, error: Any, *, retryable: bool = True) -> None:
        self.error = error
        self.retryable = retryable


def _encode_body(body: Any) -> bytes | None:
    if body is None:
        return None
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    return json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def _body_for_log(body: Any) -> Any:
    """正文进日志前的形态：字节先按 JSON 解开，解不开就只报长度（不打印二进制）。"""
    if isinstance(body, (bytes, bytearray)):
        try:
            return json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return f"<{len(body)} bytes>"
    return body


def _truncate(value: Any) -> str:
    """脱敏之后才截断：先截后脱敏会把 ``***REDACTED***`` 截成 ``***REDAC``，
    那既不脱敏也不可读。"""
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    )
    if len(text) <= MAX_LOG_BODY_CHARS:
        return text
    return f"{text[:MAX_LOG_BODY_CHARS]}…(+{len(text) - MAX_LOG_BODY_CHARS} chars)"


def _decode(response: httpx.Response) -> tuple[Any, bytes]:
    raw = response.content
    if not raw:
        return None, raw
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, json.JSONDecodeError):
        # 实例在容器层直接返回 HTML 错误页（真实见过：路径不存在时 Tomcat 的 404 页），
        # 状态码仍是要害，正文留作诊断
        return {"nonJson": redaction.redact_text(raw[:MAX_LOG_BODY_CHARS].decode("utf-8", "replace"))}, raw


def _looks_like_certificate_problem(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in ("certificate", "ssl", "handshake", "cert")
    )


def _raise_for_status(request: ResolvedRequest, response: Dhis2Response, environment_key: str) -> None:
    """状态码 → S4 错误（总体 SRS 19.2.1）。"""
    detail = {
        "environmentKey": environment_key,
        "logicalResource": request.logical_name,
        "status": response.status,
        "request": request.describe(),
    }
    excerpt = _safe_excerpt(response)
    if excerpt:
        detail["response"] = excerpt
    status = response.status
    if status == 401:
        raise Dhis2AuthError(
            f"authentication failed on {environment_key}: HTTP {status}", details=detail
        )
    if status == 403:
        raise Dhis2PermissionError(
            f"the service account lacks permission on {environment_key}: HTTP {status}",
            details=detail,
        )
    if status == 404:
        raise Dhis2NotFoundError(
            f"not found on {environment_key}: {request.describe()}", details=detail
        )
    if status in (429, 502, 503, 504):
        raise Dhis2UnreachableError(
            f"DHIS2 returned HTTP {status} for {request.describe()}", details=detail
        )
    if 500 <= status < 600:
        raise Dhis2UnreachableError(
            f"DHIS2 server error HTTP {status} for {request.describe()}", details=detail
        )
    if status == 409 and _is_import_report(response.payload):
        # 在线实测 2.41.10：校验不通过的导入是 **HTTP 409 + 完整 ImportReport**（顶层
        # ``status=WARNING``，结论在 ``response.status=ERROR``）。逐对象错误只在这份报告里，
        # 把它 raise 掉就等于把 DX-EXE-027 要求的信息丢掉——调用方需要的是问题清单，不是异常。
        return
    if request.logical_name in {"metadata.import", "metadata.delete", "tracker.import"}:
        raise Dhis2ImportError(
            f"the instance rejected the import request: HTTP {status}", details=detail
        )
    raise Dhis2ResponseInvalidError(
        f"unexpected HTTP {status} from {request.describe()}", details=detail
    )


def _is_import_report(payload: Any) -> bool:
    """响应体是不是一份 ImportReport（决定 409 该不该 raise）。"""
    if not isinstance(payload, dict):
        return False
    if "typeReports" in payload or "objectReports" in payload:
        return True
    inner = payload.get("response")
    if isinstance(inner, dict):
        return inner.get("responseType") == "ImportReport" or any(
            key in inner for key in ("typeReports", "objects", "stats", "importReport")
        )
    return payload.get("responseType") == "ImportReport"


def _safe_excerpt(response: Dhis2Response) -> str:
    payload = response.payload
    if isinstance(payload, dict) and "nonJson" in payload:
        return str(payload["nonJson"])[:500]
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("httpStatusCodeResponse")
        if isinstance(message, str):
            return redaction.redact_text(message)[:500]
    return ""


__all__ = [
    "CORRELATION_HEADER",
    "Dhis2Client",
    "Dhis2Response",
    "PAGING_PAGER",
    "PAGING_TOKEN",
    "CancelledError",
]
