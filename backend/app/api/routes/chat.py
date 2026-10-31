import asyncio
import json
import logging
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.api.deps import limit_per_user
from app.cache import semantic
from app.clients.llm import llm, title_llm
from app.core import metrics
from app.core.config import CHAT_MODEL, RATE_LIMIT_CHAT
from app.core.errors import DependencyUnavailable
from app.guardrails import check_question
from app.guardrails.content import WITHHELD, AnswerGuard
from app.prompts.title import TITLE_SHOTS, TITLE_SYSTEM
from app.rag import knowledge_base
from app.rag.answer import build_messages, cite
from app.schemas.chat import ChatRequest
from app.services import usage

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/title", dependencies=[limit_per_user("title", RATE_LIMIT_CHAT)])
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
        raise DependencyUnavailable("The language model") from e
    tally = usage.Tally()
    tally.add(out)
    await usage.record("title", tally, CHAT_MODEL)

    # clean up quotes / "Title:" prefixes, and reject output that is an answer rather than a title
    lines = (out.content or "").strip().splitlines()
    text = lines[0] if lines else ""
    text = text.removeprefix("Title:").removeprefix("title:").strip().strip("\"'*#`").strip().rstrip(".!?:")
    if not text or len(text.split()) > 8 or len(text) > 60 or any(c in text for c in "|{}<>"):
        return {"title": ""}  # the frontend keeps the current title
    if text.isupper():
        text = text.title()
    return {"title": text}


@router.post("/chat", dependencies=[limit_per_user("chat", RATE_LIMIT_CHAT)])
async def chat(req: ChatRequest):
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    cacheable = semantic.cacheable(req.messages)
    asked_at = time.time()  # before the documents are read: see app/cache/semantic.py

    async def retrieve():
        """A cached answer, or else the chunks to write a new one from."""
        if cacheable and (hit := await asyncio.to_thread(semantic.lookup, question)):
            return hit, []
        try:
            return None, await asyncio.to_thread(knowledge_base.retrieve, question)
        except Exception as e:
            raise DependencyUnavailable("Document search") from e

    # the safety check calls a model too: run it alongside the search, not before it.
    # It runs on a cache hit as well: the question is new even when the answer is not.
    (hit, docs), _ = await asyncio.gather(retrieve(), check_question(question))
    if hit:
        return StreamingResponse(
            iter([hit.answer]),
            media_type="text/plain; charset=utf-8",
            headers={"X-Sources": json.dumps(hit.sources), "X-Cache": "hit"},
        )
    messages = build_messages(req.messages, docs)
    sources = cite(docs)

    async def token_stream():
        started, first = time.perf_counter(), True
        tally = usage.Tally()
        guard = AnswerGuard(question)
        answer: list[str] = []
        try:
            async for chunk in llm.astream(messages):
                if first:
                    metrics.llm_first_token.labels("chat").observe(time.perf_counter() - started)
                    first = False
                tally.add(chunk)  # the token counts arrive with the last chunk
                text = chunk.content.replace(WITHHELD, "")  # the marker means something to the frontend
                if notice := guard.check(text):
                    yield WITHHELD + notice
                    return
                answer.append(text)
                yield text
        except Exception:
            # the headers are already sent, so the error can only go into the text itself
            log.exception("chat stream failed")
            yield "\n\n[The language model stopped responding. Please try again.]"
            return
        metrics.llm_stream.labels("chat").observe(time.perf_counter() - started)
        await usage.record("chat", tally, CHAT_MODEL)
        if notice := await guard.finish():
            yield WITHHELD + notice
            return
        if cacheable:  # only a complete answer that passed the guardrails is reused
            await asyncio.to_thread(semantic.remember, question, "".join(answer), sources, asked_at)

    return StreamingResponse(
        token_stream(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Sources": json.dumps(sources), "X-Cache": "miss" if cacheable else "off"},
    )
