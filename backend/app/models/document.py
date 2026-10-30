"""Knowledge-base documents and their ingestion batches."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Document(Base):
    """An uploaded file. The original lives in MinIO, the chunks/vectors in Qdrant."""

    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String)
    ext: Mapped[str] = mapped_column(String(8))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), unique=True)  # one row per file content
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    minio_key: Mapped[str | None] = mapped_column(String)
    # indexing (direct upload) | queued -> processing -> ready | failed (batch ingestion, see app/ingestion/)
    status: Mapped[str] = mapped_column(String(16), default="indexing")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # batch ingestion only
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_batches.id", ondelete="SET NULL"), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker: Mapped[str | None] = mapped_column(String(64))  # who is processing it (pod name)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)  # last failure, kept while retrying


class IngestBatch(Base):
    """A group of documents (up to INGEST_BATCH_SIZE) queued together; batches are processed in order."""

    __tablename__ = "ingest_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String)  # folder or "upload", for display
    # filling (producer still adding) -> open (sealed) -> done (every document ready or failed)
    status: Mapped[str] = mapped_column(String(8), default="filling")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
