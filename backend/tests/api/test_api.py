"""HTTP contract: error shape, request ids, health checks, ownership. No backing services needed."""

import pytest

from app.api.routes import health
from app.rag import knowledge_base
from app.repositories import chat_store


def test_every_response_has_a_request_id(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert len(r.headers["x-request-id"]) == 32


def test_caller_request_id_is_echoed_but_sanitized(client):
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    assert client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"}).headers["x-request-id"] != "bad id"


def test_errors_share_one_shape(client):
    r = client.get("/documents", headers={"X-Request-ID": "req-1"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Not logged in.", "code": "http_401", "request_id": "req-1"}


def test_dependency_failure_does_not_leak_internals(client, logged_in, monkeypatch):
    def broken():
        raise ConnectionError("password=hunter2 host=10.0.0.5")

    monkeypatch.setattr(knowledge_base, "list_documents", broken)
    r = client.get("/documents")
    assert r.status_code == 503
    assert r.json()["code"] == "dependency_unavailable"
    assert "hunter2" not in r.text and "10.0.0.5" not in r.text


def test_unhandled_error_is_a_generic_500_with_request_id(client, logged_in, monkeypatch):
    def bug(_user_id):
        raise KeyError("internal detail")

    monkeypatch.setattr(chat_store, "list_for", bug)
    r = client.get("/chats", headers={"X-Request-ID": "req-500"})
    assert r.status_code == 500
    assert r.json() == {"detail": "Something went wrong. Please try again.", "code": "internal_error",
                        "request_id": "req-500"}
    assert r.headers["x-request-id"] == "req-500"


def test_duplicate_upload_is_409(client, logged_in, monkeypatch):
    def duplicate(name, data):
        raise knowledge_base.DuplicateDocument("Already there.")

    monkeypatch.setattr(knowledge_base, "ingest", duplicate)
    r = client.post("/upload", files={"file": ("a.txt", b"hello")})
    assert (r.status_code, r.json()["code"]) == (409, "duplicate_document")


def test_someone_elses_chat_is_not_found(client, logged_in, monkeypatch):
    def not_owner(*args):
        raise chat_store.NotOwner()

    monkeypatch.setattr(chat_store, "save", not_owner)
    r = client.put("/chats/abc", json={"title": "t", "updatedAt": 0, "messages": []})
    assert r.status_code == 404


def _pings(monkeypatch, failing: set[str]):
    def make(name):
        def ping():
            if name in failing:
                raise ConnectionError(name)
        return ping

    monkeypatch.setattr(health, "CRITICAL", {"postgres": make("postgres")})
    monkeypatch.setattr(health, "OPTIONAL", {n: make(n) for n in ("qdrant", "minio", "ollama")})


@pytest.mark.parametrize(("failing", "status", "code"), [
    (set(), "ok", 200),
    ({"ollama"}, "degraded", 200),  # chat breaks, but login and history still work: stay in rotation
    ({"postgres"}, "unavailable", 503),
])
def test_readiness(client, monkeypatch, failing, status, code):
    _pings(monkeypatch, failing)
    r = client.get("/health/ready")
    assert (r.status_code, r.json()["status"]) == (code, status)
    assert all(r.json()["checks"][n]["ok"] == (n not in failing) for n in r.json()["checks"])
