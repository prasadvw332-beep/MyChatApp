"""In-memory stand-ins for the backing services, so tests need no Docker, Postgres or Ollama.

FakeVectorStore implements the VectorStore protocol; the other two replace the module-level
functions of doc_store and storage that knowledge_base calls.
"""

from types import SimpleNamespace

from langchain_core.documents import Document


class FakeVectorStore:
    def __init__(self, hits: list[tuple[Document, float]] | None = None, fail_add: bool = False):
        self.chunks: dict[str, Document] = {}
        self.hits = hits or []
        self.fail_add = fail_add

    def add(self, docs, ids=None):
        if self.fail_add:
            raise ConnectionError("vector db down")
        for i, d in enumerate(docs):
            self.chunks[ids[i] if ids else f"{d.metadata['doc_id']}/{i}"] = d

    def search(self, query, k):
        return self.hits[:k]

    def delete_doc(self, doc_id):
        self.chunks = {k: d for k, d in self.chunks.items() if d.metadata["doc_id"] != doc_id}

    def doc_ids_for_source(self, filename):
        return {d.metadata["doc_id"] for d in self.chunks.values() if d.metadata["source"] == filename}

    def list_docs(self):
        return []

    def ping(self):
        pass


class FakeDocStore:
    """Mirrors doc_store: rows keyed by doc_id, unique content hash."""

    Duplicate = type("Duplicate", (Exception,), {})

    def __init__(self):
        self.rows: dict[str, SimpleNamespace] = {}

    def sha256(self, data):
        import hashlib
        return hashlib.sha256(data).hexdigest()

    def find_duplicate(self, digest):
        return next((r for r in self.rows.values() if r.sha256 == digest and r.status == "ready"), None)

    def create(self, doc_id, filename, data, digest, status="indexing", batch_id=None):
        if any(r.sha256 == digest for r in self.rows.values()):
            raise self.Duplicate()
        self.rows[doc_id] = SimpleNamespace(doc_id=doc_id, filename=filename, sha256=digest, status=status, chunks=0)

    def mark_ready(self, doc_id, chunks):
        self.rows[doc_id].status, self.rows[doc_id].chunks = "ready", chunks

    def delete(self, doc_id):
        self.rows.pop(doc_id, None)

    def ids_for_filename(self, filename):
        return {r.doc_id for r in self.rows.values() if r.filename == filename}


class FakeStorage:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def save(self, doc_id, filename, data):
        self.files[doc_id] = data

    def delete(self, doc_id):
        self.files.pop(doc_id, None)
