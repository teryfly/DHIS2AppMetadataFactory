"""DHIS2 Client 的传输行为（DX-CLI-001…007）。

用 ``httpx.MockTransport`` 而不是 S4 自己的假实例：这里要证明的是 Client 自身的规矩
（只有读请求重试、写请求绝不重试、认证头按范围解析、异常与日志脱敏、TLS 判定、指标
单独归类），与假实例的实现无关。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from factory_dhis2 import deps
from factory_dhis2.client import tracing
from factory_dhis2.client.auth import CredentialProvider
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.client.tracing import metrics, reset_metrics
from factory_dhis2.config import Dhis2Settings
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.errors import (
    Dhis2AuthError,
    Dhis2NotFoundError,
    Dhis2PermissionError,
    Dhis2UnreachableError,
)
from factory_dhis2.profiles import registry
from factory_dhis2.target import EnvironmentTarget

SECRET = "dhp_pat_supersecretvalue1"
BASE = "https://dhis.example.org/instance"


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def make_target(
    *,
    auth_mode: AuthMode = AuthMode.PAT,
    tls_required: bool = True,
    refs: dict[str, str] | None = None,
) -> EnvironmentTarget:
    registry.load_builtin_profiles()
    secret_refs = refs or {
        scope.value: f"memory://dx/{scope.value}"
        for scope in (
            CredentialScope.DISCOVERY,
            CredentialScope.IMPORT,
            CredentialScope.TRACKER,
            CredentialScope.DATASTORE,
            CredentialScope.APPS,
            CredentialScope.MAINTENANCE,
        )
    }
    provider = CredentialProvider(
        secret_refs,
        auth_mode=auth_mode,
        resolver=lambda ref: f"{SECRET}" if auth_mode is AuthMode.PAT else "svcacct:district",
    )
    return EnvironmentTarget(
        key="dev",
        name="Dev",
        base_url=BASE,
        environment_class=EnvironmentClass.DEV,
        auth_mode=auth_mode,
        tls_required=tls_required,
        llm_data_policy=LlmDataPolicy.LOCAL_ONLY,
        timeout_seconds=60,
        status=EnvironmentStatus.ACTIVE,
        profile=registry.profile_for_version("2.41.10"),
        dhis2_version="2.41.10",
        credentials=provider,
    )


@pytest.fixture(autouse=True)
def _clean_metrics() -> Iterator[None]:
    reset_metrics()
    deps.reset_providers()
    yield
    deps.reset_providers()
    reset_metrics()


def install(handler: Any, sleeper: RecordingSleeper | None = None) -> RecordingSleeper:
    deps.set_transport(httpx.MockTransport(handler))
    recorder = sleeper or RecordingSleeper()
    deps.set_sleeper(recorder)
    return recorder


def client(scope: CredentialScope = CredentialScope.DISCOVERY) -> Dhis2Client:
    return Dhis2Client(make_target(), scope=scope)


def test_get_reads_are_retried_with_exponential_backoff() -> None:
    """DX-CLI-003：读请求在 429/502/503/504 与超时时重试，退避递增。"""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, json={"message": "too many requests"})
        return httpx.Response(200, json={"version": "2.41.10"})

    sleeper = install(handler)
    response = client().call("system.info")
    assert response.payload == {"version": "2.41.10"}
    assert attempts["n"] == 3
    # 0.5s 起步、因子 2：第二次重试前等 1.0s
    assert sleeper.delays == [0.5, 1.0]


def test_write_requests_are_never_retried() -> None:
    """DX-CLI-003：导入类写请求禁止自动重试，失败交给执行管线判断。"""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, json={"message": "unavailable"})

    install(handler)
    with pytest.raises(Dhis2UnreachableError):
        client(scope=CredentialScope.IMPORT).call(
            "metadata.import",
            scope=CredentialScope.IMPORT,
            params={"importMode": "COMMIT"},
            body={"dataElements": []},
        )
    assert attempts["n"] == 1, "写请求被重试了：一次 COMMIT 重发就是重复写入"


def test_connection_timeout_is_reported_and_retried_for_reads() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectTimeout("connection timed out")
        return httpx.Response(200, json={"ok": True})

    install(handler)
    assert client().call("system.info").payload == {"ok": True}
    assert calls["n"] == 2


def test_authorization_header_uses_the_resolved_pat() -> None:
    """DX-CLI-002：凭证在请求时按范围解析，只存在于内存。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={})

    install(handler)
    client().call("system.info")
    assert seen == [f"Bearer {SECRET}"]


def test_basic_auth_encodes_user_and_password() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={})

    install(handler)
    registry.load_builtin_profiles()
    target = make_target(auth_mode=AuthMode.BASIC)
    Dhis2Client(target).call("system.info")
    assert seen[0].startswith("Basic ")
    import base64

    assert base64.b64decode(seen[0].split()[1]).decode() == "svcacct:district"


def test_a_json_body_alone_carries_a_content_type() -> None:
    """在线实测：不带 ``Content-Type`` 的 JSON POST 被实例以 **415** 拒掉。

    httpx 只在用 ``json=`` 传参时自动补这个头，而 Client 为了让字节序确定用的是
    ``content=``，所以头必须自己带——这一条是契约测试第一次真打 COMMIT 类请求时抓出来的。
    """
    seen: list[tuple[str | None, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers.get("Content-Type"), request.content))
        return httpx.Response(200, json={})

    install(handler)
    client(scope=CredentialScope.IMPORT).call(
        "metadata.import", scope=CredentialScope.IMPORT, body={"dataElements": [{"id": "de1"}]}
    )
    content_type, content = seen[0]
    assert content_type == "application/json", content_type
    assert b'"id": "de1"' in content or b'"id":"de1"' in content, content
    # 读请求没有请求体，就不该带 Content-Type
    client().call("system.info")
    assert seen[1][0] is None, seen[1]


def test_401_and_403_map_to_distinct_errors() -> None:
    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad token"})

    install(unauthorized)
    with pytest.raises(Dhis2AuthError):
        client().call("system.info")

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "not allowed"})

    install(forbidden)
    with pytest.raises(Dhis2PermissionError):
        client().call("system.info")


def test_404_maps_to_not_found() -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"<html>Not Found</html>")

    install(missing)
    from factory_dhis2.execution.reads import path_args

    target = make_target()
    with pytest.raises(Dhis2NotFoundError) as excinfo:
        Dhis2Client(target).call(
            "metadata.read.one",
            path_args=path_args(target.profile, "dataElement", "FTRrcoaog83"),
        )
    assert "dataElements" in str(excinfo.value.details["request"])


def test_error_message_never_contains_the_secret() -> None:
    """DX-CLI-004：异常文本里也不能出现凭证值。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": f"token {SECRET} rejected"})

    install(handler)
    with pytest.raises(Dhis2AuthError) as excinfo:
        client().call("system.info")
    assert SECRET not in str(excinfo.value)
    assert SECRET not in json.dumps(excinfo.value.details, default=str)


def test_urls_are_built_from_the_logical_resource_only() -> None:
    """DX-CLI-005：路径来自 Profile 目录，代码里不写死版本相关路径。"""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(str(request.url))
        return httpx.Response(200, json={"codes": ["aB1"]})

    install(handler)
    client().call("system.id", params={"limit": "3"})
    assert paths == [f"{BASE}/api/system/id.json?limit=3"]


def test_tracker_resource_resolves_under_the_new_tracker_api() -> None:
    """DX-CAP-004 / FR-API-002。"""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={})

    install(handler)
    client(scope=CredentialScope.TRACKER).call(
        "tracker.events.get", params={"program": "wy6kQ2qDnaF"},
        scope=CredentialScope.TRACKER,
    )
    assert paths == ["/instance/api/tracker/events.json"]


def test_span_and_metric_are_recorded_per_call() -> None:
    """DX-CLI-007：环境、逻辑资源、状态码、耗时；指标独立于核心 API。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    install(handler)
    client().call("system.info")
    recorded = metrics.calls
    assert len(recorded) == 1
    assert recorded[0].environment_key == "dev"
    assert recorded[0].logical_resource == "system.info"
    assert recorded[0].status == 200
    assert recorded[0].duration_ms >= 0.0
    assert metrics.by_environment() == {"dev": 1}


def test_failed_call_still_produces_a_metric() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    install(handler)
    with pytest.raises(Dhis2UnreachableError):
        client().call("system.info")
    assert metrics.calls[-1].status == 500 or metrics.calls[-1].status is None


# --- OpenTelemetry 指标（DX-CLI-007 的「外部 collector 看得见」那一半）-----------
#
# 累加器只能证明「我们自己记了一笔」。下面这组用 SDK 的内存 reader 采集，证明数据点真的
# 进了 OTel、维度是 SRS 点名的三项，而且没有混进核心 API 的 meter。没装 ``opentelemetry``
# 时这几个用例跳过：它是可选依赖（``[otel]`` extra）。


def _points(reader: Any) -> dict[str, list[dict[str, Any]]]:
    """``{指标名: [{scope, attributes, count, value}, …]}``，按名字索引才有读法。

    直方图的数据点是 ``(count, sum)``，计数器的是 ``value``——两种都在这里归一成
    ``count``/``value``，断言才不必关心仪器类型。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    data = reader.get_metrics_data()
    for resource in getattr(data, "resource_metrics", None) or []:
        for scope_metrics in resource.scope_metrics:
            scope_name = getattr(getattr(scope_metrics, "scope", None), "name", "")
            for metric in scope_metrics.metrics:
                for point in getattr(metric.data, "data_points", None) or []:
                    count = getattr(point, "count", None)
                    out.setdefault(metric.name, []).append(
                        {
                            "scope": scope_name,
                            "attributes": dict(point.attributes),
                            "count": count,
                            "value": (
                                getattr(point, "sum", None)
                                if count is not None
                                else getattr(point, "value", None)
                            ),
                        }
                    )
    return out


@pytest.fixture
def otel() -> Iterator[tuple[Any, Any]]:
    """(reader, provider)：provider 交给 S4，核心 API 那条 meter 也要挂在同一个上面。"""
    pytest.importorskip("opentelemetry.sdk.metrics", reason="需要 opentelemetry-sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    tracing.configure_meter_provider(provider)
    try:
        yield reader, provider
    finally:
        tracing.configure_meter_provider(None)


def test_a_call_records_a_real_otel_histogram_and_counter(otel: tuple[Any, Any]) -> None:
    reader, _provider = otel
    install(lambda request: httpx.Response(200, json={}))
    client().call("system.info")

    points = _points(reader)
    assert set(points) == {tracing.METRIC_DURATION, tracing.METRIC_CALLS}
    duration = points[tracing.METRIC_DURATION][0]
    assert duration["scope"] == tracing.METER_NAME
    assert duration["count"] == 1
    assert duration["value"] is not None and duration["value"] >= 0.0
    assert points[tracing.METRIC_CALLS][0]["value"] == 1


def test_metric_dimensions_are_exactly_environment_resource_status(
    otel: tuple[Any, Any],
) -> None:
    """维度就是 SRS 点名的三项：环境、逻辑资源、状态码。

    刻意没有 projectId、URL、凭证：前两者是高基数（时间序列会炸），后者根本不该出现在
    观测数据里（DX-CLI-002）。
    """
    reader, _provider = otel
    install(lambda request: httpx.Response(200, json={}))
    client().call("metadata.read", dhis2_type="dataElement", params={"pageSize": "50"})

    point = _points(reader)[tracing.METRIC_DURATION][0]
    assert point["attributes"] == {
        tracing.ATTRIBUTE_ENVIRONMENT: "dev",
        tracing.ATTRIBUTE_RESOURCE: "metadata.read",
        tracing.ATTRIBUTE_STATUS: "200",
    }


def test_a_500_is_visible_in_the_metrics(otel: tuple[Any, Any]) -> None:
    reader, _provider = otel
    install(lambda request: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(Dhis2UnreachableError):
        client().call("system.info")

    point = _points(reader)[tracing.METRIC_DURATION][0]
    assert point["attributes"][tracing.ATTRIBUTE_STATUS] == "500"


def test_retries_do_not_inflate_the_call_counter(otel: tuple[Any, Any]) -> None:
    """三次尝试是**一次**业务调用：尝试次数在 span 属性上，不在指标维度里。"""
    reader, _provider = otel
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, json={"message": "too many requests"})
        return httpx.Response(200, json={})

    install(handler)
    client().call("system.info")

    points = _points(reader)
    assert attempts["n"] == 3
    assert points[tracing.METRIC_DURATION][0]["count"] == 1
    assert points[tracing.METRIC_CALLS][0]["value"] == 1


def test_dhis2_latency_does_not_join_the_core_api_meter(otel: tuple[Any, Any]) -> None:
    """NFR-OBS-002 的实际风险：实例慢到 60 秒时，「Factory API 变慢」的报警不该响。

    同一个 provider 上另开一条 meter 代表核心 API：两条曲线各自归自己的 scope，
    核心那条里不能出现 DHIS2 的耗时数据点。
    """
    pytest.importorskip("opentelemetry", reason="需要 opentelemetry-sdk")
    from opentelemetry import metrics as otel_metrics

    reader, provider = otel
    core_histogram = otel_metrics.get_meter(
        "factory.control", meter_provider=provider
    ).create_histogram("http.server.duration", unit="ms")

    install(lambda request: httpx.Response(200, json={}))
    client().call("system.info")
    core_histogram.record(5.0, attributes={"http.route": "/api/projects"})

    points = _points(reader)
    assert {item["scope"] for item in points[tracing.METRIC_DURATION]} == {tracing.METER_NAME}
    assert {item["scope"] for item in points["http.server.duration"]} == {"factory.control"}
    assert points[tracing.METRIC_DURATION][0]["count"] == 1, (
        "核心 API 的那次请求不给 DHIS2 直方图加数据点，反之也一样"
    )


def test_scope_switch_reuses_the_same_environment_binding() -> None:
    """DX-BR-02：每个请求绑定唯一环境，但范围按操作切换。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-Scope", ""))
        return httpx.Response(200, json={})

    install(handler)
    base = client(scope=CredentialScope.DISCOVERY)
    importer = base.with_scope(CredentialScope.IMPORT)
    assert importer.target.key == base.target.key
    assert importer.default_scope is CredentialScope.IMPORT


def test_correlation_id_is_forwarded() -> None:
    """DX-CLI-001：correlationId 传递到 DHIS2 调用。"""
    from factory_foundation.api import bind_operation_context

    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-Correlation-Id"))
        return httpx.Response(200, json={})

    install(handler)
    with bind_operation_context(correlation_id="corr-0123456789", source_subsystem="S4"):
        client().call("system.info")
    assert seen == ["corr-0123456789"]


def test_settings_override_the_retry_attempt_count() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, json={})

    install(handler)
    target = make_target()
    strict = Dhis2Client(target, settings=Dhis2Settings(retry_max_attempts=1))
    with pytest.raises(Dhis2UnreachableError):
        strict.call("system.info")
    assert attempts["n"] == 1


# --- 调试级正文日志（DX-CLI-004）--------------------------------------------------


def test_bodies_are_not_logged_by_default(caplog: pytest.LogCaptureFixture) -> None:
    """默认连 DEBUG 也不出正文：开关不在日志级别上，而在设置里。

    正文里可能是个人姓名与卫生数据，"debug 模式就全打出来"不是可接受的默认。
    """
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "ANC Rapid Test", "password": SECRET})

    install(handler)
    with caplog.at_level(logging.DEBUG, logger="factory_dhis2.client"):
        Dhis2Client(make_target(), settings=Dhis2Settings()).call("system.info")
    assert not [
        item for item in caplog.records if "exchange" in item.getMessage()
    ], "默认连 DEBUG 也不记正文"
    assert SECRET not in caplog.text


def test_debug_body_logging_redacts_credentials_and_sensitive_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """开了开关之后：正文可以看到，凭证看不到（两层都要过）。"""
    import logging

    from factory_dhis2.client.redaction import REDACTED

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "ANC", "password": SECRET})

    install(handler)
    with caplog.at_level(logging.DEBUG, logger="factory_dhis2.client"):
        Dhis2Client(make_target(), settings=Dhis2Settings(log_request_bodies=True)).call(
            "metadata.import", dhis2_type="dataElement", body={"password": SECRET, "name": "ANC"}
        )

    # 一次调用记两条：发出去的和收到的（响应那条才带 status 与 responseBody）
    outgoing, incoming = (
        item.extra_fields for item in caplog.records if "exchange" in item.getMessage()
    )
    dump = json.dumps([outgoing, incoming], ensure_ascii=False, default=str)
    assert SECRET not in dump, "解析过的凭证值本身也不能出现在正文日志里"
    assert REDACTED in dump
    assert "ANC" in dump, "正文其余部分照旧可见，否则调试日志没有用"
    assert outgoing["headers"]["Authorization"] == f"Bearer {REDACTED}"
    assert REDACTED in outgoing["requestBody"]
    assert "password" in incoming["responseBody"]
    assert incoming["status"] == 200


def test_debug_body_logging_truncates_after_redaction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """先脱敏再截断：反过来的话 ``***REDACTED***`` 会被截成 ``***REDAC``。"""
    import logging

    from factory_dhis2.client.session import MAX_LOG_BODY_CHARS

    install(lambda request: httpx.Response(200, json={"blob": "x" * 9_000}))
    with caplog.at_level(logging.DEBUG, logger="factory_dhis2.client"):
        Dhis2Client(make_target(), settings=Dhis2Settings(log_request_bodies=True)).call(
            "system.info"
        )

    body = [
        item.extra_fields["responseBody"]
        for item in caplog.records
        if "exchange" in item.getMessage() and "responseBody" in item.extra_fields
    ][-1]
    assert len(body) <= MAX_LOG_BODY_CHARS + 20, body[-30:]
    assert body.endswith("chars)"), "截断处要说明还剩多少没打"


def test_structured_log_fields_reach_the_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S0 的 formatter 只把 ``record.extra_fields`` 并进 JSON。

    平铺进 ``extra=`` 的字段会设到 LogRecord 的属性上、但**不出现在日志行里**——结构化
    日志静默丢失，重试与超时的诊断信息也就没了。这条用真的 formatter 锁住那个嵌套写法。
    """
    import logging

    from factory_foundation.common.logging import RedactingJsonFormatter

    install(lambda request: httpx.Response(503, json={}))
    with (
        caplog.at_level(logging.DEBUG, logger="factory_dhis2.client"),
        pytest.raises(Dhis2UnreachableError),
    ):
        client().call("system.info")

    record = next(item for item in caplog.records if "retrying" in item.getMessage())
    line = json.loads(RedactingJsonFormatter().format(record))
    assert {"environmentKey", "logicalResource", "attempt", "status", "delaySeconds"} <= set(
        line
    ), line
