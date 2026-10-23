"""Ollama model clients: chat models and embeddings."""

from langchain_core.embeddings import Embeddings
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.config import CHAT_MODEL, EMBED_MODEL, OLLAMA_URL


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


_base_embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_URL)
embeddings: Embeddings = (
    PrefixedEmbeddings(_base_embeddings, "search_document: ", "search_query: ")
    if "nomic" in EMBED_MODEL
    else _base_embeddings
)
llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0.2)
# short, deterministic output for chat titles; the cap stops a runaway answer from blocking Ollama
title_llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0, num_predict=20)
