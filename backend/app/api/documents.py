import asyncio
import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.config import MAX_UPLOAD_MB
from app.services import rag, storage
from app.services.chunking import SUPPORTED

router = APIRouter()


@router.get("/documents")
async def documents():
    try:
        return await asyncio.to_thread(rag.list_documents)
    except Exception as e:
        raise HTTPException(503, f"Could not reach Qdrant: {e}")


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
        return await asyncio.to_thread(rag.ingest, name, data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(503, f"Indexing failed (are Ollama and Qdrant running?): {e}")


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    try:
        await asyncio.to_thread(rag.delete_document, doc_id)
    except Exception as e:
        raise HTTPException(503, f"Could not reach Qdrant: {e}")
    return {"ok": True}


@router.get("/documents/{doc_id}/file")
async def document_file(doc_id: str):
    """Stream the original uploaded file from MinIO (opens inline in the browser)."""
    try:
        name = await asyncio.to_thread(storage.find, doc_id)
        if not name:
            raise HTTPException(404, "Original file not found (it may have been uploaded before MinIO was added).")
        obj = await asyncio.to_thread(storage.open_file, name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Could not reach MinIO: {e}")

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
