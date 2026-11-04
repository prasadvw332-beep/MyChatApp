import json
import logging
import time
import uuid
from contextlib import aclosing

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import loop, memory, sql_tool, supervisor
from app.api.deps import CurrentUser, limit_per_user
from app.core import llm_tracing, metrics
from app.core.config import AGENT_MEMORY, RATE_LIMIT_CHAT
from app.core.errors import DependencyUnavailableError, NotFoundError
from app.core.logging import request_id
from app.guardrails import check_question
from app.guardrails.content import AnswerGuard
from app.models import User
from app.schemas.chat import ChatRequest

router = APIRouter()
log = logging.getLogger(__name__)

SQL_TOOLS = sql_tool.tools()  # [] unless SQL_AGENT_URL is set


class ResumeAgentRequest(BaseModel):
    thread_id: str = Field(max_length=80)
    approved: bool


def _tools(request: Request, user: User) -> list:
    """Shared tools (MCP, opened at startup) + this user's own (memory) + text-to-SQL."""
    shared = getattr(request.app.state, "agent_tools", None)  # opened by the lifespan in main.py
    if shared is None:
        raise DependencyUnavailableError("The agent")
    return [*shared, *(memory.tools_for(user.id) if AGENT_MEMORY else []), *SQL_TOOLS]


# one budget with /chat: both run the chat model
@router.post("/agent/chat", dependencies=[limit_per_user("chat", RATE_LIMIT_CHAT)])
async def agent_chat(req: ChatRequest, request: Request, user: User = CurrentUser):
    """Like /chat, but the model decides which tools to use. Streams NDJSON events (see app/agents/loop.py)."""
    tools = _tools(request, user)
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    await check_question(question)
    about_user = memory.prompt_block(user.id) if AGENT_MEMORY else ""
    # the user id leads the thread id, so /agent/resume can check whose paused run it is
    thread_id = f"{user.id}-{uuid.uuid4().hex}"
    run = loop.run(req.messages, tools, thread_id=thread_id, about_user=about_user,
                   trace=llm_tracing.config("agent", user.id, request_id()))
    return StreamingResponse(_stream(run, question), media_type="application/x-ndjson")


@router.post("/agent/resume", dependencies=[limit_per_user("chat", RATE_LIMIT_CHAT)])
async def agent_resume(body: ResumeAgentRequest, request: Request, user: User = CurrentUser):
    """Continue a run that paused for approval ({"type": "approval_required"}), with the user's decision."""
    if not body.thread_id.startswith(f"{user.id}-"):
        raise NotFoundError("No paused run with that id.")  # someone else's run: as if it didn't exist
    run = loop.resume(body.thread_id, body.approved, _tools(request, user),
                      trace=llm_tracing.config("agent", user.id, request_id()))
    return StreamingResponse(_stream(run, ""), media_type="application/x-ndjson")


async def _stream(run, question: str):
    started, first = time.perf_counter(), True
    guard = AnswerGuard(question)
    try:
        async with aclosing(run) as events:
            async for event in events:
                if first:  # the model's first output: a token or its first tool call
                    metrics.llm_first_token.labels("agent").observe(time.perf_counter() - started)
                    first = False
                if event["type"] == "token" and (notice := guard.check(event["text"])):
                    yield _withheld(notice)
                    return
                yield json.dumps(event) + "\n"
                if event["type"] == "approval_required":
                    return  # paused: the answer isn't finished, so there's nothing for the output check yet
    except Exception:
        # the headers are already sent, so the error can only go into the stream itself
        log.exception("agent run failed")
        yield json.dumps({"type": "error", "detail": "The language model stopped responding. Please try again."}) + "\n"
        return
    metrics.llm_stream.labels("agent").observe(time.perf_counter() - started)
    if notice := await guard.finish():
        yield _withheld(notice)


def _withheld(notice: str) -> str:
    """Tells the frontend to replace the answer (and its sources) with the notice."""
    return json.dumps({"type": "withheld", "detail": notice}) + "\n"


@router.post("/agent/team", dependencies=[limit_per_user("chat", RATE_LIMIT_CHAT)])
async def agent_team(req: ChatRequest, request: Request, user: User = CurrentUser):
    """A supervisor and specialist agents (app/agents/supervisor.py). Same NDJSON events as /agent/chat,
    plus {"type": "handoff"} each time the supervisor gives a specialist a task."""
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    await check_question(question)
    return StreamingResponse(_stream(supervisor.run(question, _tools(request, user)), question),
                             media_type="application/x-ndjson")
