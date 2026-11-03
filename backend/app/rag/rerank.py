"""Reranking: reorder the retrieved chunks with a model that reads question and chunk together.

Retrieval is fast because the question and each chunk are embedded separately (a bi-encoder):
the chunk vectors are computed once, at index time. That's also its weakness: the model never
sees the two texts side by side. A cross-encoder does: it scores each (question, chunk) pair
in one pass. Far more accurate, but too slow to run on the whole collection, so production
pipelines do both:

    vector/hybrid search: RERANK_CANDIDATES (20) candidates, cheap  ->  reranker: best TOP_K (5)

RERANK=cross_encoder   a local model (sentence-transformers; BAAI/bge-reranker-base by default)
RERANK=api             a hosted /rerank endpoint (Cohere, Jina and Voyage share this request shape)
"""

import logging
from functools import cache

import httpx
from langchain_core.documents import Document

from app.core.config import RERANK, RERANK_API_KEY, RERANK_API_URL, RERANK_MODEL
from app.core.telemetry import span

log = logging.getLogger(__name__)


def enabled() -> bool:
    return RERANK != "off"


def rerank(question: str, docs: list[Document], top_k: int) -> list[Document]:
    """The best `top_k` of `docs` for `question`. On a reranker failure: the original order (logged),
    because a worse ranking beats no answer."""
    if not enabled() or len(docs) <= 1:
        return docs[:top_k]
    with span("rag.rerank", mode=RERANK, candidates=len(docs)):
        try:
            scores = _cross_encoder(question, docs) if RERANK == "cross_encoder" else _api(question, docs)
        except Exception:
            log.exception("reranking failed; keeping the retrieval order")
            return docs[:top_k]
    ranked = sorted(zip(scores, range(len(docs)), docs, strict=True), key=lambda t: t[0], reverse=True)
    for score, _, doc in ranked:
        doc.metadata["rerank_score"] = round(float(score), 4)
    return [doc for _, _, doc in ranked[:top_k]]


@cache
def _model():
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as e:
        raise RuntimeError("RERANK=cross_encoder needs: uv add sentence-transformers") from e
    return CrossEncoder(RERANK_MODEL)  # downloaded from Hugging Face on first use, then cached on disk


def _cross_encoder(question: str, docs: list[Document]) -> list[float]:
    return list(_model().predict([(question, d.page_content) for d in docs]))


def _api(question: str, docs: list[Document]) -> list[float]:
    resp = httpx.post(
        RERANK_API_URL,
        headers={"Authorization": f"Bearer {RERANK_API_KEY}"},
        json={"model": RERANK_MODEL, "query": question, "documents": [d.page_content for d in docs],
              "top_n": len(docs)},
        timeout=30,
    )
    resp.raise_for_status()
    scores = [0.0] * len(docs)
    for r in resp.json()["results"]:  # [{"index": 3, "relevance_score": 0.92}, ...]
        scores[r["index"]] = r["relevance_score"]
    return scores
