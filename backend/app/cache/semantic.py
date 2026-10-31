"""Semantic answer cache: a question close enough to one already answered gets the stored answer, without
retrieval or the chat model.

What is cached, and when it is not:
    - first questions only (a chat with one message). A follow-up ("and his email?") means something
      different in every chat, so its answer is never stored or looked up.
    - answers that streamed to the end and passed the output guardrails; never an error or a withheld one.
    - per chat model: switching CHAT_MODEL starts from an empty cache.

Answers are shared by all users, like the documents they were written from (clients/answer_store.py).
They go stale when the documents change, so every upload, re-index and delete calls `forget_all()`. A
lookup only accepts answers to questions asked after the last clear: an answer still being written from
the old documents when a file arrived is stored, but never served. Answers also expire after
SEMANTIC_CACHE_TTL_SECONDS.

The module-level functions are what the app calls. They never raise: a cache failure is logged and
counted, and the request goes ahead as if nothing was cached.
"""

import logging
import time
from dataclasses import dataclass

from langchain_core.embeddings import Embeddings

from app.clients import answer_store
from app.clients.answer_store import AnswerStore
from app.clients.llm import embeddings
from app.core import metrics
from app.core.config import CHAT_MODEL, SEMANTIC_CACHE, SEMANTIC_CACHE_THRESHOLD, SEMANTIC_CACHE_TTL_SECONDS
from app.schemas.chat import Message

log = logging.getLogger(__name__)


@dataclass
class Hit:
    answer: str
    sources: list[dict]
    question: str  # the question the answer was written for
    score: float


class SemanticCache:
    def __init__(self, store: AnswerStore, embedder: Embeddings, model: str, threshold: float, ttl_seconds: int):
        self.store = store
        self.embedder = embedder
        self.model = model
        self.threshold = threshold
        self.ttl = ttl_seconds

    def lookup(self, question: str) -> Hit | None:
        not_before = max(time.time() - self.ttl, self.store.cleared_at())
        found = self.store.search(self.embedder.embed_query(question), self.model, not_before, self.threshold)
        if not found:
            return None
        answer, score = found
        return Hit(answer["answer"], answer["sources"], answer["question"], score)

    def store_answer(self, question: str, answer: str, sources: list[dict], asked_at: float) -> None:
        """`asked_at` is when the documents were read, not now: that is what a clear is compared with."""
        self.store.put(f"{self.model}\n{question}", self.embedder.embed_query(question), {
            "model": self.model, "question": question, "answer": answer, "sources": sources, "asked_at": asked_at})

    def clear(self) -> None:
        self.store.clear(time.time(), self.embedder.embed_query("dimension probe"))


cache: SemanticCache | None = SemanticCache(
    answer_store.qdrant(), embeddings, CHAT_MODEL, SEMANTIC_CACHE_THRESHOLD, SEMANTIC_CACHE_TTL_SECONDS,
) if SEMANTIC_CACHE else None


def cacheable(messages: list[Message]) -> bool:
    """Only a chat's first question: later ones depend on the conversation before them."""
    return cache is not None and len(messages) == 1 and messages[0].role == "user"


def lookup(question: str) -> Hit | None:
    if cache is None:
        return None
    try:
        hit = cache.lookup(question)
    except Exception as e:
        metrics.cache_errors.labels("semantic").inc()
        log.warning("semantic cache lookup failed: %s", e)
        return None
    metrics.semantic_cache.labels("hit" if hit else "miss").inc()
    if hit:
        log.info("semantic cache hit (score %.3f)", hit.score)
    return hit


def remember(question: str, answer: str, sources: list[dict], asked_at: float) -> None:
    if cache is None or not answer.strip():
        return
    try:
        cache.store_answer(question, answer, sources, asked_at)
    except Exception as e:
        metrics.cache_errors.labels("semantic").inc()
        log.warning("semantic cache write failed: %s", e)


def forget_all() -> None:
    """The documents changed: no stored answer can be trusted any more."""
    if cache is None:
        return
    try:
        cache.clear()
    except Exception as e:
        # stale answers stay servable until they expire; loud, because that is a correctness problem
        metrics.cache_errors.labels("semantic").inc()
        log.error("semantic cache could not be cleared after a document change: %s", e)
