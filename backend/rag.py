"""RAG pipeline: load -> chunk -> embed (Ollama) -> store/search (Qdrant), built on LangChain.

Chunking strategy (structure-aware + contextual headers):
  1. Join all pages into one text, so a section that crosses a page break stays together.
  2. Split the text into sections at headings (Markdown `#` headings or short ALL-CAPS lines
     such as "EDUCATION"; letter-spaced PDF headings like "E D U C AT I O N" are repaired).
  3. Split each section on its own with a recursive splitter (paragraph -> line -> sentence),
     so a chunk never mixes two sections.
  4. Prefix every chunk with "Document: <title> / Section: <heading>" before embedding, so a
     question like "where did he study?" matches the Education chunk even though that word
     isn't in the chunk body.
"""

import bisect
import io
import os
import re
import uuid

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    VectorParams,
)

import storage

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:1b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6335")
# v2: new chunk format + nomic task prefixes, so vectors aren't comparable with the old collection
COLLECTION = os.getenv("QDRANT_COLLECTION", "documents_v2")

SUPPORTED = {".pdf", ".txt", ".md"}
TOP_K = 5
MIN_CHUNK = 250  # chars; smaller trailing pieces are merged into the previous chunk
# If even the best chunk scores below this, the question isn't about the documents.
# Measured with nomic-embed-text + prefixes: related questions >= ~0.60, unrelated <= ~0.57.
MIN_SCORE = float(os.getenv("MIN_SCORE", "0.58"))


class PrefixedEmbeddings(Embeddings):
    """nomic-embed-text is trained with task prefixes; retrieval is noticeably worse without them."""

    def __init__(self, inner: Embeddings, doc_prefix: str, query_prefix: str):
        self.inner = inner
        self.doc_prefix = doc_prefix
        self.query_prefix = query_prefix

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents([self.doc_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(self.query_prefix + text)


_base_embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_URL)
embeddings: Embeddings = (
    PrefixedEmbeddings(_base_embeddings, "search_document: ", "search_query: ")
    if "nomic" in EMBED_MODEL
    else _base_embeddings
)
llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0.2)
# short, deterministic output for chat titles; the cap stops a runaway answer from blocking Ollama
title_llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0, num_predict=20)
client = QdrantClient(url=QDRANT_URL)
splitter = RecursiveCharacterTextSplitter(
    chunk_size=800, chunk_overlap=100, add_start_index=True
)

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


def _extract(filename: str, data: bytes) -> list[tuple[int | None, str]]:
    """Return (page_number, text) pairs. page_number is None for plain text."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    return [(None, data.decode("utf-8", errors="replace"))]


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


def _unspace(line: str) -> str:
    """Repair letter-spaced PDF headings: 'W O R K  E X P E R I E N C E' -> 'WORK EXPERIENCE'."""
    tokens = line.split()
    if len(tokens) >= 4 and sum(len(t) <= 2 for t in tokens) >= 0.7 * len(tokens):
        return " ".join(w.replace(" ", "") for w in re.split(r"\s{2,}", line.strip()))
    return line


def _heading(line: str) -> tuple[int, str] | None:
    """Return (level, title) if the line looks like a heading."""
    s = line.strip()
    m = _MD_HEADING.match(s)
    if m:
        return len(m.group(1)), m.group(2).strip()
    s = s.rstrip(":").strip()
    if (
        3 <= len(s) <= 60
        and sum(c.isalpha() for c in s) >= 3
        and s.upper() == s
        and len(s.split()) <= 6
        and not s.endswith((".", ",", ";"))
    ):
        return 1, s
    return None


def _sections(text: str) -> list[tuple[str, int, str]]:
    """Split text at headings -> [(heading path, start offset, body)]."""
    sections: list[tuple[str, int, str]] = []
    stack: list[str] = []
    buf: list[str] = []
    start = offset = 0

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append((" > ".join(stack), start, body))

    for raw in text.splitlines(keepends=True):
        line = _unspace(raw.rstrip("\r\n"))
        h = _heading(line)
        if h:
            flush()
            buf = []
            level, title = h
            del stack[level - 1 :]
            stack.append(title)
        else:
            if not buf:
                start = offset
            buf.append(line)
        offset += len(raw)
    flush()
    return sections


def _source_filter(key: str, value: str) -> Filter:
    return Filter(must=[FieldCondition(key=f"{META}.{key}", match=MatchValue(value=value))])


def chunk(filename: str, data: bytes) -> list[Document]:
    pages = [(p, t) for p, t in _extract(filename, data) if t.strip()]
    if not pages:
        raise ValueError("No readable text found in this file (scanned PDFs need OCR).")

    # one continuous text, remembering where each page starts
    full, page_starts, page_nums = "", [], []
    for p, t in pages:
        page_starts.append(len(full))
        page_nums.append(p)
        full += t.rstrip() + "\n"

    title = re.sub(r"[_\-]+", " ", os.path.splitext(filename)[0]).strip()
    docs: list[Document] = []
    for section, start, body in _sections(full):
        pieces = splitter.create_documents([body])
        # fold a tiny last piece into the previous one, so it doesn't become a header-only chunk
        if len(pieces) > 1 and len(pieces[-1].page_content) < MIN_CHUNK:
            tail, prev = pieces.pop().page_content, pieces[-1]
            overlap = next(
                (k for k in range(min(len(prev.page_content), len(tail)), 0, -1)
                 if prev.page_content.endswith(tail[:k])),
                0,
            )
            prev.page_content += ("" if overlap else "\n") + tail[overlap:]
        for piece in pieces:
            pos = start + max(piece.metadata.get("start_index", 0), 0)
            page = page_nums[bisect.bisect_right(page_starts, pos) - 1]
            header = f"Document: {title}" + (f"\nSection: {section}" if section else "")
            meta = {"source": filename, "section": section, "chunk": len(docs)}
            if page:
                meta["page"] = page
            docs.append(Document(page_content=f"{header}\n\n{piece.page_content}", metadata=meta))
    return docs


def ingest(filename: str, data: bytes) -> dict:
    docs = chunk(filename, data)
    doc_id = uuid.uuid4().hex
    for d in docs:
        d.metadata["doc_id"] = doc_id

    _ensure_collection()
    old_ids = _doc_ids_for(filename)
    storage.save(doc_id, filename, data)
    try:
        _store().add_documents(docs)
    except Exception:
        storage.delete(doc_id)
        raise
    # re-uploading the same file replaces the old version
    for old in old_ids:
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
