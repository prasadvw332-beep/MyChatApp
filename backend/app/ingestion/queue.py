"""Batch ingestion queue, kept in PostgreSQL (no extra queue service needed).

  producer (cli.py or POST /ingest/batches, see api/ingest.py)
      stores each file in MinIO and adds a `queued` row, INGEST_BATCH_SIZE documents per batch
  workers (worker.py, e.g. 3 replicas)
      claim one document at a time, index it, mark it `ready` or `failed`

Batches run in order: workers only take documents from the oldest unfinished batch, so batch 2
starts once every document of batch 1 is ready or failed. Inside a batch, whichever worker is
free takes the next document.

Claiming uses SELECT ... FOR UPDATE SKIP LOCKED: two workers never get the same row and never
wait on each other's locks. A worker that dies mid-document leaves its row `processing`; after
INGEST_LEASE_SECONDS another worker takes it over, up to INGEST_MAX_ATTEMPTS in total.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, or_, select, update

from app.core.config import INGEST_LEASE_SECONDS, INGEST_MAX_ATTEMPTS
from app.core.db import SessionLocal
from app.models import BatchStatus, DocStatus, Document, IngestBatch
from app.services import doc_store, storage

PENDING = (DocStatus.QUEUED, DocStatus.PROCESSING)
BATCH_STATES = (*PENDING, DocStatus.READY, DocStatus.FAILED)  # every status a batch document can have


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- producer ----------

def create_batch(source: str) -> int:
    with SessionLocal.begin() as db:
        batch = IngestBatch(source=source, status=BatchStatus.FILLING, created_at=_now())
        db.add(batch)
        db.flush()
        return batch.id


def add(batch_id: int, filename: str, data: bytes) -> str | None:
    """Store the file and queue it. Returns the doc_id, or None if this content is already known."""
    digest = doc_store.sha256(data)
    doc_id = uuid.uuid4().hex
    # file first, row second: a worker can claim the row as soon as it exists
    storage.save(doc_id, filename, data)
    try:
        doc_store.create(doc_id, filename, data, digest, status=DocStatus.QUEUED, batch_id=batch_id)
    except doc_store.Duplicate:  # same content already queued or indexed (unique sha256)
        storage.delete(doc_id)
        return None
    return doc_id


def seal(batch_id: int) -> None:
    """The producer is done adding: the batch may now be marked done when its documents are."""
    with SessionLocal.begin() as db:
        db.execute(update(IngestBatch).where(IngestBatch.id == batch_id).values(status=BatchStatus.OPEN))
    _close_if_done(batch_id)


# ---------- workers ----------

def claim(worker: str) -> Document | None:
    """Take the next document of the current batch, or None if there's nothing to do right now."""
    with SessionLocal.begin() as db:
        batch_id = db.scalar(
            select(IngestBatch.id).where(IngestBatch.status != BatchStatus.DONE).order_by(IngestBatch.id).limit(1)
        )
        if batch_id is None:
            return None
        expired = _now() - timedelta(seconds=INGEST_LEASE_SECONDS)
        doc = db.scalars(
            select(Document)
            .where(
                Document.batch_id == batch_id,
                or_(Document.status == DocStatus.QUEUED,
                    and_(Document.status == DocStatus.PROCESSING, Document.claimed_at < expired)),
            )
            .order_by(Document.attempts, Document.uploaded_at)  # retries go after fresh documents
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if doc:
            doc.status, doc.worker, doc.claimed_at = DocStatus.PROCESSING, worker, _now()
            doc.attempts += 1
            return doc
    # nothing claimable: either the batch is still filling, others are busy with its last
    # documents, or it just finished (in which case this lets the next batch start)
    _close_if_done(batch_id)
    return None


def finish(doc: Document, chunks: int) -> bool:
    """Mark a document ready. False if it was deleted while being processed."""
    with SessionLocal.begin() as db:
        row = db.get(Document, doc.doc_id)
        if row is None:
            return False
        row.status, row.chunks, row.error, row.claimed_at = DocStatus.READY, chunks, None, None
    _close_if_done(doc.batch_id)
    return True


def fail(doc: Document, error: str, retry: bool = True) -> str:
    """Record a failure: back to the queue while attempts remain, else `failed`. Returns the new status."""
    status = DocStatus.QUEUED if retry and doc.attempts < INGEST_MAX_ATTEMPTS else DocStatus.FAILED
    with SessionLocal.begin() as db:
        row = db.get(Document, doc.doc_id)
        if row is None:
            return "deleted"
        row.status, row.error, row.claimed_at = status, error[:2000], None
    _close_if_done(doc.batch_id)
    return status


def _close_if_done(batch_id: int | None) -> None:
    # Runs in its own transaction AFTER the document update committed. If two workers finish the
    # last two documents at the same moment, the one that commits second is guaranteed to see both.
    if batch_id is None:
        return
    with SessionLocal.begin() as db:
        db.execute(
            update(IngestBatch)
            .where(
                IngestBatch.id == batch_id,
                IngestBatch.status == BatchStatus.OPEN,
                ~exists().where(Document.batch_id == batch_id, Document.status.in_(PENDING)),
            )
            .values(status=BatchStatus.DONE, finished_at=_now())
        )


# ---------- progress and admin ----------

def progress(limit: int = 50) -> dict:
    """Newest batches with per-status document counts, plus totals over all batches."""
    with SessionLocal() as db:
        batches = db.scalars(select(IngestBatch).order_by(IngestBatch.id.desc()).limit(limit)).all()
        counts: dict[int, dict[str, int]] = {}
        for batch_id, status, n in db.execute(
            select(Document.batch_id, Document.status, func.count())
            .where(Document.batch_id.in_([b.id for b in batches]))
            .group_by(Document.batch_id, Document.status)
        ):
            counts.setdefault(batch_id, {})[status] = n
        totals = dict(db.execute(
            select(Document.status, func.count()).where(Document.batch_id.is_not(None)).group_by(Document.status)
        ).all())
        workers = list(db.scalars(
            select(Document.worker).where(Document.status == DocStatus.PROCESSING).distinct()
        ))

    def row(b: IngestBatch) -> dict:
        c = counts.get(b.id, {})
        return {
            "id": b.id, "source": b.source, "status": b.status,
            "total": sum(c.values()), **{s: c.get(s, 0) for s in BATCH_STATES},
            "created_at": b.created_at.isoformat(timespec="seconds"),
            "finished_at": b.finished_at.isoformat(timespec="seconds") if b.finished_at else None,
        }

    return {
        "totals": {s: totals.get(s, 0) for s in BATCH_STATES},
        "active_workers": workers,
        "batches": [row(b) for b in batches],
    }


def failures(batch_id: int) -> list[dict]:
    with SessionLocal() as db:
        docs = db.scalars(
            select(Document).where(Document.batch_id == batch_id, Document.status == DocStatus.FAILED)
            .order_by(Document.filename)
        )
        return [{"doc_id": d.doc_id, "filename": d.filename, "attempts": d.attempts, "error": d.error}
                for d in docs]


def retry_failed() -> int:
    """Queue every failed document again (with fresh attempts) and reopen their batches."""
    with SessionLocal.begin() as db:
        batch_ids = select(Document.batch_id).where(Document.status == DocStatus.FAILED, Document.batch_id.is_not(None))
        db.execute(update(IngestBatch).where(IngestBatch.id.in_(batch_ids), IngestBatch.status == BatchStatus.DONE)
                   .values(status=BatchStatus.OPEN, finished_at=None))
        result = db.execute(
            update(Document).where(Document.status == DocStatus.FAILED, Document.batch_id.is_not(None))
            .values(status=DocStatus.QUEUED, attempts=0)
        )
        return result.rowcount


def older_versions(doc: Document) -> list[str]:
    """Earlier ready documents with the same name: re-importing a file replaces the old version."""
    with SessionLocal() as db:
        return list(db.scalars(select(Document.doc_id).where(
            Document.filename == doc.filename, Document.doc_id != doc.doc_id,
            Document.status == DocStatus.READY, Document.uploaded_at < doc.uploaded_at,
        )))
