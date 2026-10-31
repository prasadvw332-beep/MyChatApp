import json
import logging
import time
from contextlib import aclosing

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agents import loop
from app.core import metrics
from app.core.errors import DependencyUnavailable
from app.guardrails import check_question
from app.guardrails.content import AnswerGuard
from app.schemas.chat import ChatRequest

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/agent/chat")
async def agent_chat(req: ChatRequest, request: Request):
    """Like /chat, but the model decides which tools to use. Streams NDJSON events (see app/agents/loop.py)."""
    tools = getattr(request.app.state, "agent_tools", None)  # opened by the lifespan in main.py
    if tools is None:
        raise DependencyUnavailable("The agent")
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    await check_question(question)

    async def events():
        started, first = time.perf_counter(), True
        guard = AnswerGuard(question)
        try:
            async with aclosing(loop.run(req.messages, tools)) as run:
                async for event in run:
                    if first:  # the model's first output: a token or its first tool call
                        metrics.llm_first_token.labels("agent").observe(time.perf_counter() - started)
                        first = False
                    if event["type"] == "token" and (notice := guard.check(event["text"])):
                        yield _withheld(notice)
                        return
                    yield json.dumps(event) + "\n"
        except Exception:
            # the headers are already sent, so the error can only go into the stream itself
            log.exception("agent run failed")
            yield json.dumps({"type": "error", "detail": "The language model stopped responding. Please try again."}) + "\n"
            return
        metrics.llm_stream.labels("agent").observe(time.perf_counter() - started)
        if notice := await guard.finish():
            yield _withheld(notice)

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _withheld(notice: str) -> str:
    """Tells the frontend to replace the answer (and its sources) with the notice."""
    return json.dumps({"type": "withheld", "detail": notice}) + "\n"
