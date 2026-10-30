"""Batch ingestion: queue many files for the workers and follow their progress."""

import asyncio
import os

from fastapi import APIRouter, HTTPException, UploadFile

from app.core.config import INGEST_BATCH_SIZE, MAX_UPLOAD_MB
from app.ingestion import queue
from app.rag.chunking import SUPPORTED

router = APIRouter(prefix="/ingest")


@router.get("/batches")
async def batches():
    return await asyncio.to_thread(queue.progress)


@router.get("/batches/{batch_id}/failures")
async def failures(batch_id: int):
    return await asyncio.to_thread(queue.failures, batch_id)


@router.post("/batches")
async def create(files: list[UploadFile]):
    """Queue uploaded files, INGEST_BATCH_SIZE per batch. Returns at once; workers do the indexing."""
    rejected = [f.filename for f in files if os.path.splitext(f.filename or "")[1].lower() not in SUPPORTED]
    if rejected:
        raise HTTPException(400, f"Unsupported file type: {', '.join(rejected[:5])}. Use: {', '.join(sorted(SUPPORTED))}")
    created, queued, skipped = [], 0, 0
    for start in range(0, len(files), INGEST_BATCH_SIZE):
        batch_id = await asyncio.to_thread(queue.create_batch, "upload")
        created.append(batch_id)
        try:
            for f in files[start:start + INGEST_BATCH_SIZE]:
                data = await f.read()
                if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
                    skipped += 1
                    continue
                doc_id = await asyncio.to_thread(queue.add, batch_id, f.filename, data)
                queued, skipped = queued + (doc_id is not None), skipped + (doc_id is None)
        except Exception as e:
            raise HTTPException(503, f"Queueing failed (are MinIO and PostgreSQL running?): {e}")
        finally:
            await asyncio.to_thread(queue.seal, batch_id)
    return {"batches": created, "queued": queued, "skipped": skipped}


@router.post("/retry-failed")
async def retry_failed():
    return {"queued": await asyncio.to_thread(queue.retry_failed)}
