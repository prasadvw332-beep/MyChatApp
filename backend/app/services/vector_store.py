"""Chunk vectors: the interface the RAG code depends on, and its Qdrant implementation.

knowledge_base only knows `VectorStore`. Swapping Qdrant for another database (pgvector, Chroma, ...)
means writing one more class with these methods and changing the `store = ...` line at the bottom;
the RAG logic stays untouched. Tests can assign a fake: `knowledge_base.store = FakeStore()`.
"""

from typing import Protocol

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    VectorParams,
)

from app.core.config import EMBED_BATCH_SIZE, QDRANT_COLLECTION, QDRANT_TIMEOUT, QDRANT_URL
from app.services.llm import embeddings


class VectorStore(Protocol):
    """Chunks carry `doc_id` and `source` (filename) in their metadata."""

    def add(self, docs: list[Document], ids: list[str] | None = None) -> None: ...

    def search(self, query: str, k: int) -> list[tuple[Document, float]]:
        """Most similar chunks first, with their similarity score (higher = closer)."""
        ...

    def delete_doc(self, doc_id: str) -> None: ...

    def doc_ids_for_source(self, filename: str) -> set[str]: ...

    def list_docs(self) -> list[dict]:
        """One {doc_id, filename, chunks} per stored document, sorted by filename."""
        ...

    def ping(self) -> None:
        """Raise if the database can't be reached (used by the readiness check)."""
        ...


# langchain-qdrant keeps our metadata under this payload key
META = "metadata"


def _filter(key: str, value: str) -> Filter:
    return Filter(must=[FieldCondition(key=f"{META}.{key}", match=MatchValue(value=value))])


class QdrantStore:
    def __init__(self, url: str, collection: str, embedder: Embeddings, timeout: int = 30):
        self.client = QdrantClient(url=url, timeout=timeout)
        self.collection = collection
        self.embedder = embedder

    def ping(self) -> None:
        self.client.get_collections()

    def _exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def _ensure_collection(self) -> None:
        if not self._exists():
            size = len(self.embedder.embed_query("dimension probe"))
            try:
                self.client.create_collection(
                    self.collection,
                    vectors_config=VectorParams(size=size, distance=Distance.COSINE),
                )
            except Exception:
                if not self._exists():  # else another worker created it first
                    raise
        # deleting/re-indexing one document filters on doc_id; without an index that scans every chunk
        self.client.create_payload_index(self.collection, f"{META}.doc_id", field_schema=PayloadSchemaType.KEYWORD)

    def _langchain(self) -> QdrantVectorStore:
        return QdrantVectorStore(client=self.client, collection_name=self.collection, embedding=self.embedder)

    def _scroll(self, scroll_filter: Filter | None = None):
        """All points' metadata, 256 per request."""
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                scroll_filter=scroll_filter,
                limit=256,
                offset=offset,
                with_payload=[META],
                with_vectors=False,
            )
            for p in points:
                yield (p.payload or {}).get(META, {})
            if offset is None:
                break

    def add(self, docs: list[Document], ids: list[str] | None = None) -> None:
        self._ensure_collection()
        # one embedding request per EMBED_BATCH_SIZE chunks instead of one per chunk
        self._langchain().add_documents(docs, ids=ids, batch_size=EMBED_BATCH_SIZE)

    def search(self, query: str, k: int) -> list[tuple[Document, float]]:
        if not self._exists() or self.client.count(self.collection).count == 0:
            return []
        return self._langchain().similarity_search_with_score(query, k=k)

    def delete_doc(self, doc_id: str) -> None:
        if self._exists():
            self.client.delete(self.collection, points_selector=_filter("doc_id", doc_id))

    def doc_ids_for_source(self, filename: str) -> set[str]:
        if not self._exists():
            return set()
        ids = {m.get("doc_id") for m in self._scroll(_filter("source", filename))}
        ids.discard(None)
        return ids

    def list_docs(self) -> list[dict]:
        if not self._exists():
            return []
        found: dict[str, dict] = {}
        for m in self._scroll():
            d = found.setdefault(
                m.get("doc_id", "?"),
                {"doc_id": m.get("doc_id", "?"), "filename": m.get("source", "?"), "chunks": 0},
            )
            d["chunks"] += 1
        return sorted(found.values(), key=lambda d: d["filename"].lower())


store: VectorStore = QdrantStore(QDRANT_URL, QDRANT_COLLECTION, embeddings, QDRANT_TIMEOUT)
