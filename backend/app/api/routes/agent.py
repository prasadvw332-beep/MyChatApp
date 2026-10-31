import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agents import loop
from app.core import metrics
from app.core.errors import DependencyUnavailable
from app.schemas.chat import ChatRequest

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/agent/chat")
async def agent_chat(req: ChatRequest, request: Request):
    """Like /chat, but the model decides which tools to use. Streams NDJSON events (see app/agents/loop.py)."""
    tools = getattr(request.app.state, "agent_tools", None)  # opened by the lifespan in main.py
    if tools is None:
        raise DependencyUnavailable("The agent")

    async def events():
        started, first = time.perf_counter(), True
        try:
            async for event in loop.run(req.messages, tools):
                if first:  # the model's first output: a token or its first tool call
                    metrics.llm_first_token.labels("agent").observe(time.perf_counter() - started)
                    first = False
                yield json.dumps(event) + "\n"
        except Exception:
            # the headers are already sent, so the error can only go into the stream itself
            log.exception("agent run failed")
            yield json.dumps({"type": "error", "detail": "The language model stopped responding. Please try again."}) + "\n"
            return
        metrics.llm_stream.labels("agent").observe(time.perf_counter() - started)

    return StreamingResponse(events(), media_type="application/x-ndjson")
