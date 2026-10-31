"""Prometheus metrics for the backend and the ingestion workers.

Every metric is defined here, so this file is the list of what can be graphed in Grafana
(deploy/observability/grafana/dashboards/mychatapp.json).

They are served on their own port (METRICS_PORT, default 9100), not on the API port: nginx forwards
all of /api/* to the backend, so a /metrics route there would be readable by anyone on the internet.

Labels are kept to small, fixed sets (a route template, never the raw path with its ids), because
every distinct label value is a separate time series that Prometheus keeps in memory.
"""

import logging

from prometheus_client import Counter, Histogram, start_http_server

from app.core.config import METRICS_PORT

log = logging.getLogger(__name__)

# LLM calls on a CPU take seconds to minutes; the default buckets stop at 10s
_SLOW = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300)

# HTTP (recorded by core/middleware.py)
http_requests = Counter("http_requests_total", "HTTP requests handled", ["method", "route", "status"])
http_duration = Histogram("http_request_duration_seconds", "Time to handle a request, including a streamed body",
                          ["method", "route"], buckets=_SLOW)

# LLM (endpoint = chat | agent)
llm_first_token = Histogram("llm_time_to_first_token_seconds", "Time until the model's first token",
                            ["endpoint"], buckets=_SLOW)
llm_stream = Histogram("llm_stream_duration_seconds", "Time to stream the whole answer", ["endpoint"], buckets=_SLOW)

# Retrieval
retrieval_duration = Histogram("retrieval_duration_seconds", "Embedding the question + vector search")
retrieval_no_context = Counter("retrieval_no_context_total", "Questions where no chunk passed MIN_SCORE")

# Agent
agent_tool_calls = Counter("agent_tool_calls_total", "Agent tool calls", ["tool", "ok"])

# Ingestion (outcome = indexed | retry | failed)
ingest_documents = Counter("ingest_documents_total", "Documents processed by the workers", ["outcome"])
ingest_duration = Histogram("ingest_duration_seconds", "Time to chunk, embed and store one document", buckets=_SLOW)
ingest_chunks = Counter("ingest_chunks_total", "Chunks written to the vector store")


def start_server() -> None:
    """Serve /metrics on METRICS_PORT in a background thread (0 disables it)."""
    if not METRICS_PORT:
        return
    try:
        start_http_server(METRICS_PORT)
    except OSError as e:  # e.g. the old process still holds the port during a --reload restart
        log.warning("metrics server not started on port %d: %s", METRICS_PORT, e)
        return
    log.info("metrics on :%d/metrics", METRICS_PORT)
