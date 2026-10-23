"""Original uploaded files, kept in MinIO (S3-compatible object storage).

Objects are stored as `<doc_id>/<filename>`, so every document version has its own prefix.
"""

import io
import os

from minio import Minio

from app.config import MINIO_ACCESS_KEY, MINIO_BUCKET, MINIO_ENDPOINT, MINIO_SECRET_KEY

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,  # local http
)

# text/plain for .md too, so browsers show it instead of downloading it
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


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


def delete(doc_id: str) -> None:
    if not client.bucket_exists(MINIO_BUCKET):
        return
    for obj in client.list_objects(MINIO_BUCKET, prefix=f"{doc_id}/", recursive=True):
        client.remove_object(MINIO_BUCKET, obj.object_name)
