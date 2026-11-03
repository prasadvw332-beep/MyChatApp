"""Files in an S3 bucket: AWS S3, or any S3-compatible store such as the MinIO of this stack.

    s3://company-docs           every object in the bucket
    s3://company-docs/hr        only objects under hr/ (names are stored relative to it: "policy.pdf")

Credentials are separate from MINIO_* (the app's own file store): the source bucket usually
belongs to another system and should get a read-only key. Unset, they fall back to the local
MinIO, so you can try this without AWS: create a bucket in the MinIO console, upload files to it,
and import s3://<that bucket>.
"""

from functools import partial

from minio import Minio

from app.core.config import S3_SOURCE_ACCESS_KEY, S3_SOURCE_ENDPOINT, S3_SOURCE_SECRET_KEY, S3_SOURCE_SECURE
from app.ingestion.connectors import SourceFile


def parse(url: str) -> tuple[str, str]:
    """'s3://bucket/a/b' -> ('bucket', 'a/b/'); the trailing slash keeps 'a/b' from matching 'a/bc'."""
    bucket, _, prefix = url.removeprefix("s3://").partition("/")
    if not bucket:
        raise ValueError(f"no bucket in {url!r}, expected s3://bucket[/prefix]")
    prefix = prefix.strip("/")
    return bucket, f"{prefix}/" if prefix else ""


def _client() -> Minio:
    return Minio(
        S3_SOURCE_ENDPOINT,
        access_key=S3_SOURCE_ACCESS_KEY,
        secret_key=S3_SOURCE_SECRET_KEY,
        secure=S3_SOURCE_SECURE,
    )


def _download(client: Minio, bucket: str, key: str) -> bytes:
    resp = client.get_object(bucket, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def list_files(url: str, client: Minio | None = None) -> list[SourceFile]:
    bucket, prefix = parse(url)
    client = client or _client()
    if not client.bucket_exists(bucket):
        raise ValueError(f"bucket not found (or no access): {bucket}")
    return [
        SourceFile(obj.object_name.removeprefix(prefix), obj.size or 0, partial(_download, client, bucket, obj.object_name))
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True)
        if not obj.is_dir
    ]
