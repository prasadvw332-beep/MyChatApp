"""Logging and request tracing.

Every request gets an id (the caller's X-Request-ID, or a new one). It is returned in the
X-Request-ID response header and in error bodies, and added to every log line written while
handling that request, so one id finds everything that happened to a user's failed request.

Production logs one JSON object per line (what log collectors such as CloudWatch, Loki or
Elasticsearch parse); development logs readable text.
"""

import json
import logging
import sys
from contextvars import ContextVar

from app.core.config import IS_PRODUCTION, LOG_LEVEL

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def request_id() -> str:
    return _request_id.get()


def set_request_id(rid: str):
    """Returns a token for _request_id.reset()."""
    return _request_id.set(rid)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
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
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        _JsonFormatter() if IS_PRODUCTION
        else logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(LOG_LEVEL)
    # our access log replaces uvicorn's (which has no request id or duration)
    logging.getLogger("uvicorn.access").disabled = True
    # one line per Ollama/Qdrant call (httpx) or migration plugin (alembic) drowns the useful lines
    for noisy in ("httpx", "httpcore", "alembic.runtime.plugins"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

