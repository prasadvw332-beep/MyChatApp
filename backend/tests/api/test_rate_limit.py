"""Rate limits: 429 with Retry-After once a user (or IP) uses up a window. Counted in memory (conftest.py)."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.core import config, rate_limit
from app.core.errors import TooManyRequests
from app.core.rate_limit import Limit
from app.repositories import user_store
from tests.fakes import FakeRateLimits

NOW = 1_000_050.5  # 29.5 s before a minute ends, 12.5 minutes before an hour ends


@pytest.fixture
def clock(monkeypatch):
    """Frozen time: clock.now is what rate_limit sees, and can be moved forward."""
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(time=lambda: clock.now))
    return clock


def check(name, subject, limit):
    asyncio.run(rate_limit.check(name, subject, limit))


@pytest.mark.parametrize("text, limit", [
    ("20/minute", Limit(20, 60)), (" 5 / Hours ", Limit(5, 3600)), ("1/second", Limit(1, 1)),
    ("100/day", Limit(100, 86400)), ("", None),
])
def test_parse(text, limit):
    assert Limit.parse(text) == limit


@pytest.mark.parametrize("text", ["20", "20/fortnight", "x/minute", "0/minute", "-1/hour", "1/2/minute"])
def test_parse_rejects_nonsense(text):
    with pytest.raises(ValueError):
        Limit.parse(text)


def test_limit_per_window_and_subject(clock):
    limit = Limit(2, 60)
    check("chat", "user:1", limit)
    check("chat", "user:1", limit)
    with pytest.raises(TooManyRequests) as e:
        check("chat", "user:1", limit)
    assert e.value.headers == {"Retry-After": "30"}  # until the minute is over (29.5 s, rounded up)
    check("chat", "user:2", limit)  # another user has their own count
    check("upload", "user:1", limit)  # and so does another limit
    clock.now += 30
    check("chat", "user:1", limit)  # a new window


def test_over_limit_is_a_429_in_the_usual_error_shape(client, logged_in, clock, rate_limits, metric):
    window = int(NOW) // 60 * 60
    rate_limits.hits["chat:user:1", window] = Limit.parse(config.RATE_LIMIT_CHAT).count
    before = metric("rate_limit_rejections_total", limit="chat")
    body = {"messages": [{"role": "user", "content": "hi"}]}

    r = client.post("/chat", json=body, headers={"X-Request-ID": "req-9"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "30"
    assert r.json() == {"detail": "Too many requests. Please try again in 30 seconds.",
                        "code": "rate_limited", "request_id": "req-9"}
    # /agent/chat shares the budget: both run the chat model
    assert client.post("/agent/chat", json=body).status_code == 429
    assert metric("rate_limit_rejections_total", limit="chat") == before + 2


def test_register_is_limited_per_ip(client, clock, monkeypatch):
    def taken(*_):
        raise user_store.UsernameTaken

    monkeypatch.setattr(user_store, "create", taken)
    creds = {"username": "someone", "password": "long enough"}
    limit = Limit.parse(config.RATE_LIMIT_REGISTER).count
    assert [client.post("/auth/register", json=creds).status_code for _ in range(limit)] == [409] * limit
    r = client.post("/auth/register", json=creds)
    assert r.status_code == 429
    assert r.json()["detail"] == "Too many requests. Please try again in 13 minutes."


def test_audited_once_per_window(clock, caplog):
    with caplog.at_level(logging.INFO, logger="app.audit"):
        for _ in range(4):
            try:
                check("eval", "user:7", Limit(1, 3600))
            except TooManyRequests:
                pass
    assert [r.fields["event"] for r in caplog.records] == ["rate_limit.exceeded"]
    assert caplog.records[0].fields["subject"] == "user:7"


def test_database_down_lets_requests_through(monkeypatch, metric):
    monkeypatch.setattr(rate_limit, "counts", FakeRateLimits(fail=True))
    before = metric("rate_limit_errors_total")
    for _ in range(3):
        check("chat", "user:1", Limit(1, 60))
    assert metric("rate_limit_errors_total") == before + 3


def test_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    for _ in range(3):
        check("chat", "user:1", Limit(1, 60))
