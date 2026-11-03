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

AWS S3 sends the same records through SNS, SQS or EventBridge rather than straight to a URL.
At scale, events go through a queue the app consumes at its own pace, so a burst of 10,000 uploads
can't overwhelm the endpoint: app/ingestion/kafka_consumer.py is that path, sharing the handling
(app/ingestion/events.py) with this one. SharePoint, Google Drive and Slack have their own
change notifications (webhooks or change feeds) that fit the same handler shape.
"""

import asyncio
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.audit import audit
from app.core.config import INGEST_WEBHOOK_TOKEN
from app.ingestion import events

router = APIRouter()
log = logging.getLogger(__name__)


def _authorized(header: str) -> bool:
    # constant-time comparison: a plain == leaks, through timing, how much of the token was right
    return bool(INGEST_WEBHOOK_TOKEN) and hmac.compare_digest(header.encode(), f"Bearer {INGEST_WEBHOOK_TOKEN}".encode())


@router.post("/ingest/events/s3")
async def s3_events(request: Request, authorization: str = Header("")):
    if not INGEST_WEBHOOK_TOKEN:
        raise HTTPException(404, "Not Found")  # off: look like any unknown path
    if not _authorized(authorization):
        audit("ingest.events.unauthorized", ip=request.client.host if request.client else "-")
        raise HTTPException(401, "Bad token.")
    result = await asyncio.to_thread(events.handle, await request.json())  # shared with the Kafka consumer
    audit("ingest.events", **result)
    return result
