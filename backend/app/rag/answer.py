"""Turn retrieved chunks + chat history into model messages. Shared by the API and the evals."""

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.guardrails.injection import fence, with_canary
from app.prompts.rag import PLAIN_PROMPT, RAG_PROMPT
from app.schemas.chat import Message


def format_context(docs: list[Document]) -> str:
    return "\n\n".join(fence(d.page_content, d.metadata.get("source"), d.metadata.get("page")) for d in docs)


def build_messages(history: list[Message], docs: list[Document]) -> list[BaseMessage]:
    system = RAG_PROMPT.format(context=format_context(docs)) if docs else PLAIN_PROMPT
    messages: list[BaseMessage] = [SystemMessage(with_canary(system))]
    for m in history:
        messages.append(AIMessage(m.content) if m.role == "assistant" else HumanMessage(m.content))
    return messages


def cite(docs: list[Document]) -> list[dict]:
    """One source entry per (file, page), in retrieval order."""
    seen, sources = set(), []
    for d in docs:
        key = (d.metadata.get("source"), d.metadata.get("page"))
        if key not in seen:
            seen.add(key)
            sources.append({"source": key[0], "page": key[1], "doc_id": d.metadata.get("doc_id")})
    return sources
