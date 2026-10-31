"""Logging and request tracing.

Every request gets an id (the caller's X-Request-ID, or a new one). It is returned in the
X-Request-ID response header and in error bodies, and added to every log line written while
handling that request, so one id finds everything that happened to a user's failed request.
Once the request is authenticated, the user id is added too ("everything user 9 did").

Production logs one JSON object per line (what log collectors such as CloudWatch, Loki or
Elasticsearch parse); development logs readable text.

When tracing is on (core/telemetry.py), each line also carries the trace and span id, so a log
line leads to its trace in Grafana (Tempo) and back.

Structured fields go in `extra={"fields": {...}}`. Any field whose name looks like a secret
(password, token, secret, authorization, cookie) is replaced with "***" before it is written.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar

from opentelemetry import trace

from app.core.config import IS_PRODUCTION, LOG_LEVEL

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_user_id: ContextVar[int | None] = ContextVar("user_id", default=None)

_SECRET_KEY = re.compile(r"password|passwd|secret|token|authorization|cookie|api_?key", re.IGNORECASE)


def request_id() -> str:
    return _request_id.get()


def set_request_id(rid: str):
    """Returns a token for .var.reset(token)."""
    return _request_id.set(rid)


def set_user_id(user_id: int | None):
    """Called once the request is authenticated (app/api/deps.py). Returns a reset token."""
    return _user_id.set(user_id)


def user_id() -> int | None:
    return _user_id.get()


def redact(fields: dict) -> dict:
    return {k: "***" if _SECRET_KEY.search(k) else v for k, v in fields.items()}


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        uid = _user_id.get()
        record.user_id = uid
        record.user = f" u={uid}" if uid is not None else ""
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else None
        record.span_id = format(ctx.span_id, "016x") if ctx.is_valid else None
        record.trace = f" t={record.trace_id[:8]}" if ctx.is_valid else ""
        if hasattr(record, "fields"):
            record.fields = redact(record.fields)
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if getattr(record, "user_id", None) is not None:
            entry["user_id"] = record.user_id
        if getattr(record, "trace_id", None):
            entry["trace_id"], entry["span_id"] = record.trace_id, record.span_id
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(
        _JsonFormatter() if IS_PRODUCTION
        else logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s%(user)s%(trace)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(LOG_LEVEL)
    # our access log replaces uvicorn's (which has no request id or duration)
    logging.getLogger("uvicorn.access").disabled = True
    # one line per Ollama/Qdrant call (httpx) or migration plugin (alembic) drowns the useful lines
    for noisy in ("httpx", "httpcore", "alembic.runtime.plugins"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
