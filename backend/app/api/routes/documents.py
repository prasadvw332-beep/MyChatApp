import asyncio
import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.audit import audit
from app.core.config import MAX_UPLOAD_MB
from app.core.errors import AppError, DependencyUnavailable, NotFound
from app.rag import knowledge_base
from app.rag.chunking import SUPPORTED
from app.services import doc_store, storage

router = APIRouter()


@router.get("/documents")
async def documents():
    try:
        return await asyncio.to_thread(knowledge_base.list_documents)
    except Exception as e:
        raise DependencyUnavailable("The document list") from e


class TagsIn(BaseModel):
    tags: list[str] = Field(max_length=20)


@router.put("/documents/{doc_id}/tags")
async def set_tags(doc_id: str, body: TagsIn):
    tags = list(dict.fromkeys(t.strip() for t in body.tags if t.strip()))
    doc = await asyncio.to_thread(doc_store.set_tags, doc_id, tags)
    if not doc:
        raise NotFound("Document not found.")
    audit("document.tags", doc_id=doc_id, tags=tags)
    return doc


@router.post("/upload")
async def upload(file: UploadFile):
    name = file.filename or "upload"
    ext = os.path.splitext(name)[1].lower()
    if ext not in SUPPORTED:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Use: {', '.join(sorted(SUPPORTED))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
    try:
        result = await asyncio.to_thread(knowledge_base.ingest, name, data)
    except AppError as e:
        audit("document.upload.rejected", filename=name, size_bytes=len(data), reason=e.code)
        raise  # duplicate (409) or unreadable file (422): the global handler shapes the response
    except Exception as e:
        raise DependencyUnavailable("Indexing") from e
    audit("document.upload", doc_id=result["doc_id"], filename=name, size_bytes=len(data), chunks=result["chunks"])
    return result


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    try:
        await asyncio.to_thread(knowledge_base.delete_document, doc_id)
    except Exception as e:
        raise DependencyUnavailable("Deleting documents") from e
    audit("document.delete", doc_id=doc_id)
    return {"ok": True}


@router.get("/documents/{doc_id}/file")
async def document_file(doc_id: str):
    """Stream the original uploaded file from MinIO (opens inline in the browser)."""
    try:
        name = await asyncio.to_thread(storage.find, doc_id)
        obj = await asyncio.to_thread(storage.open_file, name) if name else None
    except Exception as e:
        raise DependencyUnavailable("File storage") from e
    if obj is None:
        raise NotFound("Original file not found (it may have been uploaded before MinIO was added).")

    def body():
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    filename = name.split("/", 1)[1]
    return StreamingResponse(
        body(),
        media_type=obj.headers.get("Content-Type", "application/octet-stream"),
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"},
    )
