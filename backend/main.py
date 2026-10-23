import asyncio
import json
import os
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

import rag
import storage

MAX_UPLOAD_MB = 20

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Sources"],
)

RAG_PROMPT = (
    "You are a helpful assistant that answers questions using the provided context "
    "from the user's uploaded documents.\n"
    "Rules:\n"
    "- For questions about the documents, use ONLY the context below.\n"
    "- If such an answer is not in the context, say you couldn't find it in the uploaded documents.\n"
    "- If the message is a greeting or clearly unrelated to the documents, just reply normally.\n"
    "- Be concise and do not invent facts.\n\n"
    "Context:\n{context}"
)
PLAIN_PROMPT = "You are a helpful assistant."


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


@app.get("/documents")
async def documents():
    try:
        return await asyncio.to_thread(rag.list_documents)
    except Exception as e:
        raise HTTPException(503, f"Could not reach Qdrant: {e}")


@app.post("/upload")
async def upload(file: UploadFile):
    name = file.filename or "upload"
    ext = os.path.splitext(name)[1].lower()
    if ext not in rag.SUPPORTED:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Use: {', '.join(sorted(rag.SUPPORTED))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
    try:
        return await asyncio.to_thread(rag.ingest, name, data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(503, f"Indexing failed (are Ollama and Qdrant running?): {e}")


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    try:
        await asyncio.to_thread(rag.delete_document, doc_id)
    except Exception as e:
        raise HTTPException(503, f"Could not reach Qdrant: {e}")
    return {"ok": True}


@app.get("/documents/{doc_id}/file")
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


TITLE_SYSTEM = (
    "You create short titles for chat conversations. Reply with only a 2-5 word title in "
    "Title Case that names the topic. Never answer the question."
)
# Few-shot as chat turns, on topics unrelated to real chats so the small model doesn't copy them
TITLE_SHOTS = [
    ("Questions: How do I bake sourdough bread?", "Sourdough Bread Baking"),
    ("Questions: hello | what are the symptoms of flu?", "Flu Symptoms"),
]


@app.post("/title")
async def title(req: ChatRequest):
    """Generate a short chat title from the user's questions (used for the history sidebar)."""
    asked = [m.content.strip()[:300] for m in req.messages if m.role == "user"][:4]
    messages = [SystemMessage(TITLE_SYSTEM)]
    for q, a in TITLE_SHOTS:
        messages += [HumanMessage(q), AIMessage(a)]
    messages.append(HumanMessage("Questions: " + " | ".join(asked)))
    try:
        out = await rag.title_llm.ainvoke(messages)
    except Exception as e:
        raise HTTPException(503, f"Ollama error: {e}")

    # clean up quotes / "Title:" prefixes, and reject output that is an answer rather than a title
    lines = (out.content or "").strip().splitlines()
    text = lines[0] if lines else ""
    text = text.removeprefix("Title:").removeprefix("title:").strip().strip("\"'*#`").strip().rstrip(".!?:")
    if not text or len(text.split()) > 8 or len(text) > 60 or any(c in text for c in "|{}<>"):
        return {"title": ""}  # the frontend keeps the current title
    if text.isupper():
        text = text.title()
    return {"title": text}


@app.post("/chat")
async def chat(req: ChatRequest):
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    try:
        docs = await asyncio.to_thread(rag.retrieve, question)
    except Exception as e:
        raise HTTPException(503, f"Search failed (are Ollama and Qdrant running?): {e}")

    if docs:
        context = "\n\n".join(
            f"[{d.metadata.get('source')}"
            + (f", page {d.metadata['page']}" if "page" in d.metadata else "")
            + f"]\n{d.page_content}"
            for d in docs
        )
        system = RAG_PROMPT.format(context=context)
    else:
        system = PLAIN_PROMPT

    messages = [SystemMessage(system)]
    for m in req.messages:
        messages.append(AIMessage(m.content) if m.role == "assistant" else HumanMessage(m.content))

    seen, sources = set(), []
    for d in docs:
        key = (d.metadata.get("source"), d.metadata.get("page"))
        if key not in seen:
            seen.add(key)
            sources.append({"source": key[0], "page": key[1], "doc_id": d.metadata.get("doc_id")})

    async def token_stream():
        try:
            async for chunk in rag.llm.astream(messages):
                yield chunk.content
        except Exception as e:
            yield f"\n\n[Error talking to Ollama: {e}]"

    return StreamingResponse(
        token_stream(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Sources": json.dumps(sources)},
    )
