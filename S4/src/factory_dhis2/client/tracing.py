"""DHIS2 调用的可观测性（DX-CLI-007、总体 SRS 22 章引言、NFR-OBS-002）。

每个 DHIS2 调用产生一个 OpenTelemetry span 与一条指标（环境、逻辑资源、状态码、耗时）。
指标登记在**独立的 meter** 上：DHIS2 的外部延迟不计入核心 API 指标，否则一个慢实例会把
Factory 自身的 API 延迟曲线一起拖走，报警就失去了意义。分离是**结构性的**——仪器只在
``METER_NAME`` 这个 meter 上创建，任何测试与运行时路径都不会把它们挂到默认 meter。

OTel 是可选依赖：没装时 span 是 no-op、指标仪器为 ``None``，但进程内累加器照样工作，
所以「哪个环境调了几次、最慢一次多久」在没有 collector 的环境里依然可查。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import trace_span

METER_NAME = "factory.dhis2"
#: 指标名（点号分隔是 OTel 约定；核心 API 指标用的是另一个 meter 上的另一批名字）
METRIC_DURATION = "dhis2.call.duration"
METRIC_CALLS = "dhis2.call.count"

#: span 与指标上的属性名（00 文档 6.8 的字段命名）
ATTRIBUTE_ENVIRONMENT = "dhis2.environmentKey"
ATTRIBUTE_RESOURCE = "dhis2.logicalResource"
ATTRIBUTE_METHOD = "dhis2.method"
ATTRIBUTE_STATUS = "dhis2.status"
ATTRIBUTE_DURATION_MS = "dhis2.durationMs"
ATTRIBUTE_TRIES = "dhis2.attempts"


@dataclass(frozen=True)
class CallMetric:
    environment_key: str
    logical_resource: str
    status: int | None
    duration_ms: float
    attempts: int

    def attributes(self) -> dict[str, str]:
        """指标维度：环境、逻辑资源、状态码（SRS 点名的三项，不多不少）。

        状态码统一转成字符串，因为「没拿到响应」（传输失败）与「500」必须都能被记录，而
        OTel 的属性值不接受 ``None``。
        """
        return {
            ATTRIBUTE_ENVIRONMENT: self.environment_key,
            ATTRIBUTE_RESOURCE: self.logical_resource,
            ATTRIBUTE_STATUS: "none" if self.status is None else str(self.status),
        }


@dataclass
class Dhis2Metrics:
    """进程内的调用指标累加器。

    生产上 OTLP 导出是唯一出路，但“DHIS2 延迟单独监控”这条要求必须能在没有 collector
    的环境里被验证，所以这里同时留一份可查询的累加值。
    """

    calls: list[CallMetric] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, metric: CallMetric) -> None:
        with self._lock:
            self.calls.append(metric)

    def by_environment(self) -> dict[str, int]:
        with self._lock:
            out: dict[str, int] = {}
            for metric in self.calls:
                out[metric.environment_key] = out.get(metric.environment_key, 0) + 1
            return out

    def total_ms(self, environment_key: str | None = None) -> float:
        with self._lock:
            return sum(
                metric.duration_ms
                for metric in self.calls
                if environment_key is None or metric.environment_key == environment_key
            )

    def slowest(self) -> CallMetric | None:
        with self._lock:
            return max(self.calls, key=lambda m: m.duration_ms, default=None)

    def clear(self) -> None:
        with self._lock:
            self.calls.clear()


metrics = Dhis2Metrics()


def reset_metrics() -> None:
    metrics.clear()


@dataclass(frozen=True)
class _Instruments:
    """DHIS2 专属 meter 上的仪器；OTel 未安装时为 ``None``（见 :func:`instruments`）。"""

    duration: Any
    calls: Any
    meter_name: str = METER_NAME


_INSTRUMENTS: _Instruments | None = None
_INSTRUMENTS_LOCK = threading.Lock()
_METER_PROVIDER: Any = None


def configure_meter_provider(provider: Any) -> None:
    """指定 DHIS2 指标写到哪个 ``MeterProvider``（测试用内存 reader 的那个）。

    设过之后必须重建仪器：仪器属于创建它的那个 provider，换 provider 不重建的话数据会
    静默流向旧的（也就是永远读不到）。
    """
    global _METER_PROVIDER, _INSTRUMENTS
    with _INSTRUMENTS_LOCK:
        _METER_PROVIDER = provider
        _INSTRUMENTS = None


def instruments() -> _Instruments | None:
    """本模块自己的 meter 上的仪器；OTel 缺失时返回 ``None``。

    只认 ``METER_NAME``：这就是「不计入核心 API 指标」的实现方式——核心指标在别的 meter
    上，而这里创建的直方图与计数器永远不会出现在别的 meter 里。
    """
    global _INSTRUMENTS
    if _INSTRUMENTS is not None:
        return _INSTRUMENTS
    with _INSTRUMENTS_LOCK:
        if _INSTRUMENTS is not None:
            return _INSTRUMENTS
        try:
            from opentelemetry import metrics as otel_metrics
        except Exception:  # noqa: BLE001 - OTel 是可选依赖，缺失即降级
            return None
        meter = otel_metrics.get_meter(
            METER_NAME, meter_provider=_METER_PROVIDER
        )
        _INSTRUMENTS = _Instruments(
            duration=meter.create_histogram(
                METRIC_DURATION,
                unit="ms",
                description="一次 DHIS2 调用的往返耗时（含重试）",
            ),
            calls=meter.create_counter(
                METRIC_CALLS,
                unit="1",
                description="DHIS2 调用次数",
            ),
        )
        return _INSTRUMENTS


def record_call(metric: CallMetric) -> None:
    """累加 + 上报指标；异常不能冒到业务调用方（观测失败不是 DHIS2 失败）。"""
    metrics.record(metric)
    tools = instruments()
    if tools is None:
        return
    attributes = metric.attributes()
    try:
        tools.duration.record(metric.duration_ms, attributes=attributes)
        tools.calls.add(1, attributes=attributes)
    except Exception:  # noqa: BLE001 - 观测层永不影响业务
        return


@contextmanager
def dhis2_span(
    *,
    environment_key: str,
    logical_resource: str,
    method: str,
    extra: Mapping[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """包一次 DHIS2 调用的 span；结束时把 ``outcome`` 写进属性与指标。

    用法：``with dhis2_span(...) as outcome: outcome["status"] = ...``。把可变字典交给调用
    方，是为了让 Client 在异常路径上也能记录状态码——“请求失败”才是最需要观测的时刻。
    """
    record: dict[str, Any] = {"status": None, "attempts": 1, "error": None}
    started = time.monotonic()
    with trace_span(f"dhis2.{logical_resource}") as span:
        try:
            if span is not None:
                span.set_attribute(ATTRIBUTE_ENVIRONMENT, environment_key)
                span.set_attribute(ATTRIBUTE_RESOURCE, logical_resource)
                span.set_attribute(ATTRIBUTE_METHOD, method)
                for key, value in (extra or {}).items():
                    span.set_attribute(key, value)
            yield record
        finally:
            duration_ms = (time.monotonic() - started) * 1000.0
            if span is not None:
                span.set_attribute(ATTRIBUTE_DURATION_MS, duration_ms)
                span.set_attribute(ATTRIBUTE_TRIES, int(record.get("attempts", 1)))
                if record.get("status") is not None:
                    span.set_attribute(ATTRIBUTE_STATUS, record["status"])
            record_call(
                CallMetric(
                    environment_key=environment_key,
                    logical_resource=logical_resource,
                    status=record.get("status"),
                    duration_ms=duration_ms,
                    attempts=int(record.get("attempts", 1) or 1),
                )
            )


def instrumentation_available() -> bool:
    """OTel 是否真正装了（否则 tracer 是 no-op，测试据此调整断言）。"""
    from factory_foundation.api import tracer as _tracer

    return type(_tracer(METER_NAME)).__module__.startswith("opentelemetry")


def metrics_available() -> bool:
    """指标仪器是否可用（OTel 未安装时只有进程内累加器）。"""
    return instruments() is not None


__all__ = [
    "ATTRIBUTE_DURATION_MS",
    "ATTRIBUTE_ENVIRONMENT",
    "ATTRIBUTE_METHOD",
    "ATTRIBUTE_RESOURCE",
    "ATTRIBUTE_STATUS",
    "ATTRIBUTE_TRIES",
    "METRIC_CALLS",
    "METRIC_DURATION",
    "METER_NAME",
    "CallMetric",
    "Dhis2Metrics",
    "configure_meter_provider",
    "dhis2_span",
    "instruments",
    "instrumentation_available",
    "metrics",
    "metrics_available",
    "record_call",
    "reset_metrics",
]
