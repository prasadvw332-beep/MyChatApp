"""The RAG rules in knowledge_base, with every backing service faked."""

import pytest
from langchain_core.documents import Document

from app.rag import knowledge_base as kb
from app.rag.chunking import UnreadableDocument
from tests.fakes import FakeDocStore, FakeStorage, FakeVectorStore

TEXT = b"# Notes\nThe cobalt giraffe sings opera in Lima on Tuesdays."


@pytest.fixture
def env(monkeypatch):
    fakes = {"store": FakeVectorStore(), "doc_store": FakeDocStore(), "storage": FakeStorage()}
    for name, fake in fakes.items():
        monkeypatch.setattr(kb, name, fake)
    return fakes


def test_ingest_writes_metadata_file_and_vectors(env):
    result = kb.ingest("notes.md", TEXT)

    doc_id = result["doc_id"]
    assert result["chunks"] == 1
    assert env["doc_store"].rows[doc_id].status == "ready"
    assert env["storage"].files[doc_id] == TEXT
    assert env["store"].doc_ids_for_source("notes.md") == {doc_id}


def test_same_content_is_rejected(env):
    kb.ingest("notes.md", TEXT)
    with pytest.raises(kb.DuplicateDocument) as e:
        kb.ingest("copy.md", TEXT)
    assert e.value.status == 409


def test_reupload_replaces_previous_version(env):
    old = kb.ingest("notes.md", TEXT)["doc_id"]
    new = kb.ingest("notes.md", TEXT + b" Updated.")["doc_id"]

    assert kb._versions_of("notes.md") == {new}
    assert old not in env["storage"].files


def test_failed_save_leaves_nothing_behind(env, monkeypatch):
    monkeypatch.setattr(kb, "store", FakeVectorStore(fail_add=True))
    with pytest.raises(ConnectionError):
        kb.ingest("notes.md", TEXT)

    assert env["doc_store"].rows == {}
    assert env["storage"].files == {}


def test_unreadable_file_fails_before_any_write(env):
    with pytest.raises(UnreadableDocument) as e:
        kb.ingest("empty.txt", b"   ")
    assert e.value.status == 422
    assert env["doc_store"].rows == {} and env["storage"].files == {}


def test_index_is_idempotent(env):
    kb.index("doc1", "notes.md", TEXT)
    kb.index("doc1", "notes.md", TEXT)  # a retried worker job
    assert len(env["store"].chunks) == 1


@pytest.mark.parametrize(("best_score", "expected"), [(0.9, 2), (kb.MIN_SCORE - 0.01, 0)])
def test_retrieve_gates_on_best_hit_only(monkeypatch, best_score, expected):
    hits = [(Document(page_content="a"), best_score), (Document(page_content="b"), 0.1)]
    monkeypatch.setattr(kb, "store", FakeVectorStore(hits=hits))
    assert len(kb.retrieve("question")) == expected
