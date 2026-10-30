import jwt
import pytest

from app.core import config, security
from app.models import User


def _production(monkeypatch, **overrides):
    safe = dict(IS_PRODUCTION=True, JWT_SECRET="x" * 40, MINIO_SECRET_KEY="strong-secret",
                DATABASE_URL="postgresql+psycopg://app:s3cret@db:5432/app", COOKIE_SECURE=True)
    for k, v in (safe | overrides).items():
        monkeypatch.setattr(config, k, v)


def test_production_with_safe_settings_starts(monkeypatch):
    _production(monkeypatch)
    config.validate()


@pytest.mark.parametrize("override", [
    {"JWT_SECRET": ""},
    {"MINIO_SECRET_KEY": "minioadmin123"},
    {"DATABASE_URL": "postgresql+psycopg://postgres:postgres@db/app"},
    {"COOKIE_SECURE": False},
])
def test_production_refuses_insecure_defaults(monkeypatch, override):
    _production(monkeypatch, **override)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        config.validate()


def test_development_allows_defaults(monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", False)
    monkeypatch.setattr(config, "JWT_SECRET", "")
    config.validate()


def test_session_token_round_trip():
    assert security.user_id_from_token(security.make_token(User(id=42))) == 42


@pytest.mark.parametrize("token", [None, "", "garbage", jwt.encode({"sub": "1"}, "a-different-secret-that-is-long-enough!!", algorithm="HS256")])
def test_invalid_tokens_are_rejected(token):
    assert security.user_id_from_token(token) is None


def test_login_throttle():
    key = "mallory|1.2.3.4"
    for _ in range(security.MAX_FAILURES):
        assert not security.throttled(key)
        security.record_failure(key)
    assert security.throttled(key)
    security.clear_failures(key)
    assert not security.throttled(key)
