"""Cached answers (app/cache/semantic.py): the interface the cache depends on, and its Qdrant implementation.

Answers get their own collection next to the documents: the point's vector is the question's embedding,
its payload the answer. One extra point, the marker, holds the time of the last clear().
"""

import uuid
from typing import Protocol

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from app.core.config import QDRANT_TIMEOUT, QDRANT_URL, SEMANTIC_CACHE_COLLECTION


class AnswerStore(Protocol):
    def search(self, vector: list[float], model: str, not_before: float, min_score: float
               ) -> tuple[dict, float] | None:
        """The closest answer for `model` asked at or after `not_before`, with its score, if it reaches `min_score`."""
        ...

    def put(self, key: str, vector: list[float], answer: dict) -> None:
        """Store an answer (with `model` and `asked_at` in it); the same key replaces the previous one."""
        ...

    def cleared_at(self) -> float:
        """When clear() last ran (0 if never)."""
        ...

    def clear(self, now: float, vector: list[float]) -> None:
        """Record `now` as the clear time and delete answers asked before it. `vector` sizes a new collection."""
        ...


# the one point that is not an answer
MARKER_ID = str(uuid.UUID(int=0))


def _match(key: str, value: str) -> FieldCondition:
    return FieldCondition(key=key, match=MatchValue(value=value))


class QdrantAnswerStore:
    def __init__(self, client: QdrantClient, collection: str):
        self.client = client
        self.collection = collection

    def search(self, vector, model, not_before, min_score):
        if not self.client.collection_exists(self.collection):
            return None
        points = self.client.query_points(
            self.collection,
            query=vector,
            query_filter=Filter(must=[
                _match("kind", "answer"),
                _match("model", model),
                FieldCondition(key="asked_at", range=Range(gte=not_before)),
            ]),
            score_threshold=min_score,
            limit=1,
            with_payload=True,
        ).points
        return (points[0].payload, points[0].score) if points else None

    def put(self, key, vector, answer):
        self._ensure_collection(len(vector))
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
        self.client.upsert(self.collection, [PointStruct(id=point_id, vector=vector,
                                                         payload={"kind": "answer", **answer})])

    def cleared_at(self):
        if not self.client.collection_exists(self.collection):
            return 0.0
        points = self.client.retrieve(self.collection, [MARKER_ID], with_payload=True)
        return points[0].payload["cleared_at"] if points else 0.0

    def clear(self, now, vector):
        self._ensure_collection(len(vector))
        # the marker needs some vector; "kind" keeps it out of every search
        self.client.upsert(self.collection, [PointStruct(id=MARKER_ID, vector=vector,
                                                         payload={"kind": "marker", "cleared_at": now})])
        self.client.delete(self.collection, Filter(must=[
            _match("kind", "answer"), FieldCondition(key="asked_at", range=Range(lt=now))]))

    def _ensure_collection(self, size: int) -> None:
        if self.client.collection_exists(self.collection):
            return
        try:
            self.client.create_collection(self.collection, VectorParams(size=size, distance=Distance.COSINE))
        except Exception:
            if not self.client.collection_exists(self.collection):  # else another process created it first
                raise


def qdrant() -> QdrantAnswerStore:
    return QdrantAnswerStore(QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT), SEMANTIC_CACHE_COLLECTION)
