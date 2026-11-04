"""Ingestion caches: one PDF page's extracted text, one chunk's graph triples. Caches, so always safe to empty."""

from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ExtractionCache(Base):
    __tablename__ = "extraction_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of extractor id + page content
    extractor: Mapped[str] = mapped_column(String(120))  # to empty one extractor's rows after changing or upgrading it
    # UTF-8 bytes, not TEXT: PDF text sometimes holds NUL characters, which a PostgreSQL TEXT column refuses
    text: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GraphCache(Base):
    __tablename__ = "graph_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of model + prompt + chunk text
    model: Mapped[str] = mapped_column(String(120))
    triples: Mapped[dict] = mapped_column(JSONB)  # the model's {"entities": [...], "relations": [...]}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
