"""Ollama model clients: chat models and embeddings."""

import httpx
from langchain_core.embeddings import Embeddings
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.core.config import AGENT_MODEL, CHAT_MODEL, EMBED_MODEL, OLLAMA_TIMEOUT, OLLAMA_URL


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
    base = OllamaEmbeddings(model=model, base_url=OLLAMA_URL, client_kwargs=_TIMEOUT)
    for family, (doc_prefix, query_prefix) in EMBED_PREFIXES.items():
        if family in model:
            return PrefixedEmbeddings(base, doc_prefix, query_prefix)
    return base


embeddings = _embeddings(EMBED_MODEL)
llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0.2, client_kwargs=_TIMEOUT)
# short, deterministic output for chat titles; the cap stops a runaway answer from blocking Ollama
title_llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0, num_predict=20,
                       client_kwargs=_TIMEOUT)
# deterministic, so it picks tools consistently
agent_llm = ChatOllama(model=AGENT_MODEL, base_url=OLLAMA_URL, temperature=0, client_kwargs=_TIMEOUT)


def ping() -> None:
    httpx.get(f"{OLLAMA_URL}/api/version", timeout=3).raise_for_status()
