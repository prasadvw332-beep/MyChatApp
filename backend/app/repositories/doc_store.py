"""Document metadata in PostgreSQL (filename, size, hash, chunk count, MinIO key, status, tags)."""

import hashlib
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.models import DocStatus, Document


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_duplicate(digest: str) -> Document | None:
    with SessionLocal() as db:
        return db.scalars(select(Document).where(Document.sha256 == digest, Document.status == DocStatus.READY)).first()


class Duplicate(Exception):
    """A document with the same content hash already exists."""


def create(
    doc_id: str, filename: str, data: bytes, digest: str,
    status: DocStatus = DocStatus.INDEXING, batch_id: int | None = None,
) -> None:
    """Add the row for a new document (direct upload, or queued with a batch_id). Raises Duplicate."""
    try:
        with SessionLocal.begin() as db:
            db.add(Document(
                doc_id=doc_id, filename=filename, ext=_ext(filename),
                size_bytes=len(data), sha256=digest, minio_key=f"{doc_id}/{filename}",
                status=status, uploaded_at=datetime.now(timezone.utc), batch_id=batch_id,
            ))
    except IntegrityError:
        raise Duplicate() from None


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


def stale_indexing() -> list[str]:
    """Uploads left 'indexing' by a crash; at startup nothing is legitimately in progress."""
    with SessionLocal() as db:
        return list(db.scalars(select(Document.doc_id).where(Document.status == DocStatus.INDEXING)))


def mark_ready(doc_id: str, chunks: int) -> None:
    with SessionLocal.begin() as db:
        doc = db.get(Document, doc_id)
        doc.chunks, doc.status = chunks, DocStatus.READY


def delete(doc_id: str) -> None:
    with SessionLocal.begin() as db:
        doc = db.get(Document, doc_id)
        if doc:
            db.delete(doc)


def ids_for_filename(filename: str) -> set[str]:
    with SessionLocal() as db:
        return set(db.scalars(select(Document.doc_id).where(Document.filename == filename)))


def _out(d: Document) -> dict:
    return {
        "doc_id": d.doc_id, "filename": d.filename, "chunks": d.chunks, "size_bytes": d.size_bytes,
        "ext": d.ext, "status": d.status, "error": d.error, "tags": d.tags or [],
        "uploaded_at": d.uploaded_at.isoformat(timespec="seconds"),
    }


def list_all() -> list[dict]:
    with SessionLocal() as db:
        docs = db.scalars(select(Document).order_by(Document.filename)).all()
        return [_out(d) for d in docs]


def set_tags(doc_id: str, tags: list[str]) -> dict | None:
    with SessionLocal.begin() as db:
        doc = db.get(Document, doc_id)
        if not doc:
            return None
        doc.tags = tags
        return _out(doc)


def backfill(qdrant_docs: list[dict], storage) -> int:
    """Add rows for documents indexed before this table existed (known to Qdrant, missing here)."""
    with SessionLocal() as db:
        known = set(db.scalars(select(Document.doc_id)))
    added = 0
    for d in qdrant_docs:
        if d["doc_id"] in known or d["doc_id"] == "?":
            continue
        key, size, digest, uploaded = None, 0, None, datetime.now(timezone.utc)
        try:
            key = storage.find(d["doc_id"])
            if key:
                data, modified = storage.read(key)
                size, digest, uploaded = len(data), sha256(data), modified or uploaded
        except Exception:
            pass  # metadata is best-effort for old documents
        with SessionLocal.begin() as db:
            db.add(Document(
                doc_id=d["doc_id"], filename=d["filename"],
                ext=_ext(d["filename"]),
                size_bytes=size, sha256=digest, chunks=d["chunks"], minio_key=key,
                status=DocStatus.READY, uploaded_at=uploaded,
            ))
        added += 1
    return added
