"""Password hashing and cookie sessions (a signed JWT in an httpOnly cookie)."""

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import COOKIE_SECURE, JWT_SECRET, SESSION_DAYS
from app.models import User

COOKIE = "session"
_hasher = PasswordHash.recommended()  # argon2id

_secret = JWT_SECRET
if not _secret:
    _secret = secrets.token_urlsafe(48)
    logging.getLogger("uvicorn.error").warning(
        "JWT_SECRET is not set: using a random one, so everyone is logged out on every restart.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _hasher.verify(password, hashed)


# hashed on a miss too, so a wrong username takes as long as a wrong password
_DUMMY_HASH = hash_password("not-a-real-password")


def check_password(user: User | None, password: str) -> bool:
    return verify_password(password, user.password_hash if user else _DUMMY_HASH) and user is not None


def make_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    return jwt.encode({"sub": str(user.id), "exp": exp}, _secret, algorithm="HS256")


def session_cookie(token: str) -> dict:
    return dict(key=COOKIE, value=token, max_age=SESSION_DAYS * 86400, httponly=True,
                samesite="lax", secure=COOKIE_SECURE, path="/")


def user_id_from_token(token: str | None) -> int | None:
    """The user id in a valid, unexpired session token, else None."""
    if not token:
        return None
    try:
        return int(jwt.decode(token, _secret, algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None

# --- brute-force throttle: 5 failures per username+IP in 5 minutes (in memory, per process) ---
_failures: dict[str, list[float]] = {}
WINDOW, MAX_FAILURES = 300, 5


def throttled(key: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(key, []) if now - t < WINDOW]
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(key: str) -> None:
    _failures.setdefault(key, []).append(time.monotonic())


def clear_failures(key: str) -> None:
    _failures.pop(key, None)
