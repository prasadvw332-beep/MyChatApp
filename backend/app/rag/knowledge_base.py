"""RAG pipeline: chunk -> embed + store/search (clients/vector_store.py), built on LangChain.

Indexing:   extract + chunk -> (PII redaction) -> embed + store, with the document's ACL on every chunk
            -> (knowledge graph extraction)
Retrieval:  each search query (one, or several from query rewriting) -> vector or hybrid search,
            filtered by the user's ACL principals -> fused with RRF -> relevance gate -> (rerank)
            -> injection screening
The steps in brackets are off unless configured (docs/production-rag.md).
"""

import uuid

from langchain_core.documents import Document

from app.cache import semantic
from app.clients import storage
from app.clients.vector_store import VectorStore
from app.clients.vector_store import store as default_store
from app.core import metrics
from app.core.acl import PUBLIC
from app.core.config import MIN_SCORE, RERANK_CANDIDATES
from app.core.errors import ConflictError
from app.core.telemetry import span
from app.guardrails import injection, pii
from app.rag import extractors, fusion, graph, rerank
from app.rag.chunking import UnreadableDocumentError, chunk
from app.repositories import doc_store

TOP_K = 5

# the only vector-database dependency; it is an interface, so tests and other databases can replace it
store: VectorStore = default_store


class DuplicateDocumentError(ConflictError):
    """The same file content is already in the knowledge base."""

    code = "duplicate_document"


def ingest(filename: str, data: bytes, acl: list[str] | None = None) -> dict:
    """Index one uploaded file right away (the upload endpoint). Re-uploading a filename replaces it."""
    acl = acl or [PUBLIC]
    digest = doc_store.sha256(data)
    _reject_duplicate(digest)
    doc_id = uuid.uuid4().hex
    docs, ids = _chunks(doc_id, filename, data, acl)  # before any write: a file without text fails here
    old_ids = _versions_of(filename)
    _register(doc_id, filename, data, digest, acl)
    _save(doc_id, filename, data, docs, ids)
    doc_store.mark_ready(doc_id, len(docs))
    for old in old_ids - {doc_id}:
        delete_document(old)
    if graph.is_enabled():
        graph.index(doc_id, docs)
    semantic.forget_all()  # cached answers were written without this document
    return {"doc_id": doc_id, "filename": filename, "chunks": len(docs)}


def index(doc_id: str, filename: str, data: bytes, acl: list[str] | None = None) -> int:
    """Chunk, embed and store one queued document (used by the batch workers). Returns the chunk count.

    Safe to run twice for the same document: chunk ids are derived from (doc_id, position) and
    leftovers of an earlier, interrupted attempt are removed first, so a retry never duplicates.
    """
    docs, ids = _chunks(doc_id, filename, data, acl or [PUBLIC])
    store.delete_doc(doc_id)
    store.add(docs, ids=ids)
    if graph.is_enabled():
        graph.index(doc_id, docs)
    semantic.forget_all()
    return len(docs)


def set_acl(doc_id: str, acl: list[str]) -> bool:
    """Change who may see a document: its row and every chunk's payload. False if there's no such document."""
    if not doc_store.set_acl(doc_id, acl):
        return False
    store.set_acl(doc_id, acl)
    semantic.forget_all()  # a cached answer may quote a document someone just lost access to
    return True


# ---------- the steps of ingest/index, one job each ----------

def _reject_duplicate(digest: str) -> None:
    dup = doc_store.find_duplicate(digest)
    if dup:
        raise DuplicateDocumentError(f"This file is already in the knowledge base as '{dup.filename}'.")


def _chunks(doc_id: str, filename: str, data: bytes, acl: list[str]) -> tuple[list[Document], list[str]]:
    """Chunks tagged with their document and ACL, and stable ids derived from (doc_id, position)."""
    docs = chunk(filename, data)
    for d in docs:
        d.metadata["doc_id"] = doc_id
        d.metadata["acl"] = acl  # filtered on in every search when ACL_ENABLED
        if pii.at_index():  # before embedding: the vectors and the stored text never hold the data
            d.page_content = pii.redact(d.page_content)
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}/{i}")) for i in range(len(docs))]
    return docs, ids


def _versions_of(filename: str) -> set[str]:
    """Documents already stored under this filename, in either store (they can disagree after a crash)."""
    return store.doc_ids_for_source(filename) | doc_store.ids_for_filename(filename)


def _register(doc_id: str, filename: str, data: bytes, digest: str, acl: list[str]) -> None:
    """Metadata row first: its unique hash stops two simultaneous uploads of the same file."""
    try:
        doc_store.create(doc_id, filename, data, digest, acl=acl)
    except doc_store.DuplicateContentError:
        raise DuplicateDocumentError("This file is already in the knowledge base.") from None


def _save(doc_id: str, filename: str, data: bytes, docs: list[Document], ids: list[str]) -> None:
    """Original file and vectors, all or nothing: on failure, undo what was written."""
    try:
        storage.save(doc_id, filename, data)
        store.add(docs, ids=ids)
    except Exception:
        delete_document(doc_id)
        raise


def list_documents(principals: list[str] | None = None) -> list[dict]:
    return doc_store.list_all(principals)


def sync_documents() -> int:
    """One-off at startup: clean up crashed uploads, then record documents indexed before the metadata table existed."""
    _remove_crashed_uploads()
    return doc_store.backfill(store.list_docs(), storage)


def _remove_crashed_uploads() -> None:
    # left 'indexing' by a crash; would otherwise block re-uploading that file
    for doc_id in doc_store.stale_indexing():
        delete_document(doc_id)


def forget_cached(filename: str, data: bytes) -> None:
    """Remove the text the ingestion caches keep of a file: its PDF pages and its chunks' graph triples
    (personal data erasure). They are keyed by content, not document, so deleting the document leaves them.
    The embedding cache holds only vectors under a hash, no text. Raises if a cache can't be emptied."""
    if graph.is_enabled() and graph.cache:
        try:
            docs, _ = _chunks("", filename, data, [PUBLIC])  # the chunk text the triples were keyed by
        except UnreadableDocumentError:
            docs = []  # no text, so no chunks and no triples
        graph.forget(docs)
    extractors.forget(filename, data)


def delete_document(doc_id: str) -> None:
    store.delete_doc(doc_id)
    storage.delete(doc_id)
    doc_store.delete(doc_id)  # its graph relations go with the row (ON DELETE CASCADE)
    semantic.forget_all()  # cached answers may quote it


def _chunk_key(d: Document) -> tuple:
    return d.metadata.get("doc_id"), d.metadata.get("chunk"), d.page_content


def retrieve(question: str, k: int = TOP_K, principals: list[str] | None = None,
             queries: list[str] | None = None) -> list[Document]:
    """The chunks to answer `question` from, best first; [] when the documents don't cover it.

    principals  only chunks these may see (app/core/acl.py); None = no permission filter
    queries     what to search for, from query rewriting (app/rag/query.py); default: the question
    """
    queries = [q for q in (queries or [question]) if q.strip()] or [question]
    # with a reranker, fetch more candidates than needed: it picks the best k of them
    fetch = max(k, RERANK_CANDIDATES) if rerank.is_enabled() else k
    with span("rag.retrieve", k=k, queries=len(queries)) as s, metrics.retrieval_duration.time():
        rankings = [store.search(q, k=fetch, acl=principals) for q in queries]
        best = max((score for hits in rankings for _, score in hits), default=0.0)
        s.set_attribute("best_score", best)
    # gate on the best hit only: a per-chunk cutoff throws away relevant chunks
    # (e.g. the Education chunk for "university name") whose absolute score is modest
    if best < MIN_SCORE:
        metrics.retrieval_no_context.inc()
        return []
    docs = fusion.reciprocal_rank_fusion([[doc for doc, _ in hits] for hits in rankings], key=_chunk_key, limit=fetch)
    docs = rerank.rerank(question, docs, k)  # without a reranker: the first k
    # chunks are shared by all users: one carrying instructions must not reach anyone's prompt
    return injection.screen_documents(docs)
