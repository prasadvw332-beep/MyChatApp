"""Prometheus metrics, tracing off by default, and trace ids on log lines. No backing services needed."""

import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core import telemetry
from app.core.logging import _ContextFilter, _JsonFormatter
from app.main import app
from app.rag import knowledge_base

client = TestClient(app)


def _value(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_requests_are_counted_by_route_template():
    before = _value("http_requests_total", method="GET", route="/health", status="200")
    client.get("/health")
    assert _value("http_requests_total", method="GET", route="/health", status="200") == before + 1
    assert _value("http_request_duration_seconds_count", method="GET", route="/health") >= 1


def test_ids_in_the_path_do_not_become_label_values():
    client.delete("/chats/12345")  # 401, but routed: the label is the template
    assert _value("http_requests_total", method="DELETE", route="/chats/{chat_id}", status="401") >= 1
    client.get("/no/such/page/987")
    assert _value("http_requests_total", method="GET", route="unmatched", status="404") >= 1
    assert not any("987" in s.labels.get("route", "") for m in REGISTRY.collect() for s in m.samples)


def test_question_without_context_is_counted(monkeypatch):
    monkeypatch.setattr(knowledge_base.store, "search", lambda q, k: [])
    before = _value("retrieval_no_context_total")
    assert knowledge_base.retrieve("anything") == []
    assert _value("retrieval_no_context_total") == before + 1


def test_tracing_is_off_without_an_endpoint():
    assert telemetry._provider is None  # OTEL_EXPORTER_OTLP_ENDPOINT is not set in tests
    with telemetry.span("noop") as s:
        assert not s.get_span_context().is_valid


def test_sql_statements_become_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
    engine = create_engine("sqlite://")
    telemetry.trace_queries(engine)
    with engine.connect() as conn:
        conn.execute(text("SELECT 'outside any trace'"))  # e.g. the worker's idle queue poll: no span
        with telemetry.span("request"):
            conn.execute(text("SELECT 1"))
            with pytest.raises(OperationalError):
                conn.execute(text("SELECT * FROM missing"))
    ok, failed, request = exporter.get_finished_spans()
    assert ok.name == "SELECT" and ok.attributes["db.statement"] == "SELECT 1"
    assert ok.parent.span_id == request.context.span_id
    assert failed.status.status_code == trace.StatusCode.ERROR


def test_http_spans_are_named_after_service_and_call():
    assert telemetry.http_span_name(b"POST", httpx.URL("http://ollama:11434/api/chat")) == "ollama POST /api/chat"
    raw = (b"http", b"qdrant.mychatapp.svc.cluster.local", 6333, b"/collections/documents_v2/points/query")
    assert telemetry.http_span_name(b"POST", raw) == "qdrant POST /collections/documents_v2/points/query"


def _record() -> logging.LogRecord:
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "hello", None, None)
    _ContextFilter().filter(record)
    return record


def test_log_lines_carry_the_active_trace_id():
    tracer = TracerProvider().get_tracer("test")  # a real SDK tracer, without making it the global one
    with tracer.start_as_current_span("request") as s:
        record = _record()
        expected = format(s.get_span_context().trace_id, "032x")
    assert record.trace_id == expected
    assert f'"trace_id": "{expected}"' in _JsonFormatter().format(record)
    assert trace.get_current_span().get_span_context().is_valid is False


def test_log_lines_without_a_trace_have_no_trace_fields():
    record = _record()
    assert record.trace_id is None and record.trace == ""
    assert "trace_id" not in _JsonFormatter().format(record)
