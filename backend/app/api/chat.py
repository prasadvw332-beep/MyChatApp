import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.prompts import TITLE_SHOTS, TITLE_SYSTEM
from app.schemas import ChatRequest
from app.services import rag
from app.services.answer import build_messages, cite
from app.services.llm import llm, title_llm

router = APIRouter()


@router.post("/title")
async def title(req: ChatRequest):
    """Generate a short chat title from the user's questions (used for the history sidebar)."""
    asked = [m.content.strip()[:300] for m in req.messages if m.role == "user"][:4]
    messages = [SystemMessage(TITLE_SYSTEM)]
    for q, a in TITLE_SHOTS:
        messages += [HumanMessage(q), AIMessage(a)]
    messages.append(HumanMessage("Questions: " + " | ".join(asked)))
    try:
        out = await title_llm.ainvoke(messages)
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


@router.post("/chat")
async def chat(req: ChatRequest):
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    try:
        docs = await asyncio.to_thread(rag.retrieve, question)
    except Exception as e:
        raise HTTPException(503, f"Search failed (are Ollama and Qdrant running?): {e}")

    messages = build_messages(req.messages, docs)
    sources = cite(docs)

    async def token_stream():
        try:
            async for chunk in llm.astream(messages):
                yield chunk.content
        except Exception as e:
            yield f"\n\n[Error talking to Ollama: {e}]"

    return StreamingResponse(
        token_stream(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Sources": json.dumps(sources)},
    )
