"""Bucket events -> the ingestion queue. Shared by both ways events arrive:

    POST /ingest/events/s3          the bucket calls us directly (webhook, app/api/routes/events.py)
    Kafka topic "bucket-events"     the bucket publishes, we consume at our own pace (kafka_consumer.py)

Both carry the same S3 event records ({"Records": [{"eventName": "s3:ObjectCreated:Put", "s3": {...}}]}),
so the handling lives here once:
    created  download the file, queue it (the workers index it within seconds)
    removed  delete the document

Handling is idempotent, which both paths need, because both deliver "at least once": a file already
indexed is skipped by its content hash, and deleting a document that's already gone deletes nothing.
"""

import os
from urllib.parse import unquote_plus

from app.core.config import MAX_UPLOAD_MB
from app.ingestion import queue
from app.ingestion.connectors import s3
from app.rag import knowledge_base
from app.rag.chunking import SUPPORTED
from app.repositories import doc_store


def parse(body: dict) -> tuple[list[tuple[str, str, int]], list[tuple[str, str]]]:
    """(created [(bucket, key, size)], removed [(bucket, key)]) from an S3 event notification."""
    created, removed = [], []
    for record in body.get("Records", []):
        event = record.get("eventName", "")
        obj = record.get("s3", {})
        bucket = obj.get("bucket", {}).get("name", "")
        key = unquote_plus(obj.get("object", {}).get("key", ""))  # keys arrive URL-encoded ("my+file.pdf")
        if not (bucket and key):
            continue
        if "ObjectCreated" in event:
            created.append((bucket, key, int(obj["object"].get("size", 0))))
        elif "ObjectRemoved" in event:
            removed.append((bucket, key))
    return created, removed


def apply(created: list[tuple[str, str, int]], removed: list[tuple[str, str]]) -> dict:
    """Queue the created files, delete the removed ones. Returns counts."""
    client = s3._client()
    queued = skipped = deleted = 0
    batches: dict[str, int] = {}
    try:
        for bucket, key, size in created:
            if os.path.splitext(key)[1].lower() not in SUPPORTED or size > MAX_UPLOAD_MB * 1024 * 1024:
                skipped += 1
                continue
            # same source string as `cli s3://<bucket>`, so a later --sync knows these documents
            source = f"s3://{bucket}"
            if source not in batches:
                batches[source] = queue.create_batch(source)
            if queue.add(batches[source], key, s3._download(client, bucket, key)) is None:
                skipped += 1  # same content already indexed: a duplicate event, or an unchanged re-upload
            else:
                queued += 1
    finally:
        for batch_id in batches.values():
            queue.seal(batch_id)
    for _bucket, key in removed:
        for doc_id in doc_store.ids_for_filename(key):
            knowledge_base.delete_document(doc_id)
            deleted += 1
    return {"queued": queued, "skipped": skipped, "deleted": deleted}


def handle(body: dict) -> dict:
    return apply(*parse(body))
