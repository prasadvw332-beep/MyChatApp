"""RAG pipeline: chunk -> embed + store/search (services/vector_store.py), built on LangChain."""

import uuid

from langchain_core.documents import Document

from app.core.config import MIN_SCORE
from app.rag.chunking import chunk
from app.services import doc_store, storage
from app.services.vector_store import VectorStore, store as default_store

TOP_K = 5

# the only vector-database dependency; it is an interface, so tests and other databases can replace it
store: VectorStore = default_store


class DuplicateDocument(Exception):
    """The same file content is already in the knowledge base."""


def ingest(filename: str, data: bytes) -> dict:
    """Index one uploaded file right away (the upload endpoint). Re-uploading a filename replaces it."""
    digest = doc_store.sha256(data)
    _reject_duplicate(digest)
    doc_id = uuid.uuid4().hex
    docs, ids = _chunks(doc_id, filename, data)  # before any write: a file without text fails here
    old_ids = _versions_of(filename)
    _register(doc_id, filename, data, digest)
    _save(doc_id, filename, data, docs, ids)
    doc_store.mark_ready(doc_id, len(docs))
    for old in old_ids - {doc_id}:
        delete_document(old)
    return {"doc_id": doc_id, "filename": filename, "chunks": len(docs)}


def index(doc_id: str, filename: str, data: bytes) -> int:
    """Chunk, embed and store one queued document (used by the batch workers). Returns the chunk count.

    Safe to run twice for the same document: chunk ids are derived from (doc_id, position) and
    leftovers of an earlier, interrupted attempt are removed first, so a retry never duplicates.
    """
    docs, ids = _chunks(doc_id, filename, data)
    store.delete_doc(doc_id)
    store.add(docs, ids=ids)
    return len(docs)


# ---------- the steps of ingest/index, one job each ----------

def _reject_duplicate(digest: str) -> None:
    dup = doc_store.find_duplicate(digest)
    if dup:
        raise DuplicateDocument(f"This file is already in the knowledge base as '{dup.filename}'.")


def _chunks(doc_id: str, filename: str, data: bytes) -> tuple[list[Document], list[str]]:
    """Chunks tagged with their document, and stable ids derived from (doc_id, position)."""
    docs = chunk(filename, data)
    for d in docs:
        d.metadata["doc_id"] = doc_id
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}/{i}")) for i in range(len(docs))]
    return docs, ids


def _versions_of(filename: str) -> set[str]:
    """Documents already stored under this filename, in either store (they can disagree after a crash)."""
    return store.doc_ids_for_source(filename) | doc_store.ids_for_filename(filename)


def _register(doc_id: str, filename: str, data: bytes, digest: str) -> None:
    """Metadata row first: its unique hash stops two simultaneous uploads of the same file."""
    try:
        doc_store.create(doc_id, filename, data, digest)
    except doc_store.Duplicate:
        raise DuplicateDocument("This file is already in the knowledge base.") from None


def _save(doc_id: str, filename: str, data: bytes, docs: list[Document], ids: list[str]) -> None:
    """Original file and vectors, all or nothing: on failure, undo what was written."""
    try:
        storage.save(doc_id, filename, data)
        store.add(docs, ids=ids)
    except Exception:
        delete_document(doc_id)
        raise


def list_documents() -> list[dict]:
    return doc_store.list_all()


def sync_documents() -> int:
    """One-off at startup: clean up crashed uploads, then record documents indexed before the metadata table existed."""
    _remove_crashed_uploads()
    return doc_store.backfill(store.list_docs(), storage)


def _remove_crashed_uploads() -> None:
    # left 'indexing' by a crash; would otherwise block re-uploading that file
    for doc_id in doc_store.stale_indexing():
        delete_document(doc_id)


def delete_document(doc_id: str) -> None:
    store.delete_doc(doc_id)
    storage.delete(doc_id)
    doc_store.delete(doc_id)


def retrieve(question: str, k: int = TOP_K) -> list[Document]:
    hits = store.search(question, k=k)
    # gate on the best hit only: a per-chunk cutoff throws away relevant chunks
    # (e.g. the Education chunk for "university name") whose absolute score is modest
    if not hits or hits[0][1] < MIN_SCORE:
        return []
    return [doc for doc, _ in hits]
