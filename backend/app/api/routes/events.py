"""Event-driven ingestion: the bucket tells us when a file changes, instead of a nightly re-scan.

    S3 / MinIO --(bucket notification: ObjectCreated / ObjectRemoved)--> POST /ingest/events/s3
        created  download the file, queue it (the workers index it within seconds)
        removed  delete the document

Nightly imports (Airflow) are simple and catch everything, but a new policy is invisible until
tomorrow. Events make it minutes. Production setups use both: events for freshness, a periodic
full sync (cli --sync) to repair whatever events were lost (a webhook that was down, a missed
message). Events are "at least once": the same one can arrive twice, which is harmless here
(same content hash = skipped).

Not behind a login (a bucket can't log in): authenticated by a shared secret instead,
INGEST_WEBHOOK_TOKEN, sent as "Authorization: Bearer <token>". Unset = the endpoint is off.

MinIO, once:
    mc admin config set local notify_webhook:ingest endpoint=http://backend:8000/ingest/events/s3 auth_token=<token>
    mc admin service restart local
    mc event add local/company-docs arn:minio:sqs::ingest:webhook --event put,delete

AWS S3 sends the same records through SNS, SQS or EventBridge rather than straight to a URL;
at scale, events go through a queue (SQS, Kafka) the app consumes at its own pace, so a burst of
10,000 uploads can't overwhelm the endpoint. SharePoint, Google Drive and Slack have their own
change notifications (webhooks or change feeds) that fit the same handler shape.
"""

import asyncio
import hmac
import logging
import os
from urllib.parse import unquote_plus

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.audit import audit
from app.core.config import INGEST_WEBHOOK_TOKEN, MAX_UPLOAD_MB
from app.ingestion import queue
from app.ingestion.connectors import s3
from app.rag import knowledge_base
from app.rag.chunking import SUPPORTED
from app.repositories import doc_store

router = APIRouter()
log = logging.getLogger(__name__)


def _authorized(header: str) -> bool:
    # constant-time comparison: a plain == leaks, through timing, how much of the token was right
    return bool(INGEST_WEBHOOK_TOKEN) and hmac.compare_digest(header.encode(), f"Bearer {INGEST_WEBHOOK_TOKEN}".encode())


def _apply(created: list[tuple[str, str, int]], removed: list[tuple[str, str]]) -> dict:
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


@router.post("/ingest/events/s3")
async def s3_events(request: Request, authorization: str = Header("")):
    if not INGEST_WEBHOOK_TOKEN:
        raise HTTPException(404, "Not Found")  # off: look like any unknown path
    if not _authorized(authorization):
        audit("ingest.events.unauthorized", ip=request.client.host if request.client else "-")
        raise HTTPException(401, "Bad token.")
    body = await request.json()
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
    result = await asyncio.to_thread(_apply, created, removed)
    audit("ingest.events", **result)
    return result
