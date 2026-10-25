"""OpenTelemetry tracing helpers (FD-COM-006).

Wraps the optional ``opentelemetry`` dependency so the library degrades to a
no-op tracer when OTel is not installed, while still propagating the operation
context's correlation id onto spans.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from factory_foundation.common.context import get_operation_context

try:  # pragma: no cover - exercised depending on optional deps
    from opentelemetry import trace

    _HAVE_OTEL = True
except Exception:  # noqa: BLE001 - any import failure means no OTel
    trace = None  # type: ignore[assignment]
    _HAVE_OTEL = False


_NOOP_TRACER: Any = None


class _NoopSpan:
    def set_attribute(self, *_a: object, **_k: object) -> None:
        return None

    def set_status(self, *_a: object, **_k: object) -> None:
        return None

    def record_exception(self, *_a: object, **_k: object) -> None:
        return None


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **_: object) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


def tracer(name: str = "factory_foundation") -> Any:
    """Return a tracer — the OTel tracer when available, otherwise a no-op."""
    if _HAVE_OTEL and trace is not None:
        return trace.get_tracer(name)
    return _NoopTracer()


def configure_tracing(endpoint: str | None = None, *, service_name: str = "factory") -> None:
    """Best-effort OTLP exporter setup; no-op without OTel or endpoint."""
    if not _HAVE_OTEL or not endpoint:
        return
    try:  # pragma: no cover - requires the OTLP exporter extra
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
    except Exception:  # noqa: BLE001 - tracing must never break the app
        return


@contextmanager
def trace_span(name: str, **attributes: Any) -> Iterator[Any]:
    tr = tracer()
    with tr.start_as_current_span(name) as span:
        ctx = get_operation_context()
        if ctx is not None:
            span.set_attribute("correlationId", ctx.correlation_id)
            if ctx.project_id:
                span.set_attribute("projectId", ctx.project_id)
        for k, v in attributes.items():
            span.set_attribute(k, v)
        yield span
