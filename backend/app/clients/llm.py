"""Model clients: chat models and embeddings (Ollama), with an optional fallback model (the gateway).

LLM_FALLBACK="provider:model" puts a second model behind the chat and agent models: when a call to
Ollama fails (down, overloaded, timed out), LangChain's with_fallbacks() retries it on the fallback.

    openai:gpt-4o-mini            OpenAI                      uv add langchain-openai
    anthropic:claude-haiku-5-5    Anthropic                   uv add langchain-anthropic
    openai:<model name>           a LiteLLM proxy, with LLM_FALLBACK_BASE_URL=http://litellm:4000

The LiteLLM proxy is the production version of this idea: one OpenAI-compatible endpoint in front
of every provider, with routing, retries, budgets and keys managed in one place, so the app only
knows one API. The fallback's API key comes from its provider's usual variable (OPENAI_API_KEY,
ANTHROPIC_API_KEY). A fallback in the cloud means questions and documents leave your network when
it kicks in; PII_REDACTION=llm limits what goes with them.
"""

import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.cache.embeddings import CachedEmbeddings
from app.core.config import (
    AGENT_MODEL,
    CHAT_MODEL,
    EMBED_CACHE,
    EMBED_CACHE_MEMORY_ITEMS,
    EMBED_MODEL,
    GUARD_MODEL,
    LLM_FALLBACK,
    LLM_FALLBACK_BASE_URL,
    OLLAMA_TIMEOUT,
    OLLAMA_URL,
)
from app.repositories import embedding_cache_store


class PrefixedEmbeddings(Embeddings):
    """nomic-embed-text is trained with task prefixes; retrieval is noticeably worse without them."""

    def __init__(self, inner: Embeddings, doc_prefix: str, query_prefix: str):
        self.inner = inner
        self.doc_prefix = doc_prefix
        self.query_prefix = query_prefix

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents([self.doc_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(self.query_prefix + text)


# (document prefix, query prefix) per embedding model family; a new model is one more line here
EMBED_PREFIXES = {
    "nomic": ("search_document: ", "search_query: "),
}


# connect fails fast when Ollama is down; read allows for slow CPU generation
_TIMEOUT = {"timeout": httpx.Timeout(OLLAMA_TIMEOUT, connect=5)}


def _embeddings(model: str) -> Embeddings:
    base: Embeddings = OllamaEmbeddings(model=model, base_url=OLLAMA_URL, client_kwargs=_TIMEOUT)
    if EMBED_CACHE:
        # under the prefixes, so the cache key is the text the model really embeds
        base = CachedEmbeddings(base, model, EMBED_CACHE_MEMORY_ITEMS, embedding_cache_store)
    for family, (doc_prefix, query_prefix) in EMBED_PREFIXES.items():
        if family in model:
            return PrefixedEmbeddings(base, doc_prefix, query_prefix)
    return base


def fallback_model(temperature: float) -> BaseChatModel | None:
    """The LLM_FALLBACK model, or None. Its package is imported only when it's configured."""
    if not LLM_FALLBACK:
        return None
    provider, _, model = LLM_FALLBACK.partition(":")
    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise RuntimeError("LLM_FALLBACK=openai:... needs: uv add langchain-openai") from e
        return ChatOpenAI(model=model, temperature=temperature, base_url=LLM_FALLBACK_BASE_URL or None,
                          stream_usage=True)  # token counts on the last streamed chunk, like Ollama's
    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:
            raise RuntimeError("LLM_FALLBACK=anthropic:... needs: uv add langchain-anthropic") from e
        return ChatAnthropic(model=model, temperature=temperature)
    raise ValueError(f"LLM_FALLBACK must start with openai: or anthropic:, not {LLM_FALLBACK!r}")


def _with_fallback(primary: ChatOllama, temperature: float):
    """The primary model, or the primary with the fallback behind it. Fallbacks forward bind_tools,
    so the agent's tools reach whichever model answers."""
    fallback = fallback_model(temperature)
    return primary.with_fallbacks([fallback]) if fallback else primary


embeddings = _embeddings(EMBED_MODEL)
llm = _with_fallback(ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0.2, client_kwargs=_TIMEOUT), 0.2)
# short, deterministic output for chat titles; the cap stops a runaway answer from blocking Ollama
title_llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0, num_predict=20,
                       client_kwargs=_TIMEOUT)
# deterministic helper calls with room to write: query rewriting, graph extraction
utility_llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0, num_predict=512,
                         client_kwargs=_TIMEOUT)
# deterministic, so it picks tools consistently
agent_llm = _with_fallback(ChatOllama(model=AGENT_MODEL, base_url=OLLAMA_URL, temperature=0, client_kwargs=_TIMEOUT), 0)
# content safety classifier (app/guardrails/content.py): answers "safe" or "unsafe\nS1,S10"
guard_llm = ChatOllama(model=GUARD_MODEL, base_url=OLLAMA_URL, temperature=0, num_predict=10,
                       client_kwargs=_TIMEOUT) if GUARD_MODEL else None


def ping() -> None:
    httpx.get(f"{OLLAMA_URL}/api/version", timeout=3).raise_for_status()
