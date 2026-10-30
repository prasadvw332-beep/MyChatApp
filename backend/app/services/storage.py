"""Original uploaded files, kept in MinIO (S3-compatible object storage).

Objects are stored as `<doc_id>/<filename>`, so every document version has its own prefix.
"""

import io
import os
from datetime import datetime
from email.utils import parsedate_to_datetime

import urllib3
from minio import Minio

from app.core.config import MINIO_ACCESS_KEY, MINIO_BUCKET, MINIO_ENDPOINT, MINIO_SECRET_KEY

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,  # local http
    # bounded waits, and retry brief outages (connection resets, 502/503 during a restart) with backoff
    http_client=urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=5, read=60),
        retries=urllib3.Retry(total=3, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504)),
        maxsize=10,
    ),
)

# text/plain for .md too, so browsers show it instead of downloading it
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


def ping() -> None:
    client.bucket_exists(MINIO_BUCKET)


def _ensure_bucket() -> None:
    if not client.bucket_exists(MINIO_BUCKET):
        client.make_bucket(MINIO_BUCKET)


def save(doc_id: str, filename: str, data: bytes) -> None:
    _ensure_bucket()
    ext = os.path.splitext(filename)[1].lower()
    client.put_object(
        MINIO_BUCKET,
        f"{doc_id}/{filename}",
        io.BytesIO(data),
        length=len(data),
        content_type=CONTENT_TYPES.get(ext, "application/octet-stream"),
    )


def find(doc_id: str) -> str | None:
    """Object name stored for a document, or None."""
    if not client.bucket_exists(MINIO_BUCKET):
        return None
    for obj in client.list_objects(MINIO_BUCKET, prefix=f"{doc_id}/"):
        return obj.object_name
    return None


def open_file(object_name: str):
    """Return a streaming response from MinIO; caller must close() and release_conn()."""
    return client.get_object(MINIO_BUCKET, object_name)


def read(object_name: str) -> tuple[bytes, datetime | None]:
    """The whole file and when it was stored."""
    obj = open_file(object_name)
    try:
        modified = obj.headers.get("Last-Modified")
        return obj.read(), parsedate_to_datetime(modified) if modified else None
    finally:
        obj.close()
        obj.release_conn()


def delete(doc_id: str) -> None:
    if not client.bucket_exists(MINIO_BUCKET):
        return
    for obj in client.list_objects(MINIO_BUCKET, prefix=f"{doc_id}/", recursive=True):
        client.remove_object(MINIO_BUCKET, obj.object_name)
