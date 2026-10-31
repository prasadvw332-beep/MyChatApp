"""Embedding cache: one computed vector per (model, text). A cache, so it is always safe to empty."""

from datetime import datetime

from sqlalchemy import REAL, DateTime, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of model + text (app/cache/embeddings.py)
    model: Mapped[str] = mapped_column(String(64))  # to empty one model's rows after switching EMBED_MODEL
    # REAL (4 bytes), not double: half the size, and the lost precision is far below what cosine similarity notices
    vector: Mapped[list[float]] = mapped_column(ARRAY(REAL))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
