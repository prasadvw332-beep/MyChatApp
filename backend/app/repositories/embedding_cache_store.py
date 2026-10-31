"""Cached embeddings in PostgreSQL (repository: the only module that touches the embedding_cache table)."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionLocal
from app.models import EmbeddingCache


def get_many(keys: list[str]) -> dict[str, list[float]]:
    with SessionLocal() as db:
        rows = db.execute(select(EmbeddingCache.key, EmbeddingCache.vector).where(EmbeddingCache.key.in_(keys)))
        return {key: list(vector) for key, vector in rows}


def put_many(model: str, vectors: dict[str, list[float]]) -> None:
    now = datetime.now(UTC)
    rows = [{"key": k, "model": model, "vector": v, "created_at": now} for k, v in vectors.items()]
    with SessionLocal() as db:
        # two processes embedding the same text at once both insert it: the second one is a no-op
        db.execute(insert(EmbeddingCache).values(rows).on_conflict_do_nothing(index_elements=["key"]))
        db.commit()
