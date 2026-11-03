"""Rate-limit counters in Redis (REDIS_URL set), instead of PostgreSQL.

Why move them: every limited request writes a counter. In PostgreSQL that's an upsert on a
table, a WAL entry and a row lock, on the database that also holds chats and documents. Redis
keeps counters in memory: INCR is atomic and takes microseconds, and EXPIRE removes a finished
window by itself, so there's no table to sweep. All replicas share one Redis, so they share the
counts, as before.

The same pattern backs other short-lived shared state in production: sessions, locks, queues,
caches of hot answers. Redis is a cache, not a database of record: losing it here resets the
counters, which is fine; never keep something in it only.
"""

from functools import cached_property


class RedisCounts:
    """Implements rate_limit.Counts."""

    def __init__(self, url: str):
        self.url = url

    @cached_property
    def client(self):
        try:
            import redis
        except ImportError as e:
            raise RuntimeError("REDIS_URL is set but the client isn't installed: uv add redis") from e
        # short timeouts: a slow Redis must not slow every request; rate_limit.check lets the request
        # through when counting fails
        return redis.Redis.from_url(self.url, socket_timeout=0.5, socket_connect_timeout=0.5)

    def hit(self, key: str, window_start: int, window_seconds: int) -> int:
        """Counts one request in the window and returns the window's count, this request included."""
        name = f"ratelimit:{key}:{window_start}"
        pipe = self.client.pipeline(transaction=True)  # MULTI/EXEC: both commands or neither
        pipe.incr(name)
        pipe.expire(name, window_seconds + 5, nx=True)  # set once, when the window's first request creates it
        count, _ = pipe.execute()
        return int(count)

    def ping(self) -> None:
        self.client.ping()
