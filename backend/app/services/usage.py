"""LLM token usage and cost: one record per model call (chat answer, agent run, chat title).

Each record goes to three places:
  Prometheus  totals per endpoint and model (no user ids: see models/usage.py)  -> Grafana graphs
  PostgreSQL  one llm_usage row with the user id                                 -> Grafana "top users"
  the trace   gen_ai.usage.* attributes on the request's span                    -> Tempo

Token counts come from the model server (Ollama reports them on the last streamed chunk), not from
counting words. Embedding calls are not included: Ollama's embed API reports no token counts.
"""

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from opentelemetry import trace

from app.core import metrics
from app.core.config import LLM_PRICE_INPUT_PER_MTOK, LLM_PRICE_OUTPUT_PER_MTOK
from app.core.logging import user_id as current_user_id
from app.models import LlmUsage
from app.repositories import usage_store

log = logging.getLogger(__name__)


def cost_usd(input_tokens: int, output_tokens: int) -> Decimal:
    usd = (input_tokens * LLM_PRICE_INPUT_PER_MTOK + output_tokens * LLM_PRICE_OUTPUT_PER_MTOK) / 1_000_000
    return Decimal(str(round(usd, 6)))


class Tally:
    """Adds up the usage of several model calls that make one answer (the agent's tool steps)."""

    def __init__(self):
        self.input_tokens = self.output_tokens = 0
        self.model = ""

    def add(self, message) -> None:
        """`message` is the model's reply (an AIMessage, or streamed chunks added together)."""
        if message is None:
            return
        usage = getattr(message, "usage_metadata", None) or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        meta = getattr(message, "response_metadata", None) or {}
        self.model = meta.get("model_name") or meta.get("model") or self.model
        # Ollama: eval_count tokens generated in eval_duration nanoseconds
        if meta.get("eval_count") and meta.get("eval_duration"):
            metrics.llm_speed.labels(self.model).observe(meta["eval_count"] / (meta["eval_duration"] / 1e9))


async def record(endpoint: str, tally: Tally, fallback_model: str) -> None:
    """Never raises: losing one usage row is better than failing the user's request."""
    if not (tally.input_tokens or tally.output_tokens):
        return  # e.g. the stream failed before the model reported anything
    model = tally.model or fallback_model
    cost = cost_usd(tally.input_tokens, tally.output_tokens)
    metrics.llm_tokens.labels(endpoint, model, "input").inc(tally.input_tokens)
    metrics.llm_tokens.labels(endpoint, model, "output").inc(tally.output_tokens)
    metrics.llm_cost.labels(endpoint, model).inc(float(cost))
    span = trace.get_current_span()  # OpenTelemetry's GenAI attribute names
    span.set_attribute("gen_ai.request.model", model)
    span.set_attribute("gen_ai.usage.input_tokens", tally.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", tally.output_tokens)
    row = LlmUsage(user_id=current_user_id(), endpoint=endpoint, model=model, input_tokens=tally.input_tokens,
                   output_tokens=tally.output_tokens, cost_usd=cost, created_at=datetime.now(UTC))
    try:
        await asyncio.to_thread(usage_store.add, row)
    except Exception:
        log.exception("could not store LLM usage")
