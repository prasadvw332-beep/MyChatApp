"""RAG pipeline: chunk -> embed (Ollama) -> store/search (Qdrant), built on LangChain."""

import uuid

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    VectorParams,
)

from app.config import MIN_SCORE, QDRANT_COLLECTION as COLLECTION, QDRANT_URL
from app.services import doc_store, storage
from app.services.chunking import chunk
from app.services.llm import embeddings

TOP_K = 5

client = QdrantClient(url=QDRANT_URL)

# langchain-qdrant keeps our metadata under this payload key
META = "metadata"


def _ensure_collection() -> None:
    if not client.collection_exists(COLLECTION):
        size = len(embeddings.embed_query("dimension probe"))
        client.create_collection(
            COLLECTION,
            vectors_config=VectorParams(size=size, distance=Distance.COSINE),
        )


def _store() -> QdrantVectorStore:
    return QdrantVectorStore(
        client=client, collection_name=COLLECTION, embedding=embeddings
    )


def _source_filter(key: str, value: str) -> Filter:
    return Filter(must=[FieldCondition(key=f"{META}.{key}", match=MatchValue(value=value))])


class DuplicateDocument(Exception):
    """The same file content is already in the knowledge base."""


def ingest(filename: str, data: bytes) -> dict:
    digest = doc_store.sha256(data)
    dup = doc_store.find_duplicate(digest)
    if dup:
        raise DuplicateDocument(f"This file is already in the knowledge base as '{dup.filename}'.")
    docs = chunk(filename, data)
    doc_id = uuid.uuid4().hex
    for d in docs:
        d.metadata["doc_id"] = doc_id

    _ensure_collection()
    old_ids = _doc_ids_for(filename) | doc_store.ids_for_filename(filename)
    doc_store.create(doc_id, filename, data, digest)
    try:
        storage.save(doc_id, filename, data)
        _store().add_documents(docs)
    except Exception:
        storage.delete(doc_id)
        doc_store.delete(doc_id)
        raise
    doc_store.mark_ready(doc_id, len(docs))
    # re-uploading the same file replaces the old version
    for old in old_ids - {doc_id}:
        delete_document(old)
    return {"doc_id": doc_id, "filename": filename, "chunks": len(docs)}


def _doc_ids_for(filename: str) -> set[str]:
    ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION,
            scroll_filter=_source_filter("source", filename),
            limit=256,
            offset=offset,
            with_payload=[META],
            with_vectors=False,
        )
        ids.update((p.payload or {}).get(META, {}).get("doc_id") for p in points)
        if offset is None:
            break
    ids.discard(None)
    return ids


def list_documents() -> list[dict]:
    return doc_store.list_all()


def sync_documents() -> int:
    """One-off at startup: record documents that were indexed before the metadata table existed."""
    return doc_store.backfill(_list_from_qdrant(), storage)


def _list_from_qdrant() -> list[dict]:
    if not client.collection_exists(COLLECTION):
        return []
    found: dict[str, dict] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION, limit=256, offset=offset, with_payload=[META], with_vectors=False
        )
        for p in points:
            m = (p.payload or {}).get(META, {})
            d = found.setdefault(
                m.get("doc_id", "?"),
                {"doc_id": m.get("doc_id", "?"), "filename": m.get("source", "?"), "chunks": 0},
            )
            d["chunks"] += 1
        if offset is None:
            break
    return sorted(found.values(), key=lambda d: d["filename"].lower())


def delete_document(doc_id: str) -> None:
    if client.collection_exists(COLLECTION):
        client.delete(COLLECTION, points_selector=_source_filter("doc_id", doc_id))
    storage.delete(doc_id)
    doc_store.delete(doc_id)


def retrieve(question: str, k: int = TOP_K) -> list[Document]:
    if not client.collection_exists(COLLECTION):
        return []
    if client.count(COLLECTION).count == 0:
        return []
    hits = _store().similarity_search_with_score(question, k=k)
    # gate on the best hit only: a per-chunk cutoff throws away relevant chunks
    # (e.g. the Education chunk for "university name") whose absolute score is modest
    if not hits or hits[0][1] < MIN_SCORE:
        return []
    return [doc for doc, _ in hits]
