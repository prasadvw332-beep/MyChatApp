"""Token usage and cost: counted from the model's own reports, summed over steps, stored per user."""

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk
from prometheus_client import REGISTRY

from app.api import deps
from app.api.routes import chat as chat_route
from app.core.logging import set_user_id
from app.main import app
from app.models import User
from app.rag import knowledge_base
from app.services import usage
from tests.fakes import FakeVectorStore


def _chunk(text, inp=0, out=0, final=False):
    """Like Ollama: token counts and timings only on the last chunk."""
    extra = {}
    if final:
        extra = {"usage_metadata": {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out},
                 "response_metadata": {"model_name": "test-model", "eval_count": out, "eval_duration": 2e9}}
    return AIMessageChunk(content=text, **extra)


class StreamingModel:
    def __init__(self, *chunks):
        self.chunks = chunks

    async def astream(self, messages):
        for c in self.chunks:
            yield c


@pytest.fixture
def saved(monkeypatch):
    rows = []
    monkeypatch.setattr(usage, "_save", rows.append)
    return rows


def _value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_cost_uses_the_configured_prices(monkeypatch):
    monkeypatch.setattr(usage, "LLM_PRICE_INPUT_PER_MTOK", 0.15)
    monkeypatch.setattr(usage, "LLM_PRICE_OUTPUT_PER_MTOK", 0.60)
    assert usage.cost_usd(1_000_000, 0) == Decimal("0.15")
    assert usage.cost_usd(2000, 1000) == Decimal("0.0009")  # 2000*0.15/1M + 1000*0.60/1M


def test_tally_sums_every_step_and_reads_the_model_name():
    tally = usage.Tally()
    tally.add(_chunk("partial"))  # no counts on intermediate chunks
    tally.add(_chunk("", 100, 10, final=True))
    tally.add(AIMessage("step 2", usage_metadata={"input_tokens": 150, "output_tokens": 20, "total_tokens": 170}))
    assert (tally.input_tokens, tally.output_tokens, tally.model) == (250, 30, "test-model")


def test_record_counts_metrics_stores_a_row_and_skips_empty_calls(saved):
    before = _value("llm_tokens_total", endpoint="chat", model="test-model", kind="output")
    tally = usage.Tally()
    tally.add(_chunk("", 40, 8, final=True))

    async def go():
        token = set_user_id(7)
        try:
            await usage.record("chat", tally, "fallback")
            await usage.record("chat", usage.Tally(), "fallback")  # stream failed: nothing reported
        finally:
            token.var.reset(token)

    asyncio.run(go())
    assert _value("llm_tokens_total", endpoint="chat", model="test-model", kind="output") == before + 8
    assert len(saved) == 1
    row = saved[0]
    assert (row.user_id, row.endpoint, row.model, row.input_tokens, row.output_tokens) == (7, "chat", "test-model", 40, 8)
    assert row.cost_usd == usage.cost_usd(40, 8)


def test_a_failing_database_does_not_fail_the_answer(monkeypatch):
    def down(_row):
        raise ConnectionError("db down")

    monkeypatch.setattr(usage, "_save", down)
    tally = usage.Tally()
    tally.add(_chunk("", 1, 1, final=True))
    asyncio.run(usage.record("chat", tally, "m"))  # logged, not raised


def test_chat_answer_records_usage_for_the_logged_in_user(saved, monkeypatch):
    monkeypatch.setattr(knowledge_base, "store", FakeVectorStore())
    monkeypatch.setattr(chat_route, "llm", StreamingModel(_chunk("Hello "), _chunk("there.", 52, 3, final=True)))
    app.dependency_overrides[deps.current_user] = lambda: User(id=1, username="tester")
    set_user_id(None)
    try:
        # the override skips current_user, which normally sets the log/user context: do it like it would
        monkeypatch.setattr(usage, "current_user_id", lambda: 1)
        r = TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    finally:
        app.dependency_overrides.clear()
    assert r.text == "Hello there."
    assert [(s.user_id, s.endpoint, s.input_tokens, s.output_tokens) for s in saved] == [(1, "chat", 52, 3)]


def test_agent_records_one_row_for_all_its_steps(saved, monkeypatch):
    from tests.test_agent import ScriptedModel, run_agent

    class CountingModel(ScriptedModel):
        def bind_tools(self, tools):
            bound = super().bind_tools(tools)
            bound.__class__ = CountingModel
            return bound

        async def astream(self, messages):
            async for chunk in super().astream(messages):
                yield chunk
            yield _chunk("", 100, 5, final=True)  # each step reports its own usage

    monkeypatch.setattr(knowledge_base, "store", FakeVectorStore(hits=[]))
    run_agent(CountingModel([("search_documents", {"query": "study"})], "Pune."), monkeypatch)
    assert [(s.endpoint, s.input_tokens, s.output_tokens) for s in saved] == [("agent", 200, 10)]
