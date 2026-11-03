"""LLM tracing with Langfuse: what the model was actually given and what it did, per answer.

OpenTelemetry (telemetry.py) already shows *how long* each step took. LLM tracing shows *what*
happened: the full prompt with the retrieved chunks, the model's output, tokens and cost, each
agent step and tool call, grouped per user and chat. It's where you go when someone reports
"the bot gave a wrong answer": was the right chunk retrieved? Did the prompt contain it? Did the
model ignore it? It also feeds evaluation: traces can be scored and turned into test datasets.

LangChain reports every model call, retriever and tool run to its callbacks; Langfuse provides a
callback handler that records them. Enabled with LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
(LANGFUSE_HOST for a self-hosted Langfuse, which runs in Docker next to this stack).
uv add langfuse

LangSmith is the same idea from the LangChain team (LANGSMITH_TRACING=true and a key, no code at
all); Arize Phoenix is an open-source alternative built on OpenTelemetry.

Traces contain prompts and answers, i.e. document content and user questions: treat the tracing
backend like the database it effectively is (access control, retention, PII_REDACTION=llm).
"""

import logging
from functools import cache

from app.core.config import LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

log = logging.getLogger(__name__)


@cache
def _handler():
    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        return None
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except ImportError:
        log.warning("LANGFUSE_* is set but the package isn't installed (uv add langfuse); tracing is off")
        return None
    Langfuse(public_key=LANGFUSE_PUBLIC_KEY, secret_key=LANGFUSE_SECRET_KEY, host=LANGFUSE_HOST)
    return CallbackHandler()


def config(name: str, user_id: int | None, request_id: str, **metadata) -> dict:
    """Keyword arguments for a model/graph call: {"config": {...}} with the Langfuse callback, or {}
    when tracing is off (so calls stay exactly as they were)."""
    handler = _handler()
    if handler is None:
        return {}
    return {"config": {
        "callbacks": [handler],
        "run_name": name,
        # Langfuse reads these keys to group traces by user and session
        "metadata": {"langfuse_user_id": str(user_id) if user_id else None, "langfuse_session_id": request_id,
                     **metadata},
    }}
