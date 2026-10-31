"""Embedding cache: the same text always gets the same vector, so the model computes it once.

Two tiers, checked in order:
    memory      an LRU in each process: a question asked again, or embedded a second time in the same
                request (semantic cache lookup, then retrieval), costs nothing
    PostgreSQL  the embedding_cache table, shared by every backend replica and ingestion worker and kept
                across restarts: re-indexing a document (a worker retry, an edited file uploaded again)
                only embeds the chunks whose text changed

Keys are sha256(model + text), where the text is what the model really sees: this class sits under
PrefixedEmbeddings, so the task prefix is included and a query never collides with a document of the
same words. Another EMBED_MODEL means other keys. The cache never fails a call: if PostgreSQL is down,
that tier is skipped and the model is asked.
"""

import hashlib
import logging
import threading
from array import array
from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol

from langchain_core.embeddings import Embeddings

from app.core import metrics

log = logging.getLogger(__name__)


class VectorRows(Protocol):
    """The persistent tier: repositories/embedding_cache_store.py, or a dict in tests."""

    def get_many(self, keys: list[str]) -> dict[str, list[float]]: ...

    def put_many(self, model: str, vectors: dict[str, list[float]]) -> None: ...


class LRU:
    """Thread-safe: the API embeds from worker threads (asyncio.to_thread).

    Vectors are kept as packed doubles: 6 KB for 768 dimensions, where a list of Python floats takes ~25 KB.
    """

    def __init__(self, size: int):
        self.size = size
        self.items: OrderedDict[str, array] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str) -> list[float] | None:
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key].tolist()
            return None

    def put(self, key: str, vector: list[float]) -> None:
        with self.lock:
            self.items[key] = array("d", vector)
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


class CachedEmbeddings(Embeddings):
    def __init__(self, inner: Embeddings, model: str, memory_items: int, rows: VectorRows | None = None):
        self.inner = inner
        self.model = model
        self.memory = LRU(memory_items)
        self.rows = rows

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self.inner.embed_documents)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], lambda texts: [self.inner.embed_query(texts[0])])[0]

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode()).hexdigest()

    def _embed(self, texts: list[str], compute: Callable[[list[str]], list[list[float]]]) -> list[list[float]]:
        keys = [self.key(t) for t in texts]
        text_of = dict(zip(keys, texts, strict=True))  # also drops duplicates within the batch
        found = {k: v for k in text_of if (v := self.memory.get(k)) is not None}
        metrics.embedding_cache.labels("memory").inc(len(found))

        missing = [k for k in text_of if k not in found]
        if missing and self.rows:
            stored = self._load(missing)
            metrics.embedding_cache.labels("database").inc(len(stored))
            for k, v in stored.items():
                self.memory.put(k, v)
            found |= stored
            missing = [k for k in missing if k not in stored]

        if missing:
            metrics.embedding_cache.labels("miss").inc(len(missing))
            fresh = dict(zip(missing, compute([text_of[k] for k in missing]), strict=True))
            for k, v in fresh.items():
                self.memory.put(k, v)
            found |= fresh
            self._save(fresh)
        return [found[k] for k in keys]

    def _load(self, keys: list[str]) -> dict[str, list[float]]:
        try:
            return self.rows.get_many(keys)
        except Exception as e:
            metrics.cache_errors.labels("embedding").inc()
            log.warning("embedding cache read failed, asking the model: %s", e)
            return {}

    def _save(self, vectors: dict[str, list[float]]) -> None:
        if not self.rows:
            return
        try:
            self.rows.put_many(self.model, vectors)
        except Exception as e:
            metrics.cache_errors.labels("embedding").inc()
            log.warning("embedding cache write failed: %s", e)
