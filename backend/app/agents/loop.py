"""The agent, as a LangGraph graph: the model calls tools until it can answer, each step reported as an event.

    START -> agent --(tool calls, steps left)--> approve --> tools --> agent --> ...
                   `--(an answer, or out of steps)--> END

agent    one model call, its tokens streamed. On the last allowed step it gets no tools, so it must answer.
approve  human-in-the-loop: if a call is to a tool in AGENT_APPROVAL_TOOLS, the run pauses (LangGraph
         interrupt) until the user approves or declines (POST /agent/resume). Otherwise it passes.
tools    runs the reply's tool calls in order (guardrails screen each result) and keeps the documents used.
         Declined calls get a "the user declined" result instead, so the model can answer without them.

Events (one dict each, streamed by routes/agent.py as NDJSON):
    {"type": "tool", "name": ..., "args": {...}}         a tool call is starting
    {"type": "tool_done", "name": ..., "ok": bool}       ... and has finished
    {"type": "token", "text": ...}                       part of the answer
    {"type": "approval_required", "thread_id": ..., "calls": [...]}
                                                         paused: resume with the thread id (last event)
    {"type": "sources", "sources": [...]}                documents the answer is based on (last event)

The nodes send events through LangGraph's stream writer (stream_mode "custom") as they happen; the
final state gives the sources. A checkpointer (checkpoint.py) saves the state after every node, under
the run's thread id: that's what a paused run continues from. The chat history still arrives with
each request; long-term facts about the user come from memory.py.
"""

import json
import logging
import operator
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from app.agents import checkpoint
from app.clients.llm import agent_llm
from app.core import metrics
from app.core.config import AGENT_APPROVAL_TOOLS, AGENT_MAX_STEPS, AGENT_MODEL
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
    declined: list[str]  # ids of the tool calls the user declined in the last approval


async def run(history: list[Message], tools: list[BaseTool], *, thread_id: str | None = None,
              about_user: str = "", trace: dict | None = None) -> AsyncIterator[dict]:
    """One agent answer. `about_user`: long-term memory for the system prompt (memory.py);
    `trace`: LLM tracing callbacks (core/llm_tracing.py)."""
    tally = usage.Tally()  # every model step of this answer, tool rounds included
    system = SystemMessage(injection.with_canary(AGENT_PROMPT + about_user))
    state: AgentState = {"messages": [system, *build_messages(history, [])[1:]], "steps": 0, "documents": [],
                         "declined": []}
    async for event in _drive(tools, tally, state, thread_id or uuid.uuid4().hex, trace):
        yield event


async def resume(thread_id: str, approved: bool, tools: list[BaseTool], trace: dict | None = None) -> AsyncIterator[dict]:
    """Continue a run paused for approval, from its checkpoint. `tools` must be the same set as before."""
    async for event in _drive(tools, usage.Tally(), Command(resume={"approved": approved}), thread_id, trace):
        yield event


async def _drive(tools: list[BaseTool], tally: usage.Tally, graph_input, thread_id: str,
                 trace: dict | None) -> AsyncIterator[dict]:
    cp = await checkpoint.saver()
    config = {
        # each round is three steps (agent, approve, tools), plus the final answer
        "recursion_limit": 3 * AGENT_MAX_STEPS + 4,
        "configurable": {"thread_id": thread_id},  # the checkpointer's key for this run
        **(trace or {}).get("config", {}),
    }
    state = None
    stream = build(tools, tally, cp).astream(graph_input, config, stream_mode=["custom", "values", "updates"])
    async for mode, data in stream:
        if mode == "custom":
            yield data
        elif mode == "values":
            state = data
        elif "__interrupt__" in data:  # paused in `approve`: the state is saved, this request ends here
            await usage.record("agent", tally, AGENT_MODEL)
            yield {"type": "approval_required", "thread_id": thread_id, **data["__interrupt__"][0].value}
            return
    await usage.record("agent", tally, AGENT_MODEL)
    await checkpoint.forget(cp, thread_id)
    yield {"type": "sources", "sources": cite(state["documents"] if state else [])}


def build(tools: list[BaseTool], tally: usage.Tally, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
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

    def approve(state: AgentState) -> dict:
        """Pause for the user's OK before risky tools. interrupt() saves the state and ends the request;
        on resume this node runs again from the top and interrupt() returns the user's answer. So
        nothing before interrupt() may have side effects: it would happen twice."""
        risky = [c for c in state["messages"][-1].tool_calls if c["name"] in AGENT_APPROVAL_TOOLS]
        if not risky:
            return {"declined": []}
        answer = interrupt({"calls": [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in risky]})
        approved = bool(answer.get("approved")) if isinstance(answer, dict) else bool(answer)
        return {"declined": [] if approved else [c["id"] for c in risky]}

    async def run_tools(state: AgentState) -> dict:
        write = get_stream_writer()
        results, documents = [], []
        for call in state["messages"][-1].tool_calls:
            write({"type": "tool", "name": call["name"], "args": call["args"]})
            if call["id"] in state.get("declined", []):
                result = ToolMessage("The user declined this action. Answer without it, or ask what they'd prefer.",
                                     tool_call_id=call["id"], status="error")
            else:
                result = await _call(by_name.get(call["name"]), call)
            results.append(result)
            documents += _documents(call, result)
            write({"type": "tool_done", "name": call["name"], "ok": result.status != "error"})
        return {"messages": results, "documents": documents}

    def next_step(state: AgentState) -> str:
        last = state["messages"][-1]
        # the step after the last allowed one had no tools; its answer is final whatever it contains
        if isinstance(last, AIMessage) and last.tool_calls and state["steps"] <= AGENT_MAX_STEPS:
            return "approve"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("approve", approve)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", next_step, ["approve", END])
    graph.add_edge("approve", "tools")
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


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
