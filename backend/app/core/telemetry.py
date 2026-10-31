"""OpenTelemetry tracing: one trace per request (or per ingested document), with a span for each
PostgreSQL query and each HTTP call to Ollama or Qdrant, sent to the OpenTelemetry Collector.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (tests and a plain `uv run` send nothing). When it is
off, the OpenTelemetry API is a no-op, so span() and the instrumented code cost almost nothing.

The trace id is also written on every log line (core/logging.py), so a slow trace in Grafana leads
to its log lines and back.
"""

import logging
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.instrumentation.utils import is_instrumentation_enabled, suppress_instrumentation

from app.core.config import APP_ENV, OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME

log = logging.getLogger(__name__)
tracer = trace.get_tracer("app")
_provider = None


def setup_tracing(service_name: str, app=None) -> None:
    """Start exporting spans. `app` (the FastAPI app) adds a server span per request."""
    global _provider
    if not OTEL_EXPORTER_OTLP_ENDPOINT or _provider is not None:
        return
    # imported here: the SDK and exporters are only needed when tracing is on
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from app.core.db import engine

    resource = Resource.create({"service.name": OTEL_SERVICE_NAME or service_name, "deployment.environment": APP_ENV})
    _provider = TracerProvider(resource=resource)
    endpoint = OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/") + "/v1/traces"
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(_provider)

    # Ollama (langchain_ollama) and Qdrant both use httpx
    HTTPXClientInstrumentor().instrument(request_hook=_name_http_span, async_request_hook=_aname_http_span)
    trace_queries(engine)
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        # probes every few seconds would bury the real traffic. receive/send would add a span per
        # body chunk: hundreds for one streamed answer.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,health/ready", exclude_spans=["receive", "send"])
    log.info("tracing to %s as %s", endpoint, OTEL_SERVICE_NAME or service_name)


def http_span_name(method: bytes | str, url) -> str:
    """'ollama POST /api/chat' instead of the default 'POST': which service and call, at a glance.
    The path holds no ids here (Ollama endpoints, Qdrant collection names), so the name set stays small,
    which matters because Tempo turns span names into Prometheus labels (the dashboard's per-step panel)."""
    if isinstance(url, tuple):  # httpx's raw form: (scheme, host, port, path)
        host, path = url[1].decode(), url[3].decode()
    else:
        host, path = url.host, url.path
    method = method.decode() if isinstance(method, bytes) else method
    return f"{host.split('.')[0]} {method} {path}"


def _name_http_span(span, request) -> None:
    if span.is_recording():
        span.update_name(http_span_name(request.method, request.url))


async def _aname_http_span(span, request) -> None:
    _name_http_span(span, request)


def trace_queries(engine) -> None:
    """A span per SQL statement, inside an existing trace only: the worker polls the queue every few
    seconds, and each poll would otherwise be a one-query trace of its own.
    (opentelemetry-instrumentation-sqlalchemy refuses SQLAlchemy 2.1 and patches the asyncio engine,
    which needs greenlet; these events are all it does for a sync engine.)"""
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _start(conn, _cursor, statement, _params, context, _executemany):
        if not trace.get_current_span().get_span_context().is_valid or not is_instrumentation_enabled():
            return
        s = tracer.start_span(statement.split(None, 1)[0].upper() if statement else "SQL", kind=trace.SpanKind.CLIENT,
                              attributes={"db.system": "postgresql", "db.statement": statement,
                                          "db.name": conn.engine.url.database or ""})
        context._otel_span = s

    @event.listens_for(engine, "after_cursor_execute")
    def _end(_conn, _cursor, _statement, _params, context, _executemany):
        if s := getattr(context, "_otel_span", None):
            s.end()

    @event.listens_for(engine, "handle_error")
    def _error(context):
        if s := getattr(context.execution_context, "_otel_span", None):
            s.record_exception(context.original_exception)
            s.set_status(trace.StatusCode.ERROR)
            s.end()


def shutdown() -> None:
    """Send the spans still in the buffer (call on exit)."""
    if _provider is not None:
        _provider.shutdown()


untraced = suppress_instrumentation  # `with untraced():` for calls that should never become spans


@contextmanager
def span(name: str, **attributes):
    with tracer.start_as_current_span(name, attributes={k: v for k, v in attributes.items() if v is not None}) as s:
        yield s
