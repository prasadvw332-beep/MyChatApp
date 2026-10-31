"""Rate limits on the expensive endpoints: each request there runs the model, embeds a document or
creates an account, so one client in a loop could take the model server away from everyone else.

    @router.post("/chat", dependencies=[limit_per_user("chat", RATE_LIMIT_CHAT)])   (app/api/deps.py)

Fixed windows: "20/minute" allows 20 requests in each calendar minute, per user (per IP on routes
without a login). The counts live in PostgreSQL, so every backend replica sees the same numbers: one
upsert per limited request, next to a model call that takes seconds. A fixed window lets a client send
up to twice the limit across a window boundary; that is fine for what this is for (no single client
hogs the model), and far simpler than a sliding log.

A rejected request gets 429 with Retry-After. If PostgreSQL can't be reached the request goes ahead
(counted in rate_limit_errors_total): a limiter must not turn a database blip into a full outage.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Protocol

from app.core import config, metrics
from app.core.audit import audit
from app.core.errors import TooManyRequests
from app.repositories import rate_limit_store

log = logging.getLogger(__name__)

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True)
class Limit:
    count: int
    seconds: int

    @classmethod
    def parse(cls, text: str) -> "Limit | None":
        """"20/minute" -> Limit(20, 60); empty -> None (no limit). Anything else stops the app at startup."""
        if not text.strip():
            return None
        try:
            count, unit = text.strip().lower().split("/")
            limit = cls(int(count), _UNITS[unit.strip().removesuffix("s")])
        except (ValueError, KeyError):
            limit = None
        if limit is None or limit.count < 1:
            raise ValueError(f"A rate limit looks like 20/minute (second, minute, hour or day), not {text!r}")
        return limit


class Counts(Protocol):
    """repositories/rate_limit_store.py, or a dict in tests."""

    def hit(self, key: str, window_start: int, window_seconds: int) -> int: ...


counts: Counts = rate_limit_store


async def check(name: str, subject: str, limit: Limit | None) -> None:
    """Counts this request against `name` for `subject` ("user:9", "ip:1.2.3.4"); raises TooManyRequests over the limit."""
    if limit is None or not config.RATE_LIMIT_ENABLED:
        return
    now = time.time()
    window_start = int(now) // limit.seconds * limit.seconds
    try:
        used = await asyncio.to_thread(counts.hit, f"{name}:{subject}", window_start, limit.seconds)
    except Exception:
        metrics.rate_limit_errors.inc()
        log.warning("rate limit %s could not be checked; letting the request through", name, exc_info=True)
        return
    if used <= limit.count:
        return
    metrics.rate_limited.labels(name).inc()
    if used == limit.count + 1:  # once per window, not once for every rejected request
        audit("rate_limit.exceeded", limit=name, subject=subject)
    raise TooManyRequests(retry_after=max(1, math.ceil(window_start + limit.seconds - now)))
