"""把内存实例接到 ``httpx.BaseTransport`` 上（DX-API-001、00 文档 6.1）。

这一层让**真实代码路径**在测试里被真的执行：``Dhis2Client`` 拼 URL、装认证头、按 Profile
的 resources 目录解析逻辑名、发请求、解 pager、重试、把非 JSON 响应变成可诊断的错误——
这些都在被测范围内，只有最外面的“网络”被换掉了。

安装方式就是 S4 的接缝本身：

```python
instance = FakeDhis2Instance()
deps.set_transport(FakeDhis2Transport(instance))
```

响应构造刻意贴近真实服务器：``Content-Type: application/json``、非 JSON 时用
``text/html``、异步任务给绝对的 ``Location``。测试断言的是客户端对这些行为的反应。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from factory_dhis2.testing.instance import FakeDhis2Instance

_JSON = "application/json;charset=UTF-8"
_HTML = "text/html;charset=UTF-8"


class FakeDhis2Transport(httpx.BaseTransport):
    """按真实 HTTP 语义伺服一个 :class:`FakeDhis2Instance`。"""

    def __init__(
        self, instance: FakeDhis2Instance | None = None, *, api_prefix: str = "api"
    ) -> None:
        self.instance = instance or FakeDhis2Instance()
        self.api_prefix = api_prefix.strip("/")

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = self._path(request.url)
        query = _query_dict(request.url)
        body = self._body(request)
        status, headers, payload = self.instance.handle(
            request.method, path, query, body, dict(request.headers)
        )
        if isinstance(payload, str):
            content_type = headers.get("Content-Type", _HTML)
            content: bytes = payload.encode("utf-8")
        elif payload is None:
            content_type = headers.get("Content-Type", _HTML)
            content = b""
        else:
            content_type = headers.get("Content-Type", _JSON)
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        out_headers = dict(headers)
        out_headers["Content-Type"] = content_type
        location = out_headers.get("Location")
        if location and "://" not in location:
            # 实测 2.41 的异步任务 Location 是绝对 URL：按请求自身补齐，而不是让 S4 去猜
            out_headers["Location"] = _absolute(request.url, location, self.api_prefix)
        return httpx.Response(status, headers=out_headers, content=content, request=request)

    def _path(self, url: httpx.URL) -> str:
        """取出 ``/api`` 之后的资源路径：部署子路径（``/dhis2``、``/stable-2-41-10``）也要跳过。"""
        raw = url.path.lstrip("/")
        marker = f"{self.api_prefix}/"
        if raw.startswith(marker):
            raw = raw[len(marker) :]
        elif f"/{marker}" in raw:
            raw = raw.split(f"/{marker}", 1)[1]
        elif raw.rstrip("/") == self.api_prefix:
            raw = ""
        if raw.endswith(".json"):
            raw = raw[: -len(".json")]
        return raw

    def _body(self, request: httpx.Request) -> Any:
        if not request.content:
            return None
        try:
            return json.loads(request.content)
        except (ValueError, UnicodeDecodeError):
            return request.content.decode("utf-8", "replace")


def _absolute(url: httpx.URL, location: str, api_prefix: str) -> str:
    """把实例给的相对 Location 补成绝对 URL，**保留部署子路径**。

    真实实例的 Location 是绝对的（含 ``/stable-2-41-10`` 这类子路径），所以这里按当前请求
    自身的前缀补，而不是假定实例挂在根路径上。
    """
    path = location.lstrip("/")
    if api_prefix and not path.startswith(f"{api_prefix}/"):
        path = f"{api_prefix}/{path}"
    marker = f"/{api_prefix}/"
    request_url = str(url)
    if marker in request_url:
        base = request_url.split(marker, 1)[0]
        return f"{base}/{path}"
    netloc = url.netloc.decode("utf-8") if isinstance(url.netloc, bytes) else str(url.netloc)
    return f"{url.scheme}://{netloc}/{path}"


def _query_dict(url: httpx.URL) -> dict[str, Any]:
    """``filter`` 在 2.41 里可以重复出现，因此重复键保留为列表。"""
    collected: dict[str, Any] = {}
    for key, value in url.params.multi_items():
        if key in collected:
            existing = collected[key]
            collected[key] = existing + [value] if isinstance(existing, list) else [existing, value]
        else:
            collected[key] = value
    return collected


__all__ = ["FakeDhis2Transport"]
