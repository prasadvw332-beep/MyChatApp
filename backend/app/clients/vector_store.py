"""Chunk vectors: the interface the RAG code depends on, and its Qdrant implementations.

knowledge_base only knows `VectorStore`. Swapping Qdrant for another database (pgvector, Chroma, ...)
means writing one more class with these methods and changing the `store = ...` line at the bottom;
the RAG logic stays untouched. Tests can assign a fake: `knowledge_base.store = FakeStore()`.

Two Qdrant stores:
    QdrantStore         dense vectors only (embeddings), through langchain-qdrant
    HybridQdrantStore   dense + BM25 sparse vectors in one collection, both searched and fused
                        with RRF (HYBRID_SEARCH=true; its own collection, so documents need re-indexing)
"""

import uuid
from typing import Protocol

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.clients.llm import embeddings
from app.core.config import EMBED_BATCH_SIZE, HYBRID_SEARCH, QDRANT_COLLECTION, QDRANT_TIMEOUT, QDRANT_URL
from app.rag import fusion, sparse


class VectorStore(Protocol):
    """Chunks carry `doc_id`, `source` (filename) and, with permissions on, `acl` in their metadata."""

    def add(self, docs: list[Document], ids: list[str] | None = None) -> None: ...

    def search(self, query: str, k: int, acl: list[str] | None = None) -> list[tuple[Document, float]]:
        """Most similar chunks first, with their similarity score (higher = closer). With `acl`, only
        chunks whose acl shares a principal with it."""
        ...

    def set_acl(self, doc_id: str, acl: list[str]) -> None: ...

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


def _acl_filter(acl: list[str] | None) -> Filter | None:
    """Chunks visible to any of these principals. Chunks indexed before ACLs existed have no acl field:
    they don't match, so turn ACLs on only after re-indexing (or they disappear from search)."""
    if acl is None:
        return None
    return Filter(must=[FieldCondition(key=f"{META}.acl", match=MatchAny(any=acl))])


class QdrantStore:
    def __init__(self, url: str, collection: str, embedder: Embeddings, timeout: int = 30):
        self.client = QdrantClient(url=url, timeout=timeout)
        self.collection = collection
        self.embedder = embedder

    def ping(self) -> None:
        self.client.get_collections()

    def _exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def _create(self, size: int) -> None:
        self.client.create_collection(self.collection, vectors_config=VectorParams(size=size, distance=Distance.COSINE))

    def _ensure_collection(self) -> None:
        if not self._exists():
            size = len(self.embedder.embed_query("dimension probe"))
            try:
                self._create(size)
            except Exception:
                if not self._exists():  # else another worker created it first
                    raise
        # deleting/re-indexing one document filters on doc_id; without an index that scans every chunk
        self.client.create_payload_index(self.collection, f"{META}.doc_id", field_schema=PayloadSchemaType.KEYWORD)
        # every search filters on acl when permissions are on
        self.client.create_payload_index(self.collection, f"{META}.acl", field_schema=PayloadSchemaType.KEYWORD)

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

    def _empty(self) -> bool:
        return not self._exists() or self.client.count(self.collection).count == 0

    def add(self, docs: list[Document], ids: list[str] | None = None) -> None:
        self._ensure_collection()
        # one embedding request per EMBED_BATCH_SIZE chunks instead of one per chunk
        self._langchain().add_documents(docs, ids=ids, batch_size=EMBED_BATCH_SIZE)

    def search(self, query: str, k: int, acl: list[str] | None = None) -> list[tuple[Document, float]]:
        if self._empty():
            return []
        return self._langchain().similarity_search_with_score(query, k=k, filter=_acl_filter(acl))

    def set_acl(self, doc_id: str, acl: list[str]) -> None:
        """New permissions for every chunk of a document: a payload update, no re-embedding."""
        if self._exists():
            self.client.set_payload(self.collection, payload={"acl": acl}, key=META,
                                    points=_filter("doc_id", doc_id))

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


DENSE, SPARSE = "dense", "bm25"


class HybridQdrantStore(QdrantStore):
    """Each point has two named vectors: the embedding ("dense") and BM25 weights ("bm25", sparse.py).

    A search runs both and fuses the two rankings in Python with RRF (fusion.py), instead of
    Qdrant's built-in fusion (query_points with prefetch + FusionQuery), to show the steps and to
    keep the dense cosine score: retrieve() gates on it ("is the question about the documents at
    all?"), and an RRF score can't answer that.

    Points use the same payload layout as langchain-qdrant ({"page_content", "metadata"}), so
    listing, deleting and set_acl are inherited unchanged.
    """

    def _create(self, size: int) -> None:
        self.client.create_collection(
            self.collection,
            vectors_config={DENSE: VectorParams(size=size, distance=Distance.COSINE)},
            # Qdrant multiplies each query token by its inverse document frequency at search time
            sparse_vectors_config={SPARSE: SparseVectorParams(modifier=Modifier.IDF)},
        )

    def add(self, docs: list[Document], ids: list[str] | None = None) -> None:
        self._ensure_collection()
        ids = ids or [str(uuid.uuid4()) for _ in docs]
        for start in range(0, len(docs), EMBED_BATCH_SIZE):
            batch = docs[start:start + EMBED_BATCH_SIZE]
            dense = self.embedder.embed_documents([d.page_content for d in batch])
            points = []
            for doc, point_id, vector in zip(batch, ids[start:], dense, strict=False):
                indices, values = sparse.document_vector(doc.page_content)
                points.append(PointStruct(
                    id=point_id,
                    vector={DENSE: vector, SPARSE: SparseVector(indices=indices, values=values)},
                    payload={"page_content": doc.page_content, META: doc.metadata},
                ))
            self.client.upsert(self.collection, points=points)

    def search(self, query: str, k: int, acl: list[str] | None = None) -> list[tuple[Document, float]]:
        if self._empty():
            return []
        only = _acl_filter(acl)
        wide = k * 4  # each side fetches more than k, so fusion has something to choose from
        dense_hits = self.client.query_points(
            self.collection, query=self.embedder.embed_query(query), using=DENSE,
            query_filter=only, limit=wide, with_payload=True,
        ).points
        indices, values = sparse.query_vector(query)
        keyword_hits = self.client.query_points(
            self.collection, query=SparseVector(indices=indices, values=values), using=SPARSE,
            query_filter=only, limit=wide, with_payload=True,
        ).points if indices else []

        cosine = {p.id: p.score for p in dense_hits}
        fused = fusion.rrf([dense_hits, keyword_hits], key=lambda p: p.id, limit=k)
        # a keyword-only hit has no cosine score: 0, so it never lifts the "is this relevant" gate
        return [(Document(p.payload.get("page_content", ""), metadata=p.payload.get(META, {})), cosine.get(p.id, 0.0))
                for p in fused]


store: VectorStore = (
    HybridQdrantStore(QDRANT_URL, f"{QDRANT_COLLECTION}_hybrid", embeddings, QDRANT_TIMEOUT) if HYBRID_SEARCH
    else QdrantStore(QDRANT_URL, QDRANT_COLLECTION, embeddings, QDRANT_TIMEOUT)
)
