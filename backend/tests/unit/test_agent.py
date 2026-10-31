"""The agent loop and its MCP tools, with a scripted model instead of Ollama."""

import asyncio
import json

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessageChunk

from app.agents import loop
from app.agents import tools as agent_tools
from app.api import deps
from app.main import app
from app.models import User
from app.rag import knowledge_base
from app.schemas.chat import Message
from app.services import usage
from tests.fakes import FakeVectorStore, ScriptedModel

CV = Document("Studied at Pune University.", metadata={"source": "cv.pdf", "page": 1, "doc_id": "d1"})


@pytest.fixture
def kb(monkeypatch):
    monkeypatch.setattr(knowledge_base, "store", FakeVectorStore(hits=[(CV, 0.9)]))


def run_agent(model, monkeypatch, question="Where did I study?"):
    monkeypatch.setattr(loop, "agent_llm", model)

    async def go():
        async with agent_tools.connect() as tools:
            return [e async for e in loop.run([Message(role="user", content=question)], tools)]

    return asyncio.run(go())


def test_mcp_server_exposes_the_knowledge_base_tools():
    async def names():
        async with agent_tools.connect() as tools:
            return {t.name for t in tools}

    assert asyncio.run(names()) == {"search_documents", "list_documents", "read_document"}


def test_agent_searches_then_answers_with_sources(kb, monkeypatch):
    model = ScriptedModel([("search_documents", {"query": "university"})], "Pune University.")
    events = run_agent(model, monkeypatch)

    assert events[0] == {"type": "tool", "name": "search_documents", "args": {"query": "university"}}
    assert events[1] == {"type": "tool_done", "name": "search_documents", "ok": True}
    assert "".join(e["text"] for e in events if e["type"] == "token").strip() == "Pune University."
    assert events[-1] == {"type": "sources", "sources": [{"source": "cv.pdf", "page": 1, "doc_id": "d1"}]}
    # the passage reached the model through MCP
    assert "Pune University" in str(model.calls[1]["messages"][-1].content)


def test_greeting_needs_no_tools(kb, monkeypatch):
    events = run_agent(ScriptedModel("Hello!"), monkeypatch, question="hi")
    assert [e["type"] for e in events] == ["token", "sources"]
    assert events[-1]["sources"] == []


def test_unknown_tool_goes_back_to_the_model_as_an_error(kb, monkeypatch):
    model = ScriptedModel([("delete_everything", {})], "Sorry, I can't do that.")
    events = run_agent(model, monkeypatch)
    assert {"type": "tool_done", "name": "delete_everything", "ok": False} in events
    assert model.calls[1]["messages"][-1].status == "error"


def test_step_limit_forces_an_answer_without_tools(kb, monkeypatch):
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 2)
    search = [("search_documents", {"query": "x"})]
    model = ScriptedModel(search, search, "Best I can tell: Pune.")
    events = run_agent(model, monkeypatch)
    assert [c["tools"] for c in model.calls] == [True, True, False]
    assert events[-2]["type"] == "token"


def test_agent_is_a_langgraph_graph_of_model_and_tools():
    nodes = set(loop.build([], usage.Tally()).get_graph().nodes)
    assert {"agent", "tools"} <= nodes


def test_tokens_stream_while_the_model_is_still_writing(monkeypatch):
    """The second word is only written after the first has reached the caller: if the graph held tokens
    back until the model step finished, this would wait forever."""
    first_seen = asyncio.Event()

    class Waiting:
        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            yield AIMessageChunk(content="one ")
            await first_seen.wait()
            yield AIMessageChunk(content="two")

    monkeypatch.setattr(loop, "agent_llm", Waiting())

    async def go():
        texts = []
        async for e in loop.run([Message(role="user", content="hi")], []):
            if e["type"] == "token":
                texts.append(e["text"])
                first_seen.set()
        return texts

    assert asyncio.run(asyncio.wait_for(go(), timeout=5)) == ["one ", "two"]


def test_route_streams_ndjson_events(client, kb, monkeypatch):
    monkeypatch.setattr(loop, "agent_llm", ScriptedModel("Hi there."))
    app.dependency_overrides[deps.current_user] = lambda: User(id=1, username="tester")
    app.state.agent_tools = []
    try:
        r = client.post("/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    finally:
        app.dependency_overrides.clear()
        del app.state.agent_tools
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines()]
    assert events[-1] == {"type": "sources", "sources": []}


def test_route_is_503_until_the_tools_are_connected(client, monkeypatch):
    app.dependency_overrides[deps.current_user] = lambda: User(id=1, username="tester")
    try:
        r = client.post("/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
