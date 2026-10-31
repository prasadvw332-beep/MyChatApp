"""The agent loop: the model calls tools until it can answer, and each step is reported as an event.

Events (one dict each, streamed by routes/agent.py as NDJSON):
    {"type": "tool", "name": ..., "args": {...}}         a tool call is starting
    {"type": "tool_done", "name": ..., "ok": bool}       ... and has finished
    {"type": "token", "text": ...}                       part of the answer
    {"type": "sources", "sources": [...]}                documents the answer is based on (last event)
"""

import json
import logging
from collections.abc import AsyncIterator

from langchain_core.documents import Document
from langchain_core.messages import AIMessageChunk, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.core import metrics
from app.core.config import AGENT_MAX_STEPS
from app.core.telemetry import span
from app.prompts.agents import AGENT_PROMPT
from app.rag.answer import build_messages, cite
from app.schemas.chat import Message
from app.services.llm import agent_llm

log = logging.getLogger(__name__)


async def run(history: list[Message], tools: list[BaseTool]) -> AsyncIterator[dict]:
    messages: list[BaseMessage] = [SystemMessage(AGENT_PROMPT), *build_messages(history, [])[1:]]
    by_name = {t.name: t for t in tools}
    with_tools = agent_llm.bind_tools(tools)
    used: list[Document] = []

    for step in range(AGENT_MAX_STEPS + 1):
        # out of steps: answer with what it has, without tools
        model = with_tools if step < AGENT_MAX_STEPS else agent_llm
        reply: AIMessageChunk | None = None
        async for chunk in model.astream(messages):
            reply = chunk if reply is None else reply + chunk
            if chunk.content:
                yield {"type": "token", "text": chunk.content}
        if reply is None:
            break
        messages.append(reply)
        if not reply.tool_calls:
            break
        for call in reply.tool_calls:
            yield {"type": "tool", "name": call["name"], "args": call["args"]}
            result = await _call(by_name.get(call["name"]), call)
            messages.append(result)
            used += _documents(call, result)
            yield {"type": "tool_done", "name": call["name"], "ok": result.status != "error"}

    yield {"type": "sources", "sources": cite(used)}


async def _call(tool: BaseTool | None, call: dict) -> ToolMessage:
    with span("agent.tool", tool=call["name"]) as s:
        result = await _invoke(tool, call)
        ok = result.status != "error"
        s.set_attribute("ok", ok)
    # a tool name the model made up gets one shared label value, so it can't grow the series count
    metrics.agent_tool_calls.labels(call["name"] if tool else "unknown", str(ok).lower()).inc()
    return result


async def _invoke(tool: BaseTool | None, call: dict) -> ToolMessage:
    """Run one tool call. Failures go back to the model as an error result, so it can recover."""
    if tool is None:
        return ToolMessage(f"There is no tool named {call['name']!r}.", tool_call_id=call["id"], status="error")
    log.info("agent tool call: %s", call["name"])
    try:
        return await tool.ainvoke(call)
    except Exception:
        log.exception("agent tool %s failed", call["name"])
        return ToolMessage("The tool failed. Try another way or answer without it.",
                           tool_call_id=call["id"], status="error")


def _documents(call: dict, result: ToolMessage) -> list[Document]:
    """What the knowledge-base tools returned, as Documents for cite()."""
    if result.status == "error":
        return []
    data = _json(result)
    if call["name"] == "search_documents":
        return [Document(r.get("text", ""), metadata={"source": r.get("source"), "page": r.get("page"),
                                                      "doc_id": r.get("doc_id")})
                for r in data.get("results", [])]
    if call["name"] == "read_document" and data.get("filename"):
        return [Document("", metadata={"source": data["filename"], "doc_id": call["args"].get("doc_id")})]
    return []


def _json(result: ToolMessage) -> dict:
    blocks = result.content if isinstance(result.content, list) else [result.content]
    text = "".join(b if isinstance(b, str) else b.get("text", "") for b in blocks)
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
