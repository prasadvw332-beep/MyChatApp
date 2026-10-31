"""The agent, as a LangGraph graph: the model calls tools until it can answer, each step reported as an event.

    START -> agent --(tool calls, steps left)--> tools --> agent --> ...
                   `--(an answer, or out of steps)--> END

agent  one model call, its tokens streamed. On the last allowed step it gets no tools, so it must answer.
tools  runs the reply's tool calls in order (guardrails screen each result) and keeps the documents used.

Events (one dict each, streamed by routes/agent.py as NDJSON):
    {"type": "tool", "name": ..., "args": {...}}         a tool call is starting
    {"type": "tool_done", "name": ..., "ok": bool}       ... and has finished
    {"type": "token", "text": ...}                       part of the answer
    {"type": "sources", "sources": [...]}                documents the answer is based on (last event)

The nodes send events through LangGraph's stream writer (stream_mode "custom") as they happen; the
final state gives the sources. No checkpointer: a run lives within one request, and the chat history
arrives with the request.
"""

import json
import logging
import operator
from collections.abc import AsyncIterator
from typing import Annotated, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from app.clients.llm import agent_llm
from app.core import metrics
from app.core.config import AGENT_MAX_STEPS, AGENT_MODEL
from app.core.telemetry import span
from app.guardrails import injection
from app.prompts.agents import AGENT_PROMPT
from app.rag.answer import build_messages, cite
from app.schemas.chat import Message
from app.services import usage

log = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    steps: int  # model calls so far
    documents: Annotated[list[Document], operator.add]  # what the tools returned, for cite()


async def run(history: list[Message], tools: list[BaseTool]) -> AsyncIterator[dict]:
    tally = usage.Tally()  # every model step of this answer, tool rounds included
    system = SystemMessage(injection.with_canary(AGENT_PROMPT))
    state: AgentState = {"messages": [system, *build_messages(history, [])[1:]], "steps": 0, "documents": []}
    # each round is two steps (agent, tools), plus the final answer
    limits = {"recursion_limit": 2 * AGENT_MAX_STEPS + 4}

    async for mode, data in build(tools, tally).astream(state, limits, stream_mode=["custom", "values"]):
        if mode == "custom":
            yield data
        else:
            state = data

    await usage.record("agent", tally, AGENT_MODEL)
    yield {"type": "sources", "sources": cite(state["documents"])}


def build(tools: list[BaseTool], tally: usage.Tally) -> CompiledStateGraph:
    """The graph for one run. Built per request: compiling takes about a millisecond, and the
    tools, the model (tests replace it) and the usage tally belong to that run."""
    by_name = {t.name: t for t in tools}
    with_tools = agent_llm.bind_tools(tools)

    async def agent(state: AgentState) -> dict:
        write = get_stream_writer()
        # out of steps: answer with what it has, without tools
        model = with_tools if state["steps"] < AGENT_MAX_STEPS else agent_llm
        reply: AIMessageChunk | None = None
        async for chunk in model.astream(state["messages"]):
            reply = chunk if reply is None else reply + chunk
            if chunk.content:
                write({"type": "token", "text": chunk.content})
        tally.add(reply)
        return {"messages": [reply] if reply is not None else [], "steps": state["steps"] + 1}

    async def run_tools(state: AgentState) -> dict:
        write = get_stream_writer()
        results, documents = [], []
        for call in state["messages"][-1].tool_calls:
            write({"type": "tool", "name": call["name"], "args": call["args"]})
            result = await _call(by_name.get(call["name"]), call)
            results.append(result)
            documents += _documents(call, result)
            write({"type": "tool_done", "name": call["name"], "ok": result.status != "error"})
        return {"messages": results, "documents": documents}

    def next_step(state: AgentState) -> str:
        last = state["messages"][-1]
        # the step after the last allowed one had no tools; its answer is final whatever it contains
        if isinstance(last, AIMessage) and last.tool_calls and state["steps"] <= AGENT_MAX_STEPS:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", next_step, ["tools", END])
    graph.add_edge("tools", "agent")
    return graph.compile()


async def _call(tool: BaseTool | None, call: dict) -> ToolMessage:
    with span("agent.tool", tool=call["name"]) as s:
        result = await _invoke(tool, call)
        # any tool may return text written by someone else (an uploaded file, a web page)
        if result.status != "error" and injection.withhold_tool_result(call["name"], _text(result)):
            result = ToolMessage(injection.WITHHELD_TOOL_RESULT, tool_call_id=call["id"], status="error")
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


def _text(result: ToolMessage) -> str:
    blocks = result.content if isinstance(result.content, list) else [result.content]
    return "".join(b if isinstance(b, str) else b.get("text", "") for b in blocks)


def _json(result: ToolMessage) -> dict:
    try:
        data = json.loads(_text(result))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
