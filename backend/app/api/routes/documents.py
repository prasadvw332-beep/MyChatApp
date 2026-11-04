import asyncio
import os
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, limit_per_user
from app.clients import storage
from app.core import acl
from app.core.audit import audit
from app.core.config import MAX_UPLOAD_MB, RATE_LIMIT_UPLOAD
from app.core.errors import AppError, DependencyUnavailableError, NotFoundError
from app.models import User
from app.rag import knowledge_base
from app.rag.chunking import SUPPORTED
from app.repositories import doc_store
from app.schemas.documents import SetAclRequest, SetTagsRequest

router = APIRouter()


async def _visible(doc_id: str, user: User) -> None:
    """404 unless the user may see the document. 404, not 403: a 403 would confirm the id exists."""
    allowed = acl.get_principals(user)
    if allowed is None:
        return
    if not acl.can_see(await asyncio.to_thread(doc_store.acl_of, doc_id), allowed):
        raise NotFoundError("Document not found.")


@router.get("/documents")
async def list_documents_route(user: User = CurrentUser):
    try:
        return await asyncio.to_thread(knowledge_base.list_documents, acl.get_principals(user))
    except Exception as e:
        raise DependencyUnavailableError("The document list") from e


@router.put("/documents/{doc_id}/acl")
async def set_acl(doc_id: str, body: SetAclRequest, user: User = CurrentUser):
    """Change who may see a document ("public", "user:<id>", "group:<name>"). Takes effect in search
    at once: the chunks' payload is updated, nothing is re-embedded."""
    await _visible(doc_id, user)
    entries = list(dict.fromkeys(e.strip() for e in body.acl if e.strip())) or [f"user:{user.id}"]
    if not await asyncio.to_thread(knowledge_base.set_acl, doc_id, entries):
        raise NotFoundError("Document not found.")
    audit("document.acl", doc_id=doc_id, acl=entries)
    return {"doc_id": doc_id, "acl": entries}


@router.put("/documents/{doc_id}/tags")
async def set_tags(doc_id: str, body: SetTagsRequest, user: User = CurrentUser):
    await _visible(doc_id, user)
    tags = list(dict.fromkeys(t.strip() for t in body.tags if t.strip()))
    doc = await asyncio.to_thread(doc_store.set_tags, doc_id, tags)
    if not doc:
        raise NotFoundError("Document not found.")
    audit("document.tags", doc_id=doc_id, tags=tags)
    return doc


@router.post("/upload", dependencies=[limit_per_user("upload", RATE_LIMIT_UPLOAD)])
async def upload(
    file: UploadFile,
    user: User = CurrentUser,
    # with ACL_ENABLED: public (everyone) | private (only me) | groups (me + the groups listed, comma-separated)
    visibility: str = Form("public"),
    groups: str = Form(""),
):
    name = file.filename or "upload"
    access = acl.for_upload(user, visibility, [g.strip() for g in groups.split(",") if g.strip()])
    ext = os.path.splitext(name)[1].lower()
    if ext not in SUPPORTED:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Use: {', '.join(sorted(SUPPORTED))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
    try:
        result = await asyncio.to_thread(knowledge_base.ingest, name, data, access)
    except AppError as e:
        audit("document.upload.rejected", filename=name, size_bytes=len(data), reason=e.code)
        raise  # duplicate (409) or unreadable file (422): the global handler shapes the response
    except Exception as e:
        raise DependencyUnavailableError("Indexing") from e
    audit("document.upload", doc_id=result["doc_id"], filename=name, size_bytes=len(data), chunks=result["chunks"])
    return result


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, user: User = CurrentUser):
    await _visible(doc_id, user)
    try:
        await asyncio.to_thread(knowledge_base.delete_document, doc_id)
    except Exception as e:
        raise DependencyUnavailableError("Deleting documents") from e
    audit("document.delete", doc_id=doc_id)
    return {"ok": True}


@router.get("/documents/{doc_id}/file")
async def document_file(doc_id: str, user: User = CurrentUser):
    """Stream the original uploaded file from MinIO (opens inline in the browser)."""
    await _visible(doc_id, user)  # the original holds everything, PII included: same rule as search
    try:
        name = await asyncio.to_thread(storage.find, doc_id)
        obj = await asyncio.to_thread(storage.open_file, name) if name else None
    except Exception as e:
        raise DependencyUnavailableError("File storage") from e
    if obj is None:
        raise NotFoundError("Original file not found (it may have been uploaded before MinIO was added).")

    def iter_file_chunks():
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    filename = name.split("/", 1)[1]
    return StreamingResponse(
        iter_file_chunks(),
        media_type=obj.headers.get("Content-Type", "application/octet-stream"),
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"},
    )
