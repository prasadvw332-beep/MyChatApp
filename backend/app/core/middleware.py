"""Per-request context: request id, access log, and a last-resort 500 for unhandled errors."""

import logging
import re
import time
import uuid

from app.core.errors import unhandled
from app.core.logging import set_request_id

access_log = logging.getLogger("app.access")
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")  # a caller-supplied id goes into logs: keep it tame


class RequestContextMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware), so streamed chat responses pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _SAFE_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = set_request_id(rid)
        started = time.perf_counter()
        status, response_started = 500, False

        async def send_with_id(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status, response_started = message["status"], True
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as e:
            if response_started:  # e.g. mid-stream: too late for an error response
                raise
            await unhandled(e)(scope, receive, send_with_id)
        finally:
            ms = round((time.perf_counter() - started) * 1000, 1)
            access_log.info(
                "%s %s %s %.1fms", scope["method"], scope["path"], status, ms,
                extra={"fields": {"method": scope["method"], "path": scope["path"], "status": status,
                                  "duration_ms": ms}},
            )
            token.var.reset(token)
