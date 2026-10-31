"""Thumbs up/down on answers: stored with a copy of the rated answer, taken back, gone with the chat."""

import pytest

from app.repositories import chat_store, feedback_store

ANSWER = {"chatId": "c1", "position": 1, "mode": "chat", "question": "What is RAG?",
          "answer": "Retrieval-augmented generation.", "sources": [{"source": "notes.md"}], "requestId": "req-42"}


@pytest.fixture
def stored(monkeypatch):
    """The feedback table as a dict keyed like its unique constraint."""
    rows = {}

    def put(row):
        before = rows.get((row.user_id, row.chat_id, row.position))
        rows[row.user_id, row.chat_id, row.position] = row
        return before and before.rating

    monkeypatch.setattr(feedback_store, "put", put)

    def remove(user_id, chat_id, position=None):
        for key in [k for k in rows if k[:2] == (user_id, chat_id) and position in (None, k[2])]:
            del rows[key]

    monkeypatch.setattr(feedback_store, "remove", remove)
    return rows


def test_rating_is_stored_with_what_was_rated(client, logged_in, stored, metric):
    before = metric("answer_feedback_total", mode="chat", rating="down")
    r = client.put("/feedback", json={**ANSWER, "rating": "down", "comment": "  wrong source  "})
    assert r.status_code == 200
    row = stored[1, "c1", 1]
    assert (row.rating, row.comment, row.answer, row.request_id) == (-1, "wrong source", ANSWER["answer"], "req-42")
    assert metric("answer_feedback_total", mode="chat", rating="down") == before + 1
    client.put("/feedback", json={**ANSWER, "rating": "down", "comment": "and a typo"})  # a comment, no new rating
    assert metric("answer_feedback_total", mode="chat", rating="down") == before + 1
    client.put("/feedback", json={**ANSWER, "mode": None, "rating": "up"})  # rated after a reload
    assert stored[1, "c1", 1].mode is None and metric("answer_feedback_total", mode="unknown", rating="up") >= 1


def test_changing_and_taking_back_a_rating(client, logged_in, stored):
    client.put("/feedback", json={**ANSWER, "rating": "down"})
    client.put("/feedback", json={**ANSWER, "rating": "up", "comment": ""})
    assert stored[1, "c1", 1].rating == 1 and stored[1, "c1", 1].comment is None
    client.put("/feedback", json={**ANSWER, "rating": None})
    assert stored == {}


@pytest.mark.parametrize("bad", [{"rating": "meh"}, {"position": -1}, {"mode": "other"}, {"requestId": "a b"},
                                 {"comment": "x" * 2001}])
def test_invalid_feedback_is_rejected(client, logged_in, stored, bad):
    assert client.put("/feedback", json={**ANSWER, "rating": "up", **bad}).status_code == 422
    assert stored == {}


def test_needs_a_login(client):
    assert client.put("/feedback", json={**ANSWER, "rating": "up"}).status_code == 401


def test_deleting_a_chat_deletes_its_feedback(client, logged_in, stored, monkeypatch):
    monkeypatch.setattr(chat_store, "delete", lambda user_id, chat_id: None)
    client.put("/feedback", json={**ANSWER, "rating": "up"})
    client.put("/feedback", json={**ANSWER, "chatId": "c2", "rating": "up"})
    assert client.delete("/chats/c1").status_code == 200
    assert list(stored) == [(1, "c2", 1)]


def test_saved_chat_keeps_the_rating_on_the_message(client, logged_in, monkeypatch):
    saved = {}
    monkeypatch.setattr(chat_store, "save", lambda *args: saved.setdefault("messages", args[-1]))
    body = {"title": "t", "updatedAt": 1, "messages": [
        {"role": "user", "content": "q"}, {"role": "assistant", "content": "a", "feedback": "up"}]}
    assert client.put("/chats/c1", json=body).status_code == 200
    assert [m.feedback for m in saved["messages"]] == [None, "up"]
    assert client.put("/chats/c1", json={**body, "messages": [{"role": "assistant", "content": "a",
                                                                "feedback": "meh"}]}).status_code == 422
