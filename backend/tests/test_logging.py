"""Audit events, user id on log lines, and secret redaction."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.core.logging import _ContextFilter, redact
from app.main import app
from app.models import User
from app.rag import knowledge_base
from app.services import chat_store, user_store

client = TestClient(app)
USER = User(id=7, username="alice", password_hash=security.hash_password("correct-horse"))


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.addFilter(_ContextFilter())  # the same enrichment as the real handler

    def emit(self, record):
        self.records.append(record)

    def named(self, logger: str):
        return [r for r in self.records if r.name == logger]

    def event(self, name: str) -> logging.LogRecord:
        return next(r for r in self.named("app.audit") if r.fields["event"] == name)


@pytest.fixture
def logs():
    handler = _Capture()
    logging.getLogger().addHandler(handler)
    yield handler
    logging.getLogger().removeHandler(handler)


@pytest.fixture
def session(monkeypatch):
    """A client with a real session cookie for USER, resolved through the real current_user dependency."""
    monkeypatch.setattr(user_store, "by_id", lambda uid: USER if uid == USER.id else None)
    return TestClient(app, cookies={security.COOKIE: security.make_token(USER)})


def test_authenticated_requests_log_the_user_id(logs, session, monkeypatch):
    monkeypatch.setattr(chat_store, "list_for", lambda uid: [])
    session.get("/chats")

    access = logs.named("app.access")[-1]
    assert access.user_id == 7
    assert access.fields["status"] == 200 and "client_ip" in access.fields


def test_anonymous_requests_have_no_user_id(logs):
    client.get("/health")
    assert logs.named("app.access")[-1].user_id is None


def test_upload_is_audited_with_who_and_what(logs, session, monkeypatch):
    monkeypatch.setattr(knowledge_base, "ingest", lambda name, data: {"doc_id": "d1", "filename": name, "chunks": 3})
    session.post("/upload", files={"file": ("plan.txt", b"hello")})

    e = logs.event("document.upload")
    assert e.user_id == 7
    assert e.fields == {"event": "document.upload", "doc_id": "d1", "filename": "plan.txt", "size_bytes": 5,
                        "chunks": 3}


def test_login_audit_records_outcome_but_never_the_password(logs, monkeypatch):
    security.clear_failures("alice|testclient")
    monkeypatch.setattr(user_store, "by_username", lambda name: USER if name == "alice" else None)

    client.post("/auth/login", json={"username": "alice", "password": "wrong-password"})
    client.post("/auth/login", json={"username": "nobody", "password": "whatever-pw"})
    client.post("/auth/login", json={"username": "alice", "password": "correct-horse"})

    failures = [r for r in logs.named("app.audit") if r.fields["event"] == "auth.login.failure"]
    assert [f.fields["reason"] for f in failures] == ["bad_password", "no_such_user"]
    assert logs.event("auth.login.success").user_id == 7
    everything = " ".join(r.getMessage() + str(getattr(r, "fields", "")) for r in logs.records)
    assert "wrong-password" not in everything and "correct-horse" not in everything


def test_logout_is_audited_for_the_right_user(logs, session):
    session.post("/auth/logout")
    assert logs.event("auth.logout").user_id == 7


def test_secret_looking_fields_are_redacted():
    fields = {"password": "p", "new_password": "p", "api_key": "k", "Authorization": "Bearer x",
              "session_token": "t", "filename": "ok.txt"}
    assert redact(fields) == {"password": "***", "new_password": "***", "api_key": "***", "Authorization": "***",
                              "session_token": "***", "filename": "ok.txt"}
